"""Tolerant extraction of a JSON object from model output.

Even when JSON is requested, models wrap it: fenced code blocks, a sentence of
preamble, or a trailing remark. Rather than fail on output that is substantively
correct, this module recovers the outermost balanced JSON object.

It is deliberately conservative: it never repairs malformed JSON, because
silently "fixing" a model's output would invent data.
"""

from __future__ import annotations

import json
from typing import Any

_FENCE = "```"


def strip_code_fences(text: str) -> str:
    """Remove a surrounding Markdown code fence, if present."""
    stripped = text.strip()
    if not stripped.startswith(_FENCE):
        return stripped

    body = stripped[len(_FENCE) :]
    # An opening fence may carry a language hint, e.g. ```json
    newline = body.find("\n")
    if newline != -1 and body[:newline].strip().isalpha():
        body = body[newline + 1 :]

    end = body.rfind(_FENCE)
    if end != -1:
        body = body[:end]
    return body.strip()


def _balanced_object(text: str) -> str | None:
    """Return the first complete, brace-balanced object in *text*."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json_object(text: str | None) -> dict[str, Any] | None:
    """Parse a JSON object out of *text*, tolerating fences and surrounding prose.

    Returns ``None`` when no parseable object is present.
    """
    if not text or not text.strip():
        return None

    candidates = [strip_code_fences(text), text.strip()]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            pass
        else:
            return parsed if isinstance(parsed, dict) else None

    for candidate in candidates:
        extracted = _balanced_object(candidate)
        if extracted is None:
            continue
        try:
            parsed = json.loads(extracted)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
