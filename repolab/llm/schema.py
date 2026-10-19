"""JSON Schema preparation for structured output.

Pydantic's ``model_json_schema()`` is not accepted as-is by OpenAI-style strict
structured output, which requires that every object set
``additionalProperties: false`` and list *all* of its properties in ``required``.
Pydantic does neither: optional fields are omitted from ``required``, and
``additionalProperties`` is never emitted. :func:`to_strict_json_schema` closes
that gap, including inside ``$defs`` for nested models.

Nebius documentation also recommends restating the schema in the prompt rather
than relying on ``response_format`` alone, which :func:`describe_schema_for_prompt`
provides.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

#: JSON Schema keywords whose values are themselves schemas.
_NESTED_SCHEMA_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")


def _harden(node: Any) -> None:
    """Recursively make *node* satisfy strict structured-output rules."""
    if isinstance(node, list):
        for item in node:
            _harden(item)
        return
    if not isinstance(node, dict):
        return

    properties = node.get("properties")
    if isinstance(properties, dict):
        # Strict mode requires every property to be listed as required; a field
        # being nullable is how optionality is expressed instead.
        node["required"] = list(properties)
        node["additionalProperties"] = False
        for subschema in properties.values():
            _harden(subschema)

    for key in ("items", "additionalProperties", "not"):
        value = node.get(key)
        if isinstance(value, (dict, list)):
            _harden(value)

    for key in _NESTED_SCHEMA_KEYS:
        if key in node:
            _harden(node[key])

    defs = node.get("$defs")
    if isinstance(defs, dict):
        for subschema in defs.values():
            _harden(subschema)


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return *model*'s JSON Schema, hardened for strict structured output."""
    schema = model.model_json_schema()
    _harden(schema)
    return schema


def response_format_for(model: type[BaseModel]) -> dict[str, Any]:
    """The ``response_format`` requesting *model* via strict JSON Schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "strict": True,
            "schema": to_strict_json_schema(model),
        },
    }


#: The fallback used when strict JSON Schema does not produce valid output.
JSON_OBJECT_RESPONSE_FORMAT: dict[str, Any] = {"type": "json_object"}


def describe_schema_for_prompt(model: type[BaseModel]) -> str:
    """Render *model*'s schema for inclusion in a prompt.

    Nebius documentation recommends stating the schema in the prompt as well as
    in ``response_format``, since adherence varies by model.
    """
    return json.dumps(to_strict_json_schema(model), separators=(",", ":"))
