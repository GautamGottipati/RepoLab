"""Experience discovery: from what a repository is to what someone can do with it.

Offline, across conceptual repository types. The properties that matter:

* a sensible primary experience, and at most four runnable ones;
* the four states kept apart (detected / supported / runnable / running);
* real repository, official runtime and simulation never confused;
* nothing runnable -> reasons, what RepoLab runs, and a Mini Lab, not a dead end;
* facts are read from the tree with their lines, never invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.evidence.models import CodeReference
from repolab.experiences.adapters import DiscoveryContext
from repolab.experiences.catalog import experiences_for
from repolab.experiences.discovery import MAX_EXPERIENCES, discover
from repolab.experiences.facts import (
    PythonPackageFacts,
    RepositoryFacts,
    Snippet,
    SparkRuntimeFacts,
    gather_facts,
    python_image,
)
from repolab.experiences.model import ExperienceState, Reality, Strategy
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.tree import build_file_tree
from repolab.manifest.models import (
    BuildKind,
    BuildStrategy,
    Fidelity,
    InteractionSurface,
    RepoManifest,
    SurfaceType,
)
from repolab.runtime.models import RouteRef, RuntimePlan, ServicePlan
from tests.support.builds import cassandra_manifest
from tests.support.manifests import airflow_manifest, spark_manifest


def manifest(
    *surfaces: tuple[SurfaceType, float],
    facts: RepositoryFacts | None = None,
    runtime: RuntimePlan | None = None,
    url: str = "https://github.com/example/thing",
) -> RepoManifest:
    return RepoManifest(
        repo_url=url,
        commit_sha="c" * 40,
        interaction_surfaces=[
            InteractionSurface(type=t, name=t.value, confidence=c, reason="test", primary=i == 0)
            for i, (t, c) in enumerate(surfaces)
        ],
        build_strategy=BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=Fidelity.EXACT),
        confidence=0.8,
        facts=facts,
        runtime=runtime,
    )


def listing(m: RepoManifest, **kw):
    catalog = experiences_for(m)
    context = DiscoveryContext(
        manifest=m,
        catalog=catalog,
        surface_confidence={e.surface.value: e.confidence for e in catalog.experiences},
        **kw,
    )
    return discover(context)


SPARK_FACTS = RepositoryFacts(
    spark=SparkRuntimeFacts(
        spark_submit="bin/spark-submit",
        pyspark="bin/pyspark",
        build_command="./build/mvn -DskipTests clean package",
        build_command_ref=CodeReference(path="README.md", start_line=114),
        java_version="17",
        version="5.0.0-SNAPSHOT",
        compiler_heap_gb=4,
        quickstart=Snippet(
            code="spark.range(1000 * 1000 * 1000).count()",
            language="python",
            reference=CodeReference(path="README.md", start_line=149),
        ),
    )
)


class TestADistributedFramework:
    """A repository that ships Spark itself (the shape of apache/spark)."""

    def spark(self, **kw):
        return listing(
            manifest(
                (SurfaceType.CLI, 0.95),
                (SurfaceType.NOTEBOOK, 0.35),
                (SurfaceType.DATA_PLAYGROUND, 0.25),
                facts=SPARK_FACTS,
            ),
            **kw,
        )

    def test_experiences_not_surfaces_with_transform_first(self) -> None:
        found = self.spark()
        titles = [e.title for e in found.experiences]
        assert titles[0] == "Transform a Dataset"
        assert set(titles) == {
            "Transform a Dataset",
            "Try Spark SQL",
            "Explore Partitions & Shuffles",
        }
        assert found.experiences[0].primary
        assert [e.primary for e in found.experiences].count(True) == 1
        assert found.fallback is None
        # The CLI is detected, not offered: RepoLab has no build of it to drive.
        assert [e.title for e in found.also] == ["Try the CLI"]
        assert found.also[0].state is ExperienceState.SUPPORTED
        assert "no build strategy" in (found.also[0].why_not or "")

    def test_official_runtime_is_offered_and_disclosed_while_the_commit_is_unbuilt(self) -> None:
        primary = self.spark().experiences[0]
        official, source = primary.options
        assert official.strategy is Strategy.OFFICIAL_RELEASE
        assert official.reality is Reality.OFFICIAL_RUNTIME and official.ready
        assert "not built from this commit (5.0.0-SNAPSHOT)" in (official.disclosure or "")
        assert source.reality is Reality.REAL_REPOSITORY and not source.ready
        assert "45-90 minutes" in source.estimate
        assert source.evidence[0].path == "README.md"

    def test_a_ready_source_build_becomes_the_first_option(self) -> None:
        primary = self.spark(builds={("spark", "source_build"): "ready"}).experiences[0]
        assert primary.options[0].reality is Reality.REAL_REPOSITORY and primary.options[0].ready

    def test_starters_say_whose_they_are(self) -> None:
        primary = self.spark().experiences[0]
        sources = {a.id: a.source for a in primary.starter_actions}
        assert sources == {"transform": "repolab", "readme": "readme"}
        readme = next(a for a in primary.starter_actions if a.source == "readme")
        assert readme.code == "result = spark.range(1000 * 1000).count()"  # sized down, visibly
        assert readme.evidence[0].start_line == 149

    def test_a_live_session_makes_it_running(self) -> None:
        found = self.spark(sessions={"spark:transform": "abc"})
        primary = found.experiences[0]
        assert primary.state is ExperienceState.RUNNING and primary.session_id == "abc"


class TestOtherShapes:
    def test_a_spark_job_repository_opens_its_pipeline(self) -> None:
        found = listing(spark_manifest())
        assert found.experiences[0].title == "Run the Repository's Pipeline"
        assert found.experiences[0].route == "/playground"
        assert found.experiences[0].options[0].reality is Reality.REAL_REPOSITORY

    def test_a_cli_repository_offers_its_terminal(self) -> None:
        found = listing(cassandra_manifest())
        assert found.experiences[0].title == "Try the CLI"
        assert found.experiences[0].route == "/learn"

    def test_a_web_application_and_api_from_their_runtime_plan(self) -> None:
        service = ServicePlan(
            name="backend",
            surfaces=["api"],
            working_directory="backend",
            image="node:20",
            image_reason="t",
            package_manager="npm",
            install="npm ci --no-audit --no-fund",
            start="npm run start",
            script="start",
            port=3000,
            port_source="t",
            routes=[
                RouteRef(method="GET", path="/users", file="routes/users.js", line=3),
                RouteRef(method="POST", path="/users", file="routes/users.js", line=9),
            ],
        )
        web = service.model_copy(update={"name": "web", "surfaces": ["web"], "routes": []})
        found = listing(
            manifest(
                (SurfaceType.WEB, 0.8),
                (SurfaceType.API, 0.8),
                runtime=RuntimePlan(services=[service, web]),
            )
        )
        assert [e.title for e in found.experiences] == ["Use the Application", "Try the API"]
        api = found.experiences[1]
        assert [a.title for a in api.starter_actions] == ["GET /users", "POST /users"]
        assert api.starter_actions[0].evidence[0].path == "routes/users.js"
        assert found.experiences[0].primary

    def test_a_running_live_app_is_running(self) -> None:
        service = ServicePlan(
            name="web",
            surfaces=["web"],
            working_directory="",
            image="node:20",
            image_reason="t",
            package_manager="npm",
            install="npm ci --no-audit --no-fund",
            start="npm run start",
            script="start",
            port=3000,
            port_source="t",
        )
        found = listing(
            manifest((SurfaceType.WEB, 0.8), runtime=RuntimePlan(services=[service])),
            live_services={"web": "running"},
        )
        assert found.experiences[0].state is ExperienceState.RUNNING

    def test_a_python_library_gets_a_playground_from_its_readme(self) -> None:
        facts = RepositoryFacts(
            python=PythonPackageFacts(
                distribution="tinylib",
                import_name="tinylib",
                package_path="src/tinylib",
                requires_python=">=3.10",
                manifest=CodeReference(path="pyproject.toml"),
                snippets=[
                    Snippet(
                        code="import tinylib\nprint(tinylib.greet())",
                        language="python",
                        reference=CodeReference(path="README.md", start_line=12),
                    )
                ],
                examples=["examples/demo.py"],
            )
        )
        found = listing(manifest((SurfaceType.NOTEBOOK, 0.6), facts=facts))
        lib = found.experiences[0]
        assert lib.title == "Try the Library" and lib.primary
        assert lib.options[0].reality is Reality.REAL_REPOSITORY
        assert "python:3.10-slim" in lib.options[0].label
        assert [a.source for a in lib.starter_actions] == ["readme", "examples"]
        assert 'runpy.run_path("/opt/repolab/repo/examples/demo.py"' in (
            lib.starter_actions[1].code or ""
        )

    def test_a_workflow_system_without_a_runnable_path_is_not_a_dead_end(self) -> None:
        found = listing(airflow_manifest())
        assert found.experiences == []
        assert found.nothing_runs_because
        assert found.fallback is not None
        assert found.fallback.options[0].reality is Reality.MINI_LAB
        assert "does not run this repository" in found.fallback.options[0].label
        assert found.supported_kinds

    @pytest.mark.parametrize("surfaces", [(), ((SurfaceType.TERMINAL, 0.2),)])
    def test_an_unknown_repository_gets_reasons_and_a_mini_lab(self, surfaces) -> None:
        found = listing(manifest(*surfaces))
        assert found.experiences == [] and found.fallback is not None
        assert found.nothing_runs_because

    def test_never_more_than_four(self) -> None:
        facts = SPARK_FACTS.model_copy(deep=True)
        service = ServicePlan(
            name="web",
            surfaces=["web", "api"],
            working_directory="",
            image="node:20",
            image_reason="t",
            package_manager="npm",
            install="npm ci --no-audit --no-fund",
            start="npm run start",
            script="start",
            port=3000,
            port_source="t",
        )
        found = listing(
            manifest(
                (SurfaceType.DATA_PLAYGROUND, 0.9),
                (SurfaceType.WEB, 0.8),
                (SurfaceType.API, 0.8),
                facts=facts,
                runtime=RuntimePlan(services=[service]),
            )
        )
        assert len(found.experiences) == MAX_EXPERIENCES
        assert all(e.rank_signals for e in found.experiences)


class TestFacts:
    def test_spark_runtime_facts_come_from_the_tree_with_lines(self, tmp_path: Path) -> None:
        root = tmp_path / "spark"
        (root / "bin").mkdir(parents=True)
        (root / "bin/spark-submit").write_text("#!/bin/sh\n")
        (root / "bin/pyspark").write_text("#!/bin/sh\n")
        (root / "pom.xml").write_text(
            "<project>\n<artifactId>spark-parent_2.13</artifactId>\n<version>9.9.9-SNAPSHOT</version>\n"
            "<java.version>17</java.version>\n<jvmArgs><jvmArg>-Xmx4g</jvmArg></jvmArgs>\n</project>\n"
        )
        (root / "README.md").write_text(
            "# Spark\n\n```bash\n./build/mvn -DskipTests clean package\n```\n\n"
            "```python\n>>> spark.range(1000).count()\n```\n"
        )
        facts = gather_facts(DetectionContext(root=root, tree=build_file_tree(root)))
        spark = facts.spark
        assert spark is not None
        assert spark.build_command == "./build/mvn -DskipTests clean package"
        assert spark.build_command_ref.start_line == 4
        assert spark.java_version == "17" and spark.version == "9.9.9-SNAPSHOT"
        assert spark.compiler_heap_gb == 4
        assert spark.quickstart.code == "spark.range(1000).count()"

    def test_a_python_package_with_its_readme_example(self, tmp_path: Path) -> None:
        root = tmp_path / "lib"
        (root / "src/tinylib").mkdir(parents=True)
        (root / "src/tinylib/__init__.py").write_text("def greet(): return 'hi'\n")
        (root / "pyproject.toml").write_text(
            '[project]\nname = "tinylib"\nrequires-python = ">=3.11"\n'
        )
        (root / "README.md").write_text("```python\nimport tinylib\nprint(tinylib.greet())\n```\n")
        facts = gather_facts(DetectionContext(root=root, tree=build_file_tree(root)))
        assert facts.spark is None
        package = facts.python
        assert package.import_name == "tinylib" and package.package_path == "src/tinylib"
        assert package.snippets[0].code.startswith("import tinylib")
        assert python_image(package.requires_python) == (
            "python:3.11-slim",
            'requires-python is ">=3.11"',
        )

    def test_no_bin_spark_submit_no_spark_runtime(self, tmp_path: Path) -> None:
        root = tmp_path / "job"
        root.mkdir()
        (root / "pom.xml").write_text("<project/>")
        assert gather_facts(DetectionContext(root=root, tree=build_file_tree(root))).spark is None
