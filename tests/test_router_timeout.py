"""Timeout and transport error handling.

CLAUDE.md requires explicit execution timeouts and clear errors. The router must
never hang, and provider failures must arrive as RepoLab error types rather than
vendor exceptions leaking upward.
"""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from repolab.llm import (
    LLMError,
    LLMTimeoutError,
    LLMTransportError,
    ModelRouter,
    ModelTier,
    Purpose,
)
from repolab.testing.fakes import FakeLLMProvider, FakeMessage


class Answer(BaseModel):
    status: str


VALID = '{"status": "ok"}'


def router(**kwargs: object) -> tuple[ModelRouter, FakeLLMProvider]:
    timeout = kwargs.pop("timeout_seconds", 60.0)
    provider = FakeLLMProvider(message=FakeMessage(content=VALID), **kwargs)  # type: ignore[arg-type]
    return (
        ModelRouter(
            provider,
            {ModelTier.NANO: "vendor/nano"},
            timeout_seconds=timeout,  # type: ignore[arg-type]
        ),
        provider,
    )


class TestTimeouts:
    async def test_a_slow_call_times_out(self) -> None:
        routed, _ = router(delay_seconds=1.0, timeout_seconds=0.05)
        with pytest.raises(LLMTimeoutError) as excinfo:
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert excinfo.value.timeout_seconds == 0.05
        assert "vendor/nano" in str(excinfo.value)

    async def test_the_timeout_is_actually_enforced(self) -> None:
        """A hung provider must not outlive the budget."""
        routed, _ = router(delay_seconds=5.0, timeout_seconds=0.05)
        started = time.perf_counter()
        with pytest.raises(LLMTimeoutError):
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert time.perf_counter() - started < 1.0

    async def test_a_timeout_is_not_retried(self, capture_logs: list[dict]) -> None:
        """Retrying a hung call would double the budget, so it is raised at once."""
        routed, _ = router(delay_seconds=1.0, timeout_seconds=0.05)
        with pytest.raises(LLMTimeoutError):
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        event = next(e for e in capture_logs if e["event"] == "llm.call")
        assert event["retries"] == 0

    async def test_a_per_call_budget_overrides_the_default(self) -> None:
        routed, _ = router(delay_seconds=1.0, timeout_seconds=60.0)
        with pytest.raises(LLMTimeoutError) as excinfo:
            await routed.nano(
                purpose=Purpose.QUICK_ANSWER,
                schema=Answer,
                prompt="go",
                timeout_seconds=0.05,
            )
        assert excinfo.value.timeout_seconds == 0.05

    async def test_the_budget_is_passed_to_the_provider_as_well(self) -> None:
        routed, provider = router(timeout_seconds=12.5)
        await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert provider.calls[0]["timeout"] == 12.5

    async def test_a_timeout_is_logged_as_a_failure(self, capture_logs: list[dict]) -> None:
        routed, _ = router(delay_seconds=1.0, timeout_seconds=0.05)
        with pytest.raises(LLMTimeoutError):
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        event = next(e for e in capture_logs if e["event"] == "llm.call")
        assert event["success"] is False
        assert event["error_type"] == "LLMTimeoutError"


class TestTransportErrors:
    async def test_provider_failure_becomes_a_transport_error(self) -> None:
        routed, _ = router(fail_with=RuntimeError("503 service unavailable"))
        with pytest.raises(LLMTransportError) as excinfo:
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert "503 service unavailable" in str(excinfo.value)
        assert isinstance(excinfo.value.cause, RuntimeError)

    async def test_a_first_attempt_failure_is_retried(self) -> None:
        """The failure may be the service rejecting strict json_schema."""
        routed, provider = router(
            fail_with=RuntimeError("400 response_format unsupported"), fail_times=1
        )
        completion = await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert completion.value.status == "ok"
        assert completion.retries == 1
        assert provider.calls[1]["response_format"] == {"type": "json_object"}

    async def test_a_persistent_failure_raises_after_one_retry(self) -> None:
        routed, provider = router(fail_with=RuntimeError("401 unauthorized"))
        with pytest.raises(LLMTransportError):
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert provider.call_count == 2

    async def test_multiline_errors_are_reduced_to_one_line(self) -> None:
        routed, _ = router(fail_with=RuntimeError("first line\nsecond line"))
        with pytest.raises(LLMTransportError) as excinfo:
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert "second line" not in str(excinfo.value)


class TestErrorHierarchy:
    @pytest.mark.parametrize("error", [LLMTimeoutError, LLMTransportError])
    def test_every_error_is_an_llm_error(self, error: type) -> None:
        """Callers can catch one base class."""
        assert issubclass(error, LLMError)
