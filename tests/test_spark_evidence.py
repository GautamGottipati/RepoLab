"""The evidence bundle for a Spark run.

An explanation is only as good as what backs it, so the bundle must carry the
measurements, the repository code they describe, and the log they came from -- and
must not claim to be simulated when it really ran.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.evidence.models import EvidenceBundle, MeasurementSource
from repolab.experiences.spark.eventlog import parse_event_log
from repolab.experiences.spark.measurements import measure
from repolab.experiences.spark.runner import build_evidence
from tests.test_detectors import SPARK_REPO

FIXTURE = Path(__file__).parent / "fixtures" / "spark" / "real_event_log.jsonl"
COMMIT = "e" * 40


@pytest.fixture
def bundle(make_repo) -> EvidenceBundle:
    root, _ = make_repo(
        {
            **SPARK_REPO,
            "jobs/etl_job.py": (
                "from pyspark.sql import functions as F\n"
                "\n"
                "\n"
                "def transform_data(df, steps_per_floor_):\n"
                "    return df.select('id')\n"
            ),
        }
    )
    measurements = measure(parse_event_log(FIXTURE))
    return build_evidence(
        measurements,
        repo_root=root,
        commit_sha=COMMIT,
        event_log_path=FIXTURE,
        event_log_excerpt=FIXTURE.read_text().splitlines()[0],
        driver_tail="REPOLAB_SPARK_RUN_OK\n",
    )


class TestItIsNotSimulated:
    def test_simulated_is_false(self, bundle: EvidenceBundle) -> None:
        """This really executed, so claiming otherwise would be wrong."""
        assert bundle.simulated is False

    def test_a_simulated_bundle_could_not_hold_these_measurements(
        self, bundle: EvidenceBundle
    ) -> None:
        """The model forbids it, which is why the flag cannot drift from reality."""
        with pytest.raises(Exception, match="never produce measured results"):
            EvidenceBundle(simulated=True, measurements=bundle.measurements)


class TestMeasurements:
    def test_measurements_are_present(self, bundle: EvidenceBundle) -> None:
        assert bundle.measurements
        assert bundle.is_empty is False

    def test_every_measurement_cites_the_spark_event_log(self, bundle: EvidenceBundle) -> None:
        for measurement in bundle.measurements:
            assert measurement.source is MeasurementSource.SPARK_EVENT_LOG

    def test_measurements_carry_names_units_and_values(self, bundle: EvidenceBundle) -> None:
        for measurement in bundle.measurements:
            assert measurement.name.strip()
            assert measurement.unit.strip()
            assert isinstance(measurement.value, float)

    def test_the_key_figures_are_included(self, bundle: EvidenceBundle) -> None:
        names = {measurement.name for measurement in bundle.measurements}
        assert {"task count", "shuffle read", "empty partitions"} <= names


class TestCodeReferences:
    def test_the_repository_transform_is_cited(self, bundle: EvidenceBundle) -> None:
        """The measurements describe this function, so it must be pointed at."""
        paths = {reference.path for reference in bundle.code_references}
        assert "jobs/etl_job.py" in paths

    def test_the_line_number_is_located_not_guessed(self, bundle: EvidenceBundle) -> None:
        job = next(r for r in bundle.code_references if r.path == "jobs/etl_job.py")
        assert job.start_line == 4  # where def transform_data actually is

    def test_the_configuration_file_is_cited(self, bundle: EvidenceBundle) -> None:
        paths = {reference.path for reference in bundle.code_references}
        assert "configs/etl_config.json" in paths

    def test_references_carry_the_commit(self, bundle: EvidenceBundle) -> None:
        assert all(r.commit_sha == COMMIT for r in bundle.code_references)

    def test_a_missing_file_is_simply_not_cited(self, make_repo) -> None:
        """No invented reference when the file is not there."""
        root, _ = make_repo({"README.md": "# nothing else\n"})
        bundle = build_evidence(
            measure(parse_event_log(FIXTURE)),
            repo_root=root,
            commit_sha=COMMIT,
            event_log_path=FIXTURE,
            event_log_excerpt="",
            driver_tail="",
        )
        assert bundle.code_references == []


class TestLogReferences:
    def test_the_event_log_is_referenced(self, bundle: EvidenceBundle) -> None:
        sources = " ".join(reference.source for reference in bundle.logs)
        assert "event log" in sources

    def test_the_driver_output_is_referenced(self, bundle: EvidenceBundle) -> None:
        sources = " ".join(reference.source for reference in bundle.logs)
        assert "spark-submit" in sources

    def test_log_excerpts_are_bounded(self, bundle: EvidenceBundle) -> None:
        for reference in bundle.logs:
            assert len(reference.excerpt) <= 2000

    def test_the_excerpt_is_real_event_log_content(self, bundle: EvidenceBundle) -> None:
        event_log = next(r for r in bundle.logs if "event log" in r.source)
        assert "SparkListener" in event_log.excerpt


class TestRoundTrip:
    def test_the_bundle_survives_json(self, bundle: EvidenceBundle) -> None:
        restored = EvidenceBundle.model_validate_json(bundle.model_dump_json())
        assert len(restored.measurements) == len(bundle.measurements)
        assert restored.simulated is False
