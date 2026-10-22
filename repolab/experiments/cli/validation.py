"""Validating a proposed CLI experiment: what may run on the learner's node.

The Spark experiment has an allowlist of one variable. A CQL experiment runs
statements a model wrote, so the gate is stricter in a different way:

1. **Every statement passes the terminal's own allowlist**
   (:mod:`repolab.experiences.cli.safety`) -- the same check a learner's typing
   faces, so an experiment cannot do anything a learner could not.
2. **Every statement stays inside the experiment's keyspace.** RepoLab creates a
   fresh keyspace per run and selects it; a statement that switches keyspace,
   creates or alters one, or names another keyspace's table would escape the
   isolation every comparison depends on. Reading Cassandra's own system tables
   is the one exception, because it changes nothing.
3. **The shape is bounded**: two to four variants, a few statements each, a few
   repeats, and a cost estimated in advance and checked against a budget.

Everything validation changes is recorded on the plan, so approval is given to
what will run.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from repolab.builds.models import BuildArtifact
from repolab.experiences.cli.safety import SYSTEM_KEYSPACES, Refusal, check, split_statements
from repolab.experiments.cli.models import CliExperimentPlan, CliProposedPlan, CliVariantSpec
from repolab.experiments.validation import PlanRejected, ValidationResult

MIN_VARIANTS = 2
MAX_VARIANTS = 4
MAX_STATEMENTS_PER_VARIANT = 6
MAX_SETUP_STATEMENTS = 8
MAX_STATEMENT_LENGTH = 1000
MAX_LABEL_LENGTH = 48

#: Verbs a statement inside an experiment may not use, though a terminal may.
#: ``USE`` would leave the experiment's keyspace; ``CONSISTENCY`` changes the
#: session for the statements after it in a way the plan would not show.
_FORBIDDEN_VERBS = frozenset({"use", "consistency"})


class CliExperimentBudget(BaseModel):
    """What a CLI experiment may cost."""

    max_repeats: int = Field(default=3, ge=1)
    max_statements: int = Field(default=80, ge=1)
    max_seconds: float = Field(default=900.0, gt=0)

    startup_seconds: float = Field(default=120.0, ge=0)
    """Restoring the checkpoint and waiting for the node to answer. Observed ~60-90 s."""

    seconds_per_statement: float = Field(default=3.0, gt=0)
    """One ``cqlsh -e`` invocation, which starts a Python process every time."""

    def estimate_seconds(self, statements: int) -> float:
        return round(self.startup_seconds + statements * self.seconds_per_statement, 1)


class CliValidationResult(ValidationResult):
    plan: CliExperimentPlan | None = None  # type: ignore[assignment]


def validate_cli_plan(
    proposal: CliProposedPlan,
    *,
    artifact: BuildArtifact,
    budget: CliExperimentBudget | None = None,
) -> CliValidationResult:
    """Turn an untrusted proposal into an executable plan, or refuse it."""
    budget = budget if budget is not None else CliExperimentBudget()
    adjustments: list[str] = []

    setup, problem = _clean_statements(proposal.setup, where="setup", notes=adjustments)
    if problem is not None:
        return CliValidationResult(rejection=problem)
    if len(setup) > MAX_SETUP_STATEMENTS:
        return _reject(
            "too much setup", f"{len(setup)} setup statements; the limit is {MAX_SETUP_STATEMENTS}"
        )

    if not MIN_VARIANTS <= len(proposal.variants) <= MAX_VARIANTS:
        return _reject(
            "wrong number of variants",
            f"got {len(proposal.variants)}; an experiment compares "
            f"{MIN_VARIANTS} to {MAX_VARIANTS} variants",
        )

    variants: list[CliVariantSpec] = []
    labels: set[str] = set()
    for position, variant in enumerate(proposal.variants, start=1):
        label = _clean_label(variant.label) or f"variant {position}"
        if label.lower() in labels:
            label = f"{label} ({position})"
            adjustments.append(f"renamed a duplicate variant label to {label!r}")
        labels.add(label.lower())

        statements, problem = _clean_statements(
            variant.statements, where=f"variant {label!r}", notes=adjustments
        )
        if problem is not None:
            return CliValidationResult(rejection=problem)
        if not statements:
            return _reject("an empty variant", f"variant {label!r} runs no statements")
        if len(statements) > MAX_STATEMENTS_PER_VARIANT:
            return _reject(
                "a variant is too long",
                f"variant {label!r} has {len(statements)} statements; "
                f"the limit is {MAX_STATEMENTS_PER_VARIANT}",
            )
        variants.append(CliVariantSpec(label=label, statements=statements))

    if len({tuple(variant.statements) for variant in variants}) < len(variants):
        return _reject("variants are identical", "two variants run exactly the same statements")

    repeats = max(1, min(proposal.repeats, budget.max_repeats))
    if repeats != proposal.repeats:
        adjustments.append(f"repeats {proposal.repeats} -> {repeats} (budget)")
    if repeats == 1:
        adjustments.append("a single repeat gives no spread, so timings carry no error bar")

    runs = len(variants) * repeats
    # Each run also creates, selects and drops its keyspace: three more invocations.
    statements = sum(len(setup) + len(variant.statements) + 3 for variant in variants) * repeats
    seconds = budget.estimate_seconds(statements)
    if statements > budget.max_statements:
        return _reject(
            "over the statement budget",
            f"{statements} statements, over the limit of {budget.max_statements}",
        )
    if seconds > budget.max_seconds:
        return _reject(
            "over the time budget",
            f"an estimated {seconds:.0f}s exceeds the limit of {budget.max_seconds:.0f}s",
        )

    return CliValidationResult(
        plan=CliExperimentPlan(
            question=proposal.question,
            hypothesis=proposal.hypothesis,
            setup=setup,
            variants=variants,
            repeats=repeats,
            environment=environment_of(artifact),
            fixed_settings={
                **environment_of(artifact),
                "isolation": (
                    "every run gets a fresh keyspace (SimpleStrategy, replication 1) "
                    "created and selected by RepoLab"
                ),
                "node": "one node, restored once from the checkpoint and shared by all runs",
                "order": "repeat by repeat, every variant once per repeat",
            },
            estimated_runs=runs,
            estimated_statements=statements,
            estimated_seconds=seconds,
            adjustments=adjustments,
        )
    )


def environment_of(artifact: BuildArtifact) -> dict[str, str]:
    """The build an experiment runs on, as the approval card shows it."""
    return {
        "build": artifact.kind.value,
        "fidelity": artifact.fidelity.value,
        "version": artifact.version or "unknown",
        "checkpoint": artifact.checkpoint_tag or "",
        "cli": artifact.cli_command or "",
        "memory": artifact.memory or "",
    }


def _reject(reason: str, detail: str) -> CliValidationResult:
    return CliValidationResult(rejection=PlanRejected(reason=reason, detail=detail))


def _clean_label(label: str) -> str:
    cleaned = re.sub(r"[^\w .,'()/-]+", "", label).strip()
    return re.sub(r"\s+", " ", cleaned)[:MAX_LABEL_LENGTH].strip()


def _clean_statements(
    raw: list[str], *, where: str, notes: list[str]
) -> tuple[list[str], PlanRejected | None]:
    """Split, check and normalise statements; the first refusal rejects the plan."""
    cleaned: list[str] = []
    for entry in raw:
        parts = split_statements(entry)
        if len(parts) > 1:
            notes.append(f"split a multi-statement entry in {where} into {len(parts)} statements")
        for part in parts:
            if len(part) > MAX_STATEMENT_LENGTH:
                return [], PlanRejected(
                    reason="a statement is too long",
                    detail=f"{where}: {len(part)} characters; the limit is {MAX_STATEMENT_LENGTH}",
                )
            problem = statement_problem(part)
            if problem is not None:
                return [], PlanRejected(
                    reason="a statement is not allowed", detail=f"{where}: {problem}"
                )
            cleaned.append(part.rstrip(";").strip())
    return cleaned, None


def statement_problem(statement: str) -> str | None:
    """Why *statement* may not run inside an experiment, or ``None``."""
    outcome = check(statement)
    if isinstance(outcome, Refusal):
        return f"{statement[:80]!r} is refused: {outcome}"

    verb = outcome.verb
    if verb in _FORBIDDEN_VERBS:
        return f"{verb.upper()} would change the experiment's session; RepoLab selects the keyspace"

    bare = _without_literals(statement).lower()
    if re.search(r"\bkeyspace\b", bare):
        return (
            f"{statement[:80]!r} creates or changes a keyspace; RepoLab creates a fresh one "
            "for every run"
        )

    for qualifier in re.findall(r"\b([a-z_][a-z0-9_]*)\s*\.\s*[a-z_\"]", bare):
        if qualifier in SYSTEM_KEYSPACES and verb in {"select", "describe", "desc"}:
            continue
        return (
            f"{statement[:80]!r} names {qualifier}.*; statements must use unqualified table "
            "names so they stay in the experiment's keyspace"
        )
    return None


def _without_literals(statement: str) -> str:
    """The statement with string literals blanked, so data is never read as syntax."""
    return re.sub(r"'(?:[^']|'')*'", "''", statement)
