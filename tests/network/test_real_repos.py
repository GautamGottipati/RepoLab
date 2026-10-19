"""Ingestion of the three real test repositories.

Marked ``network``: these clone real repositories, so they are deselected by
default and run via ``make check-repos``. No credentials are needed -- LLM
enrichment is not exercised here.

Airflow and Cassandra are large; these tests assert that ingestion stays bounded
and still produces an evidence-backed manifest.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repolab.ingest import IngestionResult, IngestLimits, ingest_repository, summarize
from repolab.manifest.models import ClassificationSource, SurfaceType

pytestmark = pytest.mark.network

SPARK_ETL = "https://github.com/AlexIoannides/pyspark-example-project"
CASSANDRA = "https://github.com/apache/cassandra"
AIRFLOW = "https://github.com/apache/airflow"

#: Generous but finite, so a huge repository cannot run away with the machine.
LARGE_REPO_LIMITS = IngestLimits(max_scanned_files=1_500, max_total_read_bytes=24_000_000)


@pytest.fixture(scope="session")
def spark_etl() -> IngestionResult:
    """Ingested once per session: re-cloning per test would take minutes."""
    return asyncio.run(ingest_repository(SPARK_ETL))


@pytest.fixture(scope="session")
def cassandra() -> IngestionResult:
    return asyncio.run(ingest_repository(CASSANDRA, limits=LARGE_REPO_LIMITS))


@pytest.fixture(scope="session")
def airflow() -> IngestionResult:
    return asyncio.run(ingest_repository(AIRFLOW, limits=LARGE_REPO_LIMITS))


def assert_valid_and_evidence_backed(result) -> None:
    """Shared assertions: a usable manifest, grounded in real files."""
    manifest = result.manifest

    assert len(manifest.commit_sha) == 40
    assert manifest.languages, "no languages detected"
    assert manifest.interaction_surfaces, "no interaction surface detected"
    assert manifest.primary_surface is not None
    assert 0.0 < manifest.confidence <= 1.0

    # Evidence, not assertions.
    assert result.evidence.code_references, "manifest carries no evidence"
    assert result.evidence.measurements == [], "ingestion must not report measurements"
    assert result.architecture.nodes, "empty architecture graph"
    assert result.architecture.unsupported_nodes == [], (
        "every architecture node must cite at least one file"
    )

    # Nothing was executed: no sentinel, and enrichment was not requested.
    assert result.enriched is False

    # Every surface must justify itself, and the classification must be recorded.
    classification = manifest.classification
    assert classification is not None
    assert classification.primary_type is not None
    assert classification.rationale.strip()
    for surface in manifest.interaction_surfaces:
        assert surface.evidence, f"{surface.type} cites no evidence"
        assert surface.reason.strip(), f"{surface.type} gives no reason"

    # Strong repository evidence must never have been sent to a model.
    assert classification.resolved_by is ClassificationSource.DETERMINISTIC


class TestSparkEtlRepository:
    """The P0 demo repository."""

    def test_it_produces_a_spark_data_playground(self, spark_etl: IngestionResult) -> None:
        print("\n" + summarize(spark_etl))

        assert_valid_and_evidence_backed(spark_etl)
        assert spark_etl.manifest.primary_surface.type is SurfaceType.DATA_PLAYGROUND
        assert "Spark" in spark_etl.manifest.frameworks
        assert spark_etl.manifest.languages[0] == "Python"

    def test_the_spark_entrypoint_is_found(self, spark_etl: IngestionResult) -> None:
        commands = " ".join(e.command for e in spark_etl.manifest.entrypoints)
        assert "etl_job" in commands

    def test_concepts_cite_real_repository_files(self, spark_etl: IngestionResult) -> None:
        assert spark_etl.manifest.concepts
        for concept in spark_etl.manifest.concepts:
            assert concept.code_references

    def test_the_clone_does_not_outlive_the_ingestion(self, spark_etl: IngestionResult) -> None:
        """The result is self-contained; nothing points into a live clone."""
        assert spark_etl.evidence.code_references[0].path
        assert not list(Path("/tmp").glob("repolab-ingest-*/repo"))


class TestCassandraRepository:
    def test_it_produces_a_valid_manifest(self, cassandra: IngestionResult) -> None:
        print("\n" + summarize(cassandra))

        assert_valid_and_evidence_backed(cassandra)
        assert "Java" in cassandra.manifest.languages

    def test_the_default_branch_is_trunk_not_main(self, cassandra: IngestionResult) -> None:
        """Cassandra's default branch is `trunk`, so no branch may be assumed."""
        assert cassandra.branch == "trunk"

    def test_a_cli_surface_is_detected(self, cassandra: IngestionResult) -> None:
        """cqlsh and the shell tooling are the P1 interaction surface."""
        types = {s.type for s in cassandra.manifest.interaction_surfaces}
        assert SurfaceType.CLI in types


class TestAirflowRepository:
    def test_it_produces_a_valid_manifest(self, airflow: IngestionResult) -> None:
        print("\n" + summarize(airflow))

        assert_valid_and_evidence_backed(airflow)
        assert "Python" in airflow.manifest.languages

    def test_multiple_surfaces_are_detected(self, airflow: IngestionResult) -> None:
        """Airflow is the hybrid case: several surfaces, not one."""
        assert len(airflow.manifest.interaction_surfaces) >= 2

    def test_ingestion_stays_within_its_limits(self, airflow: IngestionResult) -> None:
        """A very large repository must be bounded, and say so."""
        assert airflow.bytes_read <= LARGE_REPO_LIMITS.max_total_read_bytes
        assert airflow.limits_hit, "expected at least one limit to be reported"


class TestClassificationOfTheRealRepositories:
    """The three repositories the specification names, and what each should yield."""

    def test_spark_repo_is_primarily_a_data_playground(self, spark_etl: IngestionResult) -> None:
        classification = spark_etl.manifest.classification
        assert classification.primary_type is SurfaceType.DATA_PLAYGROUND
        primary = classification.primary_surface
        assert primary.confidence >= 0.8
        assert any(
            "spark" in ref.path.lower() or ref.path.endswith(".py") for ref in primary.evidence
        )

    def test_cassandra_is_primarily_a_cli(self, cassandra: IngestionResult) -> None:
        classification = cassandra.manifest.classification
        assert classification.primary_type is SurfaceType.CLI

    def test_cassandra_cites_both_a_server_and_an_interactive_shell(
        self, cassandra: IngestionResult
    ) -> None:
        """'server + interactive shell -> CLI playground' is the spec's Cassandra rule."""
        cli = cassandra.manifest.classification.primary_surface
        assert "server+shell" in cli.reason or "interactive_shell" in cli.reason
        paths = " ".join(ref.path.lower() for ref in cli.evidence)
        assert "cqlsh" in paths or "shell" in paths

    def test_airflow_exposes_multiple_surfaces(self, airflow: IngestionResult) -> None:
        """Airflow is the hybrid case: several surfaces, not one."""
        classification = airflow.manifest.classification
        assert len(classification.surfaces) >= 3
        assert len(set(classification.surface_types)) >= 3

    def test_airflow_is_marked_hybrid(self, airflow: IngestionResult) -> None:
        assert airflow.manifest.classification.hybrid is True

    def test_airflow_primary_is_chosen_with_a_recorded_reason(
        self, airflow: IngestionResult
    ) -> None:
        """Whichever surface wins, the choice must be justified rather than arbitrary."""
        classification = airflow.manifest.classification
        assert classification.primary_type is not None
        assert "scored highest" in classification.rationale or "tied" in classification.rationale

    def test_no_repository_needed_a_model_to_classify(
        self, spark_etl: IngestionResult, cassandra: IngestionResult, airflow: IngestionResult
    ) -> None:
        """All three are obvious from strong evidence, so none should reach Super."""
        for result in (spark_etl, cassandra, airflow):
            assert result.manifest.classification.resolved_by is ClassificationSource.DETERMINISTIC
