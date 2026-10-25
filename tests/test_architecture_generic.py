"""One generic analysis, many kinds of repository.

Each fixture is checked for the same things: the components a newcomer would
expect, relationships that are evidenced rather than assumed, where execution
starts, the files worth opening, a flow through the system, and honesty when
little can be determined. No fixture is a demo repository, and nothing in the
analysis is named after one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.architecture.models import (
    ArchitectureViewKind,
    Completeness,
    EdgeType,
    NodeCategory,
)
from tests.support import architecture_fixtures as fx


async def build(tmp_path: Path, files: dict[str, str]):
    return await fx.analyse(fx.write_repo(tmp_path, files))


def categories(graph) -> set[NodeCategory]:
    return {node.category for node in graph.nodes}


def edge(graph, source_label: str, target_label: str):
    labels = {node.id: node.label for node in graph.nodes}
    return next(
        (
            e
            for e in graph.edges
            if labels[e.source] == source_label and labels[e.target] == target_label
        ),
        None,
    )


class TestEveryRepository:
    @pytest.mark.parametrize(
        "files",
        [fx.SPARK_PIPELINE, fx.WEB_API, fx.CLI_TOOL, fx.WORKFLOW, fx.LIBRARY, fx.HYBRID],
        ids=["spark", "web", "cli", "workflow", "library", "hybrid"],
    )
    async def test_every_component_cites_a_real_line(self, tmp_path: Path, files) -> None:
        graph, _, _ = await build(tmp_path, files)
        assert graph.nodes
        for node in graph.nodes:
            assert node.source_references, f"{node.label} cites nothing"
            for ref in node.source_references:
                assert (tmp_path / ref.path).exists(), f"{node.label} cites missing {ref.path}"
            assert node.detected_because

    @pytest.mark.parametrize(
        "files",
        [fx.SPARK_PIPELINE, fx.WEB_API, fx.CLI_TOOL, fx.WORKFLOW, fx.HYBRID],
        ids=["spark", "web", "cli", "workflow", "hybrid"],
    )
    async def test_every_relationship_cites_evidence(self, tmp_path: Path, files) -> None:
        graph, _, _ = await build(tmp_path, files)
        for e in graph.edges:
            assert e.evidence, f"{e.source} -> {e.target} has no evidence"

    async def test_nothing_is_joined_to_everything(self, tmp_path: Path) -> None:
        """The old builder linked every entry point to every dependency."""
        graph, _, _ = await build(tmp_path, fx.HYBRID)
        pipeline_ids = {n.id for n in graph.nodes if "daily" in n.id}
        web_ids = {
            n.id for n in graph.nodes if n.category in {NodeCategory.API, NodeCategory.FRONTEND}
        }
        crossing = [
            e
            for e in graph.edges
            if (e.source in pipeline_ids and e.target in web_ids)
            or (e.source in web_ids and e.target in pipeline_ids)
        ]
        assert crossing == []


class TestDataPipeline:
    async def test_the_steps_are_the_components(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.SPARK_PIPELINE)
        labels = [n.label for n in graph.nodes]
        assert {"Read Orders", "Join Customers", "Aggregate Revenue", "Write Report"} <= set(labels)
        assert NodeCategory.PIPELINE in categories(graph)
        assert graph.default_view is ArchitectureViewKind.DATA_FLOW

    async def test_steps_flow_in_the_order_the_job_calls_them(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.SPARK_PIPELINE)
        flow = [graph.node(i).label for i in graph.primary_flows[0].node_ids]  # type: ignore[union-attr]
        steps = [
            label
            for label in flow
            if label in {"Read Orders", "Join Customers", "Aggregate Revenue", "Write Report"}
        ]
        assert steps == ["Read Orders", "Join Customers", "Aggregate Revenue", "Write Report"]

    async def test_reads_and_writes_are_told_apart(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.SPARK_PIPELINE)
        assert edge(graph, "Read Orders", "Object storage (S3)").type is EdgeType.READS_FROM  # type: ignore[union-attr]
        assert edge(graph, "Write Report", "Object storage (S3)").type is EdgeType.WRITES_TO  # type: ignore[union-attr]

    async def test_a_config_file_the_job_opens_belongs_to_the_pipeline(
        self, tmp_path: Path
    ) -> None:
        """Opened in ``main()``, outside every step, so it is the pipeline's."""
        graph, _, _ = await build(tmp_path, fx.SPARK_PIPELINE)
        config = next(n for n in graph.nodes if n.category is NodeCategory.CONFIGURATION)
        users = {graph.node(e.source).category for e in graph.edges if e.target == config.id}  # type: ignore[union-attr]
        assert users == {NodeCategory.PIPELINE}

    async def test_the_setting_the_code_uses_is_recorded(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.SPARK_PIPELINE)
        pipeline = next(n for n in graph.nodes if n.category is NodeCategory.PIPELINE)
        assert "spark.sql.shuffle.partitions=200" in pipeline.properties["settings"]  # type: ignore[operator]
        assert pipeline.technology == "Spark"
        assert pipeline.subtype == "spark_job"


class TestWebApplication:
    async def test_browser_to_frontend_to_api_to_database(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.WEB_API)
        assert {NodeCategory.FRONTEND, NodeCategory.API, NodeCategory.DATABASE} <= categories(graph)
        flows = [[graph.node(i).label for i in f.node_ids] for f in graph.primary_flows]  # type: ignore[union-attr]
        assert ["User's browser", "React frontend", "Orders API", "PostgreSQL"] in flows
        assert graph.default_view is ArchitectureViewKind.REQUEST_FLOW

    async def test_the_api_is_named_by_its_resource_and_lists_routes(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.WEB_API)
        api = next(n for n in graph.nodes if n.category is NodeCategory.API)
        assert api.label == "Orders API"
        assert "GET /orders/{order_id}" in api.properties["routes"]  # type: ignore[operator]
        assert api.technology == "FastAPI"

    async def test_the_server_routes_to_the_api_it_mounts(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.WEB_API)
        mounted = edge(graph, "HTTP server", "Orders API")
        assert mounted is not None and mounted.type is EdgeType.ROUTES_TO
        assert mounted.evidence[0].path == "shop/main.py"

    async def test_the_browser_is_not_an_important_file(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.WEB_API)
        roles = {f.path: f.role for f in graph.important_files}
        assert roles["shop/routes/orders.py"] == "Service / API definition"
        assert roles["web/src/main.tsx"] == "Frontend"


class TestCommandLineTool:
    async def test_commands_route_to_what_they_touch(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.CLI_TOOL)
        assert edge(graph, "`add` command", "SQLite").type is EdgeType.WRITES_TO  # type: ignore[union-attr]
        assert edge(graph, "`search` command", "SQLite").type is EdgeType.READS_FROM  # type: ignore[union-attr]
        assert graph.default_view is ArchitectureViewKind.COMMAND_FLOW


class TestWorkflow:
    async def test_scheduler_workflow_and_tasks_in_dependency_order(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.WORKFLOW)
        assert {NodeCategory.SCHEDULER, NodeCategory.TASK} <= categories(graph)
        assert edge(graph, "Extract", "Transform").type is EdgeType.TRIGGERS  # type: ignore[union-attr]
        assert edge(graph, "Transform", "Load") is not None
        assert edge(graph, "Extract", "Load") is None
        assert graph.default_view is ArchitectureViewKind.WORKFLOW
        assert graph.important_files[0].role == "Workflow definition"


class TestLibrary:
    async def test_public_api_then_its_modules(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.LIBRARY)
        flow = [graph.node(i).label for i in graph.primary_flows[0].node_ids]  # type: ignore[union-attr]
        assert flow == ["Public API (tinyparse)", "Parser", "Lexer"]
        assert graph.default_view is ArchitectureViewKind.COMPONENT_FLOW


class TestHybrid:
    async def test_each_surface_keeps_its_own_flow(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.HYBRID)
        assert graph.default_view is ArchitectureViewKind.SYSTEM_OVERVIEW
        assert {"data_playground", "api"} <= set(graph.interaction_surfaces)
        ends = {graph.node(f.node_ids[-1]).label for f in graph.primary_flows}  # type: ignore[union-attr]
        assert {"PostgreSQL", "Object storage (S3)"} <= ends

    async def test_components_know_their_surface(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.HYBRID)
        pipeline = next(n for n in graph.nodes if n.category is NodeCategory.PIPELINE)
        assert pipeline.surface == "data_playground"


class TestUnknownRepository:
    async def test_it_says_what_it_could_not_find(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.UNKNOWN)
        assert graph.completeness is Completeness.PARTIAL
        assert graph.nodes == []
        assert any("no runtime components" in gap for gap in graph.gaps)


class TestSize:
    async def test_a_large_repository_is_grouped_not_dumped(self, tmp_path: Path) -> None:
        commands = "\n".join(f"@main.command()\ndef cmd{i}():\n    pass\n" for i in range(30))
        files = {
            "notes_app/cli.py": (
                f"import click\n\n@click.group()\ndef main():\n    pass\n\n{commands}\n"
            ),
            **{
                f"services/svc{i}.py": (
                    "from fastapi import FastAPI\napp = FastAPI()\n"
                    f"@app.get('/r{i}')\ndef r():\n    pass\n"
                )
                for i in range(24)
            },
        }
        graph, _, _ = await build(tmp_path, files)
        # Readable per view: each subsystem's drill-down holds at most 20.
        assert len(graph.subsystems) <= 12
        for subsystem in graph.subsystems:
            assert len([n for n in graph.nodes if n.subsystem == subsystem.id]) <= 20
        groups = [n for n in graph.nodes if n.children]
        assert groups and all(n.detected_because.startswith("groups") for n in groups)


class TestLessonsFromRealRepositories:
    """Shapes found on real repositories that the first fixtures did not have."""

    async def test_routers_mounted_through_a_router_module_are_linked(self, tmp_path: Path) -> None:
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\nfrom app.api.main import api_router\n"
                "app = FastAPI()\napp.include_router(api_router)\n"
            ),
            "app/api/main.py": (
                "from fastapi import APIRouter\nfrom app.api.routes import items\n"
                "api_router = APIRouter()\napi_router.include_router(items.router)\n"
            ),
            "app/api/routes/items.py": (
                "from fastapi import APIRouter\nrouter = APIRouter()\n"
                "@router.get('/items')\ndef list_items():\n    return []\n"
            ),
        }
        graph, _, _ = await build(tmp_path, files)
        mounted = edge(graph, "HTTP server", "Items API")
        assert mounted is not None
        assert mounted.evidence[0].path == "app/api/main.py"

    async def test_build_scripts_are_not_entry_points(self, tmp_path: Path) -> None:
        files = {
            **fx.WEB_API,
            "web/package.json": (
                '{"name": "shop-web", "dependencies": {"react": "18.3.1"},\n'
                ' "scripts": {"dev": "vite", "build": "vite build", "lint": "eslint ."}}\n'
            ),
        }
        graph, manifest, _ = await build(tmp_path, files)
        names = {e.name for e in manifest.entrypoints}
        labels = {n.label for n in graph.nodes if n.category is NodeCategory.ENTRY_POINT}
        assert "npm run build" in names, "the manifest still records it"
        assert "npm run build" not in labels and "npm run lint" not in labels

    async def test_a_ui_package_without_an_app_is_not_a_frontend(self, tmp_path: Path) -> None:
        files = {
            **fx.WEB_API,
            "emails/package.json": '{"name": "emails", "dependencies": {"react": "18.3.1"}}\n',
        }
        graph, _, _ = await build(tmp_path, files)
        frontends = [n for n in graph.nodes if n.category is NodeCategory.FRONTEND]
        assert [f.id for f in frontends] == ["frontend:web"]

    async def test_two_components_with_one_name_are_told_apart(self, tmp_path: Path) -> None:
        route = (
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/users')\ndef f():\n    pass\n"
        )
        graph, _, _ = await build(tmp_path, {"svc/users.py": route, "svc/admin.py": route})
        labels = sorted(n.label for n in graph.nodes if n.category is NodeCategory.API)
        assert labels == ["Users API (admin.py)", "Users API (users.py)"]
