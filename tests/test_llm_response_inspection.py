"""The response inspector is what lets the model router survive reasoning models.

Two properties matter and are both asserted here: it finds the answer even when
``content`` is empty, and it never carries model reasoning text anywhere.
"""

from __future__ import annotations

from repolab.llm.response import (
    MessageShape,
    extract_text,
    inspect_message,
    looks_like_reasoning,
)
from repolab.testing.fakes import FakeMessage, FakeToolCall

SECRET_COT = "First I will consider the user's intent, then I will answer."


class TestContentOnly:
    def test_reports_content_populated(self) -> None:
        shape = inspect_message(FakeMessage(content="ready"))
        assert shape.content_populated is True
        assert shape.content_length == len("ready")
        assert shape.primary_text_field == "content"
        assert shape.reasoning_fields == []

    def test_extracts_the_text(self) -> None:
        message = FakeMessage(content="ready")
        assert extract_text(message) == "ready"


class TestEmptyContentWithReasoning:
    """The case that breaks naive routers: content empty, output elsewhere."""

    def test_reasoning_field_is_detected_but_not_used_as_the_answer(self) -> None:
        message = FakeMessage(content=None, reasoning_content=SECRET_COT)
        shape = inspect_message(message)

        assert shape.content_populated is False
        assert "reasoning_content" in shape.reasoning_fields
        # A reasoning field must never be offered as the answer.
        assert shape.primary_text_field != "reasoning_content"
        assert extract_text(message, shape) is None

    def test_answer_in_an_unexpected_field_is_still_found(self) -> None:
        """Discovery is name-agnostic, so an unknown carrier is not missed."""
        message = FakeMessage(content="", output_text="ready")
        shape = inspect_message(message)

        assert shape.content_populated is False
        assert shape.primary_text_field == "output_text"
        assert extract_text(message, shape) == "ready"

    def test_largest_non_reasoning_field_wins(self) -> None:
        message = FakeMessage(content=None, reasoning_content=SECRET_COT, answer="a longer answer")
        shape = inspect_message(message)
        assert shape.primary_text_field == "answer"


class TestNeverLeaksReasoning:
    def test_no_field_value_appears_anywhere_in_the_shape(self) -> None:
        message = FakeMessage(content=None, reasoning_content=SECRET_COT)
        shape = inspect_message(message)

        serialized = shape.model_dump_json()
        assert SECRET_COT not in serialized
        for fragment in ("consider", "intent", "answer the user"):
            assert fragment not in serialized

    def test_only_sizes_are_recorded(self) -> None:
        shape = inspect_message(FakeMessage(content=None, reasoning_content=SECRET_COT))
        field = next(f for f in shape.populated_fields if f.name == "reasoning_content")
        assert field.length == len(SECRET_COT)
        assert field.type_name == "str"
        assert field.looks_like_reasoning is True

    def test_summary_is_user_safe(self) -> None:
        shape = inspect_message(FakeMessage(content=None, reasoning_content=SECRET_COT))
        assert SECRET_COT not in shape.summary
        assert "reasoning_content" in shape.summary


class TestEmptyAndOddShapes:
    def test_entirely_empty_message(self) -> None:
        shape = inspect_message(FakeMessage(content=None))
        assert shape.content_populated is False
        assert shape.primary_text_field is None
        assert shape.summary == "empty message"

    def test_whitespace_content_counts_as_empty(self) -> None:
        shape = inspect_message(FakeMessage(content="   \n  "))
        assert shape.content_populated is False

    def test_none_message(self) -> None:
        assert inspect_message(None) == MessageShape()
        assert extract_text(None) is None

    def test_dict_messages_are_supported(self) -> None:
        shape = inspect_message({"role": "assistant", "content": "ready"})
        assert shape.content_populated is True
        assert extract_text({"role": "assistant", "content": "ready"}) == "ready"

    def test_tool_calls_are_counted_not_treated_as_text(self) -> None:
        message = FakeMessage(
            content=None, tool_calls=[FakeToolCall("get_current_temperature", {"city": "Berlin"})]
        )
        shape = inspect_message(message)
        assert shape.tool_call_count == 1
        assert "tool_calls(1)" in shape.summary
        assert all(f.name != "tool_calls" for f in shape.populated_fields)

    def test_refusal_is_flagged(self) -> None:
        shape = inspect_message(FakeMessage(content=None, refusal="I cannot help with that."))
        assert shape.refusal_present is True

    def test_structural_fields_are_ignored(self) -> None:
        shape = inspect_message(FakeMessage(content="ready"))
        assert all(f.name != "role" for f in shape.populated_fields)


class TestReasoningNameHeuristic:
    def test_known_hints(self) -> None:
        hints = ("reasoning_content", "reasoning", "thinking", "thought_process", "cot", "analysis")
        for name in hints:
            assert looks_like_reasoning(name) is True

    def test_answer_fields_are_not_flagged(self) -> None:
        for name in ("content", "output_text", "answer", "message"):
            assert looks_like_reasoning(name) is False
