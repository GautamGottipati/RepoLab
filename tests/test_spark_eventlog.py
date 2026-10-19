"""Spark event log parsing.

Asserted against an event log captured from a **real Spark 3.5.1 run**, because
the schema is the thing most likely to be got wrong: the monitoring documentation
describes the REST API's field names (`executorRunTime`,
`shuffleReadMetrics.remoteBytesRead`), while the event log uses capitalised,
space-separated keys. Parsing against the documented names would silently yield
zero for every metric, and a hand-written fixture would have hidden that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repolab.experiences.spark.eventlog import (
    IN_PROGRESS_SUFFIX,
    find_event_log,
    parse_event_log,
    parse_event_log_lines,
)

FIXTURE = Path(__file__).parent / "fixtures" / "spark" / "real_event_log.jsonl"


@pytest.fixture(scope="module")
def real_log():
    return parse_event_log(FIXTURE)


class TestTheFixtureIsReal:
    def test_it_came_from_an_actual_spark_run(self) -> None:
        """Guards the premise of every other test in this file."""
        events = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
        names = {event["Event"] for event in events}

        assert "SparkListenerApplicationStart" in names
        assert "SparkListenerTaskEnd" in names
        assert "SparkListenerStageCompleted" in names

    def test_it_uses_the_event_log_key_style_not_the_rest_api_style(self) -> None:
        """The distinction that makes this parser non-obvious."""
        task = next(
            json.loads(line)
            for line in FIXTURE.read_text().splitlines()
            if '"SparkListenerTaskEnd"' in line
        )
        metrics = task["Task Metrics"]

        assert "Executor Run Time" in metrics
        assert "executorRunTime" not in metrics
        assert "Shuffle Read Metrics" in metrics
        assert "shuffleReadMetrics" not in metrics


class TestApplicationLevel:
    def test_the_application_is_identified(self, real_log) -> None:
        assert real_log.app_id
        assert real_log.app_name == "repolab-spark-demo"

    def test_the_spark_version_is_recovered(self, real_log) -> None:
        assert real_log.spark_version == "3.5.1"

    def test_total_runtime_comes_from_the_application_events(self, real_log) -> None:
        assert real_log.started_at_ms and real_log.ended_at_ms
        assert real_log.total_runtime_ms == real_log.ended_at_ms - real_log.started_at_ms
        assert real_log.total_runtime_ms > 0

    def test_runtime_is_unknown_without_both_events(self) -> None:
        """Absent data is reported as unknown rather than guessed."""
        parsed = parse_event_log_lines(
            ['{"Event":"SparkListenerApplicationStart","Timestamp":1000}']
        )
        assert parsed.total_runtime_ms is None


class TestStages:
    def test_stages_are_parsed(self, real_log) -> None:
        assert len(real_log.stages) == 6

    def test_stages_are_ordered_by_id(self, real_log) -> None:
        ids = [stage.stage_id for stage in real_log.stages]
        assert ids == sorted(ids)

    def test_task_counts_are_recovered(self, real_log) -> None:
        """The shuffle stages have 200 tasks: the configured partition count."""
        counts = {stage.stage_id: stage.num_tasks for stage in real_log.stages}
        assert 200 in counts.values()

    def test_durations_are_computed_from_timestamps(self, real_log) -> None:
        for stage in real_log.stages:
            if stage.duration_ms is not None:
                assert stage.duration_ms >= 0

    def test_all_stages_succeeded(self, real_log) -> None:
        assert all(stage.succeeded for stage in real_log.stages)


class TestTasks:
    def test_tasks_are_parsed(self, real_log) -> None:
        assert real_log.tasks
        assert all(task.task_id >= 0 for task in real_log.tasks)

    def test_shuffle_read_sums_local_and_remote(self) -> None:
        """Either alone would understate the total."""
        line = json.dumps(
            {
                "Event": "SparkListenerTaskEnd",
                "Stage ID": 2,
                "Task Info": {"Task ID": 1, "Index": 0, "Launch Time": 10, "Finish Time": 50},
                "Task Metrics": {
                    "Shuffle Read Metrics": {
                        "Local Bytes Read": 300,
                        "Remote Bytes Read": 700,
                        "Total Records Read": 42,
                    }
                },
            }
        )
        task = parse_event_log_lines([line]).tasks[0]
        assert task.shuffle_bytes_read == 1000
        assert task.shuffle_records_read == 42

    def test_the_real_log_has_a_heavily_loaded_task(self, real_log) -> None:
        """The skewed key really did concentrate records in one partition."""
        heaviest = max(task.shuffle_records_read for task in real_log.tasks)
        assert heaviest > 100_000

    def test_the_real_log_has_tasks_that_read_nothing(self, real_log) -> None:
        """Empty partitions are the symptom this phase exists to measure."""
        assert any(task.shuffle_records_read == 0 for task in real_log.tasks)

    def test_duration_is_derived_from_task_timestamps(self, real_log) -> None:
        for task in real_log.tasks:
            if task.launch_time and task.finish_time:
                assert task.duration_ms == task.finish_time - task.launch_time

    def test_a_task_without_timestamps_has_no_duration(self) -> None:
        line = json.dumps(
            {
                "Event": "SparkListenerTaskEnd",
                "Stage ID": 0,
                "Task Info": {"Task ID": 1, "Index": 0},
            }
        )
        assert parse_event_log_lines([line]).tasks[0].duration_ms == 0

    def test_did_work_distinguishes_empty_tasks(self) -> None:
        def task_line(records: int) -> str:
            return json.dumps(
                {
                    "Event": "SparkListenerTaskEnd",
                    "Stage ID": 2,
                    "Task Info": {"Task ID": 1, "Index": 0},
                    "Task Metrics": {"Shuffle Read Metrics": {"Total Records Read": records}},
                }
            )

        assert parse_event_log_lines([task_line(5)]).tasks[0].did_work is True
        assert parse_event_log_lines([task_line(0)]).tasks[0].did_work is False

    def test_a_failed_task_is_flagged_and_excluded(self) -> None:
        line = json.dumps(
            {
                "Event": "SparkListenerTaskEnd",
                "Stage ID": 0,
                "Task Info": {"Task ID": 1, "Index": 0, "Failed": True},
            }
        )
        parsed = parse_event_log_lines([line])
        assert parsed.tasks[0].failed is True
        assert parsed.successful_tasks == []

    def test_spill_is_read_from_the_task_metrics(self) -> None:
        line = json.dumps(
            {
                "Event": "SparkListenerTaskEnd",
                "Stage ID": 2,
                "Task Info": {"Task ID": 1, "Index": 0},
                "Task Metrics": {
                    "Memory Bytes Spilled": 4096,
                    "Disk Bytes Spilled": 2048,
                },
            }
        )
        task = parse_event_log_lines([line]).tasks[0]
        assert task.memory_bytes_spilled == 4096
        assert task.disk_bytes_spilled == 2048


class TestRobustness:
    def test_a_malformed_line_is_skipped_not_fatal(self, real_log) -> None:
        lines = FIXTURE.read_text().splitlines()
        lines.insert(3, "{not valid json")
        parsed = parse_event_log_lines(lines)

        assert parsed.lines_skipped == 1
        assert len(parsed.tasks) == len(real_log.tasks)

    def test_a_truncated_final_line_is_skipped(self) -> None:
        """A log from a killed application is still worth reading."""
        lines = FIXTURE.read_text().splitlines()
        lines.append('{"Event":"SparkListenerTaskEnd","Stage ID":9,"Task In')
        parsed = parse_event_log_lines(lines)
        assert parsed.lines_skipped == 1
        assert parsed.stages

    def test_an_unknown_event_is_ignored(self) -> None:
        parsed = parse_event_log_lines(['{"Event":"SparkListenerSomethingNew","X":1}'])
        assert parsed.lines_parsed == 1
        assert parsed.is_empty

    def test_blank_lines_are_not_counted(self) -> None:
        parsed = parse_event_log_lines(["", "   ", '{"Event":"SparkListenerLogStart"}'])
        assert parsed.lines_total == 1

    def test_an_empty_log_parses_to_nothing(self) -> None:
        parsed = parse_event_log_lines([])
        assert parsed.is_empty
        assert parsed.total_runtime_ms is None

    def test_a_json_array_line_is_skipped(self) -> None:
        assert parse_event_log_lines(["[1,2,3]"]).lines_skipped == 1

    def test_the_real_log_parses_without_skipping_anything(self, real_log) -> None:
        assert real_log.lines_skipped == 0
        assert real_log.lines_parsed == real_log.lines_total


class TestFindingTheLog:
    def test_it_finds_a_completed_log(self, tmp_path: Path) -> None:
        (tmp_path / "local-123").write_text("{}")
        assert find_event_log(tmp_path).name == "local-123"

    def test_a_completed_log_is_preferred_over_in_progress(self, tmp_path: Path) -> None:
        """Spark appends .inprogress until the application ends."""
        (tmp_path / f"local-1{IN_PROGRESS_SUFFIX}").write_text("{}")
        (tmp_path / "local-2").write_text("{}")
        assert find_event_log(tmp_path).name == "local-2"

    def test_an_in_progress_log_is_used_when_it_is_all_there_is(self, tmp_path: Path) -> None:
        (tmp_path / f"local-1{IN_PROGRESS_SUFFIX}").write_text("{}")
        found = find_event_log(tmp_path)
        assert found.name.endswith(IN_PROGRESS_SUFFIX)

    def test_parsing_flags_an_in_progress_log(self, tmp_path: Path) -> None:
        path = tmp_path / f"local-1{IN_PROGRESS_SUFFIX}"
        path.write_text('{"Event":"SparkListenerLogStart","Spark Version":"3.5.1"}')
        assert parse_event_log(path).in_progress is True

    def test_an_empty_directory_yields_nothing(self, tmp_path: Path) -> None:
        assert find_event_log(tmp_path) is None

    def test_a_missing_directory_yields_nothing(self, tmp_path: Path) -> None:
        assert find_event_log(tmp_path / "nope") is None

    def test_the_source_path_is_recorded(self, real_log) -> None:
        assert real_log.source_path == str(FIXTURE)
