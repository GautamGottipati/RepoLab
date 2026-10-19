"""The Spark data-playground experience backend.

Runs the repository's own transformation on real Spark against a deterministic
synthetic dataset, with event logging enabled, and turns the resulting event log
into typed measurements.

Every number comes from the event log Spark wrote and is computed by deterministic
Python. No model produces a measurement.
"""

from __future__ import annotations

from repolab.experiences.spark.dataset import (
    DEFAULT_ROWS,
    DEFAULT_SEED,
    SKEWED_KEY,
    DatasetSpec,
    generate_dataset,
)
from repolab.experiences.spark.driver import (
    ADDED_STEPS,
    DEFAULT_SHUFFLE_PARTITIONS,
    SUCCESS_MARKER,
    SparkRunConfig,
    render_driver,
)
from repolab.experiences.spark.eventlog import (
    ParsedEventLog,
    StageRecord,
    TaskRecord,
    find_event_log,
    parse_event_log,
    parse_event_log_lines,
)
from repolab.experiences.spark.measurements import (
    Determinism,
    PartitionDistribution,
    SparkMetric,
    SparkRunMeasurements,
    StageMeasurement,
    TaskStats,
    measure,
)
from repolab.experiences.spark.runner import (
    SparkDemoRunner,
    SparkRunFailed,
    SparkRunResult,
    build_evidence,
)

__all__ = [
    "ADDED_STEPS",
    "DEFAULT_ROWS",
    "DEFAULT_SEED",
    "DEFAULT_SHUFFLE_PARTITIONS",
    "SKEWED_KEY",
    "SUCCESS_MARKER",
    "DatasetSpec",
    "Determinism",
    "ParsedEventLog",
    "PartitionDistribution",
    "SparkDemoRunner",
    "SparkMetric",
    "SparkRunConfig",
    "SparkRunFailed",
    "SparkRunMeasurements",
    "SparkRunResult",
    "StageMeasurement",
    "StageRecord",
    "TaskRecord",
    "TaskStats",
    "build_evidence",
    "find_event_log",
    "generate_dataset",
    "measure",
    "parse_event_log",
    "parse_event_log_lines",
    "render_driver",
]
