"""What happens when a build candidate fails.

The order in CLAUDE.md matters: logs first and unconditionally, then a summary,
then a bounded retry, then falling through. A model being unavailable or wrong
must not cost a reader the raw output, which is why everything after the logs is
an addition rather than a replacement.
"""

from __future__ import annotations

import json

from repolab.builds import (
    BuildAttempt,
    BuildLimits,
    SmokeTestResult,
    StepOutcome,
    describe_failure,
    diagnose,
    summarise,
)
from repolab.builds.diagnosis import failing_output
from repolab.builds.plan import BuildPlan, SmokeTest
from repolab.llm import ModelRouter, ModelTier
from repolab.testing.fakes import FakeLLMProvider, FakeMessage


def a_plan() -> BuildPlan:
    """A minimal plan; diagnosis only reads its base image and the failure."""
    return BuildPlan(base_image="eclipse-temurin:17-jdk", smoke=SmokeTest(name="s", command="c"))


def failed_step_attempt(output: str = "ivy: could not resolve guava") -> BuildAttempt:
    attempt = BuildAttempt(attempt=1, provider="fake")
    attempt.steps = [
        StepOutcome(name="extract repository", command="tar -xzf", exit_code=0, ok=True),
        StepOutcome(
            name="build the jars", command="ant jar", exit_code=1, ok=False, output_tail=output
        ),
    ]
    return attempt


def failed_smoke_attempt() -> BuildAttempt:
    attempt = BuildAttempt(attempt=1, provider="fake")
    attempt.steps = [StepOutcome(name="start", command="start", exit_code=0, ok=True)]
    attempt.smoke = SmokeTestResult.failure(
        "cqlsh connects", "cqlsh -e 'SELECT'", "connection refused", stderr="Connection refused"
    )
    return attempt


class TestLogsComeFirst:
    def test_the_failing_step_output_is_what_is_kept(self) -> None:
        assert "ivy" in failing_output(failed_step_attempt())

    def test_a_failed_smoke_test_contributes_its_own_output(self) -> None:
        """The relevant log, not the build's first step."""
        assert "Connection refused" in failing_output(failed_smoke_attempt())

    def test_a_crash_contributes_its_message(self) -> None:
        attempt = BuildAttempt(attempt=1, provider="fake")
        attempt.finish(error="SandboxError: out of memory")
        assert "out of memory" in failing_output(attempt)

    def test_the_log_is_bounded(self) -> None:
        """A runaway build log must not become an expensive prompt."""
        from repolab.builds.diagnosis import MAX_LOG_CHARS

        assert len(failing_output(failed_step_attempt("x" * 50_000))) <= MAX_LOG_CHARS


class TestTheDeterministicReason:
    def test_a_failed_step_names_the_step(self) -> None:
        assert "build the jars" in describe_failure(failed_step_attempt())

    def test_a_failed_smoke_test_says_so(self) -> None:
        assert "smoke test failed" in describe_failure(failed_smoke_attempt())

    def test_a_crash_reports_its_error(self) -> None:
        attempt = BuildAttempt(attempt=1, provider="fake")
        attempt.finish(error="SandboxError: boom")
        assert describe_failure(attempt) == "SandboxError: boom"


class TestSummarising:
    async def test_nano_writes_the_summary_when_available(self) -> None:
        provider = FakeLLMProvider(
            message=FakeMessage(
                content=json.dumps(
                    {
                        "headline": "Dependency resolution failed",
                        "likely_cause": "Ivy could not reach the repository",
                    }
                )
            )
        )
        summary = await summarise(
            failed_step_attempt(),
            router=ModelRouter(provider, {ModelTier.NANO: "fake/nano"}),
        )

        assert summary.headline == "Dependency resolution failed"
        assert summary.produced_by == "nano"

    async def test_the_raw_log_is_kept_alongside_the_summary(self) -> None:
        """So a reader can check the summary rather than take it on trust."""
        provider = FakeLLMProvider(
            message=FakeMessage(content=json.dumps({"headline": "h", "likely_cause": "c"}))
        )
        summary = await summarise(
            failed_step_attempt(), router=ModelRouter(provider, {ModelTier.NANO: "fake/nano"})
        )
        assert "ivy" in summary.log_excerpt

    async def test_without_a_model_it_still_explains_the_failure(self) -> None:
        """A model being unavailable is not a reason to stop telling anyone."""
        summary = await summarise(failed_step_attempt())

        assert summary.produced_by == "deterministic"
        assert "build the jars" in summary.headline
        assert "ivy" in summary.log_excerpt

    async def test_a_model_failure_falls_back_to_the_deterministic_reason(self) -> None:
        provider = FakeLLMProvider(fail_with=RuntimeError("upstream is down"))
        summary = await summarise(
            failed_step_attempt(), router=ModelRouter(provider, {ModelTier.NANO: "fake/nano"})
        )
        assert summary.produced_by == "deterministic"


class TestDiagnosing:
    async def test_without_a_model_a_retry_is_never_recommended(self) -> None:
        """RepoLab does not spend minutes on a guess."""
        result = await diagnose(failed_step_attempt(), a_plan())

        assert result.retry_worthwhile is False
        assert result.produced_by == "deterministic"

    async def test_super_decides_when_available(self) -> None:
        provider = FakeLLMProvider(
            message=FakeMessage(
                content=json.dumps(
                    {
                        "cause": "a transient network failure",
                        "retry_worthwhile": True,
                        "suggestion": "retry the dependency resolution",
                        "produced_by": "super",
                    }
                )
            )
        )
        result = await diagnose(
            failed_step_attempt(),
            a_plan(),
            router=ModelRouter(provider, {ModelTier.SUPER: "fake/super"}),
        )

        assert result.retry_worthwhile is True
        assert "transient" in result.cause

    async def test_a_model_failure_does_not_recommend_a_retry(self) -> None:
        provider = FakeLLMProvider(fail_with=RuntimeError("upstream is down"))
        result = await diagnose(
            failed_step_attempt(),
            a_plan(),
            router=ModelRouter(provider, {ModelTier.SUPER: "fake/super"}),
        )
        assert result.retry_worthwhile is False


class TestLimits:
    def test_the_defaults_are_conservative(self) -> None:
        """One retry recovers a diagnosable mistake; more is a candidate that
        should fall through rather than keep spending."""
        limits = BuildLimits()
        assert limits.max_attempts == 2
        assert limits.max_concurrent >= 1
        assert limits.candidate_timeout_seconds > 0
