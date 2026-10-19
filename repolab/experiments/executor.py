"""Executing approved experiment variants.

Three guarantees, each enforced here rather than requested in a prompt:

**Nothing runs without approval.** :meth:`VariantExecutor.execute` refuses any
experiment whose status does not permit execution. An agent holding this object
still cannot start a run that the user has not approved.

**Every variant starts from equivalent state.** Each run gets a fresh environment
from the same image and the same deterministically generated dataset -- the same
seed, so the same sha256. The executor *verifies* that afterwards: if two runs saw
different dataset bytes or a different image, the comparison is void and
aggregation is refused rather than reported with a footnote.

**Timing fidelity is never overstated.** Variants run sequentially by default.
Docker here has 8 GiB and 8 CPUs while each Spark variant takes 4 GiB and
``local[4]``, so two at once saturate the machine and a "duration" would be
measuring contention. ``concurrency > 1`` is available, and every run it produces
is tagged ``timing_contended`` so no explanation can present those durations as
clean.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

import structlog
from pydantic import BaseModel

from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.experiences.spark.driver import SparkRunConfig
from repolab.experiences.spark.runner import SparkDemoRunner, SparkRunResult
from repolab.experiments.models import (
    ExperimentPlan,
    ExperimentStatus,
    VariantRun,
)
from repolab.failures import explain

log = structlog.get_logger(__name__)

STEP_LABEL = "experiment"


class ApprovalRequiredError(PermissionError):
    """Execution was attempted before the user approved the experiment."""

    def __init__(self, status: ExperimentStatus) -> None:
        self.status = status
        super().__init__(f"experiment is {status.value}; variants may only run once approved")


class EquivalenceViolation(RuntimeError):
    """Two runs did not start from equivalent state, so they cannot be compared."""


#: Variant values that must survive for a comparison to mean anything.
MIN_COMPARABLE_VALUES = 2


class NotEnoughRuns(RuntimeError):
    """Too few variant values produced a measurement to compare them."""


class VariantFailure(BaseModel):
    """One repeat that did not produce a measurement.

    Kept rather than raised. A single container failing to start used to abort
    the whole experiment and discard every variant that had already run, which
    is the worst possible trade on a demo's longest path: the measurements that
    *did* happen are real, and an experiment that reports four of six runs with
    the gap disclosed is worth more than no experiment at all.
    """

    value: int
    repeat: int
    reason: str
    """What a person should be told, from :func:`repolab.failures.explain`."""


class VariantRunner(Protocol):
    """Runs one variant once.

    Narrow on purpose: the executor owns ordering, events, approval and the
    equivalence check, and knows nothing about Spark or Docker. That is what lets
    the whole loop be tested offline against a fake.
    """

    async def run_once(self, *, value: int, repeat: int) -> SparkRunResult: ...


class SparkVariantRunner:
    """Runs one Spark variant in a fresh container.

    A new environment per run is the equivalence guarantee: nothing carries over
    from the previous variant, so a run cannot inherit a warmed cache or a leftover
    file from a different partition count.
    """

    def __init__(
        self,
        *,
        repo_url: str,
        commit_sha: str,
        repository_root: Path,
        image: str,
        variable: str,
        base_config: SparkRunConfig | None = None,
        rows: int | None = None,
        seed: int | None = None,
        runs_root: Path | None = None,
        environment_factory: Callable[[], object] | None = None,
    ) -> None:
        self._repo_url = repo_url
        self._commit_sha = commit_sha
        self._repository_root = repository_root
        self._image = image
        self._variable = variable
        self._base_config = base_config if base_config is not None else SparkRunConfig()
        self._rows = rows
        self._seed = seed
        self._runs_root = runs_root
        self._environment_factory = environment_factory

    @property
    def image(self) -> str:
        return self._image

    def _new_environment(self) -> object:
        if self._environment_factory is not None:
            return self._environment_factory()
        from repolab.sandbox.docker import DockerEnvironment

        return DockerEnvironment()

    def config_for(self, value: int) -> SparkRunConfig:
        """The base configuration with only the varied key changed."""
        if self._variable != "spark.sql.shuffle.partitions":
            # Every other allowlisted key would go through extra_conf; none exist
            # yet, and guessing at one would be the kind of unvalidated execution
            # validation exists to prevent.
            raise EquivalenceViolation(f"cannot apply variable {self._variable!r} to a Spark run")
        return self._base_config.model_copy(update={"shuffle_partitions": value})

    async def run_once(self, *, value: int, repeat: int) -> SparkRunResult:
        config = self.config_for(value)
        runner = SparkDemoRunner(self._new_environment(), runs_root=self._runs_root)
        return await runner.run(
            repo_url=self._repo_url,
            commit_sha=self._commit_sha,
            repository_root=self._repository_root,
            image=self._image,
            config=config,
            rows=self._rows,
            seed=self._seed,
        )


class VariantExecutor:
    """Runs an approved plan's variants and collects their measurements."""

    def __init__(
        self,
        runner: VariantRunner,
        *,
        emitter: EventEmitter | None = None,
        on_run: Callable[[VariantRun], Awaitable[None]] | None = None,
    ) -> None:
        self._runner = runner
        self._emitter = emitter if emitter is not None else NullEmitter()
        self._on_run = on_run

        self.failures: list[VariantFailure] = []
        """Repeats that did not produce a measurement. Read after `execute`."""

    async def execute(
        self,
        plan: ExperimentPlan,
        *,
        status: ExperimentStatus,
    ) -> list[VariantRun]:
        """Run every variant in *plan*, in the order the plan lists them.

        A repeat that fails is recorded in :attr:`failures` and the rest
        continue. The experiment is only abandoned when too little survives to
        compare -- see :data:`MIN_COMPARABLE_VALUES`.

        Raises:
            ApprovalRequiredError: *status* does not permit execution.
            NotEnoughRuns: too few variant values produced any measurement.
            EquivalenceViolation: the runs did not start from equivalent state.
        """
        if not status.may_execute:
            raise ApprovalRequiredError(status)

        self.failures = []

        contended = plan.concurrency > 1
        if contended:
            self._emitter.emit(
                EventType.AGENT_STATUS,
                (
                    f"running {plan.concurrency} variants at once; timings will be "
                    "marked as contended"
                ),
                step=STEP_LABEL,
                data={"concurrency": plan.concurrency},
            )

        runs: list[VariantRun] = []
        if plan.concurrency <= 1:
            for value in plan.values:
                runs.extend(await self._run_variant(plan, value, contended=False))
        else:
            semaphore = asyncio.Semaphore(plan.concurrency)

            async def guarded(value: int) -> list[VariantRun]:
                async with semaphore:
                    return await self._run_variant(plan, value, contended=True)

            # Independent values, so they may overlap. Gathered rather than
            # streamed so one failing variant does not leave others orphaned.
            grouped = await asyncio.gather(*(guarded(value) for value in plan.values))
            for group in grouped:
                runs.extend(group)

        if self.failures:
            self._emitter.emit(
                EventType.AGENT_STATUS,
                (
                    f"{len(self.failures)} of {plan.estimated_runs} runs did not "
                    f"complete; the statistics below use the {len(runs)} that did"
                ),
                step=STEP_LABEL,
                data={"failed_runs": len(self.failures), "completed_runs": len(runs)},
            )

        measured_values = {run.value for run in runs}
        if len(measured_values) < MIN_COMPARABLE_VALUES:
            # An experiment compares values. One value, or none, is not a
            # comparison however many repeats it managed.
            raise NotEnoughRuns(
                f"only {len(measured_values)} of {len(plan.values)} values produced a "
                f"measurement, which is not enough to compare"
            )

        verify_equivalent_state(runs)
        return runs

    async def _run_variant(
        self, plan: ExperimentPlan, value: int, *, contended: bool
    ) -> list[VariantRun]:
        """Run every repeat of one variant value."""
        variant_id = f"{value}-{uuid.uuid4().hex[:6]}"

        self._emitter.emit(
            EventType.EXPERIMENT_VARIANT_STARTED,
            f"{plan.variable} = {value} ({plan.repeats} repeat(s))",
            step=STEP_LABEL,
            data={
                "variant_id": variant_id,
                "variable": plan.variable,
                "value": value,
                "repeats": plan.repeats,
            },
        )

        runs: list[VariantRun] = []
        for repeat in range(1, plan.repeats + 1):
            try:
                result = await self._runner.run_once(value=value, repeat=repeat)
            except Exception as exc:  # noqa: BLE001 - recorded, and the rest continue
                failure = explain(exc)
                self.failures.append(
                    VariantFailure(value=value, repeat=repeat, reason=failure.summary)
                )
                log.warning(
                    "experiment.variant_failed",
                    value=value,
                    repeat=repeat,
                    error=failure.technical,
                )
                self._emitter.emit(
                    EventType.LOG_LINE,
                    f"{plan.variable}={value} repeat {repeat}/{plan.repeats} "
                    f"did not complete: {failure.headline}",
                    step=STEP_LABEL,
                    data={
                        "variant_id": variant_id,
                        "value": value,
                        "repeat": repeat,
                        **failure.model_dump_public(),
                    },
                )
                continue

            run = _variant_run(
                result,
                variant_id=variant_id,
                value=value,
                repeat=repeat,
                contended=contended,
            )
            runs.append(run)

            if self._on_run is not None:
                await self._on_run(run)

            self._emitter.emit(
                EventType.PARTIAL_RESULT,
                (
                    f"{plan.variable}={value} repeat {repeat}/{plan.repeats}: "
                    f"{run.measurements.task_count} tasks, "
                    f"{run.measurements.distribution.empty_partitions} empty partitions"
                ),
                step=STEP_LABEL,
                data={
                    "variant_id": variant_id,
                    "value": value,
                    "repeat": repeat,
                    "run_id": run.run_id,
                    "task_count": run.measurements.task_count,
                    "partitions": run.measurements.distribution.partitions,
                    "empty_partitions": run.measurements.distribution.empty_partitions,
                    "total_runtime_ms": run.measurements.total_runtime_ms,
                    "timing_contended": run.timing_contended,
                },
            )

        self._emitter.emit(
            EventType.EXPERIMENT_VARIANT_COMPLETED,
            f"{plan.variable} = {value}: {len(runs)} run(s) measured",
            step=STEP_LABEL,
            data={
                "variant_id": variant_id,
                "value": value,
                "runs": len(runs),
                "event_logs": [run.event_log_sha256[:12] for run in runs],
            },
        )
        return runs


def _variant_run(
    result: SparkRunResult,
    *,
    variant_id: str,
    value: int,
    repeat: int,
    contended: bool,
) -> VariantRun:
    return VariantRun(
        variant_id=variant_id,
        value=value,
        repeat=repeat,
        run_id=result.run_id,
        dataset_hash=result.dataset.sha256,
        image=result.environment.image,
        checkpoint_id=result.environment.checkpoint_id,
        measurements=result.measurements,
        event_log_path=result.event_log_path,
        event_log_sha256=result.event_log_sha256,
        wall_clock_seconds=result.wall_clock_seconds,
        timing_contended=contended,
    )


def verify_equivalent_state(runs: list[VariantRun]) -> None:
    """Confirm every run started from equivalent state.

    A comparison between runs that saw different input data or a different image
    measures the difference in the inputs, not the difference in the variable. So
    this raises rather than annotating: a result that cannot be trusted is worse
    than no result.

    Raises:
        EquivalenceViolation: the dataset hash or image differed between runs.
    """
    if not runs:
        return

    hashes = {run.dataset_hash for run in runs}
    if len(hashes) > 1:
        raise EquivalenceViolation(
            "variants saw different datasets, so they cannot be compared: "
            f"{sorted(h[:12] for h in hashes)}"
        )

    images = {run.image for run in runs}
    if len(images) > 1:
        raise EquivalenceViolation(
            f"variants ran on different images, so they cannot be compared: {sorted(images)}"
        )
