"""Call accounting and the logging contract."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from repolab.llm import (
    ModelRouter,
    ModelTier,
    Purpose,
    SchemaValidationError,
)
from repolab.llm.telemetry import CallRecord, ModelPrice, ModelPricing
from repolab.testing.fakes import FakeLLMProvider, FakeMessage

SECRET_PROMPT = "our internal repository is at github.com/acme/very-private"
API_KEY = "sk-super-secret-key-material"

PRICING_JSON = json.dumps(
    {"vendor/nano": {"input": 0.10, "output": 0.40}},
)


class Answer(BaseModel):
    status: str


def router(
    *, pricing: ModelPricing | None = None, message: object | None = None, **kwargs: Any
) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(message=message or FakeMessage(content='{"status": "ok"}'), **kwargs)
    return (
        ModelRouter(provider, {ModelTier.NANO: "vendor/nano"}, pricing=pricing),
        provider,
    )


class TestRecordContents:
    async def test_successful_call_is_fully_described(self) -> None:
        routed, _ = router()
        record = (
            await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")
        ).record

        assert record.tier is ModelTier.NANO
        assert record.model == "vendor/nano"
        assert record.purpose == "file_summary"
        assert record.schema_name == "Answer"
        assert record.success is True
        assert record.retries == 0
        assert record.error_type is None
        assert record.latency_ms >= 0

    async def test_token_usage_is_recorded(self) -> None:
        routed, _ = router(prompt_tokens=123, completion_tokens=45)
        record = (
            await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")
        ).record
        assert record.prompt_tokens == 123
        assert record.completion_tokens == 45

    async def test_failed_call_is_logged_as_unsuccessful(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        routed, _ = router(message=FakeMessage(content="never valid"))
        with pytest.raises(SchemaValidationError):
            await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")

        event = next(e for e in capture_logs if e["event"] == "llm.call")
        assert event["success"] is False
        assert event["error_type"] == "SchemaValidationError"
        assert event["retries"] == 1


class TestCostEstimation:
    async def test_cost_is_unknown_without_pricing(self) -> None:
        """RepoLab never invents a price."""
        routed, _ = router()
        completion = await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")
        assert completion.record.estimated_cost_usd is None

    async def test_cost_is_computed_when_pricing_is_configured(self) -> None:
        routed, _ = router(
            pricing=ModelPricing.from_json(PRICING_JSON),
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
        )
        completion = await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")
        # 1M input at $0.10/M + 0.5M output at $0.40/M
        assert completion.record.estimated_cost_usd == pytest.approx(0.10 + 0.20)

    def test_unknown_model_has_no_price(self) -> None:
        pricing = ModelPricing.from_json(PRICING_JSON)
        assert pricing.estimate("vendor/unknown", 100, 100) is None

    def test_missing_token_counts_yield_no_estimate(self) -> None:
        pricing = ModelPricing.from_json(PRICING_JSON)
        assert pricing.estimate("vendor/nano", None, 100) is None

    @pytest.mark.parametrize(
        "raw", [None, "", "   ", "not json", "[1,2]", '{"m": {"input": "free"}}']
    )
    def test_malformed_pricing_degrades_to_empty(self, raw: str | None) -> None:
        """Bad pricing config must never break inference."""
        assert ModelPricing.from_json(raw).prices == {}

    def test_negative_prices_are_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            ModelPrice(input=-1.0, output=0.0)


class TestLoggingContract:
    """Prompts, output, reasoning and credentials must never be logged."""

    async def test_one_event_per_call(self, capture_logs: list[dict[str, Any]]) -> None:
        routed, _ = router()
        await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")
        assert [e["event"] for e in capture_logs] == ["llm.call"]

    async def test_prompt_text_is_not_logged(self, capture_logs: list[dict[str, Any]]) -> None:
        routed, _ = router()
        await routed.nano(
            purpose=Purpose.FILE_SUMMARY,
            schema=Answer,
            prompt=SECRET_PROMPT,
            system=f"key={API_KEY}",
        )
        serialized = repr(capture_logs)
        assert SECRET_PROMPT not in serialized
        assert API_KEY not in serialized
        assert "very-private" not in serialized

    async def test_model_output_is_not_logged(self, capture_logs: list[dict[str, Any]]) -> None:
        secret_output = '{"status": "leaked-content-marker"}'
        routed, _ = router(message=FakeMessage(content=secret_output))
        await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")
        assert "leaked-content-marker" not in repr(capture_logs)

    async def test_the_event_carries_only_metadata(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        routed, _ = router()
        await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt=SECRET_PROMPT)
        event = next(e for e in capture_logs if e["event"] == "llm.call")

        allowed = set(CallRecord.model_fields) | {"event", "log_level"}
        assert set(event) <= allowed
        for forbidden in ("messages", "prompt", "system", "content", "text", "response"):
            assert forbidden not in event

    async def test_response_shape_is_recorded_for_diagnosis(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        """Metadata still has to be useful: the shape fields answer Phase 0's question."""
        routed, _ = router(message=FakeMessage(content=None, output_text='{"status": "ok"}'))
        await routed.nano(purpose=Purpose.FILE_SUMMARY, schema=Answer, prompt="go")
        event = next(e for e in capture_logs if e["event"] == "llm.call")
        assert event["content_populated"] is False
        assert event["primary_text_field"] == "output_text"
