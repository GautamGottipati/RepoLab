"""Running an approved CLI experiment on the repository's real node.

    restore the build's checkpoint, running its server       (one node)
    wait until cqlsh answers
    for each repeat, for each variant:
        CREATE a fresh keyspace, USE it
        run the setup, then the variant's statements          (recorded verbatim)
        DROP the keyspace
    stop the node

Statements go through :class:`~repolab.experiences.cli.session.CliSessionManager`,
the same path a learner's terminal uses -- the same allowlist, the same quoting,
the same ``cqlsh -e`` -- so the experiment measures what a learner would see.

A node that dies mid-experiment is not reported as a string of failed
statements: ``docker exec`` into a stopped container fails for reasons that have
nothing to do with CQL, and recording those as results would be recording
measurements of the wrong thing.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog

from repolab.builds.models import BuildArtifact
from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.experiences.cli.session import CliSessionManager, CommandRecord, SessionStatus
from repolab.experiments.cli.models import (
    MAX_OUTPUT_CHARS,
    CliExperimentPlan,
    CliVariantRun,
    StatementOutcome,
)
from repolab.experiments.cli.statistics import parse_error, parse_rows
from repolab.experiments.executor import ApprovalRequiredError
from repolab.experiments.models import ExperimentStatus
from repolab.sandbox.models import SandboxError
from repolab.sandbox.provider import SandboxProvider

log = structlog.get_logger(__name__)

STEP_LABEL = "experiment"

#: What ``docker exec`` says about a container that is no longer running.
_NODE_GONE = ("is not running", "No such container")


class NodeStoppedError(SandboxError):
    """The node stopped while the experiment was running on it."""


def keyspace_for(experiment_id: str, variant_index: int, repeat: int) -> str:
    """A keyspace name unique to one run, and valid as an unquoted CQL identifier."""
    stem = "".join(char for char in experiment_id.lower() if char.isalnum())[:10]
    return f"repolab_x{stem}_v{variant_index}_r{repeat}"


class CliVariantRunner:
    """Runs every variant of an approved plan, in order, on one node."""

    def __init__(
        self,
        provider: SandboxProvider,
        artifact: BuildArtifact,
        *,
        repo_url: str,
        emitter: EventEmitter | None = None,
        ready_timeout_seconds: float | None = None,
    ) -> None:
        self._provider = provider
        self._artifact = artifact
        self._repo_url = repo_url
        self._emitter = emitter if emitter is not None else NullEmitter()
        self._ready_timeout = ready_timeout_seconds

    async def run(
        self,
        plan: CliExperimentPlan,
        *,
        experiment_id: str,
        status: ExperimentStatus,
        on_run: Callable[[CliVariantRun], Awaitable[None]] | None = None,
    ) -> list[CliVariantRun]:
        """Execute *plan*.

        Raises:
            ApprovalRequiredError: the experiment is not approved.
            SandboxError: the node could not be started, or stopped mid-run.
        """
        if not status.may_execute:
            raise ApprovalRequiredError(status)

        kwargs = {}
        if self._ready_timeout is not None:
            kwargs["ready_timeout_seconds"] = self._ready_timeout
        manager = CliSessionManager(self._provider, **kwargs)

        self._emitter.emit(
            EventType.STEP_STARTED,
            f"starting the {self._artifact.kind.value} node from its checkpoint",
            step=STEP_LABEL,
            data={"checkpoint": self._artifact.checkpoint_tag},
        )
        session = await manager.start(self._artifact, repo_url=self._repo_url)
        if session.status is not SessionStatus.READY:
            raise SandboxError(session.error or "the node did not start")
        self._emitter.emit(EventType.STEP_DONE, "the node is answering", step=STEP_LABEL)

        runs: list[CliVariantRun] = []
        try:
            for repeat in range(1, plan.repeats + 1):
                for index, variant in enumerate(plan.variants):
                    keyspace = keyspace_for(experiment_id, index, repeat)
                    self._emitter.emit(
                        EventType.EXPERIMENT_VARIANT_STARTED,
                        f"running {variant.label!r} (repeat {repeat})",
                        step=STEP_LABEL,
                        data={"variant": variant.label, "repeat": repeat, "keyspace": keyspace},
                    )
                    run = await self._run_variant(
                        manager, session.id, plan, index, repeat, keyspace, experiment_id
                    )
                    runs.append(run)
                    self._emitter.emit(
                        EventType.EXPERIMENT_VARIANT_COMPLETED,
                        f"{variant.label!r} (repeat {repeat}): "
                        f"{sum(1 for o in run.statements if o.ok)}/{len(run.statements)} "
                        "statements succeeded",
                        step=STEP_LABEL,
                        data={
                            "variant": variant.label,
                            "repeat": repeat,
                            "succeeded": [outcome.ok for outcome in run.statements],
                            "duration_ms": [outcome.duration_ms for outcome in run.statements],
                        },
                    )
                    if on_run is not None:
                        await on_run(run)
        finally:
            await manager.stop(session.id)
        return runs

    async def _run_variant(
        self,
        manager: CliSessionManager,
        session_id: str,
        plan: CliExperimentPlan,
        index: int,
        repeat: int,
        keyspace: str,
        experiment_id: str,
    ) -> CliVariantRun:
        variant = plan.variants[index]
        started = time.perf_counter()

        created = await self._execute(
            manager,
            session_id,
            f"CREATE KEYSPACE {keyspace} WITH replication = "
            "{'class': 'SimpleStrategy', 'replication_factor': 1}",
        )
        if created.exit_code != 0:
            raise SandboxError(f"could not create the experiment keyspace: {created.output[:300]}")
        await self._execute(manager, session_id, f"USE {keyspace}")

        setup = [
            outcome_of(record, position, "setup")
            for position, record in enumerate(
                [await self._execute(manager, session_id, text) for text in plan.setup], start=1
            )
        ]
        statements = []
        for position, text in enumerate(variant.statements, start=1):
            record = await self._execute(manager, session_id, text)
            statements.append(outcome_of(record, position, "variant"))

        try:
            await self._execute(manager, session_id, f"DROP KEYSPACE IF EXISTS {keyspace}")
        except NodeStoppedError:
            raise
        except Exception as exc:  # noqa: BLE001 - cleanup does not change the result
            log.warning("cli_experiment.drop_failed", keyspace=keyspace, error=str(exc))

        return CliVariantRun(
            variant_id=f"{experiment_id[:8]}-v{index}",
            label=variant.label,
            variant_index=index,
            repeat=repeat,
            keyspace=keyspace,
            checkpoint_tag=self._artifact.checkpoint_tag,
            setup=setup,
            statements=statements,
            wall_clock_seconds=round(time.perf_counter() - started, 3),
        )

    async def _execute(
        self, manager: CliSessionManager, session_id: str, text: str
    ) -> CommandRecord:
        record = await manager.execute(session_id, text)
        output = f"{record.stdout}\n{record.stderr}"
        if record.exit_code not in (0, None) and any(marker in output for marker in _NODE_GONE):
            raise NodeStoppedError(
                "the node stopped during the experiment, so no further statements could run. "
                "It is usually out of memory; check that no other environment is running."
            )
        return record


def outcome_of(record: CommandRecord, index: int, phase: str) -> StatementOutcome:
    """A statement's record, with what cqlsh printed parsed into fields."""
    error_class, error_code, _ = parse_error(record.stdout, record.stderr)
    return StatementOutcome(
        index=index,
        statement=record.command,
        phase=phase,  # type: ignore[arg-type]
        exit_code=record.exit_code,
        refused=record.refused,
        duration_ms=round(record.duration_seconds * 1000.0, 1),
        rows=parse_rows(record.stdout),
        error_class=error_class,
        error_code=error_code,
        stdout=record.stdout[:MAX_OUTPUT_CHARS],
        stderr=record.stderr[:MAX_OUTPUT_CHARS],
    )
