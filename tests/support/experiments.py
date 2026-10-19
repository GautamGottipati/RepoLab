"""Builders for experiment tests.

Variant runs are constructed from real measurement types rather than from
dictionaries, so a test exercises the same ``SparkRunMeasurements`` the event-log
parser produces. The numbers are stated explicitly by each test; nothing here
invents a plausible-looking default that a test might then assert against.
"""

from __future__ import annotations

import hashlib
from typing import Any

from repolab.experiences.spark.dataset import DatasetSpec
from repolab.experiences.spark.driver import SparkRunConfig
from repolab.experiences.spark.measurements import (
    PartitionDistribution,
    SparkRunMeasurements,
    StageMeasurement,
    TaskStats,
)
from repolab.experiences.spark.runner import RunEnvironmentInfo, SparkRunResult
from repolab.experiments.models import ProposedPlan, VariantRun
from repolab.experiments.validation import validate_plan

DATASET_HASH = "a" * 64
IMAGE = "apache/spark:3.5.1-python3"


def dataset_spec(*, rows: int = 1000, seed: int = 1, sha256: str = DATASET_HASH) -> DatasetSpec:
    return DatasetSpec(
        rows=rows,
        seed=seed,
        columns=["id", "first_name", "second_name", "floor", "dept"],
        skewed_key="engineering",
        skew_fraction=0.72,
        skewed_rows=int(rows * 0.72),
        sha256=sha256,
        size_bytes=rows * 40,
    )


def measurements(
    *,
    total_runtime_ms: int = 1000,
    task_count: int = 10,
    partitions: int = 200,
    empty: int = 193,
    shuffle_read: int = 2_000_000,
    spark_version: str = "3.5.1",
) -> SparkRunMeasurements:
    """Measurements shaped like a real run of the demo job."""
    return SparkRunMeasurements(
        app_id="app-test",
        spark_version=spark_version,
        total_runtime_ms=total_runtime_ms,
        stage_count=3,
        task_count=task_count,
        shuffle_bytes_read=shuffle_read,
        shuffle_records_read=200_000,
        shuffle_bytes_written=shuffle_read,
        stages=[
            StageMeasurement(
                stage_id=2,
                name="groupBy at repolab_driver.py",
                num_tasks=partitions,
                duration_ms=total_runtime_ms // 2,
                shuffle_bytes_read=shuffle_read,
                shuffle_records_read=200_000,
                tasks=TaskStats(count=partitions, nonempty_count=partitions - empty),
            )
        ],
        tasks=TaskStats(
            count=task_count,
            nonempty_count=task_count,
            slowest_ms=120,
            median_ms=40.0,
            fastest_ms=5,
            p95_ms=110.0,
            stddev_ms=25.0,
            slowest_task_id=7,
        ),
        distribution=PartitionDistribution(
            partitions=partitions,
            empty_partitions=empty,
            max_records=144_000,
            median_records=9_300.0,
            total_records=200_000,
            top_records=[144_000, 9_400, 9_300],
        ),
        event_log_lines=417,
    )


def variant_run(
    *,
    value: int,
    repeat: int,
    variant_id: str | None = None,
    contended: bool = False,
    dataset_hash: str = DATASET_HASH,
    image: str = IMAGE,
    event_log_path: str = "/tmp/repolab-test/does-not-exist.jsonl",
    **measurement_kwargs: Any,
) -> VariantRun:
    """One variant run, with its measurements stated by the caller."""
    measured = measurements(**measurement_kwargs)
    return VariantRun(
        variant_id=variant_id or f"{value}-test",
        value=value,
        repeat=repeat,
        run_id=f"run-{value}-{repeat}",
        dataset_hash=dataset_hash,
        image=image,
        measurements=measured,
        event_log_path=event_log_path,
        event_log_sha256=hashlib.sha256(f"{value}-{repeat}".encode()).hexdigest(),
        wall_clock_seconds=12.5,
        timing_contended=contended,
    )


def spark_run_result(
    *,
    value: int,
    repeat: int,
    dataset_hash: str = DATASET_HASH,
    image: str = IMAGE,
    **measurement_kwargs: Any,
) -> SparkRunResult:
    """What a runner returns, so the executor can be driven without Docker."""
    measured = measurements(**measurement_kwargs)
    return SparkRunResult(
        run_id=f"run-{value}-{repeat}",
        repo_url="https://github.com/test/repo",
        commit_sha="c" * 40,
        dataset=dataset_spec(sha256=dataset_hash),
        config=SparkRunConfig(shuffle_partitions=value),
        environment=RunEnvironmentInfo(
            kind="docker", image=image, spark_version="3.5.1", master="local[4]"
        ),
        measurements=measured,
        evidence=measured.to_measurements()
        and __import__("repolab.evidence.models", fromlist=["EvidenceBundle"]).EvidenceBundle(
            measurements=measured.to_measurements()
        ),
        event_log_path="/tmp/repolab-test/does-not-exist.jsonl",
        event_log_sha256=hashlib.sha256(f"{value}-{repeat}".encode()).hexdigest(),
        event_log_bytes=1024,
        wall_clock_seconds=12.5,
    )


def plan(
    *,
    values: list[int] | None = None,
    repeats: int = 2,
    metrics: list[str] | None = None,
    question: str = "Why does this job use 200 shuffle partitions?",
    concurrency: int = 1,
    baseline: int = 200,
):
    """A validated plan, built through the real validation path."""
    proposal = ProposedPlan(
        question=question,
        hypothesis="More partitions than distinct keys leaves most of them empty.",
        variable="spark.sql.shuffle.partitions",
        values=values if values is not None else [50, 200],
        metrics=metrics if metrics is not None else ["total runtime", "partitions"],
        repeats=repeats,
    )
    return validate_plan(
        proposal, baseline_value=baseline, image=IMAGE, concurrency=concurrency
    ).require()
