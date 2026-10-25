"""Bounded LLM enrichment.

Two properties matter: the repository is never shipped wholesale to a model, and
anything the model proposes that cannot be tied to a file is dropped.
"""

from __future__ import annotations

import json

from repolab.evidence.models import CodeReference
from repolab.ingest import IngestLimits, ingest_clone
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import DetectionContext, detect_all
from repolab.ingest.enrich import (
    ArchitectureEnrichment,
    NodeLabel,
    ProposedEdge,
    apply_enrichment,
    select_files_for_summary,
)
from repolab.ingest.tree import build_file_tree
from repolab.llm import ModelRouter, ModelTier
from repolab.manifest.architecture import (
    ArchitectureGraph,
    ArchitectureNode,
    GraphSource,
    NodeKind,
)
from repolab.testing.fakes import FakeLLMProvider, FakeMessage
from tests.test_detectors import SPARK_REPO

SUMMARY = json.dumps({"purpose": "Runs the ETL job.", "key_elements": ["main"]})
ENRICHMENT = json.dumps(
    {
        "summary": "A Spark ETL job reading parquet and writing aggregates.",
        "labels": [{"node_id": "entrypoint:etl_job", "group": "ingestion"}],
        "edges": [
            {
                "source": "entrypoint:etl_job",
                "target": "external:Spark",
                "kind": "depends_on",
                "description": "runs on Spark",
            }
        ],
    }
)


def router_returning(*payloads: str) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(messages=[FakeMessage(content=p) for p in payloads])
    router = ModelRouter(
        provider,
        {ModelTier.NANO: "fake/nano", ModelTier.SUPER: "fake/super"},
    )
    return router, provider


class TestEnrichmentIsOptional:
    async def test_without_a_router_the_manifest_is_still_produced(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(SPARK_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha), router=None)

        assert result.manifest.interaction_surfaces
        assert result.architecture.nodes
        assert result.enriched is False

    async def test_the_skip_reason_is_recorded(self, make_repo, local_clone, fake_repo_url) -> None:
        root, sha = make_repo(SPARK_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha), router=None)
        assert any("no model router configured" in note for note in result.notes)

    async def test_the_graph_stays_marked_deterministic(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(SPARK_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha), router=None)
        assert result.architecture.generated_by is GraphSource.DETERMINISTIC


class TestTheRepositoryIsNotShippedToTheModel:
    async def test_only_a_bounded_number_of_files_is_sent(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        files = {**SPARK_REPO, **{f"extra/mod{i}.py": "x = 1\n" for i in range(50)}}
        root, sha = make_repo(files)
        router, provider = router_returning(SUMMARY, ENRICHMENT)
        limits = IngestLimits(max_llm_files=3)

        await ingest_clone(fake_repo_url, local_clone(root, sha), router=router, limits=limits)
        # At most 3 summary calls, plus the two architecture passes (components,
        # then orientation), each retried at most once on a schema failure.
        assert provider.call_count <= 3 + 2 * 2

    async def test_selection_respects_the_file_cap(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        tree = build_file_tree(root)
        context = DetectionContext(root=root, tree=tree, commit_sha="abc")
        budget = ReadBudget(IngestLimits(max_llm_files=2))
        outcome = detect_all(context, budget)

        selected = select_files_for_summary(outcome, context, budget)
        assert len(selected) <= 2

    async def test_selection_is_evidence_driven(self, make_repo) -> None:
        """Files detection already found interesting are what get summarised."""
        root, _ = make_repo(SPARK_REPO)
        tree = build_file_tree(root)
        context = DetectionContext(root=root, tree=tree, commit_sha="abc")
        budget = ReadBudget()
        outcome = detect_all(context, budget)

        paths = {entry.path for entry in select_files_for_summary(outcome, context, budget)}
        assert "jobs/etl_job.py" in paths

    async def test_large_files_are_truncated_before_sending(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo({**SPARK_REPO, "jobs/etl_job.py": "# pad\n" * 5000})
        router, provider = router_returning(SUMMARY, ENRICHMENT)
        limits = IngestLimits(max_llm_file_bytes=500)

        await ingest_clone(fake_repo_url, local_clone(root, sha), router=router, limits=limits)
        for call in provider.calls:
            for message in call.get("messages", []):
                # The system message is a fixed prompt plus the response schema;
                # repository text travels in the others.
                if message.get("role") == "system":
                    assert "# pad" not in message.get("content", "")
                    continue
                assert len(message.get("content", "")) < 5000

    async def test_the_total_byte_budget_bounds_what_is_sent(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        """The cap on total bytes sent to models is the real guarantee."""
        # Many separately-interesting files, so selection has plenty to offer.
        files = {
            **SPARK_REPO,
            **{
                f"jobs/job{i}.py": (
                    "from pyspark.sql import SparkSession\n"
                    "SparkSession.builder.getOrCreate()\n" + "# pad\n" * 200
                )
                for i in range(10)
            },
        }
        root, sha = make_repo(files)
        router, provider = router_returning(*([SUMMARY] * 20 + [ENRICHMENT]))
        total_cap = 2_000
        limits = IngestLimits(max_llm_total_bytes=total_cap, max_llm_file_bytes=500)

        result = await ingest_clone(
            fake_repo_url, local_clone(root, sha), router=router, limits=limits
        )

        summary_calls = [
            call
            for call in provider.calls
            if any("File:" in m.get("content", "") for m in call.get("messages", []))
        ]
        sent = sum(
            len(m.get("content", ""))
            for call in summary_calls
            for m in call.get("messages", [])
            if "File:" in m.get("content", "")
        )
        # Each prompt adds a small header, so allow generous slack over the cap
        # while still proving the budget bounds the volume.
        assert sent < total_cap * 2
        assert "max_llm_total_bytes" in result.limits_hit


class TestUngroundedClaimsAreDropped:
    def graph_with_two_nodes(self) -> ArchitectureGraph:
        return ArchitectureGraph(
            nodes=[
                ArchitectureNode(
                    id="entrypoint:etl_job",
                    kind=NodeKind.ENTRYPOINT,
                    name="etl_job",
                    file_references=[CodeReference(path="jobs/etl_job.py")],
                ),
                ArchitectureNode(
                    id="external:Spark",
                    kind=NodeKind.EXTERNAL,
                    name="Spark",
                    file_references=[CodeReference(path="requirements.txt")],
                ),
            ]
        )

    def test_an_edge_to_an_invented_node_is_dropped(self) -> None:
        graph = self.graph_with_two_nodes()
        dropped, _ = apply_enrichment(
            graph,
            ArchitectureEnrichment(
                summary="s",
                edges=[
                    ProposedEdge(
                        source="entrypoint:etl_job",
                        target="service:imaginary-kafka",
                        kind="depends_on",
                        description="invented",
                    )
                ],
            ),
        )
        assert dropped == 1
        assert graph.edges == []

    def test_an_unknown_edge_kind_is_dropped(self) -> None:
        graph = self.graph_with_two_nodes()
        dropped, _ = apply_enrichment(
            graph,
            ArchitectureEnrichment(
                summary="s",
                edges=[
                    ProposedEdge(
                        source="entrypoint:etl_job",
                        target="external:Spark",
                        kind="telepathically_influences",
                        description="nonsense",
                    )
                ],
            ),
        )
        assert dropped == 1
        assert graph.edges == []

    def test_a_label_for_an_unknown_node_is_dropped(self) -> None:
        graph = self.graph_with_two_nodes()
        _, dropped = apply_enrichment(
            graph,
            ArchitectureEnrichment(
                summary="s", labels=[NodeLabel(node_id="nope:missing", group="x")]
            ),
        )
        assert dropped == 1

    def test_a_valid_edge_is_accepted_and_inherits_references(self) -> None:
        graph = self.graph_with_two_nodes()
        dropped, _ = apply_enrichment(
            graph,
            ArchitectureEnrichment(
                summary="s",
                edges=[
                    ProposedEdge(
                        source="entrypoint:etl_job",
                        target="external:Spark",
                        kind="depends_on",
                        description="runs on Spark",
                    )
                ],
            ),
        )
        assert dropped == 0
        assert len(graph.edges) == 1
        # The edge is only as grounded as the nodes it connects.
        assert graph.edges[0].file_references

    def test_applying_enrichment_marks_the_graph(self) -> None:
        graph = self.graph_with_two_nodes()
        apply_enrichment(
            graph,
            ArchitectureEnrichment(
                summary="s", labels=[NodeLabel(node_id="external:Spark", group="engine")]
            ),
        )
        assert graph.generated_by is GraphSource.SUPER_ENRICHED
        assert graph.node("external:Spark").group == "engine"


class TestEnrichmentFailuresDegrade:
    async def test_a_model_failure_does_not_fail_the_ingestion(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(SPARK_REPO)
        provider = FakeLLMProvider(fail_with=RuntimeError("503 unavailable"))
        router = ModelRouter(provider, {ModelTier.NANO: "fake/nano", ModelTier.SUPER: "fake/super"})

        result = await ingest_clone(fake_repo_url, local_clone(root, sha), router=router)
        assert result.manifest.interaction_surfaces
        assert any("failed" in note for note in result.notes)

    async def test_unparseable_model_output_does_not_fail_the_ingestion(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(SPARK_REPO)
        router, _ = router_returning("this is not json at all")
        result = await ingest_clone(fake_repo_url, local_clone(root, sha), router=router)
        assert result.architecture.nodes
