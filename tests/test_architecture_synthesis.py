"""The Nemotron pass, against a scripted model.

Super may rename, describe, group, relate and summarise -- and everything it
proposes is checked against the evidence. These tests give it the proposals a
model really makes when it overreaches, and assert each is refused and counted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repolab.architecture.models import EdgeType, NodeCategory, Provenance, load_graph
from repolab.architecture.synthesis import (
    ArchitectureSynthesis,
    ComponentGroup,
    ComponentRefinement,
    ProposedFlow,
    ProposedRelationship,
    apply_synthesis,
    synthesise,
)
from repolab.llm import LLMError
from repolab.manifest.architecture import GraphSource
from tests.support import architecture_fixtures as fx


class Super:
    def __init__(self, value: ArchitectureSynthesis | None = None, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.prompts: list[str] = []

    async def super(self, *, prompt: str, **_: Any):
        self.prompts.append(prompt)
        if self.fail:
            raise LLMError("the model is unavailable")
        value = self.value

        class Completion:
            pass

        Completion.value = value  # type: ignore[attr-defined]
        return Completion()


async def spark(tmp_path: Path):
    graph, _, index = await fx.analyse(fx.write_repo(tmp_path, fx.SPARK_PIPELINE))
    ids = {n.label: n.id for n in graph.nodes}
    return graph, index, ids


class TestWhatIsAccepted:
    async def test_names_and_descriptions_are_marked_inferred(self, tmp_path: Path) -> None:
        graph, index, ids = await spark(tmp_path)
        graph = apply_synthesis(
            graph,
            ArchitectureSynthesis(
                summary="A nightly Spark job that totals revenue per region.",
                components=[
                    ComponentRefinement(
                        node_id=ids["Join Customers"],
                        label="Enrich orders with customers",
                        description="Adds customer attributes to each order.",
                    )
                ],
            ),
            index,
        )
        node = graph.node(ids["Join Customers"])
        assert node is not None
        assert node.label == "Enrich orders with customers"
        assert set(node.inferred_fields) == {"label", "description"}
        assert node.provenance is Provenance.STATIC, "the component itself is still detected"
        assert graph.summary_provenance is Provenance.INFERRED
        assert graph.generated_by is GraphSource.SUPER_ENRICHED

    async def test_a_cited_relationship_is_added_as_inferred(self, tmp_path: Path) -> None:
        graph, index, ids = await spark(tmp_path)
        graph = apply_synthesis(
            graph,
            ArchitectureSynthesis(
                summary="x",
                relationships=[
                    ProposedRelationship(
                        source=ids["Aggregate Revenue"],
                        target=ids["Object storage (S3)"],
                        type="depends_on",
                        evidence="jobs/daily_sales.py:9",
                    )
                ],
            ),
            index,
        )
        added = [e for e in graph.edges if e.provenance is Provenance.INFERRED]
        assert len(added) == 1 and added[0].type is EdgeType.DEPENDS_ON
        assert added[0].evidence[0].path == "jobs/daily_sales.py"


class TestWhatIsRefused:
    async def test_invented_components_files_and_numbers(self, tmp_path: Path) -> None:
        graph, index, ids = await spark(tmp_path)
        before = len(graph.edges)
        graph = apply_synthesis(
            graph,
            ArchitectureSynthesis(
                summary="Processes 2 GB in 40 seconds.",
                components=[
                    ComponentRefinement(node_id="kafka:ingest", label="Kafka ingest"),
                    ComponentRefinement(
                        node_id=ids["Read Orders"], description="Reads 500 MB per run."
                    ),
                ],
                relationships=[
                    ProposedRelationship(
                        source=ids["Read Orders"],
                        target=ids["Write Report"],
                        type="data_flow",
                        evidence="jobs/invented.py:3",
                    ),
                    ProposedRelationship(
                        source=ids["Read Orders"],
                        target="warehouse:redshift",
                        type="writes_to",
                        evidence="jobs/daily_sales.py:3",
                    ),
                    ProposedRelationship(
                        source=ids["Read Orders"],
                        target=ids["Write Report"],
                        type="teleports_to",
                        evidence="jobs/daily_sales.py:3",
                    ),
                ],
                flows=[ProposedFlow(name="x", node_ids=[ids["Read Orders"], "nowhere"])],
            ),
            index,
        )
        assert len(graph.edges) == before
        assert graph.node("kafka:ingest") is None
        assert graph.summary is None
        assert graph.node(ids["Read Orders"]).description is None  # type: ignore[union-attr]
        assert graph.dropped["invented measurement"] == 2
        assert graph.dropped["unknown component"] == 1
        assert graph.dropped["relationship citing a line RepoLab did not show"] == 1
        assert graph.dropped["relationship to an unknown component"] == 1
        assert graph.dropped["unknown relationship type"] == 1
        assert graph.dropped["flow through unknown components"] == 1

    async def test_a_small_flow_is_never_folded_into_a_group(self, tmp_path: Path) -> None:
        graph, index, ids = await spark(tmp_path)
        graph = apply_synthesis(
            graph,
            ArchitectureSynthesis(
                summary="x",
                groups=[
                    ComponentGroup(
                        label="ETL steps",
                        category="pipeline",
                        member_ids=[ids["Read Orders"], ids["Join Customers"]],
                    )
                ],
            ),
            index,
        )
        assert graph.node(ids["Read Orders"]) is not None
        assert graph.dropped["grouping a graph that is already small"] == 1

    async def test_a_flow_must_follow_real_relationships(self, tmp_path: Path) -> None:
        graph, index, ids = await spark(tmp_path)
        graph = apply_synthesis(
            graph,
            ArchitectureSynthesis(
                summary="x",
                flows=[
                    ProposedFlow(name="skip", node_ids=[ids["Read Orders"], ids["Write Report"]])
                ],
            ),
            index,
        )
        assert graph.dropped["flow between unrelated components"] == 1


class TestTheModelCall:
    async def test_the_prompt_carries_evidence_not_the_repository(self, tmp_path: Path) -> None:
        graph, index, _ = await spark(tmp_path)
        router = Super(ArchitectureSynthesis(summary="A Spark job."))
        await synthesise(graph, index, router)

        prompt = router.prompts[0]
        assert "jobs/daily_sales.py:6" in prompt
        assert "CATEGORIES:" in prompt and "EDGE TYPES:" in prompt
        assert len(prompt) < 20_000

    async def test_a_failed_model_leaves_the_static_graph_and_says_so(self, tmp_path: Path) -> None:
        graph, index, _ = await spark(tmp_path)
        nodes = len(graph.nodes)
        graph = await synthesise(graph, index, Super(fail=True))
        assert len(graph.nodes) == nodes
        assert graph.generated_by is GraphSource.DETERMINISTIC
        assert any("Nemotron could not be reached" in gap for gap in graph.gaps)

    async def test_no_model_means_no_call(self, tmp_path: Path) -> None:
        graph, index, _ = await spark(tmp_path)
        assert (await synthesise(graph, index, None)).generated_by is GraphSource.DETERMINISTIC


class TestStoredGraphs:
    def test_an_earlier_graph_is_served_in_the_current_schema(self) -> None:
        graph = load_graph(
            {
                "nodes": [
                    {
                        "id": "entrypoint:job",
                        "kind": "entrypoint",
                        "name": "job",
                        "file_references": [{"path": "job.py"}],
                    },
                    {
                        "id": "external:Spark",
                        "kind": "external",
                        "name": "Spark",
                        "file_references": [{"path": "Pipfile"}],
                    },
                    {"id": "module:x", "kind": "module", "name": "x", "file_references": []},
                ],
                "edges": [
                    {"source": "entrypoint:job", "target": "external:Spark", "kind": "depends_on"}
                ],
                "generated_by": "deterministic",
            }
        )
        assert [n.category for n in graph.nodes] == [NodeCategory.ENTRY_POINT, NodeCategory.LIBRARY]
        assert graph.edges[0].provenance is Provenance.INFERRED
        assert graph.completeness.value == "partial"
        assert graph.entry_points == ["entrypoint:job"]
