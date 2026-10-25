"""The Experiment Agent, for a repository whose real surface is a CLI.

Same shape as the Spark agent, same gate:

    question
       |
    investigate the repository and its official docs   (read-only)
       |
    Ultra proposes variants: CQL statements to compare  (untrusted)
       |
    validate against the terminal allowlist + keyspace isolation + budget
       |
    WAIT FOR APPROVAL
       |
    run every variant on the real node, in fresh keyspaces
       |
    statistics in Python, from cqlsh's own output
       |
    Ultra explains; the explanation is checked against the statistics

What differs is what is *measured*. Not an event log but each statement's real
outcome: whether the server accepted it, the error class it returned if not,
the row count cqlsh printed, and the wall clock around the invocation.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from repolab.agents.models import AgentLimits, AgentRun
from repolab.agents.repository_tools import build_repository_tools
from repolab.agents.runtime import AgentRuntime
from repolab.agents.tools import ToolRegistry
from repolab.builds.models import BuildArtifact
from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.evidence.models import (
    CodeReference,
    DocumentationReference,
    EvidenceBundle,
    LogReference,
)
from repolab.experiments.cli.models import (
    CliExperiment,
    CliProposedPlan,
    CliReproducibilityRecord,
    CliStatistics,
    CliVariantRun,
)
from repolab.experiments.cli.runner import CliVariantRunner
from repolab.experiments.cli.statistics import allowed_numbers, compute_cli_statistics
from repolab.experiments.cli.validation import CliExperimentBudget, validate_cli_plan
from repolab.experiments.explanation import (
    ClaimKind,
    Explanation,
    UnsupportedClaim,
    VerifiedExplanation,
    _matches,
    numbers_in,
)
from repolab.experiments.investigation import code_references_from, documentation_from
from repolab.failures import describe, explain
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.tree import build_file_tree
from repolab.interfaces import ResearchProvider
from repolab.llm import LLMError, Purpose
from repolab.manifest.models import RepoManifest
from repolab.questions.investigation import (
    QuestionInvestigationPlanner,
    official_domain,
    overview_file,
)
from repolab.questions.models import ArchitectureFocus
from repolab.sandbox.provider import SandboxProvider

log = structlog.get_logger(__name__)

STEP_LABEL = "experiment"
AGENT_NAME = "cli_experiment_agent"

#: Ultra writes its reasoning before its JSON; both come out of this budget.
DESIGN_MAX_TOKENS = 4096
EXPLANATION_MAX_TOKENS = 8192
EXPLANATION_TIMEOUT_SECONDS = 180.0

#: Output excerpt per statement given to the explanation.
OUTPUT_EXCERPT_CHARS = 600

DESIGN_SYSTEM_PROMPT = (
    "You design controlled experiments against a real, running database node, "
    "driven through its command-line shell.\n"
    "\n"
    "You do not run anything and you do not report results. You propose CQL "
    "statements: a setup that every variant shares, and two to four variants "
    "that differ in exactly one respect the question is about. A separate "
    "validation step checks your proposal and may refuse it.\n"
    "\n"
    "Rules:\n"
    "- RepoLab creates a fresh keyspace for every run and selects it. Never write "
    "USE, never create, alter or drop a keyspace, and never qualify a table with "
    "a keyspace name. You may read Cassandra's system tables (system.local, "
    "system_schema.*) with SELECT.\n"
    "- Plain CQL only: no cqlsh commands such as SOURCE, COPY, CAPTURE or "
    "CONSISTENCY.\n"
    "- Keep it small: a few rows of setup data, at most six statements per "
    "variant.\n"
    "- Make the difference observable in what cqlsh prints: an accepted or "
    "refused statement, an error message, a row count.\n"
    "- State a hypothesis the output could contradict."
)

DESIGN_PROMPT_TEMPLATE = """\
Question from the user:
{question}

Repository: {repo_url} at commit {commit_sha}

The environment every variant runs on (built from this repository):
{environment}

What it can and cannot do:
{capabilities}

What a read-only investigation of the repository found:
{findings}

Relevant repository lines:
{code}

Official documentation retrieved:
{documentation}

At most {max_repeats} repeats. Propose the experiment."""

EXPLANATION_SYSTEM_PROMPT = (
    "You explain the results of a controlled experiment run against a real "
    "database node through its shell.\n"
    "\n"
    "Every number you are given was computed by deterministic code from the "
    "shell's own output and from timing each invocation. Do not calculate, "
    "estimate or invent any figure; quote numbers exactly as given. The outputs "
    "shown are verbatim.\n"
    "\n"
    "Label every claim:\n"
    "  measured   - a result present in the statistics. In 'support', put one "
    "name from the 'metrics' list, copied exactly.\n"
    "  documented - behaviour stated by one of the documentation excerpts. Put "
    "its URL in 'support'.\n"
    "  inferred   - a conclusion you draw. Say which claims it rests on.\n"
    "\n"
    "Durations include starting the shell itself, so small differences between "
    "them are not meaningful; prefer whether a statement succeeded, its error and "
    "its row count. If the outputs do not support the hypothesis, say so."
)


class CliExperimentAgent:
    """Turns a question into measured CLI behaviour, pausing for approval."""

    name = AGENT_NAME

    def __init__(
        self,
        *,
        repo_url: str,
        commit_sha: str,
        artifact: BuildArtifact,
        repository_root: Path | None = None,
        manifest: RepoManifest | None = None,
        router: Any | None = None,
        research: ResearchProvider | None = None,
        emitter: EventEmitter | None = None,
        budget: CliExperimentBudget | None = None,
        limits: AgentLimits | None = None,
        focus: ArchitectureFocus | None = None,
    ) -> None:
        self._focus = focus
        self.repo_url = repo_url
        self.commit_sha = commit_sha
        self.artifact = artifact
        self.repository_root = Path(repository_root) if repository_root is not None else None
        self._manifest = manifest
        self._router = router
        self._research = research
        self._emitter = emitter if emitter is not None else NullEmitter()
        self._budget = budget if budget is not None else CliExperimentBudget()
        self._limits = limits if limits is not None else AgentLimits(max_iterations=10)

    # -- propose ------------------------------------------------------------

    async def propose(
        self,
        question: str,
        *,
        job_id: str | None = None,
        experiment_id: str | None = None,
    ) -> CliExperiment:
        """Investigate, design, validate, and stop at the approval gate."""
        fields: dict[str, Any] = {
            "question": question,
            "repo_url": self.repo_url,
            "commit_sha": self.commit_sha,
            "job_id": job_id,
        }
        if experiment_id is not None:
            fields["id"] = experiment_id
        experiment = CliExperiment(**fields)

        self._emitter.emit(
            EventType.AGENT_STARTED,
            "designing an experiment to run on this repository's real environment",
            step=STEP_LABEL,
            data={"question": question[:200], "experiment_id": experiment.id, "kind": "cli"},
        )

        if self._router is None:
            experiment.fail("designing a CLI experiment needs a model, and none is configured")
            return experiment

        run, references, documentation = await self._investigate(question)

        self._emitter.emit(EventType.AGENT_STATUS, "designing the experiment", step=STEP_LABEL)
        try:
            completion = await self._router.ultra(
                purpose=Purpose.EXPERIMENT_DESIGN,
                schema=CliProposedPlan,
                system=DESIGN_SYSTEM_PROMPT,
                prompt=self._design_prompt(question, run, references, documentation),
                max_tokens=DESIGN_MAX_TOKENS,
            )
        except LLMError as exc:
            failure = explain(exc)
            experiment.fail(f"the experiment could not be designed: {failure.headline}")
            self._emitter.emit(EventType.FAILED, experiment.error or "", step=STEP_LABEL)
            return experiment

        experiment.proposal = completion.value
        result = validate_cli_plan(completion.value, artifact=self.artifact, budget=self._budget)
        if result.plan is None:
            assert result.rejection is not None
            self._emitter.emit(
                EventType.FAILED,
                f"the proposed experiment was refused: {result.rejection}",
                step=STEP_LABEL,
                data={"reason": result.rejection.reason, "detail": result.rejection.detail},
            )
            experiment.refuse(result.rejection)
            return experiment

        plan = result.plan
        experiment.plan = plan
        experiment.reproducibility = self.reproducibility_for(plan.repeats)
        experiment.evidence = EvidenceBundle(
            code_references=references, documentation=documentation
        )
        experiment.await_approval()

        self._emitter.emit(
            EventType.APPROVAL_REQUIRED,
            f"approval needed: {plan.summary()}",
            step=STEP_LABEL,
            data={"experiment_id": experiment.id, **experiment.approval_prompt()},
        )
        return experiment

    async def _investigate(
        self, question: str
    ) -> tuple[AgentRun | None, list[CodeReference], list[DocumentationReference]]:
        if self.repository_root is None:
            return None, [], []
        tree = build_file_tree(self.repository_root)
        context = DetectionContext(root=self.repository_root, tree=tree, commit_sha=self.commit_sha)
        _, tool_list = build_repository_tools(context, ReadBudget(), research=self._research)
        planner = QuestionInvestigationPlanner(
            question,
            overview=overview_file([entry.path for entry in tree.files]),
            domain=official_domain(self._manifest, self.repo_url),
            research_available=self._research is not None,
            subject=self.repo_url.rstrip("/").split("/")[-1],
        )
        runtime = AgentRuntime(
            agent=self.name,
            objective=f"Gather the evidence needed to design an experiment for: {question[:200]}",
            registry=ToolRegistry(tool_list),
            planner=planner,
            emitter=self._emitter,
            limits=self._limits,
            step_label=STEP_LABEL,
        )
        run = await runtime.run(lambda: {})
        return (
            run,
            code_references_from(run),
            documentation_from(run, version=self.artifact.version),
        )

    def _design_prompt(
        self,
        question: str,
        run: AgentRun | None,
        references: list[CodeReference],
        documentation: list[DocumentationReference],
    ) -> str:
        capabilities = self.artifact.capabilities
        lines = [f"- can: {item}" for item in capabilities.capabilities]
        lines += [f"- cannot: {item}" for item in capabilities.limitations]
        lines += [f"- note: {item}" for item in capabilities.deviations]
        findings = [step.status for step in (run.steps if run is not None else []) if step.status]
        return DESIGN_PROMPT_TEMPLATE.format(
            question=question,
            repo_url=self.repo_url,
            commit_sha=self.commit_sha[:12],
            environment="\n".join(
                f"- {key}: {value}"
                for key, value in {
                    "build": self.artifact.kind.value,
                    "fidelity": self.artifact.fidelity.value,
                    "version": self.artifact.version or "unknown",
                    "shell": self.artifact.cli_command or "cqlsh",
                    "topology": "a single node",
                }.items()
            ),
            capabilities="\n".join(lines) or "- (none recorded)",
            findings="\n".join(f"- {line}" for line in findings[:10]) or "- (nothing found)",
            code="\n".join(
                f"- {ref.path}:{ref.start_line}: {(ref.excerpt or '').strip()[:160]}"
                for ref in references[:8]
            )
            or "- (no matches)",
            documentation="\n".join(
                f"- {doc.url}: {(doc.excerpt or '').strip()[:300]}" for doc in documentation[:3]
            )
            or "- (none retrieved)",
            max_repeats=self._budget.max_repeats,
        ) + (f"\n\n{self._focus.describe()}" if self._focus else "")

    def reproducibility_for(self, repeats: int) -> CliReproducibilityRecord:
        return CliReproducibilityRecord(
            repo_url=self.repo_url,
            commit_sha=self.commit_sha,
            build_kind=self.artifact.kind.value,
            fidelity=self.artifact.fidelity.value,
            application_version=self.artifact.version,
            checkpoint_tag=self.artifact.checkpoint_tag,
            cli_command=self.artifact.cli_command,
            memory=self.artifact.memory,
            repeats=repeats,
            isolation="a fresh keyspace per run, on one node restored from the checkpoint",
        )

    # -- execute ------------------------------------------------------------

    async def execute(
        self,
        experiment: CliExperiment,
        *,
        provider: SandboxProvider,
        on_run: Callable[[CliVariantRun], Awaitable[None]] | None = None,
        ready_timeout_seconds: float | None = None,
    ) -> CliExperiment:
        """Run an approved experiment through to an explanation.

        Raises:
            ApprovalRequiredError: the experiment has not been approved.
        """
        plan = experiment.plan
        if plan is None:
            experiment.fail("no validated plan to execute")
            return experiment

        experiment.start()
        if experiment.reproducibility is not None:
            experiment.reproducibility.started_at = experiment.started_at

        async def record(run: CliVariantRun) -> None:
            experiment.runs.append(run)
            if on_run is not None:
                await on_run(run)

        runner = CliVariantRunner(
            provider,
            self.artifact,
            repo_url=self.repo_url,
            emitter=self._emitter,
            ready_timeout_seconds=ready_timeout_seconds,
        )
        try:
            await runner.run(
                plan, experiment_id=experiment.id, status=experiment.status, on_run=record
            )
        except Exception as exc:
            failure = explain(exc)
            self._emitter.emit(
                EventType.FAILED, failure.summary, step=STEP_LABEL, data=failure.model_dump_public()
            )
            experiment.fail(describe(exc))
            raise

        statistics = compute_cli_statistics(experiment.runs)
        experiment.statistics = statistics

        prior = experiment.evidence or EvidenceBundle()
        experiment.evidence = EvidenceBundle(
            code_references=list(prior.code_references),
            documentation=list(prior.documentation),
            measurements=[m for run in experiment.runs for m in run.to_measurements()],
            logs=_output_logs(experiment.runs),
        )

        self._emitter.emit(
            EventType.STEP_DONE,
            f"ran {len(experiment.runs)} runs across {len(plan.variants)} variants",
            step=STEP_LABEL,
            data={"runs": len(experiment.runs), "variants": len(plan.variants)},
        )

        experiment.explanation = await self.explain(experiment)
        experiment.complete()
        if experiment.reproducibility is not None:
            experiment.reproducibility.finished_at = experiment.finished_at
        return experiment

    # -- explain ------------------------------------------------------------

    async def explain(self, experiment: CliExperiment) -> VerifiedExplanation | None:
        plan, statistics = experiment.plan, experiment.statistics
        if plan is None or statistics is None or self._router is None:
            return None

        self._emitter.emit(EventType.AGENT_STATUS, "interpreting what came back", step=STEP_LABEL)
        try:
            completion = await self._router.ultra(
                purpose=Purpose.RESULT_INTERPRETATION,
                schema=Explanation,
                system=EXPLANATION_SYSTEM_PROMPT,
                prompt=explanation_prompt(experiment),
                max_tokens=EXPLANATION_MAX_TOKENS,
                timeout_seconds=EXPLANATION_TIMEOUT_SECONDS,
            )
        except LLMError as exc:
            failure = explain(exc)
            experiment.explanation_error = f"{failure.headline} {failure.why}"[:500]
            self._emitter.emit(
                EventType.AGENT_STATUS,
                "the explanation could not be produced; the outputs stand on their own",
                step=STEP_LABEL,
            )
            return None

        documentation = experiment.evidence.documentation if experiment.evidence else []
        return verify_cli_explanation(
            completion.value, statistics=statistics, documentation=list(documentation)
        )


def explanation_prompt(experiment: CliExperiment) -> str:
    """What the explanation model is given: the plan, the statistics, the outputs."""
    plan, statistics = experiment.plan, experiment.statistics
    assert plan is not None and statistics is not None

    outputs: list[str] = []
    first_repeat = [run for run in experiment.runs if run.repeat == 1]
    for run in first_repeat:
        for outcome in run.statements:
            text = (outcome.stdout + "\n" + outcome.stderr).strip()[:OUTPUT_EXCERPT_CHARS]
            outputs.append(f"[{run.label} / statement {outcome.index}] {outcome.statement}\n{text}")

    documentation = experiment.evidence.documentation if experiment.evidence else []
    code = experiment.evidence.code_references if experiment.evidence else []
    payload = {
        "question": plan.question,
        "hypothesis": plan.hypothesis,
        "environment": plan.environment,
        "held_fixed": plan.fixed_settings,
        "setup": plan.setup,
        "variants": [variant.model_dump() for variant in plan.variants],
        "metrics": statistics.metrics,
        "statistics": statistics.model_dump(mode="json"),
        "code_references": [f"{ref.path}:{ref.start_line}" for ref in code[:8]],
        "documentation": [
            {"url": doc.url, "excerpt": (doc.excerpt or "")[:400]} for doc in documentation[:3]
        ],
    }
    return (
        json.dumps(payload, indent=1)
        + "\n\nVerbatim output from the first repeat:\n\n"
        + "\n\n".join(outputs)
        + "\n\nExplain what these results show about the question."
    )


def verify_cli_explanation(
    explanation: Explanation,
    *,
    statistics: CliStatistics,
    documentation: list[DocumentationReference],
) -> VerifiedExplanation:
    """The same three checks as for Spark, against the CLI statistics."""
    metrics = set(statistics.metrics)
    urls = {doc.url for doc in documentation}
    allowed = allowed_numbers(statistics)
    unsupported: list[UnsupportedClaim] = []
    checked = 0

    for claim in explanation.claims:
        if claim.kind is ClaimKind.MEASURED:
            checked += 1
            if claim.support not in metrics:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=(
                            f"cites {claim.support!r}, which is not a metric this "
                            "experiment measured"
                        ),
                    )
                )
                continue
            fabricated = [n for n in numbers_in(claim.statement) if not _matches(n, allowed)]
            if fabricated:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=f"states {fabricated}, which no measurement produced",
                    )
                )
        elif claim.kind is ClaimKind.DOCUMENTED:
            checked += 1
            if claim.support not in urls:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=(
                            f"cites {claim.support!r}, which is not one of the documentation "
                            "pages retrieved for this experiment"
                        ),
                    )
                )
    return VerifiedExplanation(
        explanation=explanation, unsupported=unsupported, checked_claims=checked
    )


def _output_logs(runs: list[CliVariantRun]) -> list[LogReference]:
    """cqlsh's verbatim output for each variant statement, first repeat only."""
    logs: list[LogReference] = []
    for run in runs:
        if run.repeat != 1:
            continue
        for outcome in run.statements:
            text = (outcome.stdout + "\n" + outcome.stderr).strip()
            if text:
                logs.append(
                    LogReference(
                        source=(
                            f"cqlsh · {run.label} · statement {outcome.index}: "
                            f"{outcome.statement[:120]}"
                        ),
                        excerpt=text[:1500],
                    )
                )
    return logs
