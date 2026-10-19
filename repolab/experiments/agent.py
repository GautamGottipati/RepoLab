"""The Experiment Agent.

This is the feature RepoLab exists for. A user asks why the job uses 200 shuffle
partitions; instead of answering from what a model happens to know, RepoLab runs
the job at several partition counts and reports what actually happened.

    question
       |
       v
    investigate repository evidence        (read-only tools, deterministic plan)
       |
       v
    version-matched official documentation (Tavily, official domains only)
       |
       v
    Ultra proposes a plan and a hypothesis (untrusted output)
       |
       v
    validate + estimate cost              (deterministic; the allowlist lives here)
       |
       v
    WAIT FOR APPROVAL  <--- nothing below this line runs until the user says yes
       |
       v
    execute variants from equivalent state
       |
       v
    compute statistics in Python
       |
       v
    assemble the evidence bundle
       |
       v
    Ultra explains, labelling measured / documented / inferred
       |
       v
    verify the explanation against the statistics

Two Ultra calls, and both are bounded. The first is given the investigation's
findings and the list of metrics RepoLab can measure, and produces a proposal
that deterministic code then has the final word on. The second is given the
question, the plan, the computed statistics, code references, log excerpts and
documentation -- and nothing else.

The agent holds no capability of its own. It cannot start a container except
through :class:`~repolab.experiments.executor.VariantExecutor`, which refuses any
experiment the user has not approved.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from repolab.agents.models import AgentLimits, AgentRun
from repolab.agents.repository_tools import build_repository_tools
from repolab.agents.runtime import AgentRuntime
from repolab.agents.tools import ToolRegistry
from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.experiences.spark.driver import (
    ADDED_STEPS,
    DEFAULT_SHUFFLE_PARTITIONS,
    SparkRunConfig,
)
from repolab.experiments.evidence import build_evidence
from repolab.experiments.executor import (
    SparkVariantRunner,
    VariantExecutor,
    VariantFailure,
    VariantRunner,
)
from repolab.experiments.experiment import Experiment
from repolab.experiments.explanation import (
    SYSTEM_PROMPT,
    Explanation,
    VerifiedExplanation,
    build_explanation_input,
    default_notes,
    verify_explanation,
)
from repolab.experiments.investigation import (
    ExperimentInvestigationPlanner,
    code_references_from,
    documentation_from,
    investigation_summary,
)
from repolab.experiments.models import (
    ExperimentPlan,
    ProposedPlan,
    ReproducibilityRecord,
    ResourceProfile,
    VariantRun,
)
from repolab.experiments.statistics import compute_statistics
from repolab.experiments.validation import (
    ALLOWED_VARIABLES,
    ExperimentBudget,
    measurable_metrics,
    validate_plan,
)
from repolab.failures import describe, explain
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.tree import build_file_tree
from repolab.interfaces import ResearchProvider
from repolab.llm import LLMError, ModelRouter, Purpose

log = structlog.get_logger(__name__)

STEP_LABEL = "experiment"

AGENT_NAME = "experiment_agent"

#: Output budget for the design call.
#:
#: Nemotron Ultra writes its reasoning into ``content`` before the JSON, so the
#: budget has to cover both. At 1024 the design call needed its retry; at 2048 it
#: does not.
DESIGN_MAX_TOKENS = 2048

#: Output budget for the explanation call. Deliberately generous.
#:
#: Nemotron Ultra writes its reasoning into ``content`` before the JSON, so this
#: is a thinking-plus-output budget, and exhausting it loses the whole
#: explanation after minutes of real Spark runs.
#:
#: Tuning it to an observation does not work. Three runs over the *same*
#: statistics used 1,030, 2,848 and more than 3,142 tokens -- reasoning length
#: varies run to run, not just with payload size, so any cap set just above a
#: measured value will fail eventually. 2048 failed, then 4096 failed on a
#: larger experiment that had succeeded in 1,030 tokens the day before.
#:
#: An unused cap costs nothing -- generation is billed by tokens produced -- so
#: the budget is set well clear of anything observed rather than just above it.
EXPLANATION_MAX_TOKENS = 8192

#: Timeout for the explanation call. Observed latency is around 9 s; this is
#: generous because an experiment that has already run its variants should wait
#: for its explanation rather than discard it.
EXPLANATION_TIMEOUT_SECONDS = 180.0

OBJECTIVE_TEMPLATE = "Gather the repository evidence needed to design an experiment for: {question}"

DESIGN_SYSTEM_PROMPT = (
    "You design controlled experiments against real software.\n"
    "\n"
    "You do not run anything and you do not report results. You propose one "
    "variable to vary, the values to try, and which measurable quantities to "
    "compare. A separate validation step will check your proposal and may "
    "reject it.\n"
    "\n"
    "Rules:\n"
    "- Vary exactly one variable, chosen from the allowed list given to you.\n"
    "- Include the current value so there is a baseline to compare against.\n"
    "- Choose values that span a wide enough range to show an effect, and keep "
    "the total number of runs small.\n"
    "- Request only metrics from the measurable list. Do not invent metric names.\n"
    "- State a hypothesis that the measurements could contradict."
)

DESIGN_PROMPT_TEMPLATE = """\
Question from the user:
{question}

Repository: {repo_url} at commit {commit_sha}

What a read-only investigation of this repository found:
{findings}

Relevant lines in the repository:
{code}

Official documentation retrieved (version {version}):
{documentation}

RepoLab will run the repository's own job in a container at {master} and measure
it from Spark's event log. RepoLab has already added this to the job, which you
should account for but must not change:
{added}

Variables you may vary (nothing else is permitted):
{variables}

Metrics RepoLab can actually measure (nothing else can be reported):
{metrics}

The current value of the variable in question is {baseline}.
At most {max_runs} total runs, and at most {max_repeats} repeats per value.

Propose the experiment."""


class ExperimentAgent:
    """Turns a question into a measured answer, pausing for approval in between."""

    name = AGENT_NAME

    def __init__(
        self,
        *,
        repo_url: str,
        commit_sha: str,
        repository_root: Path,
        image: str,
        router: ModelRouter | None = None,
        research: ResearchProvider | None = None,
        emitter: EventEmitter | None = None,
        budget: ExperimentBudget | None = None,
        base_config: SparkRunConfig | None = None,
        limits: AgentLimits | None = None,
        baseline_value: int = DEFAULT_SHUFFLE_PARTITIONS,
        concurrency: int = 1,
        rows: int | None = None,
        seed: int | None = None,
        runs_root: Path | None = None,
        host_cpus: int | None = None,
        environment_factory: Callable[[], object] | None = None,
        runner: VariantRunner | None = None,
    ) -> None:
        self.repo_url = repo_url
        self.commit_sha = commit_sha
        self.repository_root = Path(repository_root)
        self.image = image

        self._router = router
        self._research = research
        self._emitter = emitter if emitter is not None else NullEmitter()
        self._budget = budget if budget is not None else ExperimentBudget()
        self._config = base_config if base_config is not None else SparkRunConfig()
        self._limits = limits if limits is not None else AgentLimits(max_iterations=14)
        self._baseline = baseline_value
        self._concurrency = max(1, concurrency)
        self._rows = rows
        self._seed = seed
        self._runs_root = runs_root
        self._host_cpus = host_cpus
        self._environment_factory = environment_factory
        self._runner = runner

        self.investigation: AgentRun | None = None

        self._variant_failures: list[VariantFailure] = []
        """Runs that did not complete, carried so the explanation discloses them."""

    def _failure_notes(self) -> list[str]:
        """What the model is told about runs that did not happen.

        Given to the explanation as a note rather than left out: a conclusion
        drawn from four runs when six were planned is a weaker conclusion, and
        the model should be reasoning about the data that exists.
        """
        if not self._variant_failures:
            return []

        values = sorted({failure.value for failure in self._variant_failures})
        return [
            f"{len(self._variant_failures)} planned run(s) did not complete "
            f"(at {', '.join(str(value) for value in values)}), so the statistics "
            f"rest on fewer measurements than the plan asked for"
        ]

    # -- propose ----------------------------------------------------------

    async def propose(
        self,
        question: str,
        *,
        job_id: str | None = None,
        experiment_id: str | None = None,
    ) -> Experiment:
        """Investigate, design, validate, and stop at the approval gate.

        Returns an experiment in ``AWAITING_APPROVAL`` when the plan is sound, or
        in ``FAILED`` carrying the rejection when it is not. Nothing is executed
        either way.

        *experiment_id* lets the caller fix the id before the design starts, so a
        client can poll an experiment that is still being designed.
        """
        fields: dict[str, Any] = {
            "question": question,
            "repo_url": self.repo_url,
            "commit_sha": self.commit_sha,
            "job_id": job_id,
        }
        if experiment_id is not None:
            fields["id"] = experiment_id
        experiment = Experiment(**fields)

        self._emitter.emit(
            EventType.AGENT_STARTED,
            "designing an experiment rather than answering from memory",
            step=STEP_LABEL,
            data={"question": question[:200], "experiment_id": experiment.id},
        )

        investigation, references, documentation = await self._investigate(question)
        self.investigation = investigation

        proposal = await self._design(
            question,
            investigation=investigation,
            references=references,
            documentation=documentation,
        )
        experiment.proposal = proposal

        result = validate_plan(
            proposal,
            budget=self._budget,
            baseline_value=self._baseline,
            config=self._config,
            image=self.image,
            concurrency=self._concurrency,
        )
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
        experiment.reproducibility = self.reproducibility_for(plan)
        experiment.evidence = build_evidence(
            [],
            baseline_value=plan.baseline_value,
            code_references=references,
            documentation=documentation,
        )
        experiment.await_approval()

        self._emitter.emit(
            EventType.APPROVAL_REQUIRED,
            f"approval needed: {plan.summary()}",
            step=STEP_LABEL,
            data={"experiment_id": experiment.id, **experiment.approval_prompt()},
        )
        return experiment

    async def _investigate(self, question: str) -> tuple[AgentRun, list[Any], list[Any]]:
        """Run the read-only investigation. Needs no approval."""
        tree = build_file_tree(self.repository_root)
        context = DetectionContext(root=self.repository_root, tree=tree, commit_sha=self.commit_sha)
        tools, tool_list = build_repository_tools(context, ReadBudget(), research=self._research)
        registry = ToolRegistry(tool_list)

        planner = ExperimentInvestigationPlanner(
            question,
            research_available=self._research is not None,
            version=_version_from_image(self.image),
            variable_hint=question,
        )
        runtime = AgentRuntime(
            agent=self.name,
            objective=OBJECTIVE_TEMPLATE.format(question=question[:200]),
            registry=registry,
            planner=planner,
            emitter=self._emitter,
            limits=self._limits,
            step_label=STEP_LABEL,
        )
        run = await runtime.run(lambda: {"findings": len(tools.run.findings)})

        return (
            run,
            code_references_from(run),
            documentation_from(run, version=_version_from_image(self.image)),
        )

    async def _design(
        self,
        question: str,
        *,
        investigation: AgentRun,
        references: list[Any],
        documentation: list[Any],
    ) -> ProposedPlan:
        """Ask Ultra for a plan, or fall back to a deterministic one.

        The fallback is not a convenience: the demo must work when credentials are
        absent, and a sweep across partition counts is exactly the experiment the
        question calls for. It is labelled as deterministic in the hypothesis so
        nobody mistakes it for the model's reasoning.
        """
        self._emitter.emit(
            EventType.AGENT_STATUS,
            "designing the partition experiment",
            step=STEP_LABEL,
        )

        if self._router is None:
            return self._fallback_proposal(question)

        prompt = DESIGN_PROMPT_TEMPLATE.format(
            question=question,
            repo_url=self.repo_url,
            commit_sha=self.commit_sha[:12],
            findings="\n".join(f"- {line}" for line in investigation_summary(investigation))
            or "- (nothing found)",
            code="\n".join(
                f"- {ref.path}:{ref.start_line}: {(ref.excerpt or '').strip()[:120]}"
                for ref in references[:8]
            )
            or "- (no matches)",
            documentation="\n".join(
                f"- {doc.url}: {(doc.excerpt or '').strip()[:200]}" for doc in documentation[:3]
            )
            or "- (none retrieved)",
            version=_version_from_image(self.image),
            master=self._config.master,
            added="\n".join(f"- {step}" for step in ADDED_STEPS),
            variables="\n".join(
                f"- {name} (allowed range {low}-{high})"
                for name, (low, high) in ALLOWED_VARIABLES.items()
            ),
            metrics="\n".join(f"- {metric}" for metric in measurable_metrics()),
            baseline=self._baseline,
            max_runs=self._budget.max_runs,
            max_repeats=self._budget.max_repeats,
        )

        try:
            completion = await self._router.ultra(
                purpose=Purpose.EXPERIMENT_DESIGN,
                schema=ProposedPlan,
                system=DESIGN_SYSTEM_PROMPT,
                prompt=prompt,
                max_tokens=DESIGN_MAX_TOKENS,
            )
        except LLMError as exc:
            log.warning("experiment.design_failed", error=str(exc))
            self._emitter.emit(
                EventType.AGENT_STATUS,
                "the model was unavailable; using the deterministic sweep",
                step=STEP_LABEL,
            )
            return self._fallback_proposal(question)

        self._emitter.emit(
            EventType.AGENT_STATUS,
            f"proposed varying {completion.value.variable}",
            step=STEP_LABEL,
            data={
                "model_latency_ms": completion.record.latency_ms,
                "retries": completion.retries,
            },
        )
        return completion.value

    #: The sweep the fallback would like to run, widest span first after the
    #: baseline, so trimming to a budget keeps the comparison informative.
    FALLBACK_VALUES: tuple[int, ...] = (8, 400, 50)

    def _fallback_proposal(self, question: str) -> ProposedPlan:
        """A deterministic sweep, for when no model is available.

        Trimmed to fit the budget it will be validated against: proposing more
        runs than are allowed would be refused, which for the no-credentials path
        would mean no experiment at all.
        """
        repeats = min(2, self._budget.max_repeats)
        affordable = max(2, self._budget.max_runs // max(1, repeats))

        values = [self._baseline]
        for candidate in self.FALLBACK_VALUES:
            if len(values) >= min(affordable, self._budget.max_values):
                break
            if candidate != self._baseline:
                values.append(candidate)

        return ProposedPlan(
            question=question,
            hypothesis=(
                "Deterministic fallback, not a model hypothesis: varying the shuffle "
                "partition count changes how many partitions are empty and how long "
                "the job takes, because the data has fewer distinct keys than there "
                "are partitions."
            ),
            variable="spark.sql.shuffle.partitions",
            values=sorted(values),
            metrics=list(measurable_metrics()),
            repeats=repeats,
        )

    def reproducibility_for(self, plan: ExperimentPlan) -> ReproducibilityRecord:
        """The record that says what it would take to repeat this experiment.

        Public because a caller that supplies its own plan -- the CLI's
        ``--values`` mode, or a test -- still needs the same record, built the
        same way.
        """
        from repolab.experiences.spark.dataset import generate_dataset

        kwargs: dict[str, int] = {}
        if self._rows is not None:
            kwargs["rows"] = self._rows
        if self._seed is not None:
            kwargs["seed"] = self._seed
        # Generated to obtain the hash, not to use the bytes: the dataset is
        # deterministic, so the hash recorded here is the one every variant sees.
        _, dataset = generate_dataset(**kwargs)

        return ReproducibilityRecord(
            repo_url=self.repo_url,
            commit_sha=self.commit_sha,
            image=self.image,
            dataset=dataset,
            command=f"spark-submit --conf {plan.variable}=<value> repolab_driver.py",
            base_configuration=plan.fixed_settings,
            resources=ResourceProfile(
                master=self._config.master,
                driver_memory=self._config.driver_memory,
                host_cpus=self._host_cpus,
                concurrency=plan.concurrency,
            ),
            repeats=plan.repeats,
            added_steps=list(ADDED_STEPS),
        )

    # -- execute ----------------------------------------------------------

    async def execute(
        self,
        experiment: Experiment,
        *,
        on_run: Callable[[VariantRun], Awaitable[None]] | None = None,
    ) -> Experiment:
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

        async def record(run: VariantRun) -> None:
            # Appended as each run finishes, *before* the caller's callback, so a
            # caller that persists sees the measurements already taken. Without
            # this the experiment's `runs` stayed empty until every variant was
            # done, and a mid-experiment save wrote an experiment with no runs --
            # which defeats the point of saving mid-experiment at all.
            experiment.runs.append(run)
            if on_run is not None:
                await on_run(run)

        executor = VariantExecutor(self._build_runner(plan), emitter=self._emitter, on_run=record)
        try:
            runs = await executor.execute(plan, status=experiment.status)
        except Exception as exc:
            failure = explain(exc)
            self._emitter.emit(
                EventType.FAILED,
                failure.summary,
                step=STEP_LABEL,
                data=failure.model_dump_public(),
            )
            experiment.fail(describe(exc))
            raise

        experiment.runs = runs
        experiment.statistics = compute_statistics(
            runs, metrics=plan.metrics, baseline_value=plan.baseline_value
        )

        # A result computed from fewer runs than were planned has to say so,
        # here and in what the model is given to explain.
        self._variant_failures = list(executor.failures)

        references = experiment.evidence.code_references if experiment.evidence is not None else []
        documentation = experiment.evidence.documentation if experiment.evidence is not None else []
        experiment.evidence = build_evidence(
            runs,
            baseline_value=plan.baseline_value,
            code_references=list(references),
            documentation=list(documentation),
        )

        self._emitter.emit(
            EventType.STEP_DONE,
            (
                f"measured {len(runs)} runs across {len(plan.values)} variants; "
                f"statistics computed for {len(experiment.statistics.metrics)} metrics"
                + (
                    f" ({len(self._variant_failures)} run(s) did not complete)"
                    if self._variant_failures
                    else ""
                )
            ),
            step=STEP_LABEL,
            data={
                "runs": len(runs),
                "variants": len(plan.values),
                "metrics": experiment.statistics.metrics,
            },
        )

        experiment.explanation = await self.explain(experiment)

        if experiment.reproducibility is not None:
            version = next(
                (run.measurements.spark_version for run in runs if run.measurements.spark_version),
                None,
            )
            experiment.reproducibility.application_version = version

        experiment.complete()
        if experiment.reproducibility is not None:
            experiment.reproducibility.finished_at = experiment.finished_at
        return experiment

    def _build_runner(self, plan: ExperimentPlan) -> VariantRunner:
        if self._runner is not None:
            return self._runner
        return SparkVariantRunner(
            repo_url=self.repo_url,
            commit_sha=self.commit_sha,
            repository_root=self.repository_root,
            image=self.image,
            variable=plan.variable,
            base_config=self._config,
            rows=self._rows,
            seed=self._seed,
            runs_root=self._runs_root,
            environment_factory=self._environment_factory,
        )

    # -- explain ----------------------------------------------------------

    async def explain(self, experiment: Experiment) -> VerifiedExplanation | None:
        """Ask Ultra to explain the measurements, then check what it said.

        The payload is assembled by
        :func:`~repolab.experiments.explanation.build_explanation_input`, so the
        model receives the computed statistics and never the raw event logs.
        """
        plan, statistics = experiment.plan, experiment.statistics
        if plan is None or statistics is None or experiment.evidence is None:
            return None

        payload = build_explanation_input(
            plan=plan,
            statistics=statistics,
            evidence=experiment.evidence,
            notes=[*default_notes(plan, statistics), *self._failure_notes()],
        )

        if self._router is None:
            return None

        self._emitter.emit(
            EventType.AGENT_STATUS,
            "interpreting the measured results",
            step=STEP_LABEL,
        )

        try:
            completion = await self._router.ultra(
                purpose=Purpose.RESULT_INTERPRETATION,
                schema=Explanation,
                system=SYSTEM_PROMPT,
                prompt=payload.to_prompt(),
                max_tokens=EXPLANATION_MAX_TOKENS,
                timeout_seconds=EXPLANATION_TIMEOUT_SECONDS,
            )
        except LLMError as exc:
            # Recorded on the experiment, not just logged. "No model configured"
            # and "the model failed" are different situations, and reporting the
            # second as the first would quietly misdescribe what happened.
            failure = explain(exc)
            log.warning("experiment.explanation_failed", error=failure.technical)
            # Why the explanation is missing, in the words the screen will use.
            experiment.explanation_error = f"{failure.headline} {failure.why}"[:500]
            self._emitter.emit(
                EventType.AGENT_STATUS,
                "the explanation could not be produced; the measurements stand on their own",
                step=STEP_LABEL,
                data=failure.model_dump_public(),
            )
            return None

        verified = verify_explanation(
            completion.value,
            statistics=statistics,
            plan=plan,
            documentation=list(experiment.evidence.documentation),
        )

        if not verified.grounded:
            # Reported, not suppressed: a reader needs to know which claims the
            # evidence did not support.
            self._emitter.emit(
                EventType.AGENT_STATUS,
                (
                    f"{len(verified.unsupported)} claim(s) could not be traced to a "
                    "measurement and are flagged"
                ),
                step=STEP_LABEL,
                data={"unsupported": [item.problem for item in verified.unsupported]},
            )
        return verified


def _version_from_image(image: str) -> str:
    """The application version an image tag names, e.g. ``3.5.1``.

    Best effort, and only used to version-match a documentation search. The
    authoritative version is the one the event log reports after a run.
    """
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", image)
    return match.group(1) if match else "latest"
