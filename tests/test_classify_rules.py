"""Deterministic classification rules.

One test per signal rule in the specification, plus the two properties that make
a classification trustworthy: it never reduces a repository to a single type, and
every surface it proposes carries evidence and a reason.
"""

from __future__ import annotations

import pytest

from repolab.classify import classify_deterministic
from repolab.classify.signals import STRONG_CONFIDENCE, SURFACE_PRECEDENCE
from repolab.evidence.models import CodeReference
from repolab.ingest.detectors.base import DetectorRun
from repolab.manifest.models import EntryPoint, EntryPointKind, Service, SurfaceType


def run_with(*signals: tuple[str, str]) -> DetectorRun:
    """Build a DetectorRun from ``(kind, path)`` pairs, each properly cited."""
    run = DetectorRun()
    for index, (kind, path) in enumerate(signals, start=1):
        run.add(
            kind,
            f"{kind} evidence",
            detail=path,
            references=[CodeReference(path=path, start_line=index)],
        )
    return run


def surface(classification, surface_type: SurfaceType):
    return next((s for s in classification.surfaces if s.type is surface_type), None)


class TestSparkRule:
    """SparkSession / pyspark / Spark dependencies -> data playground."""

    def test_spark_session_yields_a_data_playground(self) -> None:
        result = classify_deterministic(run_with(("spark_session", "jobs/etl.py")))
        assert result.primary_type is SurfaceType.DATA_PLAYGROUND

    def test_a_spark_dependency_alone_is_enough_to_propose_it(self) -> None:
        """A bounded code scan may never reach the job, so the manifest counts."""
        run = DetectorRun()
        run.add(
            "framework",
            "Spark",
            references=[CodeReference(path="requirements.txt")],
        )
        result = classify_deterministic(run)
        assert surface(result, SurfaceType.DATA_PLAYGROUND) is not None

    def test_a_spark_job_entrypoint_corroborates(self) -> None:
        signals = run_with(("spark_session", "jobs/etl.py"))
        without = classify_deterministic(signals)
        with_entry = classify_deterministic(
            signals,
            entrypoints=[
                EntryPoint(name="etl", kind=EntryPointKind.SPARK_JOB, command="spark-submit etl.py")
            ],
        )
        playground = surface(with_entry, SurfaceType.DATA_PLAYGROUND)
        assert playground is not None
        assert playground.confidence > surface(without, SurfaceType.DATA_PLAYGROUND).confidence
        assert "spark_job" in playground.reason


class TestHttpRule:
    """HTTP server/routes + frontend -> web experience."""

    def test_flask_app_yields_a_web_surface(self) -> None:
        result = classify_deterministic(run_with(("flask_app", "app.py")))
        assert result.primary_type is SurfaceType.WEB

    def test_fastapi_yields_an_api_surface(self) -> None:
        result = classify_deterministic(run_with(("fastapi_app", "api/main.py")))
        assert result.primary_type is SurfaceType.API

    def test_routes_and_a_frontend_framework_produce_a_web_experience(self) -> None:
        run = run_with(("route_decorator", "api/routes.py"))
        run.add("framework", "React", references=[CodeReference(path="package.json")])
        run.add("framework", "Next.js", references=[CodeReference(path="package.json")])
        result = classify_deterministic(run)

        assert surface(result, SurfaceType.WEB) is not None
        assert result.primary_type is SurfaceType.WEB


class TestCliRule:
    """CLI framework / console entry point -> terminal experience."""

    def test_click_yields_a_cli_surface(self) -> None:
        result = classify_deterministic(run_with(("click_command", "tool/cli.py")))
        assert result.primary_type is SurfaceType.CLI

    def test_a_console_entrypoint_corroborates_cli(self) -> None:
        result = classify_deterministic(
            run_with(("argparse", "tool/cli.py")),
            entrypoints=[EntryPoint(name="tool", kind=EntryPointKind.CLI_SCRIPT, command="tool")],
        )
        cli = surface(result, SurfaceType.CLI)
        assert cli is not None
        assert "cli_script" in cli.reason


class TestServerPlusShellRule:
    """server + interactive shell -> CLI playground. This is the Cassandra shape."""

    def test_both_signals_produce_a_cli_playground(self) -> None:
        result = classify_deterministic(
            run_with(
                ("interactive_shell", "bin/cqlsh"),
                ("server_daemon", "bin/cassandra"),
                ("bin_executable", "bin/nodetool"),
            )
        )
        cli = surface(result, SurfaceType.CLI)
        assert cli is not None
        assert result.primary_type is SurfaceType.CLI
        assert "playground" in cli.name.lower()
        assert "server+shell" in cli.reason

    def test_a_shell_without_a_server_is_not_a_playground(self) -> None:
        result = classify_deterministic(run_with(("interactive_shell", "bin/repl")))
        cli = surface(result, SurfaceType.CLI)
        assert cli is not None
        assert "playground" not in cli.name.lower()

    def test_the_playground_cites_both_kinds_of_evidence(self) -> None:
        result = classify_deterministic(
            run_with(("interactive_shell", "bin/cqlsh"), ("server_daemon", "bin/cassandra"))
        )
        paths = {ref.path for ref in surface(result, SurfaceType.CLI).evidence}
        assert {"bin/cqlsh", "bin/cassandra"} <= paths


class TestLibraryRule:
    """library without primary executable -> notebook / code experience."""

    def test_a_library_with_nothing_runnable_yields_a_notebook(self) -> None:
        result = classify_deterministic(run_with(("library_package", "pyproject.toml")))
        assert result.primary_type is SurfaceType.NOTEBOOK
        assert "library-without-executable" in surface(result, SurfaceType.NOTEBOOK).reason

    def test_a_library_that_ships_a_command_is_not_a_notebook(self) -> None:
        result = classify_deterministic(
            run_with(("library_package", "pyproject.toml"), ("click_command", "pkg/cli.py"))
        )
        assert result.primary_type is SurfaceType.CLI

    def test_notebooks_in_the_tree_propose_a_notebook_surface(self) -> None:
        result = classify_deterministic(run_with(("notebook_file", "explore.ipynb")))
        assert surface(result, SurfaceType.NOTEBOOK) is not None


class TestHybrid:
    """multiple strong signals -> hybrid."""

    def test_two_strong_surfaces_mark_the_result_hybrid(self) -> None:
        run = run_with(
            ("spark_session", "jobs/etl.py"),
            ("spark_import", "jobs/etl.py"),
            ("spark_submit", "run.sh"),
            ("spark_config", "conf.json"),
            ("fastapi_app", "api/main.py"),
            ("route_decorator", "api/routes.py"),
            ("uvicorn_run", "api/main.py"),
            ("port", "api/main.py"),
        )
        result = classify_deterministic(run)

        strong = [s for s in result.surfaces if s.confidence >= STRONG_CONFIDENCE]
        assert len(strong) >= 2
        assert result.hybrid is True

    def test_one_strong_surface_is_not_hybrid(self) -> None:
        result = classify_deterministic(
            run_with(("spark_session", "a.py"), ("spark_import", "a.py"), ("spark_submit", "r.sh"))
        )
        assert result.hybrid is False

    def test_a_repository_is_never_reduced_to_one_type(self) -> None:
        result = classify_deterministic(
            run_with(
                ("spark_session", "jobs/etl.py"),
                ("fastapi_app", "api/main.py"),
                ("click_command", "cli.py"),
            )
        )
        assert len(result.surfaces) >= 3
        assert len({s.type for s in result.surfaces}) >= 3


class TestEveryClassificationIsJustified:
    """surface + confidence + evidence + reason, for every surface."""

    @pytest.fixture
    def rich(self):
        return classify_deterministic(
            run_with(
                ("spark_session", "jobs/etl.py"),
                ("fastapi_app", "api/main.py"),
                ("click_command", "cli.py"),
                ("interactive_shell", "bin/repl"),
            )
        )

    def test_every_surface_carries_evidence(self, rich) -> None:
        assert rich.surfaces
        for surface_ in rich.surfaces:
            assert surface_.evidence, f"{surface_.type} has no evidence"
            assert surface_.is_supported

    def test_every_surface_carries_a_reason(self, rich) -> None:
        for surface_ in rich.surfaces:
            assert surface_.reason.strip()

    def test_every_surface_carries_a_bounded_confidence(self, rich) -> None:
        for surface_ in rich.surfaces:
            assert 0.0 < surface_.confidence <= 0.95

    def test_a_primary_is_recommended_with_a_rationale(self, rich) -> None:
        assert rich.primary_type is not None
        assert rich.primary_surface is not None
        assert rich.rationale.strip()

    def test_exactly_one_surface_is_primary(self, rich) -> None:
        assert sum(1 for s in rich.surfaces if s.primary) == 1

    def test_an_uncitable_signal_is_ignored(self) -> None:
        """A signal RepoLab cannot point at is not evidence."""
        run = DetectorRun()
        run.add("spark_session", "no references at all")
        result = classify_deterministic(run)
        assert result.primary_type is SurfaceType.TERMINAL


class TestScoringAndPrecedence:
    def test_one_strong_signal_outweighs_many_weak_ones(self) -> None:
        """Fifty sys.argv hits must not outrank one FastAPI(...)."""
        run = DetectorRun()
        for index in range(50):
            run.add(
                "sys_argv",
                "sys.argv",
                references=[CodeReference(path=f"mod{index}.py", start_line=1)],
            )
        run.add("fastapi_app", "FastAPI()", references=[CodeReference(path="api.py")])

        result = classify_deterministic(run)
        assert result.primary_type is SurfaceType.API

    def test_repeating_one_kind_does_not_inflate_confidence(self) -> None:
        once = classify_deterministic(run_with(("argparse", "a.py")))
        many = classify_deterministic(run_with(*[("argparse", f"mod{i}.py") for i in range(10)]))
        assert surface(many, SurfaceType.CLI).confidence == pytest.approx(
            surface(once, SurfaceType.CLI).confidence
        )

    def test_a_tie_is_broken_by_precedence_not_alphabetically(self) -> None:
        """`api` would win alphabetically; the richer experience should win."""
        result = classify_deterministic(
            run_with(("spark_session", "jobs/etl.py"), ("fastapi_app", "api/main.py"))
        )
        top_two = result.surfaces[:2]
        assert top_two[0].confidence == top_two[1].confidence
        assert result.primary_type is SurfaceType.DATA_PLAYGROUND
        assert "richer experience" in result.rationale

    def test_precedence_order_is_documented(self) -> None:
        assert SURFACE_PRECEDENCE[0] is SurfaceType.DATA_PLAYGROUND
        assert SURFACE_PRECEDENCE[-1] is SurfaceType.TERMINAL

    def test_surfaces_are_ranked_by_confidence(self) -> None:
        result = classify_deterministic(
            run_with(
                ("spark_session", "jobs/etl.py"),
                ("spark_import", "jobs/etl.py"),
                ("spark_submit", "run.sh"),
                ("sys_argv", "cli.py"),
            )
        )
        confidences = [s.confidence for s in result.surfaces]
        assert confidences == sorted(confidences, reverse=True)

    def test_services_corroborate_session_based_surfaces(self) -> None:
        signals = run_with(("argparse", "cli.py"))
        without = classify_deterministic(signals)
        with_services = classify_deterministic(
            signals, services=[Service(name="Cassandra", ports=[9042])]
        )
        assert (
            surface(with_services, SurfaceType.CLI).confidence
            > surface(without, SurfaceType.CLI).confidence
        )


class TestFallback:
    def test_nothing_detected_yields_a_low_confidence_terminal(self) -> None:
        result = classify_deterministic(DetectorRun())

        assert result.primary_type is SurfaceType.TERMINAL
        assert result.surfaces[0].confidence < 0.5
        assert result.surfaces[0].primary is True
        assert result.ambiguous is True
        assert result.rationale


class TestEvidenceOrdering:
    """The strongest evidence must be cited first, not whatever arrived first."""

    def test_strong_signal_evidence_outranks_weak(self) -> None:
        run = DetectorRun()
        # Ten weak references arrive before the one that actually matters.
        for index in range(10):
            run.add(
                "shell_script",
                "script",
                references=[CodeReference(path=f"scripts/s{index}.sh")],
            )
        run.add(
            "interactive_shell",
            "cqlsh",
            references=[CodeReference(path="bin/cqlsh")],
        )

        cli = surface(classify_deterministic(run), SurfaceType.CLI)
        assert cli.evidence[0].path == "bin/cqlsh"

    def test_evidence_is_capped(self) -> None:
        run = DetectorRun()
        for index in range(40):
            run.add(
                "argparse",
                "argparse",
                references=[CodeReference(path=f"mod{index}.py", start_line=index + 1)],
            )
        cli = surface(classify_deterministic(run), SurfaceType.CLI)
        assert 0 < len(cli.evidence) <= 6

    def test_duplicate_references_are_not_cited_twice(self) -> None:
        run = DetectorRun()
        for _ in range(3):
            run.add(
                "argparse",
                "argparse",
                references=[CodeReference(path="cli.py", start_line=4)],
            )
        cli = surface(classify_deterministic(run), SurfaceType.CLI)
        assert len(cli.evidence) == 1


class TestHybridThreshold:
    def test_hybrid_uses_a_lower_bar_than_the_model_gate(self) -> None:
        """Two substantial surfaces is a hybrid even if neither closes the gate."""
        from repolab.classify.signals import HYBRID_CONFIDENCE, STRONG_CONFIDENCE

        assert HYBRID_CONFIDENCE < STRONG_CONFIDENCE

    def test_two_substantial_surfaces_are_hybrid(self) -> None:
        run = run_with(
            ("interactive_shell", "bin/shell"),
            ("click_command", "cli.py"),
            ("argparse", "cli.py"),
            ("flask_app", "web/app.py"),
        )
        # A Flask app plus Flask declared as a dependency: substantial, but not
        # overwhelming -- which is exactly the band `hybrid` is meant to catch.
        run.add("framework", "Flask", references=[CodeReference(path="requirements.txt")])

        result = classify_deterministic(run)
        confidences = {s.type: s.confidence for s in result.surfaces}
        assert confidences[SurfaceType.WEB] >= 0.6
        assert confidences[SurfaceType.WEB] < 0.8
        assert result.hybrid is True
