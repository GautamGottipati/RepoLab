"""The real Spark demo, end to end.

Marked `docker`: this actually runs Spark in a container and measures it. Run with
`make test-docker`.

The second test is the phase's reproducibility criterion: two runs over identical
inputs must agree exactly on every structural figure, while timings are free to
differ.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.experiences.spark import SparkDemoRunner, SparkRunConfig
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.limits import DEFAULT_LIMITS
from repolab.ingest.url import validate_repo_url
from repolab.sandbox.docker import DockerEnvironment, docker_available

pytestmark = pytest.mark.docker

DEMO_REPO = "https://github.com/AlexIoannides/pyspark-example-project"
IMAGE = "apache/spark:3.5.1-python3"

#: Small enough to keep the test quick, large enough to produce real skew.
ROWS = 50_000


@pytest.fixture(scope="module")
def clone():
    if not docker_available():
        pytest.skip("docker is not available")
    repo_url = validate_repo_url(DEMO_REPO)
    with TemporaryClone(repo_url, limits=DEFAULT_LIMITS) as cloned:
        yield repo_url, cloned


async def run_once(clone, tmp_path: Path, rows: int = ROWS):
    repo_url, cloned = clone
    environment = DockerEnvironment(host_workdir=tmp_path / "work")
    runner = SparkDemoRunner(environment, runs_root=tmp_path / "runs")
    return await runner.run(
        repo_url=repo_url.normalized,
        commit_sha=cloned.commit_sha,
        repository_root=Path(cloned.path),
        image=IMAGE,
        config=SparkRunConfig(),
        rows=rows,
    )


class TestARealRun:
    async def test_it_measures_a_real_spark_application(self, clone, tmp_path: Path) -> None:
        result = await run_once(clone, tmp_path)
        measurements = result.measurements

        assert measurements.spark_version == "3.5.1"
        assert measurements.app_id
        assert measurements.task_count > 0
        assert measurements.stage_count > 0
        assert measurements.failed_task_count == 0

    async def test_the_event_log_is_real_and_preserved(self, clone, tmp_path: Path) -> None:
        result = await run_once(clone, tmp_path)
        log = Path(result.event_log_path)

        assert log.is_file()
        assert log.stat().st_size > 10_000
        assert "SparkListenerApplicationStart" in log.read_text()
        assert result.measurements.event_log_skipped_lines == 0

    async def test_a_real_shuffle_happened(self, clone, tmp_path: Path) -> None:
        result = await run_once(clone, tmp_path)
        assert result.measurements.shuffle_bytes_read > 0
        assert result.measurements.shuffle_records_read > 0

    async def test_the_configured_partitions_are_observed(self, clone, tmp_path: Path) -> None:
        result = await run_once(clone, tmp_path)
        assert result.measurements.distribution.partitions == 200

    async def test_most_partitions_are_empty(self, clone, tmp_path: Path) -> None:
        """200 partitions over 7 keys: the finding the demo question is about."""
        result = await run_once(clone, tmp_path)
        distribution = result.measurements.distribution

        assert distribution.empty_partitions > 150
        assert distribution.empty_fraction > 0.75

    async def test_the_skew_is_measurable(self, clone, tmp_path: Path) -> None:
        result = await run_once(clone, tmp_path)
        assert result.measurements.distribution.record_skew_ratio > 5

    async def test_the_evidence_is_not_simulated(self, clone, tmp_path: Path) -> None:
        result = await run_once(clone, tmp_path)
        assert result.evidence.simulated is False
        assert result.evidence.measurements
        assert result.evidence.code_references


class TestStructuralReproducibility:
    async def test_two_runs_agree_on_every_structural_figure(self, clone, tmp_path: Path) -> None:
        """The phase's reproducibility criterion, against real Spark."""
        first = await run_once(clone, tmp_path / "first")
        second = await run_once(clone, tmp_path / "second")

        assert first.dataset_hash == second.dataset_hash
        assert (
            first.measurements.structural_fingerprint()
            == second.measurements.structural_fingerprint()
        )

    async def test_timings_are_allowed_to_differ(self, clone, tmp_path: Path) -> None:
        """Stated plainly: wall clock is not reproducible, and is not claimed to be."""
        first = await run_once(clone, tmp_path / "a")
        second = await run_once(clone, tmp_path / "b")

        timing_names = {
            metric.name
            for metric in first.measurements.metrics()
            if metric.determinism.value == "timing"
        }
        assert "total runtime" in timing_names
        assert timing_names.isdisjoint(set(first.measurements.structural_fingerprint()))
        # Both ran; neither is required to match the other.
        assert first.measurements.total_runtime_ms
        assert second.measurements.total_runtime_ms
