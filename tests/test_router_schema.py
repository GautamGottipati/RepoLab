"""Structured output: schema hardening, tolerant parsing, and the single retry."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from repolab.llm import ModelRouter, ModelTier, Purpose, SchemaValidationError
from repolab.llm.parsing import extract_json_object, strip_code_fences
from repolab.llm.schema import (
    JSON_OBJECT_RESPONSE_FORMAT,
    describe_schema_for_prompt,
    response_format_for,
    to_strict_json_schema,
)
from repolab.testing.fakes import FakeLLMProvider, FakeMessage


class Nested(BaseModel):
    label: str
    weight: float | None = None


class Answer(BaseModel):
    status: str
    count: int = 0
    nested: Nested | None = None


def router(*messages: object) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(messages=list(messages))
    return ModelRouter(provider, {ModelTier.NANO: "vendor/nano"}), provider


async def ask(routed: ModelRouter) -> Answer:
    completion = await routed.nano(purpose=Purpose.SMOKE_TEST, schema=Answer, prompt="go")
    return completion.value


class TestStrictSchemaHardening:
    """Pydantic's schema is not strict-mode ready; the router must harden it."""

    def test_every_property_becomes_required(self) -> None:
        schema = to_strict_json_schema(Answer)
        assert set(schema["required"]) == {"status", "count", "nested"}

    def test_additional_properties_are_forbidden(self) -> None:
        assert to_strict_json_schema(Answer)["additionalProperties"] is False

    def test_nested_definitions_are_hardened_too(self) -> None:
        schema = to_strict_json_schema(Answer)
        nested = schema["$defs"]["Nested"]
        assert nested["additionalProperties"] is False
        assert set(nested["required"]) == {"label", "weight"}

    def test_pydantic_alone_would_not_satisfy_strict_mode(self) -> None:
        """Documents why this module exists."""
        raw = Answer.model_json_schema()
        assert "additionalProperties" not in raw
        assert set(raw["required"]) != {"status", "count", "nested"}

    def test_response_format_shape(self) -> None:
        response_format = response_format_for(Answer)
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "Answer"
        assert response_format["json_schema"]["strict"] is True

    def test_prompt_description_is_compact_json(self) -> None:
        described = describe_schema_for_prompt(Answer)
        assert described.startswith("{")
        assert ", " not in described


class TestTolerantParsing:
    def test_plain_object(self) -> None:
        assert extract_json_object('{"status": "ok"}') == {"status": "ok"}

    def test_fenced_json(self) -> None:
        assert extract_json_object('```json\n{"status": "ok"}\n```') == {"status": "ok"}

    def test_unlabelled_fence(self) -> None:
        assert extract_json_object('```\n{"status": "ok"}\n```') == {"status": "ok"}

    def test_object_wrapped_in_prose(self) -> None:
        text = 'Sure! Here is the result:\n{"status": "ok"}\nLet me know if that helps.'
        assert extract_json_object(text) == {"status": "ok"}

    def test_braces_inside_strings_do_not_confuse_the_scanner(self) -> None:
        assert extract_json_object('{"status": "a}b{c"}') == {"status": "a}b{c"}

    def test_escaped_quote_inside_string(self) -> None:
        assert extract_json_object('{"status": "say \\"hi\\""}') == {"status": 'say "hi"'}

    def test_nested_objects(self) -> None:
        parsed = extract_json_object('prefix {"a": {"b": {"c": 1}}} suffix')
        assert parsed == {"a": {"b": {"c": 1}}}

    @pytest.mark.parametrize("text", [None, "", "   ", "no json here", "[1, 2, 3]", "{unclosed"])
    def test_unparseable_input_returns_none(self, text: str | None) -> None:
        assert extract_json_object(text) is None

    def test_a_json_array_is_not_an_object(self) -> None:
        """The router always requests an object, so a bare array is a failure."""
        assert extract_json_object('[{"status": "ok"}]') is None

    def test_strip_code_fences_leaves_unfenced_text_alone(self) -> None:
        assert strip_code_fences('  {"a": 1}  ') == '{"a": 1}'


class TestValidationSucceeds:
    async def test_valid_json_returns_a_typed_object(self) -> None:
        routed, _ = router(FakeMessage(content='{"status": "ok", "count": 2}'))
        value = await ask(routed)
        assert isinstance(value, Answer)
        assert (value.status, value.count) == ("ok", 2)

    async def test_fenced_output_is_accepted_without_a_retry(self) -> None:
        routed, provider = router(FakeMessage(content='```json\n{"status": "ok"}\n```'))
        assert (await ask(routed)).status == "ok"
        assert provider.call_count == 1

    async def test_nested_models_validate(self) -> None:
        routed, _ = router(
            FakeMessage(content='{"status": "ok", "count": 1, "nested": {"label": "x"}}')
        )
        value = await ask(routed)
        assert value.nested is not None
        assert value.nested.label == "x"


class TestSingleRetry:
    async def test_invalid_then_valid_succeeds_with_one_retry(self) -> None:
        routed, provider = router(
            FakeMessage(content="I cannot do that."),
            FakeMessage(content='{"status": "ok"}'),
        )
        completion = await routed.nano(purpose=Purpose.SMOKE_TEST, schema=Answer, prompt="go")
        assert completion.value.status == "ok"
        assert completion.retries == 1
        assert provider.call_count == 2

    async def test_schema_violation_triggers_the_retry(self) -> None:
        routed, provider = router(
            FakeMessage(content='{"count": "not-an-int"}'),
            FakeMessage(content='{"status": "ok"}'),
        )
        assert (await ask(routed)).status == "ok"
        assert provider.call_count == 2

    async def test_the_retry_downgrades_to_json_object(self) -> None:
        """Covers models whose serving stack rejects strict json_schema."""
        routed, provider = router(
            FakeMessage(content="nope"), FakeMessage(content='{"status": "ok"}')
        )
        await ask(routed)
        assert provider.calls[0]["response_format"]["type"] == "json_schema"
        assert provider.calls[1]["response_format"] == JSON_OBJECT_RESPONSE_FORMAT

    async def test_the_retry_feeds_back_the_failure(self) -> None:
        routed, provider = router(
            FakeMessage(content="nope"), FakeMessage(content='{"status": "ok"}')
        )
        await ask(routed)
        retry_messages = provider.calls[1]["messages"]
        assert len(retry_messages) > len(provider.calls[0]["messages"])
        assert any("rejected" in m["content"] for m in retry_messages)
        assert any(m["role"] == "assistant" for m in retry_messages)

    async def test_retry_names_the_schema(self) -> None:
        routed, provider = router(
            FakeMessage(content="nope"), FakeMessage(content='{"status": "ok"}')
        )
        await ask(routed)
        assert any("Answer" in m["content"] for m in provider.calls[1]["messages"])

    async def test_it_retries_exactly_once(self) -> None:
        routed, provider = router(FakeMessage(content="never valid"))
        with pytest.raises(SchemaValidationError):
            await ask(routed)
        assert provider.call_count == 2

    async def test_exhausted_retry_reports_the_detail(self) -> None:
        routed, _ = router(FakeMessage(content='{"count": "not-an-int"}'))
        with pytest.raises(SchemaValidationError) as excinfo:
            await ask(routed)
        error = excinfo.value
        assert error.retries == 1
        assert error.schema_name == "Answer"
        assert error.errors is not None
        assert "2 attempt(s)" in str(error)

    async def test_error_excerpt_is_truncated(self) -> None:
        routed, _ = router(FakeMessage(content="x" * 5000))
        with pytest.raises(SchemaValidationError) as excinfo:
            await ask(routed)
        assert excinfo.value.excerpt is not None
        assert len(excinfo.value.excerpt) <= 500
