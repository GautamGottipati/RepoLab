"""Typed measurements derived from a Spark event log.

Every number here is computed by deterministic Python from the event log Spark
wrote. **No model is involved.** CLAUDE.md is explicit: models may reason about
measurements, but never produce them, and statistics are computed by application
code.

Each measurement records the event-log field it came from, so a later explanation
can cite its provenance rather than asserting a figure.
"""

from __future__ import annotations

import statistics
from enum import StrEnum

from pydantic import BaseModel, Field

from repolab.evidence.models import Measurement, MeasurementSource
from repolab.experiences.spark.eventlog import ParsedEventLog, StageRecord, TaskRecord


class Determinism(StrEnum):
    """Whether a measurement is expected to repeat across identical runs.

    The distinction is not cosmetic: counts and byte totals are reproducible,
    wall-clock timings are not, and presenting them together as though both were
    stable would misrepresent what the numbers support.
    """

    STRUCTURAL = "structural"
    """Reproducible given the same inputs: counts, bytes, distribution."""

    TIMING = "timing"
    """Varies between runs: wall clock, durations."""


class SparkMetric(BaseModel):
    """One measured quantity, with where it came from."""

    name: str
    value: float
    unit: str
    determinism: Determinism
    field: str
    """The event-log field this was derived from."""

    stage_id: int | None = None

    def to_measurement(self, variant_id: str | None = None) -> Measurement:
        """Convert to the evidence-bundle measurement type."""
        return Measurement(
            name=self.name,
            value=self.value,
            unit=self.unit,
            source=MeasurementSource.SPARK_EVENT_LOG,
            variant_id=variant_id,
        )


class TaskStats(BaseModel):
    """Statistics over a set of tasks."""

    count: int = 0
    nonempty_count: int = 0
    """Tasks that actually read records; the rest did nothing."""

    slowest_ms: int = 0
    median_ms: float = 0.0
    fastest_ms: int = 0
    total_run_time_ms: int = 0
    p95_ms: float = 0.0
    stddev_ms: float = 0.0

    slowest_task_id: int | None = None

    @property
    def skew_ratio(self) -> float:
        """How much slower the slowest task is than the median.

        1.0 means perfectly even. A large value is the signature of skew.
        """
        if self.median_ms <= 0:
            return 0.0
        return round(self.slowest_ms / self.median_ms, 2)


class PartitionDistribution(BaseModel):
    """How work was spread across partitions.

    The point of recording this is that a shuffle partition count chosen without
    regard to the data produces a long tail of empty tasks and a few heavy ones.
    """

    partitions: int = 0
    empty_partitions: int = 0
    max_records: int = 0
    median_records: float = 0.0
    total_records: int = 0
    top_records: list[int] = Field(default_factory=list)
    """The heaviest partitions' record counts, largest first."""

    @property
    def empty_fraction(self) -> float:
        if self.partitions == 0:
            return 0.0
        return round(self.empty_partitions / self.partitions, 4)

    @property
    def record_skew_ratio(self) -> float:
        """Heaviest partition relative to the median non-empty partition."""
        if self.median_records <= 0:
            return 0.0
        return round(self.max_records / self.median_records, 2)


class StageMeasurement(BaseModel):
    """What one stage did."""

    stage_id: int
    name: str
    num_tasks: int
    duration_ms: int | None = None

    shuffle_bytes_read: int = 0
    shuffle_records_read: int = 0
    shuffle_bytes_written: int = 0
    shuffle_records_written: int = 0
    memory_bytes_spilled: int = 0
    disk_bytes_spilled: int = 0

    tasks: TaskStats = Field(default_factory=TaskStats)

    @property
    def shuffled(self) -> bool:
        return bool(self.shuffle_bytes_read or self.shuffle_bytes_written)

    @property
    def spilled(self) -> bool:
        return bool(self.memory_bytes_spilled or self.disk_bytes_spilled)


class SparkRunMeasurements(BaseModel):
    """Everything measured from one Spark run."""

    app_id: str | None = None
    spark_version: str | None = None

    total_runtime_ms: int | None = None
    stage_count: int = 0
    task_count: int = 0
    failed_task_count: int = 0

    shuffle_bytes_read: int = 0
    shuffle_records_read: int = 0
    shuffle_bytes_written: int = 0
    shuffle_records_written: int = 0
    memory_bytes_spilled: int = 0
    disk_bytes_spilled: int = 0

    stages: list[StageMeasurement] = Field(default_factory=list)
    tasks: TaskStats = Field(default_factory=TaskStats)
    distribution: PartitionDistribution = Field(default_factory=PartitionDistribution)

    event_log_lines: int = 0
    event_log_skipped_lines: int = 0

    @property
    def shuffle_stage(self) -> StageMeasurement | None:
        """The stage that read the most shuffle data, if any did."""
        shuffling = [stage for stage in self.stages if stage.shuffle_bytes_read]
        if not shuffling:
            return None
        return max(shuffling, key=lambda stage: stage.shuffle_bytes_read)

    def metrics(self) -> list[SparkMetric]:
        """Flatten into individual metrics, each labelled with its determinism."""
        found: list[SparkMetric] = []

        def add(
            name: str,
            value: float | None,
            unit: str,
            determinism: Determinism,
            field: str,
        ) -> None:
            if value is not None:
                found.append(
                    SparkMetric(
                        name=name,
                        value=float(value),
                        unit=unit,
                        determinism=determinism,
                        field=field,
                    )
                )

        add(
            "total runtime",
            self.total_runtime_ms,
            "ms",
            Determinism.TIMING,
            "SparkListenerApplicationEnd.Timestamp - ApplicationStart.Timestamp",
        )
        add(
            "stage count",
            self.stage_count,
            "stages",
            Determinism.STRUCTURAL,
            "SparkListenerStageCompleted",
        )
        add("task count", self.task_count, "tasks", Determinism.STRUCTURAL, "SparkListenerTaskEnd")
        add(
            "shuffle read",
            self.shuffle_bytes_read,
            "bytes",
            Determinism.STRUCTURAL,
            "Shuffle Read Metrics.Local + Remote Bytes Read",
        )
        add(
            "shuffle write",
            self.shuffle_bytes_written,
            "bytes",
            Determinism.STRUCTURAL,
            "Shuffle Write Metrics.Shuffle Bytes Written",
        )
        add(
            "shuffle records read",
            self.shuffle_records_read,
            "records",
            Determinism.STRUCTURAL,
            "Shuffle Read Metrics.Total Records Read",
        )
        add(
            "memory spilled",
            self.memory_bytes_spilled,
            "bytes",
            Determinism.STRUCTURAL,
            "Task Metrics.Memory Bytes Spilled",
        )
        add(
            "disk spilled",
            self.disk_bytes_spilled,
            "bytes",
            Determinism.STRUCTURAL,
            "Task Metrics.Disk Bytes Spilled",
        )
        add(
            "slowest task",
            self.tasks.slowest_ms,
            "ms",
            Determinism.TIMING,
            "Task Info.Finish Time - Launch Time",
        )
        add(
            "median task",
            self.tasks.median_ms,
            "ms",
            Determinism.TIMING,
            "Task Info.Finish Time - Launch Time",
        )
        add(
            "task skew ratio",
            self.tasks.skew_ratio,
            "ratio",
            Determinism.TIMING,
            "slowest / median task duration",
        )
        add(
            "partitions",
            self.distribution.partitions,
            "partitions",
            Determinism.STRUCTURAL,
            "Task Info.Partition ID",
        )
        add(
            "empty partitions",
            self.distribution.empty_partitions,
            "partitions",
            Determinism.STRUCTURAL,
            "Shuffle Read Metrics.Total Records Read == 0",
        )
        add(
            "heaviest partition records",
            self.distribution.max_records,
            "records",
            Determinism.STRUCTURAL,
            "Shuffle Read Metrics.Total Records Read",
        )
        add(
            "partition skew ratio",
            self.distribution.record_skew_ratio,
            "ratio",
            Determinism.STRUCTURAL,
            "heaviest / median non-empty partition records",
        )
        return found

    def to_measurements(self, variant_id: str | None = None) -> list[Measurement]:
        """Evidence-bundle measurements, every one sourced from the event log."""
        return [metric.to_measurement(variant_id) for metric in self.metrics()]

    def structural_fingerprint(self) -> dict[str, float]:
        """The fields that must match across identical runs.

        Used to check reproducibility without comparing timings, which legitimately
        vary.
        """
        return {
            metric.name: metric.value
            for metric in self.metrics()
            if metric.determinism is Determinism.STRUCTURAL
        }


def _task_stats(tasks: list[TaskRecord]) -> TaskStats:
    """Durations and spread, computed with the standard library."""
    if not tasks:
        return TaskStats()

    durations = [task.duration_ms for task in tasks]
    slowest = max(tasks, key=lambda task: task.duration_ms)

    return TaskStats(
        count=len(tasks),
        nonempty_count=sum(1 for task in tasks if task.did_work),
        slowest_ms=slowest.duration_ms,
        median_ms=round(statistics.median(durations), 2),
        fastest_ms=min(durations),
        total_run_time_ms=sum(task.executor_run_time_ms for task in tasks),
        p95_ms=round(_percentile(durations, 0.95), 2),
        stddev_ms=round(statistics.pstdev(durations), 2) if len(durations) > 1 else 0.0,
        slowest_task_id=slowest.task_id,
    )


def _percentile(values: list[int], fraction: float) -> float:
    """The *fraction* percentile by nearest rank. Deterministic for a given input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return float(ordered[index])


def _distribution(tasks: list[TaskRecord]) -> PartitionDistribution:
    """How shuffle records landed across the partitions of one stage.

    **Every task in the stage counts, including those that read nothing.** A
    reduce task that received no rows still occupies a shuffle partition, and
    those empty partitions are precisely the symptom of a partition count chosen
    without regard to the data. Filtering them out would hide the finding.
    """
    if not tasks:
        return PartitionDistribution()

    records = [task.shuffle_records_read for task in tasks]
    nonempty = [count for count in records if count > 0]

    return PartitionDistribution(
        partitions=len(tasks),
        empty_partitions=sum(1 for count in records if count == 0),
        max_records=max(records) if records else 0,
        # The median over *non-empty* partitions: a median of mostly zeros would
        # make the skew ratio meaningless.
        median_records=round(statistics.median(nonempty), 2) if nonempty else 0.0,
        total_records=sum(records),
        top_records=sorted(records, reverse=True)[:5],
    )


def _stage_measurement(stage: StageRecord, tasks: list[TaskRecord]) -> StageMeasurement:
    return StageMeasurement(
        stage_id=stage.stage_id,
        name=stage.name,
        num_tasks=stage.num_tasks or len(tasks),
        duration_ms=stage.duration_ms,
        shuffle_bytes_read=sum(task.shuffle_bytes_read for task in tasks),
        shuffle_records_read=sum(task.shuffle_records_read for task in tasks),
        shuffle_bytes_written=sum(task.shuffle_bytes_written for task in tasks),
        shuffle_records_written=sum(task.shuffle_records_written for task in tasks),
        memory_bytes_spilled=sum(task.memory_bytes_spilled for task in tasks),
        disk_bytes_spilled=sum(task.disk_bytes_spilled for task in tasks),
        tasks=_task_stats(tasks),
    )


def measure(parsed: ParsedEventLog) -> SparkRunMeasurements:
    """Compute measurements from a parsed event log.

    Pure arithmetic over what Spark recorded. Nothing is estimated, and nothing is
    filled in when the log does not say.
    """
    successful = parsed.successful_tasks
    by_stage: dict[int, list[TaskRecord]] = {}
    for task in successful:
        by_stage.setdefault(task.stage_id, []).append(task)

    stages = [
        _stage_measurement(stage, by_stage.get(stage.stage_id, [])) for stage in parsed.stages
    ]

    # The distribution comes from the shuffle-reading stage, where partitioning
    # actually determines how work is spread.
    shuffle_stage_id = None
    if stages:
        shuffling = [stage for stage in stages if stage.shuffle_bytes_read]
        if shuffling:
            shuffle_stage_id = max(shuffling, key=lambda stage: stage.shuffle_bytes_read).stage_id

    distribution_tasks = by_stage.get(shuffle_stage_id, []) if shuffle_stage_id is not None else []

    return SparkRunMeasurements(
        app_id=parsed.app_id,
        spark_version=parsed.spark_version,
        total_runtime_ms=parsed.total_runtime_ms,
        stage_count=len(parsed.stages),
        task_count=len(successful),
        failed_task_count=len(parsed.tasks) - len(successful),
        shuffle_bytes_read=sum(task.shuffle_bytes_read for task in successful),
        shuffle_records_read=sum(task.shuffle_records_read for task in successful),
        shuffle_bytes_written=sum(task.shuffle_bytes_written for task in successful),
        shuffle_records_written=sum(task.shuffle_records_written for task in successful),
        memory_bytes_spilled=sum(task.memory_bytes_spilled for task in successful),
        disk_bytes_spilled=sum(task.disk_bytes_spilled for task in successful),
        stages=stages,
        tasks=_task_stats(successful),
        distribution=_distribution(distribution_tasks),
        event_log_lines=parsed.lines_total,
        event_log_skipped_lines=parsed.lines_skipped,
    )
