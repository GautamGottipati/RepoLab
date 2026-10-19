"""Build plans.

A plan is deterministic and inspectable: it can be shown to a user before anything
executes, and it contains only commands RepoLab chose -- never repository code.
"""

from __future__ import annotations

import pytest

from repolab.builds import BuildPlan, UnsupportedRepositoryError, plan_for, spark_plan
from repolab.builds.spark import SMOKE_MARKER, SUBSTITUTE_PYSPARK
from repolab.classify import classify_deterministic
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import DetectionContext, detect_all
from repolab.ingest.languages import detect_languages
from repolab.ingest.tree import build_file_tree
from repolab.manifest.builder import build_manifest
from repolab.manifest.models import RepoManifest
from tests.test_detectors import CLI_REPO, SPARK_REPO

REPO = "https://github.com/AlexIoannides/pyspark-example-project"


def manifest_for(root, files_note: str = "") -> RepoManifest:
    tree = build_file_tree(root)
    context = DetectionContext(root=root, tree=tree, commit_sha="e" * 40)
    outcome = detect_all(context, ReadBudget())
    classification = classify_deterministic(
        outcome.run, entrypoints=outcome.entrypoints, services=outcome.services
    )
    return build_manifest(
        repo_url=REPO,
        commit_sha="e" * 40,
        outcome=outcome,
        languages=detect_languages(tree),
        classification=classification,
    )


class TestPlanSelection:
    def test_a_spark_repository_gets_the_spark_plan(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        plan = plan_for(manifest_for(root))
        assert isinstance(plan, BuildPlan)
        assert "spark" in plan.base_image.lower()

    def test_an_unsupported_repository_says_so(self, make_repo) -> None:
        """Only the Spark shape is supported so far, and RepoLab admits it."""
        root, _ = make_repo(CLI_REPO)
        with pytest.raises(UnsupportedRepositoryError, match="no build plan"):
            plan_for(manifest_for(root))

    def test_the_error_names_the_surface_it_saw(self, make_repo) -> None:
        root, _ = make_repo(CLI_REPO)
        with pytest.raises(UnsupportedRepositoryError, match="cli"):
            plan_for(manifest_for(root))


class TestSparkPlan:
    @pytest.fixture
    def plan(self, make_repo) -> BuildPlan:
        root, _ = make_repo(SPARK_REPO)
        return spark_plan(manifest_for(root))

    def test_it_installs_spark(self, plan: BuildPlan) -> None:
        commands = " ".join(step.command for step in plan.steps)
        assert f"pyspark=={SUBSTITUTE_PYSPARK}" in commands

    def test_it_checks_the_toolchain_first(self, plan: BuildPlan) -> None:
        assert "java" in plan.steps[0].command

    def test_it_records_the_versions_it_installed(self, plan: BuildPlan) -> None:
        """So an experiment can cite what it actually measured."""
        assert any(step.name == "record versions" for step in plan.steps)

    def test_the_smoke_test_runs_a_real_spark_job(self, plan: BuildPlan) -> None:
        assert "repolab_smoke.py" in plan.smoke.command
        assert plan.smoke.expect_stdout_contains == SMOKE_MARKER

    def test_the_smoke_test_checks_more_than_the_exit_code(self, plan: BuildPlan) -> None:
        """A silent success is not proof the job ran."""
        assert plan.smoke.expect_exit_code == 0
        assert plan.smoke.expect_stdout_contains

    def test_extraction_uses_tar_not_the_repository_tooling(self, plan: BuildPlan) -> None:
        extract = plan.extract_step
        assert "tar -xzf" in extract.command
        assert plan.archive_path in extract.command

    def test_the_steps_run_in_a_deliberate_order(self, plan: BuildPlan) -> None:
        names = [step.name for step in plan.all_steps]
        assert names[0] == "prepare the workspace"
        assert names[1] == "extract repository"
        assert names.index("install pyspark") < names.index("write the smoke job")

    def test_every_step_has_a_timeout(self, plan: BuildPlan) -> None:
        assert all(step.timeout_seconds > 0 for step in plan.all_steps)

    def test_it_declares_its_capabilities(self, plan: BuildPlan) -> None:
        assert any("pyspark" in item for item in plan.capabilities)

    def test_a_plan_can_be_shown_before_running(self, plan: BuildPlan) -> None:
        described = plan.describe()
        assert plan.base_image in described
        assert "smoke test" in described

    def test_the_base_image_is_overridable(self, make_repo) -> None:
        """A prepared image avoids depending on network egress inside the sandbox."""
        root, _ = make_repo(SPARK_REPO)
        plan = spark_plan(manifest_for(root), base_image="docker://my/prepared:1")
        assert plan.base_image == "docker://my/prepared:1"


class TestPinDisclosure:
    def test_a_usable_pin_produces_no_deviation(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)  # pins pyspark>=3.5
        assert spark_plan(manifest_for(root)).deviations == []

    def test_an_unusable_pin_is_disclosed(self, make_repo) -> None:
        root, _ = make_repo({**SPARK_REPO, "requirements.txt": "pyspark==2.4.0\npandas==2.1.0\n"})
        plan = spark_plan(manifest_for(root))

        assert any("pyspark 2.4.0" in item for item in plan.deviations)
        assert any("python 3.6" in item for item in plan.deviations)

    def test_the_disclosure_explains_why(self, make_repo) -> None:
        root, _ = make_repo({**SPARK_REPO, "requirements.txt": "pyspark==2.4.0\n"})
        plan = spark_plan(manifest_for(root))
        assert any("requires Python 3.6" in item for item in plan.deviations)

    def test_a_limitation_is_recorded_alongside(self, make_repo) -> None:
        root, _ = make_repo({**SPARK_REPO, "requirements.txt": "pyspark==2.4.0\n"})
        plan = spark_plan(manifest_for(root))
        assert any("not the dependency versions" in item for item in plan.limitations)

    def test_the_raw_specifier_is_not_echoed(self, make_repo) -> None:
        """'pyspark ==2.4.0' would read as a typo; the pin is rendered cleanly."""
        root, _ = make_repo({**SPARK_REPO, "requirements.txt": "pyspark==2.4.0\n"})
        plan = spark_plan(manifest_for(root))
        assert not any("==2.4.0" in item for item in plan.deviations)
