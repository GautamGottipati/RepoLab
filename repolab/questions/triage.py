"""Deciding what kind of question was asked, and what to do about it.

The order follows the classification rule in CLAUDE.md: deterministic rules
first, a model only when the rules do not settle it, and deterministic code with
the last word either way.

That last word is the **backend gate**. A model may say a question is testable;
only this module decides whether RepoLab can test it here, from facts it can
check -- whether the repository has a runnable Spark playground, or a built,
smoke-tested environment with a CLI an experiment knows how to drive. A
testable question with nothing to run it on is answered instead, and the answer
says why, so "this could be tested" is never quietly turned into "this was
explained".
"""

from __future__ import annotations

import re
from typing import Any, Protocol

import structlog

from repolab.builds.models import BuildArtifact
from repolab.experiments.validation import variable_in_question
from repolab.llm import LLMError, Purpose
from repolab.manifest.models import RepoManifest, SurfaceType
from repolab.questions.models import (
    MAX_REASON_LENGTH,
    ExperimentBackend,
    QuestionKind,
    Route,
    Triage,
    TriageVerdict,
)

log = structlog.get_logger(__name__)

#: Phrasings that ask what running the software would show.
_TESTABLE = (
    r"\bwhat (happens|would happen|will happen) (if|when)\b",
    r"\bwhat if\b",
    r"\bhow (does|do|would|will) .+ (affect|change|impact|influence)\b",
    r"\b(effect|impact|influence) of\b",
    r"\bif i (change|set|increase|decrease|raise|lower|drop|remove|add|insert|query|use|run)\b",
    r"\b(faster|slower|latency|throughput|benchmark|measure|compare|comparison)\b",
    r"\b(does|will|would|can) (it|this|cassandra|spark|the query|the job) "
    r"(fail|break|error|reject|allow)\b",
    r"\bwhy does (this|the) .+ use \d+\b",
    r"\b(try|test|experiment) (it|this|whether|if)\b",
)

#: Phrasings that ask about an idea rather than this code.
_CONCEPTUAL = (
    r"^\s*(what|who) (is|are|was|were) (a |an |the )?[\w .+-]+\??\s*$",
    r"\b(explain|define|definition|meaning|overview|introduction|concept of)\b",
    r"^\s*what does .+ (mean|stand for)\b",
    r"\bdifference between\b",
    r"\bwhat is the (purpose|idea|point) of\b",
)

#: Phrasings that ask about this repository's code.
_REPOSITORY = (
    r"\bwhere (is|are|does|do)\b",
    r"\bwhich (file|files|class|classes|module|modules|function|functions|package|method)\b",
    r"\b(in|of) (this|the) (repo|repository|codebase|code|project|source)\b",
    r"\bhow is .+ (implemented|built|structured|organi[sz]ed|configured)\b",
    r"\b(implemented|implementation|entry ?point|source code|call(s|ed)? by)\b",
    r"\bhow does (this|the) (repo|repository|code|project|codebase)\b",
)

TRIAGE_SYSTEM_PROMPT = (
    "You classify a question a developer asked about a software repository.\n"
    "\n"
    "Kinds:\n"
    "  conceptual - about an idea, or the software in general (what is X, explain Y).\n"
    "  repository - about this repository's code (where is X, how is Y implemented).\n"
    "  testable   - about behaviour that running the software and observing it "
    "could show (what happens if, how does X affect Y, does Z fail).\n"
    "\n"
    "Choose testable only when running something would genuinely answer it. "
    "You do not decide whether RepoLab can run it."
)

TRIAGE_PROMPT_TEMPLATE = """\
Repository: {repo_url}
Languages: {languages}
Frameworks: {frameworks}
Interaction surfaces: {surfaces}

Question:
{question}

Classify the question."""


class TriageRouter(Protocol):
    async def nano(self, **kwargs: Any) -> Any: ...


def _hits(patterns: tuple[str, ...], text: str) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text))


def rule_kind(question: str) -> QuestionKind | None:
    """The kind the phrasing alone settles, or ``None`` when it is ambiguous.

    Settled only when exactly one kind's phrasings match. A question that reads
    as both ("explain what happens if...") is ambiguous, and guessing would be
    exactly the kind of decision a rule should not make.
    """
    text = question.strip().lower()
    scores = {
        QuestionKind.TESTABLE: _hits(_TESTABLE, text),
        QuestionKind.CONCEPTUAL: _hits(_CONCEPTUAL, text),
        QuestionKind.REPOSITORY: _hits(_REPOSITORY, text),
    }
    matched = [kind for kind, score in scores.items() if score > 0]
    return matched[0] if len(matched) == 1 else None


def _fallback_kind(question: str) -> QuestionKind:
    """Best effort without a model: the most-matched kind, conceptual if none."""
    text = question.strip().lower()
    ranked = sorted(
        (
            (_hits(_TESTABLE, text), QuestionKind.TESTABLE),
            (_hits(_REPOSITORY, text), QuestionKind.REPOSITORY),
            (_hits(_CONCEPTUAL, text), QuestionKind.CONCEPTUAL),
        ),
        key=lambda pair: -pair[0],
    )
    score, kind = ranked[0]
    return kind if score > 0 else QuestionKind.CONCEPTUAL


# ---------------------------------------------------------------------------
# What this repository can run
# ---------------------------------------------------------------------------


def spark_backend_available(manifest: RepoManifest | None) -> bool:
    """Whether this repository's Spark playground is runnable.

    Decided by the experience catalog -- the same check that decides whether the
    Playground tab opens -- rather than by a second opinion kept here.
    """
    if manifest is None:
        return False
    from repolab.experiences.catalog import ExperienceStatus, experiences_for

    return any(
        item.surface is SurfaceType.DATA_PLAYGROUND and item.status is ExperienceStatus.RUNNABLE
        for item in experiences_for(manifest).experiences
    )


def cli_artifact(artifacts: list[BuildArtifact]) -> BuildArtifact | None:
    """The best built environment a CLI experiment could run against.

    It must be real (not a Mini Lab), smoke-tested, restorable, and driven by
    cqlsh: the statement allowlist an experiment's commands must pass is a CQL
    allowlist, and running another CLI's commands under it would be running
    commands nobody vetted.
    """
    for artifact in artifacts:
        if artifact.simulated or not artifact.ready:
            continue
        if artifact.checkpoint_tag is None or not artifact.cli_command:
            continue
        if not artifact.cli_command.rstrip().endswith("cqlsh"):
            continue
        return artifact
    return None


def _no_backend_reason(manifest: RepoManifest | None, artifacts: list[BuildArtifact]) -> str:
    if manifest is None:
        return "this repository has not been investigated yet"
    surfaces = {surface.type for surface in manifest.interaction_surfaces}
    if SurfaceType.CLI in surfaces:
        if not artifacts:
            return (
                "no environment has been built for it yet. Build one from the Learn tab, "
                "then ask again to test it"
            )
        if all(artifact.simulated for artifact in artifacts):
            return "only a simulated Mini Lab is available, and simulations are never measured"
        return "its built environment has no CLI an experiment knows how to drive safely"
    return "RepoLab has no runnable environment for this repository's interaction surfaces"


# ---------------------------------------------------------------------------


async def triage(
    question: str,
    *,
    manifest: RepoManifest | None,
    artifacts: list[BuildArtifact] | None = None,
    router: TriageRouter | None = None,
    design_available: bool = True,
) -> Triage:
    """Classify *question* and decide whether to answer it or test it.

    *design_available* is whether a model can design a non-Spark experiment.
    The Spark sweep has a deterministic fallback; a CQL experiment for an
    arbitrary question does not, and pretending otherwise would mean running a
    canned experiment that does not address what was asked.
    """
    artifacts = list(artifacts or [])
    spark = spark_backend_available(manifest)

    kind, reason, decided_by = await _classify(question, manifest=manifest, router=router)

    # The demo path: a question naming the Spark variable, on a repository whose
    # Spark job RepoLab can run, is testable whatever the wording.
    if spark and variable_in_question(question) is not None and kind is not QuestionKind.TESTABLE:
        kind, decided_by = QuestionKind.TESTABLE, "rules"
        reason = "it names a Spark setting this repository's job can be run with"

    if kind is not QuestionKind.TESTABLE:
        return Triage(kind=kind, reason=reason, decided_by=decided_by, route=Route.ANSWER)

    if spark:
        return Triage(
            kind=kind,
            reason=reason,
            decided_by=decided_by,
            route=Route.EXPERIMENT,
            backend=ExperimentBackend.SPARK,
        )

    if cli_artifact(artifacts) is not None:
        if design_available:
            return Triage(
                kind=kind,
                reason=reason,
                decided_by=decided_by,
                route=Route.EXPERIMENT,
                backend=ExperimentBackend.CLI,
            )
        downgraded = (
            "This question could be tested against the built environment, but designing "
            "that experiment needs a model and none is configured."
        )
    else:
        downgraded = (
            "This question could be tested, but it cannot be run here: "
            f"{_no_backend_reason(manifest, artifacts)}."
        )

    return Triage(
        kind=kind,
        reason=reason,
        decided_by=decided_by,
        route=Route.ANSWER,
        downgraded=downgraded,
    )


async def _classify(
    question: str, *, manifest: RepoManifest | None, router: TriageRouter | None
) -> tuple[QuestionKind, str, str]:
    """The question's kind, its reason, and which step decided it."""
    settled = rule_kind(question)
    if settled is not None:
        return settled, _rule_reason(settled), "rules"

    if router is not None:
        try:
            completion = await router.nano(
                purpose=Purpose.QUESTION_TRIAGE,
                schema=TriageVerdict,
                system=TRIAGE_SYSTEM_PROMPT,
                prompt=_triage_prompt(question, manifest),
                max_tokens=512,
            )
            verdict: TriageVerdict = completion.value
            return verdict.kind, verdict.reason[:MAX_REASON_LENGTH], "model"
        except LLMError as exc:
            log.warning("questions.triage_failed", error=str(exc))

    fallback = _fallback_kind(question)
    return fallback, _rule_reason(fallback), "fallback"


def _rule_reason(kind: QuestionKind) -> str:
    return {
        QuestionKind.TESTABLE: "it asks what running the software would show",
        QuestionKind.CONCEPTUAL: "it asks about an idea rather than a behaviour to observe",
        QuestionKind.REPOSITORY: "it asks about where or how this repository's code does something",
    }[kind]


def _triage_prompt(question: str, manifest: RepoManifest | None) -> str:
    if manifest is None:
        return TRIAGE_PROMPT_TEMPLATE.format(
            repo_url="(unknown)",
            languages="(unknown)",
            frameworks="(unknown)",
            surfaces="(unknown)",
            question=question,
        )
    return TRIAGE_PROMPT_TEMPLATE.format(
        repo_url=manifest.repo_url,
        languages=", ".join(manifest.languages) or "(none detected)",
        frameworks=", ".join(manifest.frameworks) or "(none detected)",
        surfaces=", ".join(
            f"{surface.type.value} ({surface.name})" for surface in manifest.interaction_surfaces
        )
        or "(none detected)",
        question=question,
    )
