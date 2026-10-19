"""Measurement statistics.

Every figure is arithmetic over what Spark recorded, so these tests assert exact
expected values. The property that matters most is that **empty partitions are
counted**: filtering them out would hide the finding this phase exists to produce.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repolab.evidence.models import MeasurementSource
from repolab.experiences.spark.eventlog import (
    ParsedEventLog,
    StageRecord,
    TaskRecord,
    parse_event_log,
)
from repolab.experiences.spark.measurements import Determinism, measure

FIXTURE = Path(__file__).parent / "fixtures" / "spark" / "real_event_log.jsonl"


def task(
    task_id: int,
    *,
    stage: int = 2,
    duration: int = 10,
    records: int = 0,
    read: int = 0,
    written: int = 0,
    spill_memory: int = 0,
    spill_disk: int = 0,
    failed: bool = False,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        stage_id=stage,
        index=task_id,
        launch_time=1000,
        finish_time=1000 + duration,
        failed=failed,
        executor_run_time_ms=duration,
        shuffle_bytes_read=read,
        shuffle_records_read=records,
        shuffle_bytes_written=written,
        memory_bytes_spilled=spill_memory,
        disk_bytes_spilled=spill_disk,
    )


def log_with(tasks: list[TaskRecord], *, stage_ids: tuple[int, ...] = (2,)) -> ParsedEventLog:
    return ParsedEventLog(
        app_id="local-1",
        spark_version="3.5.1",
        started_at_ms=1000,
        ended_at_ms=9000,
        stages=[
            StageRecord(
                stage_id=stage_id,
                name=f"stage {stage_id}",
                num_tasks=sum(1 for t in tasks if t.stage_id == stage_id),
                submission_time=1000,
                completion_time=5000,
            )
            for stage_id in stage_ids
        ],
        tasks=tasks,
    )


@pytest.fixture(scope="module")
def real():
    return measure(parse_event_log(FIXTURE))


class TestTaskStatistics:
    def test_durations_are_computed_exactly(self) -> None:
        tasks = [task(1, duration=10), task(2, duration=20), task(3, duration=90)]
        stats = measure(log_with(tasks)).tasks

        assert stats.count == 3
        assert stats.fastest_ms == 10
        assert stats.median_ms == 20
        assert stats.slowest_ms == 90
        assert stats.slowest_task_id == 3

    def test_the_median_of_an_even_count_is_interpolated(self) -> None:
        tasks = [task(i, duration=d) for i, d in enumerate([10, 20, 30, 40], start=1)]
        assert measure(log_with(tasks)).tasks.median_ms == 25.0

    def test_skew_ratio_is_slowest_over_median(self) -> None:
        tasks = [task(1, duration=10), task(2, duration=10), task(3, duration=100)]
        assert measure(log_with(tasks)).tasks.skew_ratio == 10.0

    def test_an_even_distribution_has_a_skew_ratio_of_one(self) -> None:
        tasks = [task(i, duration=50) for i in range(1, 6)]
        assert measure(log_with(tasks)).tasks.skew_ratio == 1.0

    def test_a_single_task_is_handled(self) -> None:
        stats = measure(log_with([task(1, duration=42)])).tasks
        assert stats.count == 1
        assert stats.median_ms == stats.slowest_ms == 42
        assert stats.stddev_ms == 0.0

    def test_no_tasks_yields_zeroes_not_errors(self) -> None:
        stats = measure(ParsedEventLog()).tasks
        assert stats.count == 0
        assert stats.skew_ratio == 0.0

    def test_failed_tasks_are_excluded_from_statistics(self) -> None:
        tasks = [task(1, duration=10), task(2, duration=9999, failed=True)]
        measurements = measure(log_with(tasks))

        assert measurements.task_count == 1
        assert measurements.failed_task_count == 1
        assert measurements.tasks.slowest_ms == 10


class TestPartitionDistribution:
    def test_empty_partitions_are_counted(self) -> None:
        """The whole point: 200 partitions, most of which do nothing."""
        tasks = [task(1, records=1000, read=500)] + [
            task(i, records=0, read=0) for i in range(2, 201)
        ]
        distribution = measure(log_with(tasks)).distribution

        assert distribution.partitions == 200
        assert distribution.empty_partitions == 199
        assert distribution.empty_fraction == 0.995

    def test_the_heaviest_partition_is_identified(self) -> None:
        tasks = [
            task(1, records=100, read=10),
            task(2, records=100, read=10),
            task(3, records=5000, read=10),
        ]
        distribution = measure(log_with(tasks)).distribution

        assert distribution.max_records == 5000
        assert distribution.top_records[0] == 5000

    def test_the_median_ignores_empty_partitions(self) -> None:
        """A median over mostly zeroes would make the skew ratio meaningless."""
        tasks = [task(1, records=100, read=1), task(2, records=100, read=1)] + [
            task(i, records=0, read=1) for i in range(3, 20)
        ]
        distribution = measure(log_with(tasks)).distribution

        assert distribution.median_records == 100
        assert distribution.record_skew_ratio == 1.0

    def test_record_skew_ratio_is_heaviest_over_median(self) -> None:
        tasks = [
            task(1, records=10, read=1),
            task(2, records=10, read=1),
            task(3, records=500, read=1),
        ]
        assert measure(log_with(tasks)).distribution.record_skew_ratio == 50.0

    def test_totals_add_up(self) -> None:
        tasks = [task(i, records=10, read=1) for i in range(1, 6)]
        assert measure(log_with(tasks)).distribution.total_records == 50

    def test_no_shuffle_means_an_empty_distribution(self) -> None:
        tasks = [task(i, records=0, read=0) for i in range(1, 4)]
        assert measure(log_with(tasks)).distribution.partitions == 0


class TestAggregateTotals:
    def test_shuffle_and_spill_totals_sum_across_tasks(self) -> None:
        tasks = [
            task(1, read=100, written=200, records=5, spill_memory=10, spill_disk=20),
            task(2, read=300, written=400, records=7, spill_memory=30, spill_disk=40),
        ]
        measurements = measure(log_with(tasks))

        assert measurements.shuffle_bytes_read == 400
        assert measurements.shuffle_bytes_written == 600
        assert measurements.shuffle_records_read == 12
        assert measurements.memory_bytes_spilled == 40
        assert measurements.disk_bytes_spilled == 60

    def test_total_runtime_comes_from_the_application_events(self) -> None:
        assert measure(log_with([task(1)])).total_runtime_ms == 8000

    def test_stage_measurements_are_grouped_by_stage(self) -> None:
        tasks = [task(1, stage=1, written=500), task(2, stage=2, read=500, records=9)]
        measurements = measure(log_with(tasks, stage_ids=(1, 2)))

        by_id = {stage.stage_id: stage for stage in measurements.stages}
        assert by_id[1].shuffle_bytes_written == 500
        assert by_id[2].shuffle_bytes_read == 500
        assert by_id[1].shuffled and by_id[2].shuffled

    def test_a_stage_without_shuffle_is_marked_as_such(self) -> None:
        measurements = measure(log_with([task(1, stage=0)], stage_ids=(0,)))
        assert measurements.stages[0].shuffled is False

    def test_the_shuffle_stage_is_the_heaviest_reader(self) -> None:
        tasks = [task(1, stage=1, read=10, records=1), task(2, stage=2, read=900, records=9)]
        measurements = measure(log_with(tasks, stage_ids=(1, 2)))
        assert measurements.shuffle_stage.stage_id == 2


class TestMetricProvenance:
    def test_every_metric_names_its_event_log_field(self, real) -> None:
        """A figure without provenance cannot be cited as evidence."""
        for metric in real.metrics():
            assert metric.field.strip()

    def test_metrics_are_split_by_determinism(self, real) -> None:
        kinds = {metric.determinism for metric in real.metrics()}
        assert kinds == {Determinism.STRUCTURAL, Determinism.TIMING}

    def test_counts_and_bytes_are_structural(self, real) -> None:
        by_name = {metric.name: metric for metric in real.metrics()}
        for name in ("task count", "shuffle read", "empty partitions"):
            assert by_name[name].determinism is Determinism.STRUCTURAL

    def test_durations_are_timing(self, real) -> None:
        by_name = {metric.name: metric for metric in real.metrics()}
        for name in ("total runtime", "slowest task", "median task"):
            assert by_name[name].determinism is Determinism.TIMING

    def test_the_fingerprint_contains_only_structural_fields(self, real) -> None:
        fingerprint = real.structural_fingerprint()
        assert "task count" in fingerprint
        assert "total runtime" not in fingerprint
        assert "slowest task" not in fingerprint

    def test_every_measurement_cites_the_event_log(self, real) -> None:
        for measurement in real.to_measurements():
            assert measurement.source is MeasurementSource.SPARK_EVENT_LOG


class TestAgainstTheRealRun:
    def test_it_measured_the_real_shuffle(self, real) -> None:
        assert real.shuffle_bytes_read > 0
        assert real.shuffle_records_read > 0

    def test_the_configured_partition_count_is_visible(self, real) -> None:
        assert real.distribution.partitions == 200

    def test_most_partitions_did_nothing(self, real) -> None:
        """200 partitions over 7 distinct keys: the finding, from real data."""
        assert real.distribution.empty_partitions > 150
        assert real.distribution.empty_fraction > 0.75

    def test_the_skew_is_substantial(self, real) -> None:
        assert real.distribution.record_skew_ratio > 5

    def test_the_event_log_parsed_cleanly(self, real) -> None:
        assert real.event_log_lines > 0
        assert real.event_log_skipped_lines == 0

    def test_the_spark_version_is_recorded(self, real) -> None:
        assert real.spark_version == "3.5.1"


class TestDeterminismOfTheStatistics:
    def test_measuring_the_same_log_twice_is_identical(self) -> None:
        """No randomness, no ordering dependence."""
        first = measure(parse_event_log(FIXTURE))
        second = measure(parse_event_log(FIXTURE))
        assert first.model_dump_json() == second.model_dump_json()

    def test_task_order_does_not_change_the_result(self) -> None:
        tasks = [task(i, duration=i * 10, records=i, read=1) for i in range(1, 11)]
        forward = measure(log_with(tasks)).structural_fingerprint()
        backward = measure(log_with(list(reversed(tasks)))).structural_fingerprint()
        assert forward == backward

    def test_the_fingerprint_is_json_serialisable(self, real) -> None:
        assert json.loads(json.dumps(real.structural_fingerprint()))
