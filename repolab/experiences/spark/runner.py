"""Running the Spark demo and measuring it.

    environment -> dataset -> repository -> spark-submit -> event log
                                                              |
                                                              v
                                              parse -> measure -> evidence

The raw event log is preserved on disk and its hash recorded: it is the primary
measurement source, and CLAUDE.md requires that raw logs are never discarded.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.evidence.models import (
    CodeReference,
    EvidenceBundle,
    LogReference,
    Measurement,
)
from repolab.experiences.spark.dataset import DatasetSpec, generate_dataset
from repolab.experiences.spark.driver import (
    ADDED_STEPS,
    DISCLOSURE_MARKER,
    SUCCESS_MARKER,
    SparkRunConfig,
    render_driver,
)
from repolab.experiences.spark.eventlog import ParsedEventLog, find_event_log, parse_event_log
from repolab.experiences.spark.measurements import SparkRunMeasurements, measure

log = structlog.get_logger(__name__)

STEP_LABEL = "spark_run"

#: Layout inside the environment.
WORKDIR = "/work"
REPO_DIR = f"{WORKDIR}/repo"
DATASET_PATH = f"{WORKDIR}/dataset.csv"
DRIVER_PATH = f"{WORKDIR}/repolab_driver.py"
OUTPUT_PATH = f"{WORKDIR}/output"
EVENT_LOG_DIR = f"{WORKDIR}/events"

#: Where runs are kept on the host, so a raw event log outlives its container.
RUNS_ROOT = Path(".repolab/runs")

SPARK_SUBMIT = "/opt/spark/bin/spark-submit"


class SparkRunFailed(RuntimeError):
    """The Spark application did not complete successfully."""


class RunEnvironmentInfo(BaseModel):
    """Where this run executed."""

    kind: str
    image: str
    spark_version: str | None = None
    master: str
    checkpoint_id: str | None = None


class SparkRunResult(BaseModel):
    """One complete, measured Spark run."""

    run_id: str
    repo_url: str
    commit_sha: str

    dataset: DatasetSpec
    config: SparkRunConfig
    environment: RunEnvironmentInfo

    measurements: SparkRunMeasurements
    evidence: EvidenceBundle

    event_log_path: str
    """The preserved raw measurement source on the host."""

    event_log_sha256: str
    event_log_bytes: int

    driver_stdout_tail: str = ""
    added_steps: list[str] = Field(default_factory=lambda: list(ADDED_STEPS))
    wall_clock_seconds: float = 0.0
    ran_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def dataset_hash(self) -> str:
        return self.dataset.sha256


def _code_references(repo_root: Path, commit_sha: str) -> list[CodeReference]:
    """Cite the repository code these measurements describe.

    Line numbers are located by reading the files, so a reference points at the
    real definition rather than a guess.
    """
    references: list[CodeReference] = []

    job = repo_root / "jobs" / "etl_job.py"
    if job.is_file():
        lines = job.read_text(errors="replace").splitlines()
        start = next(
            (i for i, line in enumerate(lines, 1) if line.startswith("def transform_data")),
            None,
        )
        references.append(
            CodeReference(
                path="jobs/etl_job.py",
                start_line=start,
                end_line=(start + 18) if start else None,
                commit_sha=commit_sha,
                excerpt="the repository's transform_data, executed by this run",
            )
        )

    config = repo_root / "configs" / "etl_config.json"
    if config.is_file():
        references.append(
            CodeReference(
                path="configs/etl_config.json",
                start_line=1,
                commit_sha=commit_sha,
                excerpt=config.read_text(errors="replace").strip()[:200],
            )
        )
    return references


def build_evidence(
    measurements: SparkRunMeasurements,
    *,
    repo_root: Path,
    commit_sha: str,
    event_log_path: Path,
    event_log_excerpt: str,
    driver_tail: str,
) -> EvidenceBundle:
    """Assemble the evidence for this run.

    ``simulated`` is False: this really executed, and the measurements come from
    the event log Spark wrote.
    """
    return EvidenceBundle(
        measurements=measurements.to_measurements(),
        code_references=_code_references(repo_root, commit_sha),
        logs=[
            LogReference(
                source=f"spark event log ({event_log_path.name})",
                excerpt=event_log_excerpt[:2000],
            ),
            LogReference(source="spark-submit stdout", excerpt=driver_tail[-2000:]),
        ],
        simulated=False,
    )


class SparkDemoRunner:
    """Runs the Spark demo in an execution environment and measures it."""

    def __init__(
        self,
        environment,  # ExecutionEnvironment
        *,
        emitter: EventEmitter | None = None,
        runs_root: Path | None = None,
    ) -> None:
        self._environment = environment
        self._emitter = emitter if emitter is not None else NullEmitter()
        self._runs_root = Path(runs_root) if runs_root is not None else RUNS_ROOT

    async def run(
        self,
        *,
        repo_url: str,
        commit_sha: str,
        repository_root: Path,
        image: str,
        config: SparkRunConfig | None = None,
        rows: int | None = None,
        seed: int | None = None,
    ) -> SparkRunResult:
        """Execute the demo and return its measurements.

        Raises:
            SparkRunFailed: the application did not complete.
        """
        config = config or SparkRunConfig()
        run_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()

        dataset_kwargs: dict[str, int] = {}
        if rows is not None:
            dataset_kwargs["rows"] = rows
        if seed is not None:
            dataset_kwargs["seed"] = seed
        data, dataset = generate_dataset(**dataset_kwargs)

        self._emitter.emit(
            EventType.STEP_STARTED,
            f"generated dataset: {dataset.describe()}",
            step=STEP_LABEL,
            data={"dataset_sha256": dataset.sha256, "rows": dataset.rows},
        )

        await self._environment.start(image)
        try:
            await self._prepare(repository_root, data, config)
            stdout, stderr = await self._submit(config)
            event_log = await self._collect_event_log(run_id)

            parsed = parse_event_log(event_log)
            measurements = measure(parsed)
            self._report(measurements, parsed)

            excerpt = "\n".join(event_log.read_text(errors="replace").splitlines()[:6])
            evidence = build_evidence(
                measurements,
                repo_root=repository_root,
                commit_sha=commit_sha,
                event_log_path=event_log,
                event_log_excerpt=excerpt,
                driver_tail=stdout + stderr,
            )

            raw = event_log.read_bytes()
            return SparkRunResult(
                run_id=run_id,
                repo_url=repo_url,
                commit_sha=commit_sha,
                dataset=dataset,
                config=config,
                environment=RunEnvironmentInfo(
                    kind=getattr(self._environment, "kind", "unknown"),
                    image=image,
                    spark_version=measurements.spark_version,
                    master=config.master,
                ),
                measurements=measurements,
                evidence=evidence,
                event_log_path=str(event_log),
                event_log_sha256=hashlib.sha256(raw).hexdigest(),
                event_log_bytes=len(raw),
                driver_stdout_tail=stdout[-4000:],
                wall_clock_seconds=round(time.perf_counter() - started, 3),
            )
        finally:
            await self._environment.stop()

    # -- steps ------------------------------------------------------------

    async def _prepare(self, repository_root: Path, data: bytes, config: SparkRunConfig) -> None:
        """Place the repository, the dataset and the driver into the environment."""
        await self._environment.run(f"mkdir -p {REPO_DIR} {EVENT_LOG_DIR}")

        files: dict[str, bytes] = {DATASET_PATH: data}
        for path in sorted(repository_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(repository_root)
            if any(part in {".git", "__pycache__"} for part in relative.parts):
                continue
            files[f"{REPO_DIR}/{relative.as_posix()}"] = path.read_bytes()

        driver = render_driver(
            repo_root=REPO_DIR,
            dataset_path=DATASET_PATH,
            output_path=OUTPUT_PATH,
            config=config,
        )
        files[DRIVER_PATH] = driver.encode()

        await self._environment.put_files(files)
        self._emitter.emit(
            EventType.PROGRESS,
            f"placed {len(files)} file(s) into the environment",
            step=STEP_LABEL,
            data={"files": len(files)},
        )

    async def _submit(self, config: SparkRunConfig) -> tuple[str, str]:
        """Run spark-submit, and insist on evidence that it worked."""
        args = " ".join(config.submit_args(EVENT_LOG_DIR))
        command = f"{SPARK_SUBMIT} {args} {DRIVER_PATH}"

        self._emitter.emit(
            EventType.STEP_STARTED,
            f"spark-submit with {config.shuffle_partitions} shuffle partitions",
            step=STEP_LABEL,
            data={"shuffle_partitions": config.shuffle_partitions},
        )

        result = await self._environment.run(command, timeout=1800.0)
        stdout, stderr = result.stdout or "", result.stderr or ""

        if result.exit_code != 0:
            raise SparkRunFailed(
                f"spark-submit exited {result.exit_code}: {(stderr or stdout)[-600:]}"
            )
        # A zero exit code is not proof the driver finished its work.
        if SUCCESS_MARKER not in stdout:
            raise SparkRunFailed(
                f"the driver did not report success; tail: {(stdout or stderr)[-600:]}"
            )
        if DISCLOSURE_MARKER not in stdout:
            log.warning("spark.disclosure_marker_missing")

        return stdout, stderr

    async def _collect_event_log(self, run_id: str) -> Path:
        """Copy the event log out of the environment and keep it.

        The log is the primary measurement source, so it is preserved under the
        runs directory rather than read once and thrown away.
        """
        host_events = self._host_path(EVENT_LOG_DIR)
        found = find_event_log(host_events) if host_events else None

        if found is None:
            raise SparkRunFailed(
                "no Spark event log was produced; measurements would have no source"
            )

        destination = self._runs_root / run_id
        destination.mkdir(parents=True, exist_ok=True)
        preserved = destination / found.name
        shutil.copy2(found, preserved)

        self._emitter.emit(
            EventType.PARTIAL_RESULT,
            f"preserved the event log ({preserved.stat().st_size / 1024:.0f} KiB)",
            step=STEP_LABEL,
            data={"event_log": str(preserved)},
        )
        return preserved

    def _host_path(self, container_path: str) -> Path | None:
        """Translate a container path to the host, where the mount allows it."""
        host_workdir = getattr(self._environment, "host_workdir", None)
        container_workdir = getattr(self._environment, "container_workdir", WORKDIR)
        if host_workdir is None:
            return None
        relative = container_path.removeprefix(f"{container_workdir}/")
        return Path(host_workdir) / relative

    def _report(self, measurements: SparkRunMeasurements, parsed: ParsedEventLog) -> None:
        self._emitter.emit(
            EventType.STEP_DONE,
            (
                f"measured {measurements.task_count} tasks across "
                f"{measurements.stage_count} stages from the event log"
            ),
            step=STEP_LABEL,
            data={
                "tasks": measurements.task_count,
                "stages": measurements.stage_count,
                "shuffle_bytes_read": measurements.shuffle_bytes_read,
                "event_log_lines": parsed.lines_total,
            },
        )


def flat_measurements(result: SparkRunResult) -> list[Measurement]:
    """The run's measurements, for anything that wants them without the rest."""
    return result.evidence.measurements
