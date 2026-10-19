"""Safe inspection of chat-completion message shapes.

Reasoning models do not reliably populate ``content``: depending on the model and
the serving stack, the user-facing answer may arrive in ``content`` while private
reasoning arrives in some *other* field, or ``content`` may be empty entirely.
The model router has to cope with that, so RepoLab measures it rather than
assuming it.

Two rules govern this module:

* **Assume nothing about field names.** Every populated field on the message
  object is reported, including ones RepoLab has never seen, so an unexpected
  carrier is discovered instead of missed.
* **Never capture the text.** Only the field's type and size are recorded.
  Private model chain-of-thought must never reach logs, events, or the UI
  (CLAUDE.md), so this module is deliberately incapable of carrying it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

#: Substrings that suggest a field carries reasoning rather than the answer.
#: Used only to *label* discovered fields -- discovery itself is name-agnostic.
REASONING_NAME_HINTS: tuple[str, ...] = (
    "reasoning",
    "thinking",
    "thought",
    "analysis",
    "cot",
)

#: Message fields that never carry answer or reasoning payload.
_STRUCTURAL_FIELDS: frozenset[str] = frozenset(
    {"role", "function_call", "tool_calls", "annotations", "audio", "refusal"}
)


class FieldShape(BaseModel):
    """The size and type of one message field. Never its value."""

    name: str
    type_name: str
    length: int
    """Characters for strings, items for collections."""

    looks_like_reasoning: bool = False


class MessageShape(BaseModel):
    """What a provider actually put on a chat message, and where."""

    populated_fields: list[FieldShape] = []
    """Every non-empty, non-structural field found, largest first."""

    content_populated: bool = False
    content_length: int = 0

    reasoning_fields: list[str] = []
    """Populated fields whose names suggest reasoning content."""

    tool_call_count: int = 0

    refusal_present: bool = False

    @property
    def primary_text_field(self) -> str | None:
        """The field a caller should read the answer from.

        ``content`` when populated, otherwise the largest populated field that
        does not look like reasoning -- which is what makes an empty-``content``
        response recoverable.
        """
        if self.content_populated:
            return "content"
        for field in self.populated_fields:
            if not field.looks_like_reasoning:
                return field.name
        return None

    @property
    def summary(self) -> str:
        """One-line, user-safe description of the shape."""
        if not self.populated_fields and not self.tool_call_count:
            return "empty message"
        parts = [f"{f.name}({f.length})" for f in self.populated_fields]
        if self.tool_call_count:
            parts.append(f"tool_calls({self.tool_call_count})")
        return ", ".join(parts)


def _candidate_items(raw: Any) -> dict[str, Any]:
    """Return the message's fields as a mapping, whatever object shape it has."""
    if isinstance(raw, dict):
        return dict(raw)

    # Pydantic-style SDK models (the openai SDK builds on pydantic).
    dumped = getattr(raw, "model_dump", None)
    if callable(dumped):
        try:
            result = dumped()
            if isinstance(result, dict):
                return result
        except Exception:  # noqa: BLE001 - shape probing must never raise
            pass

    items: dict[str, Any] = {}
    for name in dir(raw):
        if name.startswith("_"):
            continue
        try:
            value = getattr(raw, name)
        except Exception:  # noqa: BLE001 - attribute access may be dynamic
            continue
        if callable(value):
            continue
        items[name] = value
    return items


def _measure(value: Any) -> int | None:
    """Return the size of *value*, or ``None`` if it holds no payload."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return len(value) if stripped else None
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) or None
    if isinstance(value, (int, float)):
        return len(str(value))
    return None


def looks_like_reasoning(field_name: str) -> bool:
    """Whether *field_name* suggests reasoning rather than the answer."""
    lowered = field_name.lower()
    return any(hint in lowered for hint in REASONING_NAME_HINTS)


def inspect_message(raw: Any) -> MessageShape:
    """Describe which fields a provider populated on a chat message.

    Records only field names, types and sizes -- never any text content.
    """
    shape = MessageShape()
    if raw is None:
        return shape

    items = _candidate_items(raw)
    fields: list[FieldShape] = []

    for name, value in items.items():
        if name == "tool_calls":
            shape.tool_call_count = len(value) if isinstance(value, (list, tuple)) else 0
            continue
        if name == "refusal":
            shape.refusal_present = _measure(value) is not None
            continue
        if name == "content":
            length = _measure(value)
            shape.content_populated = length is not None
            shape.content_length = length or 0
            if length is not None:
                fields.append(FieldShape(name=name, type_name=type(value).__name__, length=length))
            continue
        if name in _STRUCTURAL_FIELDS:
            continue

        length = _measure(value)
        if length is None:
            continue
        fields.append(
            FieldShape(
                name=name,
                type_name=type(value).__name__,
                length=length,
                looks_like_reasoning=looks_like_reasoning(name),
            )
        )

    fields.sort(key=lambda f: f.length, reverse=True)
    shape.populated_fields = fields
    shape.reasoning_fields = [f.name for f in fields if f.looks_like_reasoning]
    return shape


def extract_text(raw: Any, shape: MessageShape | None = None) -> str | None:
    """Return the assistant's answer text, tolerating an empty ``content``.

    This is the one place that touches values rather than shapes; it returns the
    answer only, never a field flagged as reasoning.
    """
    if raw is None:
        return None
    shape = shape or inspect_message(raw)
    field = shape.primary_text_field
    if field is None:
        return None
    items = _candidate_items(raw)
    value = items.get(field)
    return value if isinstance(value, str) and value.strip() else None
