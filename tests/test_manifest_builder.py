"""Manifest assembly and the architecture graph.

The end of the deterministic path: a repository on disk becomes a valid
``RepoManifest`` plus a graph in which every node cites files.
"""

from __future__ import annotations

import pytest

from repolab.ingest import ingest_clone
from repolab.manifest.architecture import GraphSource, NodeKind
from repolab.manifest.models import (
    BuildKind,
    ClassificationSource,
    Fidelity,
    RepoManifest,
    SurfaceType,
)
from tests.test_detectors import CLI_REPO, SPARK_REPO, WEB_REPO


@pytest.fixture
async def spark_result(make_repo, local_clone, fake_repo_url):
    root, sha = make_repo(SPARK_REPO)
    return await ingest_clone(fake_repo_url, local_clone(root, sha)), root


class TestManifestIsValid:
    async def test_it_is_a_repo_manifest(self, spark_result) -> None:
        result, _ = spark_result
        assert isinstance(result.manifest, RepoManifest)

    async def test_it_round_trips_through_json(self, spark_result) -> None:
        """The manifest crosses worker -> Postgres -> API -> browser."""
        result, _ = spark_result
        restored = RepoManifest.model_validate_json(result.manifest.model_dump_json())
        assert restored == result.manifest

    async def test_the_commit_sha_is_recorded(self, spark_result, make_repo) -> None:
        result, _ = spark_result
        assert len(result.manifest.commit_sha) == 40

    async def test_the_repo_url_is_the_normalised_one(self, spark_result) -> None:
        result, _ = spark_result
        assert result.manifest.repo_url == "https://github.com/example/demo"


class TestSparkManifest:
    async def test_the_primary_surface_is_the_data_playground(self, spark_result) -> None:
        result, _ = spark_result
        primary = result.manifest.primary_surface
        assert primary is not None
        assert primary.type is SurfaceType.DATA_PLAYGROUND

    async def test_spark_is_a_framework(self, spark_result) -> None:
        result, _ = spark_result
        assert "Spark" in result.manifest.frameworks

    async def test_python_is_the_leading_language(self, spark_result) -> None:
        result, _ = spark_result
        assert result.manifest.languages[0] == "Python"

    async def test_smoke_tests_are_proposed_per_surface(self, spark_result) -> None:
        result, _ = spark_result
        names = {t.name for t in result.manifest.smoke_tests}
        assert "spark job completes" in names

    async def test_concepts_are_grounded_in_files(self, spark_result) -> None:
        result, root = spark_result
        assert result.manifest.concepts
        for concept in result.manifest.concepts:
            assert concept.code_references
            for ref in concept.code_references:
                assert (root / ref.path).exists()

    async def test_confidence_is_reported_and_bounded(self, spark_result) -> None:
        result, _ = spark_result
        assert 0.0 <= result.manifest.confidence <= 1.0
        assert result.manifest.confidence > 0.5


class TestBuildStrategyInference:
    async def test_docker_compose_implies_a_source_build(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(WEB_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))
        assert result.manifest.build_strategy.kind is BuildKind.SOURCE_BUILD
        assert result.manifest.build_strategy.steps

    async def test_no_build_tooling_means_a_partial_build(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        """Without a declared build, claiming an exact source build would be a guess."""
        root, sha = make_repo({"notes.txt": "nothing here\n"})
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))

        strategy = result.manifest.build_strategy
        assert strategy.kind is BuildKind.PARTIAL_BUILD
        assert strategy.fidelity is Fidelity.PARTIAL
        assert strategy.limitations

    async def test_a_jvm_build_records_the_toolchain_risk(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(
            {
                "pom.xml": "<project><dependencies></dependencies></project>\n",
                "src/main/java/App.java": (
                    "public class App { public static void main(String[] a){} }\n"
                ),
            }
        )
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))
        assert any("toolchain" in lim for lim in result.manifest.build_strategy.limitations)


class TestArchitectureGraph:
    async def test_every_node_cites_at_least_one_file(self, spark_result) -> None:
        """An architecture claim without a file reference is not evidence."""
        result, _ = spark_result
        assert result.architecture.nodes
        assert result.architecture.unsupported_nodes == []

    async def test_node_references_point_at_real_files(self, spark_result) -> None:
        result, root = spark_result
        for node in result.architecture.nodes:
            for ref in node.file_references:
                assert (root / ref.path).exists(), f"{node.id} cites missing {ref.path}"

    async def test_entrypoints_become_nodes(self, spark_result) -> None:
        result, _ = spark_result
        kinds = {node.kind for node in result.architecture.nodes}
        assert NodeKind.ENTRYPOINT in kinds

    async def test_config_and_framework_nodes_exist(self, spark_result) -> None:
        result, _ = spark_result
        kinds = {node.kind for node in result.architecture.nodes}
        assert {NodeKind.CONFIG, NodeKind.EXTERNAL} <= kinds

    async def test_edges_connect_known_nodes_only(self, spark_result) -> None:
        result, _ = spark_result
        known = {node.id for node in result.architecture.nodes}
        for edge in result.architecture.edges:
            assert edge.source in known
            assert edge.target in known

    async def test_the_graph_is_marked_deterministic_without_a_model(self, spark_result) -> None:
        result, _ = spark_result
        assert result.architecture.generated_by is GraphSource.DETERMINISTIC

    async def test_services_become_datastore_nodes(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(WEB_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))
        datastores = [n for n in result.architecture.nodes if n.kind is NodeKind.DATASTORE]
        assert {n.name for n in datastores} >= {"PostgreSQL", "Redis"}

    async def test_the_graph_round_trips(self, spark_result) -> None:
        from repolab.manifest.architecture import ArchitectureGraph

        result, _ = spark_result
        restored = ArchitectureGraph.model_validate_json(result.architecture.model_dump_json())
        assert len(restored.nodes) == len(result.architecture.nodes)


class TestEvidence:
    async def test_evidence_carries_code_references(self, spark_result) -> None:
        result, _ = spark_result
        assert result.evidence.code_references
        assert result.evidence.is_empty is False

    async def test_ingestion_fabricates_no_measurements(self, spark_result) -> None:
        """Ingestion measures nothing, so it must claim no measurements."""
        result, _ = spark_result
        assert result.evidence.measurements == []
        assert result.evidence.simulated is False


class TestPipelineReporting:
    async def test_enrichment_is_reported_as_skipped_without_a_router(self, spark_result) -> None:
        result, _ = spark_result
        assert result.enriched is False
        assert any("skipped" in note for note in result.notes)

    async def test_read_accounting_is_reported(self, spark_result) -> None:
        result, _ = spark_result
        assert result.files_read > 0
        assert result.bytes_read > 0
        assert result.file_count == len(SPARK_REPO)

    async def test_a_summary_can_be_rendered(self, spark_result) -> None:
        from repolab.ingest import summarize

        result, _ = spark_result
        text = summarize(result)
        assert "data_playground" in text
        assert "confidence" in text

    async def test_cli_repository_also_produces_a_valid_manifest(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(CLI_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))
        assert result.manifest.primary_surface is not None
        assert result.manifest.primary_surface.type is SurfaceType.CLI


class TestConceptsAreNotOverclaimed:
    """A concept must be justified by the evidence, not by a keyword collision."""

    async def test_partition_settings_alone_are_not_spark_configuration(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        """Cassandra's `partitioner` setting must not be labelled Spark config."""
        root, sha = make_repo(
            {
                "conf/cassandra.yaml": (
                    "partitioner: org.apache.cassandra.dht.Murmur3Partitioner\n"
                    "native_transport_port: 9042\n"
                ),
                "src/java/App.java": (
                    "public class App { public static void main(String[] a){} }\n"
                ),
            }
        )
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))

        names = {c.name for c in result.manifest.concepts}
        assert "Spark configuration" not in names
        assert "Spark" not in result.manifest.frameworks

    async def test_generic_settings_still_become_a_concept(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo({"config/app.yaml": "port: 8080\nmemory: 2g\n", "app.py": "x = 1\n"})
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))
        assert "Configuration settings" in {c.name for c in result.manifest.concepts}

    async def test_spark_configuration_is_claimed_when_spark_is_present(self, spark_result) -> None:
        result, _ = spark_result
        assert "Spark configuration" in {c.name for c in result.manifest.concepts}


class TestClassificationIsPersistedInTheManifest:
    """The classification is part of the manifest, not a parallel artefact."""

    async def test_the_manifest_carries_the_classification(self, spark_result) -> None:
        result, _ = spark_result
        assert result.manifest.classification is not None
        assert result.manifest.classification.primary_type is SurfaceType.DATA_PLAYGROUND

    async def test_the_surfaces_match_the_classification(self, spark_result) -> None:
        result, _ = spark_result
        assert result.manifest.interaction_surfaces == result.manifest.classification.surfaces

    async def test_every_manifest_surface_carries_evidence_and_a_reason(self, spark_result) -> None:
        result, root = spark_result
        for surface in result.manifest.interaction_surfaces:
            assert surface.evidence, f"{surface.type} has no evidence"
            assert surface.reason.strip()
            for ref in surface.evidence:
                assert (root / ref.path).exists()

    async def test_a_primary_experience_is_recommended_with_a_rationale(self, spark_result) -> None:
        result, _ = spark_result
        classification = result.manifest.classification
        assert classification.primary_surface is not None
        assert classification.rationale.strip()

    async def test_the_resolution_source_is_recorded(self, spark_result) -> None:
        """Strong Spark evidence must not have been sent to a model."""
        result, _ = spark_result
        assert result.manifest.classification.resolved_by is ClassificationSource.DETERMINISTIC

    async def test_the_classification_survives_a_json_round_trip(self, spark_result) -> None:
        result, _ = spark_result
        restored = RepoManifest.model_validate_json(result.manifest.model_dump_json())

        assert restored.classification is not None
        assert restored.classification.primary_type is SurfaceType.DATA_PLAYGROUND
        assert restored.interaction_surfaces[0].evidence
        assert restored.interaction_surfaces[0].reason
