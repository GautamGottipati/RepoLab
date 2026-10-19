"""Spark event log parsing.

The event log is JSON-lines written by Spark itself, and it is the authoritative
record of what a job did. This module turns it into typed events.

**The key names here were read from the Spark 3.5.1 source**
(``core/src/main/scala/org/apache/spark/util/JsonProtocol.scala``), not from the
monitoring documentation. That matters: the REST API exposes
``executorRunTime`` and ``shuffleReadMetrics.remoteBytesRead``, while the event
log writes ``"Executor Run Time"`` and ``"Shuffle Read Metrics" -> "Remote Bytes
Read"``. Parsing against the documented REST names would silently yield zero for
every metric.

No pyspark import: this is ordinary JSON, so measurements can be parsed and
tested without Spark installed.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

#: Spark appends this while the application is still running.
IN_PROGRESS_SUFFIX = ".inprogress"

# Event names, as written to the log.
APPLICATION_START = "SparkListenerApplicationStart"
APPLICATION_END = "SparkListenerApplicationEnd"
STAGE_COMPLETED = "SparkListenerStageCompleted"
TASK_END = "SparkListenerTaskEnd"
JOB_START = "SparkListenerJobStart"


class TaskRecord(BaseModel):
    """One completed task, as the event log describes it."""

    task_id: int
    stage_id: int
    index: int
    partition_id: int | None = None
    launch_time: int = 0
    finish_time: int = 0
    failed: bool = False
    executor_run_time_ms: int = 0

    shuffle_bytes_read: int = 0
    shuffle_records_read: int = 0
    shuffle_bytes_written: int = 0
    shuffle_records_written: int = 0
    memory_bytes_spilled: int = 0
    disk_bytes_spilled: int = 0
    input_records_read: int = 0

    @property
    def duration_ms(self) -> int:
        """Wall-clock duration, or 0 when the log lacks a finish time."""
        if self.finish_time and self.launch_time:
            return max(0, self.finish_time - self.launch_time)
        return 0

    @property
    def did_work(self) -> bool:
        """Whether this task actually processed anything.

        With 200 shuffle partitions over a small dataset most tasks read nothing,
        and distinguishing them is the whole point of the distribution.
        """
        return bool(self.shuffle_records_read or self.input_records_read)


class StageRecord(BaseModel):
    """One completed stage."""

    stage_id: int
    attempt_id: int = 0
    name: str = ""
    num_tasks: int = 0
    submission_time: int | None = None
    completion_time: int | None = None
    failure_reason: str | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.submission_time and self.completion_time:
            return max(0, self.completion_time - self.submission_time)
        return None

    @property
    def succeeded(self) -> bool:
        return self.failure_reason is None


class ParsedEventLog(BaseModel):
    """Everything the parser recovered from one event log."""

    app_id: str | None = None
    app_name: str | None = None
    spark_version: str | None = None
    started_at_ms: int | None = None
    ended_at_ms: int | None = None

    stages: list[StageRecord] = Field(default_factory=list)
    tasks: list[TaskRecord] = Field(default_factory=list)

    lines_total: int = 0
    lines_parsed: int = 0
    lines_skipped: int = 0
    """Malformed or truncated lines. A partially written log is still useful."""

    source_path: str | None = None
    in_progress: bool = False

    @property
    def total_runtime_ms(self) -> int | None:
        """Application wall clock, straight from the application events."""
        if self.started_at_ms and self.ended_at_ms:
            return max(0, self.ended_at_ms - self.started_at_ms)
        return None

    @property
    def successful_tasks(self) -> list[TaskRecord]:
        return [task for task in self.tasks if not task.failed]

    @property
    def is_empty(self) -> bool:
        return not self.tasks and not self.stages


def _as_int(value: object) -> int:
    """Coerce a log value to int. Spark writes numbers, but be forgiving."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return 0


def _task_from_event(payload: dict) -> TaskRecord | None:
    info = payload.get("Task Info")
    if not isinstance(info, dict):
        return None

    metrics = payload.get("Task Metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    read = metrics.get("Shuffle Read Metrics")
    read = read if isinstance(read, dict) else {}
    write = metrics.get("Shuffle Write Metrics")
    write = write if isinstance(write, dict) else {}
    inputs = metrics.get("Input Metrics")
    inputs = inputs if isinstance(inputs, dict) else {}

    # Shuffle read is local plus remote; neither alone is the total.
    shuffle_bytes_read = _as_int(read.get("Local Bytes Read")) + _as_int(
        read.get("Remote Bytes Read")
    )

    return TaskRecord(
        task_id=_as_int(info.get("Task ID")),
        stage_id=_as_int(payload.get("Stage ID")),
        index=_as_int(info.get("Index")),
        partition_id=(_as_int(info.get("Partition ID")) if "Partition ID" in info else None),
        launch_time=_as_int(info.get("Launch Time")),
        finish_time=_as_int(info.get("Finish Time")),
        failed=bool(info.get("Failed", False)),
        executor_run_time_ms=_as_int(metrics.get("Executor Run Time")),
        shuffle_bytes_read=shuffle_bytes_read,
        shuffle_records_read=_as_int(read.get("Total Records Read")),
        shuffle_bytes_written=_as_int(write.get("Shuffle Bytes Written")),
        shuffle_records_written=_as_int(write.get("Shuffle Records Written")),
        memory_bytes_spilled=_as_int(metrics.get("Memory Bytes Spilled")),
        disk_bytes_spilled=_as_int(metrics.get("Disk Bytes Spilled")),
        input_records_read=_as_int(inputs.get("Records Read")),
    )


def _stage_from_event(payload: dict) -> StageRecord | None:
    info = payload.get("Stage Info")
    if not isinstance(info, dict):
        return None
    return StageRecord(
        stage_id=_as_int(info.get("Stage ID")),
        attempt_id=_as_int(info.get("Stage Attempt ID")),
        name=str(info.get("Stage Name", "")),
        num_tasks=_as_int(info.get("Number of Tasks")),
        submission_time=(
            _as_int(info.get("Submission Time")) or None if "Submission Time" in info else None
        ),
        completion_time=(
            _as_int(info.get("Completion Time")) or None if "Completion Time" in info else None
        ),
        failure_reason=info.get("Failure Reason") or None,
    )


def parse_event_log_lines(lines: list[str]) -> ParsedEventLog:
    """Parse JSON-lines event log content.

    A malformed or truncated line is skipped and counted rather than fatal: a log
    from a crashed application is still worth reading.
    """
    parsed = ParsedEventLog()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        parsed.lines_total += 1

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            parsed.lines_skipped += 1
            continue
        if not isinstance(payload, dict):
            parsed.lines_skipped += 1
            continue

        parsed.lines_parsed += 1
        event = payload.get("Event")

        if event == APPLICATION_START:
            parsed.app_id = payload.get("App ID") or parsed.app_id
            parsed.app_name = payload.get("App Name") or parsed.app_name
            parsed.started_at_ms = _as_int(payload.get("Timestamp")) or None
        elif event == APPLICATION_END:
            parsed.ended_at_ms = _as_int(payload.get("Timestamp")) or None
        elif event == STAGE_COMPLETED:
            stage = _stage_from_event(payload)
            if stage is not None:
                parsed.stages.append(stage)
        elif event == TASK_END:
            task = _task_from_event(payload)
            if task is not None:
                parsed.tasks.append(task)
        elif event == "SparkListenerLogStart":
            parsed.spark_version = payload.get("Spark Version") or parsed.spark_version
        # Anything else is ignored: the log carries many events RepoLab does not
        # need, and an unknown one must never break parsing.

    parsed.stages.sort(key=lambda stage: (stage.stage_id, stage.attempt_id))
    parsed.tasks.sort(key=lambda task: task.task_id)
    return parsed


def parse_event_log(path: Path) -> ParsedEventLog:
    """Parse an event log file."""
    path = Path(path)
    content = path.read_text(errors="replace").splitlines()
    parsed = parse_event_log_lines(content)
    parsed.source_path = str(path)
    parsed.in_progress = path.name.endswith(IN_PROGRESS_SUFFIX)
    return parsed


def find_event_log(directory: Path) -> Path | None:
    """The event log in *directory*, preferring a completed one.

    Spark appends ``.inprogress`` until the application ends, so a finished log is
    the better source when both are present.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return None

    candidates = [item for item in sorted(directory.iterdir()) if item.is_file()]
    completed = [item for item in candidates if not item.name.endswith(IN_PROGRESS_SUFFIX)]
    chosen = completed or candidates
    if not chosen:
        return None
    # Newest, so repeated runs in one directory resolve to the latest.
    return max(chosen, key=lambda item: item.stat().st_mtime)
