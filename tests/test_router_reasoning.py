"""Reasoning-model response handling.

Phase 0 established that where a reasoning model puts its output is unknown, and
that private chain-of-thought must never reach logs, events or the UI. These
tests hold the router to both.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from repolab.llm import EmptyResponseError, ModelRouter, ModelTier, Purpose
from repolab.testing.fakes import FakeLLMProvider, FakeMessage

SECRET_COT = "Let me think step by step about what the user really wants here."


class Answer(BaseModel):
    status: str


def router(*messages: object) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(messages=list(messages))
    return ModelRouter(provider, {ModelTier.ULTRA: "vendor/ultra"}), provider


async def ask(routed: ModelRouter) -> object:
    return await routed.ultra(purpose=Purpose.RESULT_INTERPRETATION, schema=Answer, prompt="go")


class TestEmptyContentIsRecoverable:
    async def test_answer_in_an_unexpected_field_still_validates(self) -> None:
        routed, _ = router(FakeMessage(content=None, output_text='{"status": "ok"}'))
        completion = await ask(routed)
        assert completion.value.status == "ok"
        assert completion.record.content_populated is False
        assert completion.record.primary_text_field == "output_text"

    async def test_content_alongside_reasoning_uses_content(self) -> None:
        routed, _ = router(FakeMessage(content='{"status": "ok"}', reasoning_content=SECRET_COT))
        completion = await ask(routed)
        assert completion.value.status == "ok"
        assert completion.record.content_populated is True
        assert completion.record.reasoning_fields == ["reasoning_content"]

    async def test_whitespace_content_falls_through_to_another_field(self) -> None:
        routed, _ = router(FakeMessage(content="   ", answer='{"status": "ok"}'))
        completion = await ask(routed)
        assert completion.value.status == "ok"


class TestReasoningOnlyResponses:
    async def test_reasoning_only_raises_empty_response(self) -> None:
        """There was nothing to validate, which is not a schema failure."""
        routed, _ = router(FakeMessage(content=None, reasoning_content=SECRET_COT))
        with pytest.raises(EmptyResponseError) as excinfo:
            await ask(routed)
        assert "reasoning_content" in str(excinfo.value)

    async def test_it_still_retries_before_giving_up(self) -> None:
        routed, provider = router(
            FakeMessage(content=None, reasoning_content=SECRET_COT),
            FakeMessage(content='{"status": "recovered"}'),
        )
        completion = await ask(routed)
        assert completion.value.status == "recovered"
        assert provider.call_count == 2

    async def test_a_fully_empty_message_raises(self) -> None:
        routed, _ = router(FakeMessage(content=None))
        with pytest.raises(EmptyResponseError):
            await ask(routed)


class TestReasoningTextNeverEscapes:
    """The hard rule: chain-of-thought must not reach logs, records or errors."""

    async def test_not_in_the_call_record(self) -> None:
        routed, _ = router(FakeMessage(content='{"status": "ok"}', reasoning_content=SECRET_COT))
        completion = await ask(routed)
        assert SECRET_COT not in completion.record.model_dump_json()

    async def test_not_in_the_emitted_log_event(self, capture_logs: list[dict]) -> None:
        routed, _ = router(FakeMessage(content='{"status": "ok"}', reasoning_content=SECRET_COT))
        await ask(routed)
        serialized = repr(capture_logs)
        assert SECRET_COT not in serialized
        assert "step by step" not in serialized

    async def test_not_in_the_empty_response_error(self) -> None:
        routed, _ = router(FakeMessage(content=None, reasoning_content=SECRET_COT))
        with pytest.raises(EmptyResponseError) as excinfo:
            await ask(routed)
        assert SECRET_COT not in str(excinfo.value)

    async def test_only_the_field_name_and_size_are_kept(self) -> None:
        routed, _ = router(FakeMessage(content='{"status": "ok"}', reasoning_content=SECRET_COT))
        completion = await ask(routed)
        assert completion.record.reasoning_fields == ["reasoning_content"]
