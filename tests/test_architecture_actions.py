"""What each component offers: Ask, sometimes Experiment, and real measurements.

Experiment is offered only where RepoLab can run this repository *and* the
component is one that run exercises. The runtime overlay carries only values a
stored run produced -- never an estimate.
"""

from __future__ import annotations

from pathlib import Path

from repolab.architecture.actions import node_actions
from repolab.architecture.models import NodeCategory
from repolab.architecture.runtime import RuntimeOverlay, cli_overlay, spark_overlay
from repolab.builds.models import BuildArtifact, SmokeTestResult
from repolab.manifest.models import BuildKind
from tests.support import architecture_fixtures as fx


def cqlsh_build(kind: BuildKind = BuildKind.OFFICIAL_RELEASE) -> BuildArtifact:
    return BuildArtifact(
        repo_url="https://github.com/example/repo",
        commit_sha=fx.SHA,
        kind=kind,
        version="5.0.9",
        checkpoint_id="ckpt",
        checkpoint_tag="tag",
        cli_command="/opt/db/bin/cqlsh",
        smoke=SmokeTestResult(name="connects", command="x", passed=True),
        simulated=kind is BuildKind.MINI_LAB,
    )


async def built(tmp_path: Path, files: dict[str, str]):
    return await fx.analyse(fx.write_repo(tmp_path, files))


class TestExperimentAvailability:
    async def test_a_runnable_spark_job_offers_experiments_on_its_pipeline_only(
        self, tmp_path: Path
    ) -> None:
        graph, manifest, _ = await built(tmp_path, fx.SPARK_PIPELINE)
        actions = node_actions(graph, manifest, [])

        by_category = {
            graph.node(node_id).category: a  # type: ignore[union-attr]
            for node_id, a in actions.items()
        }
        assert by_category[NodeCategory.PIPELINE].experiment.available
        assert by_category[NodeCategory.TRANSFORM].experiment.available
        assert not by_category[NodeCategory.ENTRY_POINT].experiment.available
        assert not by_category[NodeCategory.STORAGE].experiment.available

    async def test_the_spark_suggestion_names_the_repositorys_own_value(
        self, tmp_path: Path
    ) -> None:
        graph, manifest, _ = await built(tmp_path, fx.SPARK_PIPELINE)
        pipeline = next(n for n in graph.nodes if n.category is NodeCategory.PIPELINE)
        action = node_actions(graph, manifest, [])[pipeline.id]
        assert action.experiment.question == "Why does this use spark.sql.shuffle.partitions=200?"
        assert action.experiment.question in action.suggested_questions

    async def test_a_web_app_with_nothing_to_run_offers_no_experiment(self, tmp_path: Path) -> None:
        graph, manifest, _ = await built(tmp_path, fx.WEB_API)
        actions = node_actions(graph, manifest, [])
        assert actions and not any(a.experiment.available for a in actions.values())
        assert all(a.ask for a in actions.values())
        assert all(a.experiment.reason for a in actions.values())

    async def test_a_built_cli_offers_experiments_on_cli_components(self, tmp_path: Path) -> None:
        graph, manifest, _ = await built(tmp_path, fx.CLI_TOOL)
        offered = {
            graph.node(i).category  # type: ignore[union-attr]
            for i, a in node_actions(graph, manifest, [cqlsh_build()]).items()
            if a.experiment.available
        }
        assert offered and offered <= {
            NodeCategory.CLI,
            NodeCategory.COMMAND,
            NodeCategory.DATABASE,
        }

    async def test_a_mini_lab_is_never_experimented_on(self, tmp_path: Path) -> None:
        graph, manifest, _ = await built(tmp_path, fx.CLI_TOOL)
        actions = node_actions(graph, manifest, [cqlsh_build(BuildKind.MINI_LAB)])
        assert not any(a.experiment.available for a in actions.values())


class TestSuggestedQuestions:
    async def test_questions_use_values_from_the_component(self, tmp_path: Path) -> None:
        graph, manifest, _ = await built(tmp_path, fx.WEB_API)
        api = next(n for n in graph.nodes if n.category is NodeCategory.API)
        questions = node_actions(graph, manifest, [])[api.id].suggested_questions
        assert any("/orders" in q for q in questions)

    async def test_a_task_question_names_its_real_upstream(self, tmp_path: Path) -> None:
        graph, manifest, _ = await built(tmp_path, fx.WORKFLOW)
        load = next(n for n in graph.nodes if n.label == "Load")
        questions = node_actions(graph, manifest, [])[load.id].suggested_questions
        assert "Why does Load depend on Transform?" in questions


class TestRuntimeOverlay:
    async def test_spark_measurements_land_on_the_pipeline_and_matching_steps(
        self, tmp_path: Path
    ) -> None:
        graph, _, _ = await built(tmp_path, fx.SPARK_PIPELINE)
        overlay = RuntimeOverlay()
        spark_overlay(
            graph,
            {
                "run_id": "abc12345-run",
                "measurements": {
                    "total_runtime_ms": 8200,
                    "stage_count": 3,
                    "task_count": 20,
                    "shuffle_bytes_read": 420,
                    "stages": [
                        {
                            "stage_id": 3,
                            "name": "join at daily_sales.py:7",
                            "duration_ms": 900,
                            "shuffle_bytes_read": 400,
                        },
                        {
                            "stage_id": 4,
                            "name": "count at NativeMethodAccessorImpl.java:0",
                            "duration_ms": 5,
                        },
                    ],
                },
            },
            overlay,
        )
        pipeline = next(n for n in graph.nodes if n.category is NodeCategory.PIPELINE)
        join = next(n for n in graph.nodes if n.label == "Join Customers")

        assert {m.name for m in overlay.measurements[pipeline.id]} >= {"total runtime", "tasks"}
        assert [m.name for m in overlay.measurements[join.id]] == [
            "stage 3 duration",
            "stage 3 shuffle read",
        ]
        assert all(
            m.provenance == "measured" and "abc12345" in m.source
            for ms in overlay.measurements.values()
            for m in ms
        )

    async def test_with_no_run_there_is_nothing_measured(self, tmp_path: Path) -> None:
        graph, _, _ = await built(tmp_path, fx.SPARK_PIPELINE)
        overlay = RuntimeOverlay()
        spark_overlay(graph, {"run_id": "x", "measurements": {}}, overlay)
        assert overlay.measurements == {}

    async def test_a_cli_experiment_reports_statements_run(self, tmp_path: Path) -> None:
        graph, _, _ = await built(tmp_path, fx.CLI_TOOL)
        overlay = RuntimeOverlay()
        cli_overlay(
            graph,
            {"id": "exp-1", "runs": [{"statements": [{"ok": True}, {"ok": False}]}]},
            overlay,
        )
        values = {m.name: m.value for ms in overlay.measurements.values() for m in ms}
        assert values == {"statements run": 2.0, "statements accepted": 1.0}
