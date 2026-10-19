"""Build model invariants.

Two properties are encoded in the types rather than trusted to callers: a build is
ready only once its smoke test passes, and fidelity is derived from the kind of
build and whether anything had to be substituted.
"""

from __future__ import annotations

from repolab.builds import (
    BuildArtifact,
    BuildAttempt,
    BuildCapabilities,
    BuildRequest,
    BuildSource,
    BuildStatus,
    SmokeTestResult,
    StepOutcome,
    fidelity_for,
)
from repolab.manifest.models import BuildKind, Fidelity

REPO = "https://github.com/AlexIoannides/pyspark-example-project"
SHA = "eeee0c2b9af7" + "0" * 28


def passing_smoke() -> SmokeTestResult:
    return SmokeTestResult(
        name="a tiny Spark job completes",
        command="python3 /work/repolab_smoke.py",
        passed=True,
        exit_code=0,
        stdout="REPOLAB_SPARK_OK 3.5.1\n",
    )


def artifact(**overrides) -> BuildArtifact:
    defaults = {"repo_url": REPO, "commit_sha": SHA}
    return BuildArtifact(**{**defaults, **overrides})


class TestReadinessRequiresASmokeTest:
    def test_a_fresh_artifact_is_not_ready(self) -> None:
        assert artifact().ready is False

    def test_a_checkpoint_alone_is_not_enough(self) -> None:
        """A built environment nobody verified is not a usable one."""
        assert artifact(checkpoint_id="ckpt-1").ready is False

    def test_a_passing_smoke_alone_is_not_enough(self) -> None:
        """Without a checkpoint there is nothing to hand to an experiment."""
        assert artifact(smoke=passing_smoke()).ready is False

    def test_both_together_make_it_ready(self) -> None:
        assert artifact(checkpoint_id="ckpt-1", smoke=passing_smoke()).ready is True

    def test_a_failing_smoke_keeps_it_unready(self) -> None:
        failed = SmokeTestResult.failure("smoke", "cmd", "exit 1", exit_code=1)
        assert artifact(checkpoint_id="ckpt-1", smoke=failed).ready is False

    def test_readiness_cannot_be_set_directly(self) -> None:
        """`ready` is computed, so it cannot be claimed without the evidence."""
        built = BuildArtifact(repo_url=REPO, commit_sha=SHA, ready=True)  # type: ignore[call-arg]
        assert built.ready is False

    def test_status_reflects_readiness(self) -> None:
        assert artifact().status is BuildStatus.PENDING
        assert artifact(attempts=[BuildAttempt()]).status is BuildStatus.FAILED
        assert artifact(checkpoint_id="c", smoke=passing_smoke()).status is BuildStatus.READY


class TestFidelityIsDerived:
    def test_no_deviations_means_a_source_build_is_exact(self) -> None:
        assert BuildCapabilities().is_exact is True
        assert fidelity_for(BuildKind.SOURCE_BUILD) is Fidelity.EXACT

    def test_any_deviation_lowers_fidelity(self) -> None:
        """A substituted version means this is not an exact reproduction."""
        capabilities = BuildCapabilities(deviations=["pyspark 2.4.0 -> 3.5.1"])
        assert capabilities.is_exact is False
        assert fidelity_for(BuildKind.SOURCE_BUILD, capabilities.deviations) is Fidelity.HIGH

    def test_capabilities_alone_do_not_decide_fidelity(self) -> None:
        """An official release has no deviations of its own and is still not exact.

        Which is why the decision moved onto the artifact, where the kind is
        known: `is_exact` is necessary for EXACT fidelity, never sufficient.
        """
        assert BuildCapabilities().is_exact is True
        assert fidelity_for(BuildKind.OFFICIAL_RELEASE) is Fidelity.HIGH

    def test_the_artifact_reports_the_derived_fidelity(self) -> None:
        built = artifact(
            capabilities=BuildCapabilities(deviations=["python 3.6 -> 3.11"]),
            checkpoint_id="c",
            smoke=passing_smoke(),
        )
        assert built.fidelity is Fidelity.HIGH

    def test_versions_are_recorded_for_citation(self) -> None:
        capabilities = BuildCapabilities(versions={"pyspark": "3.5.1", "python": "3.11.9"})
        assert capabilities.versions["pyspark"] == "3.5.1"


class TestCacheAndSource:
    def test_a_source_build_is_not_from_cache(self) -> None:
        assert artifact().from_cache is False

    def test_a_cached_artifact_says_so(self) -> None:
        cached = artifact(cached=True)
        assert cached.from_cache is True
        assert cached.source is BuildSource.CACHE

    def test_source_cannot_contradict_the_kind(self) -> None:
        """`source` is derived, so "official release built from source" is unrepresentable."""
        from repolab.manifest.models import BuildKind

        assert artifact(kind=BuildKind.OFFICIAL_RELEASE).source is BuildSource.OFFICIAL_RELEASE
        assert artifact(kind=BuildKind.MINI_LAB).source is BuildSource.MINI_LAB

    def test_cached_artifacts_keep_their_fidelity(self) -> None:
        """Caching is not a fidelity level: a cached build is a real build."""
        cached = artifact(
            cached=True,
            checkpoint_id="c",
            smoke=passing_smoke(),
        )
        assert cached.fidelity is Fidelity.EXACT

    def test_the_cache_key_is_repo_and_commit(self) -> None:
        assert artifact().cache_key == (REPO, SHA)

    def test_a_ready_build_supports_experiments(self) -> None:
        assert artifact(checkpoint_id="c", smoke=passing_smoke()).supports_real_experiments
        assert artifact().supports_real_experiments is False


class TestAttempts:
    def test_a_clean_attempt_succeeded(self) -> None:
        attempt = BuildAttempt(steps=[StepOutcome(name="a", command="true", exit_code=0)])
        assert attempt.succeeded is True
        assert attempt.failed_step is None

    def test_a_failed_step_is_identified(self) -> None:
        attempt = BuildAttempt(
            steps=[
                StepOutcome(name="ok", command="true", exit_code=0),
                StepOutcome(name="bad", command="false", exit_code=1, ok=False),
            ]
        )
        assert attempt.succeeded is False
        assert attempt.failed_step is not None
        assert attempt.failed_step.name == "bad"

    def test_an_error_makes_an_attempt_unsuccessful(self) -> None:
        attempt = BuildAttempt()
        attempt.finish(error="RuntimeError: no network")
        assert attempt.succeeded is False
        assert attempt.duration_seconds is not None

    def test_a_long_log_tail_is_bounded(self) -> None:
        from repolab.builds.models import MAX_LOG_TAIL

        step = StepOutcome(name="noisy", command="x", output_tail="y" * 50_000)
        assert len(step.output_tail) == MAX_LOG_TAIL

    def test_the_tail_keeps_the_end_where_errors_are(self) -> None:
        step = StepOutcome(name="noisy", command="x", output_tail="a" * 10_000 + "ERROR HERE")
        assert step.output_tail.endswith("ERROR HERE")


class TestBuildRequest:
    def test_the_checkpoint_tag_identifies_the_commit(self) -> None:
        request = BuildRequest(repo_url=REPO, commit_sha=SHA)
        tag = request.checkpoint_tag

        assert tag.startswith("repolab/")
        assert SHA[:12] in tag

    def test_the_tag_differs_per_commit(self) -> None:
        first = BuildRequest(repo_url=REPO, commit_sha="a" * 40).checkpoint_tag
        second = BuildRequest(repo_url=REPO, commit_sha="b" * 40).checkpoint_tag
        assert first != second

    def test_the_tag_differs_per_repository(self) -> None:
        first = BuildRequest(repo_url=REPO, commit_sha=SHA).checkpoint_tag
        second = BuildRequest(
            repo_url="https://github.com/apache/spark", commit_sha=SHA
        ).checkpoint_tag
        assert first != second


class TestSummary:
    def test_a_ready_build_reads_as_ready(self) -> None:
        built = artifact(checkpoint_id="c", smoke=passing_smoke(), provider="fake")
        summary = built.summary()
        assert summary.startswith("READY")
        assert "fake" in summary

    def test_an_unready_build_does_not_read_as_ready(self) -> None:
        assert "READY" not in artifact().summary()
