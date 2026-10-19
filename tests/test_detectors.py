"""Deterministic detection.

The central property here is that **every finding cites a file**: a manifest
claim without evidence is exactly what RepoLab is meant not to produce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import DetectionContext, detect_all
from repolab.ingest.tree import build_file_tree

SPARK_REPO: dict[str, str] = {
    "jobs/etl_job.py": """
        from pyspark.sql import SparkSession

        def main():
            spark = (SparkSession.builder
                .appName("etl")
                .config("spark.sql.shuffle.partitions", 200)
                .getOrCreate())
            spark.read.parquet("in").groupBy("k").count().write.parquet("out")

        if __name__ == "__main__":
            main()
    """,
    "configs/etl_config.json": '{"spark.sql.shuffle.partitions": 200}\n',
    "requirements.txt": "pyspark>=3.5\npandas==2.1.0\n",
    "run.sh": "#!/bin/sh\nspark-submit jobs/etl_job.py\n",
    "tests/test_etl.py": "def test_ok():\n    assert True\n",
    "README.md": "# ETL\n\nRun `spark-submit jobs/etl_job.py`.\n",
}

WEB_REPO: dict[str, str] = {
    "app/main.py": """
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/health")
        def health():
            return {"status": "ok"}
    """,
    "package.json": '{"name": "ui", "scripts": {"dev": "next dev"}, '
    '"dependencies": {"next": "14.0.0", "react": "18.2.0"}}\n',
    "pyproject.toml": """
        [project]
        name = "svc"
        dependencies = ["fastapi>=0.115", "uvicorn>=0.32"]

        [project.scripts]
        svc = "app.main:cli"
    """,
    "docker-compose.yml": """
        services:
          api:
            image: myorg/api:1.0
            ports:
              - "8000:8000"
          postgres:
            image: postgres:16
            ports:
              - "5432:5432"
          redis:
            image: redis:7-alpine
    """,
    "Dockerfile": "FROM python:3.11-slim\nEXPOSE 8000\n",
}

CLI_REPO: dict[str, str] = {
    "tool/cli.py": """
        import argparse

        def main():
            parser = argparse.ArgumentParser(description="tool")
            parser.parse_args()

        if __name__ == "__main__":
            main()
    """,
    "setup.py": """
        from setuptools import setup
        setup(
            name="tool",
            install_requires=["click>=8.0"],
            entry_points={"console_scripts": ["tool=tool.cli:main"]},
        )
    """,
}


def run_detection(root: Path, sha: str = "abc123"):
    tree = build_file_tree(root)
    context = DetectionContext(root=root, tree=tree, commit_sha=sha)
    return detect_all(context, ReadBudget())


@pytest.fixture
def spark(make_repo):
    # run_detection stamps its own SHA so references can be asserted exactly.
    root, _ = make_repo(SPARK_REPO)
    return run_detection(root), root


@pytest.fixture
def web(make_repo):
    root, _ = make_repo(WEB_REPO)
    return run_detection(root), root


class TestEveryFindingIsEvidenceBacked:
    @pytest.mark.parametrize("files", [SPARK_REPO, WEB_REPO, CLI_REPO], ids=["spark", "web", "cli"])
    def test_no_finding_lacks_a_file_reference(self, make_repo, files: dict[str, str]) -> None:
        root, sha = make_repo(files)
        outcome = run_detection(root, sha)

        assert outcome.run.findings, "detection produced nothing"
        assert outcome.unsupported_findings == []

    def test_references_carry_the_commit_sha(self, spark) -> None:
        outcome, _ = spark
        references = outcome.run.all_references
        assert references
        assert all(ref.commit_sha == "abc123" for ref in references)

    def test_references_point_at_real_files(self, spark) -> None:
        outcome, root = spark
        for ref in outcome.run.all_references:
            assert (root / ref.path).exists(), f"{ref.path} does not exist"

    def test_line_numbers_are_one_based_and_in_range(self, spark) -> None:
        outcome, root = spark
        for ref in outcome.run.all_references:
            if ref.start_line is None:
                continue
            total = len((root / ref.path).read_text().splitlines())
            assert 1 <= ref.start_line <= total


class TestSparkDetection:
    def test_spark_signals_are_recorded_for_the_classifier(self, spark) -> None:
        """Detection records signals; deciding the surface is the classifier's job."""
        outcome, _ = spark
        assert {"spark_session", "spark_import"} <= {f.kind for f in outcome.run.findings}

    def test_spark_signals_are_found(self, spark) -> None:
        outcome, _ = spark
        kinds = {f.kind for f in outcome.run.findings}
        assert {"spark_import", "spark_session", "spark_config"} <= kinds

    def test_spark_submit_is_found_in_scripts_and_docs(self, spark) -> None:
        outcome, _ = spark
        paths = {f.detail for f in outcome.run.of_kind("spark_submit")}
        assert "run.sh" in paths

    def test_spark_is_reported_as_a_framework(self, spark) -> None:
        outcome, _ = spark
        assert "Spark" in outcome.run.values_of("framework")

    def test_dependencies_are_parsed(self, spark) -> None:
        outcome, _ = spark
        names = {d.name.lower(): d for d in outcome.dependencies}
        assert names["pyspark"].version_spec == ">=3.5"
        assert names["pandas"].ecosystem == "pypi"

    def test_the_config_file_is_detected(self, spark) -> None:
        outcome, _ = spark
        assert "configs/etl_config.json" in outcome.run.values_of("configuration")

    def test_the_shuffle_setting_is_surfaced_as_a_concept_candidate(self, spark) -> None:
        """The demo question is 'why 200 partitions?', so this must be citable."""
        outcome, _ = spark
        settings = outcome.run.of_kind("configuration_setting")
        assert any("partition" in f.value.lower() for f in settings)

    def test_entrypoints_include_the_job_and_the_script(self, spark) -> None:
        outcome, _ = spark
        commands = " ".join(e.command for e in outcome.entrypoints)
        assert "jobs/etl_job.py" in commands
        assert "run.sh" in commands

    def test_tests_are_detected(self, spark) -> None:
        outcome, _ = spark
        assert outcome.run.of_kind("tests")


class TestWebDetection:
    def test_http_signals_are_recorded(self, web) -> None:
        outcome, _ = web
        assert {"fastapi_app", "route_decorator"} & {f.kind for f in outcome.run.findings}

    def test_route_declarations_are_found(self, web) -> None:
        outcome, _ = web
        assert outcome.run.of_kind("route_decorator")

    def test_compose_services_become_services(self, web) -> None:
        outcome, _ = web
        names = {s.name for s in outcome.services}
        assert "PostgreSQL" in names
        assert "Redis" in names

    def test_service_ports_are_recorded(self, web) -> None:
        outcome, _ = web
        postgres = next(s for s in outcome.services if s.name == "PostgreSQL")
        assert 5432 in postgres.ports

    def test_dockerfile_exposed_ports_are_recorded(self, web) -> None:
        outcome, _ = web
        assert "8000" in " ".join(outcome.run.values_of("exposed_ports"))

    def test_npm_scripts_become_entrypoints(self, web) -> None:
        outcome, _ = web
        assert any("npm run dev" in e.name for e in outcome.entrypoints)

    def test_console_scripts_become_entrypoints(self, web) -> None:
        outcome, _ = web
        assert any(e.name == "svc" for e in outcome.entrypoints)

    def test_frameworks_from_multiple_ecosystems(self, web) -> None:
        outcome, _ = web
        frameworks = set(outcome.run.values_of("framework"))
        assert {"FastAPI", "Next.js", "React"} <= frameworks


class TestCliDetection:
    def test_cli_signals_are_recorded(self, make_repo) -> None:
        root, sha = make_repo(CLI_REPO)
        outcome = run_detection(root, sha)
        assert "argparse" in {f.kind for f in outcome.run.findings}

    def test_setup_py_console_scripts_are_read_as_text(self, make_repo) -> None:
        root, sha = make_repo(CLI_REPO)
        outcome = run_detection(root, sha)
        assert any(e.name == "tool" for e in outcome.entrypoints)

    def test_argparse_is_detected(self, make_repo) -> None:
        root, sha = make_repo(CLI_REPO)
        outcome = run_detection(root, sha)
        assert outcome.run.of_kind("argparse")


class TestFallbackBehaviour:
    def test_an_unrecognised_repository_yields_no_surface_signals(self, make_repo) -> None:
        """Nothing is invented; the classifier then falls back to a terminal."""
        root, sha = make_repo({"notes.txt": "just some notes\n"})
        outcome = run_detection(root, sha)

        surface_kinds = {"spark_session", "fastapi_app", "argparse", "interactive_shell"}
        assert not (surface_kinds & {f.kind for f in outcome.run.findings})

    def test_more_signals_are_recorded_for_a_richer_repository(self, make_repo) -> None:
        sparse, sha1 = make_repo({"a.py": "from pyspark.sql import SparkSession\n"})
        rich, sha2 = make_repo(SPARK_REPO)

        sparse_kinds = {f.kind for f in run_detection(sparse, sha1).run.findings}
        rich_kinds = {f.kind for f in run_detection(rich, sha2).run.findings}
        assert len(rich_kinds) > len(sparse_kinds)

    def test_malformed_manifests_do_not_crash_detection(self, make_repo) -> None:
        root, sha = make_repo(
            {
                "package.json": "{not valid json",
                "pyproject.toml": "[[[broken toml",
                "app.py": "x = 1\n",
            }
        )
        outcome = run_detection(root, sha)
        assert outcome.dependencies == []
        assert outcome.unsupported_findings == []
