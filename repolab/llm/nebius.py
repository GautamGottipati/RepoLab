"""Nemotron inference over the Nebius Token Factory OpenAI-compatible API.

Verified against Nebius documentation:
  base URL   https://api.tokenfactory.nebius.com/v1/
  auth       NEBIUS_API_KEY as a bearer token
  listing    GET /v1/models
  structured response_format={"type": "json_schema", ...} | {"type": "json_object"}
  tools      standard OpenAI `tools` / `tool_choice`; the model only *requests* calls

Model IDs are never hardcoded here; callers pass the ID resolved from settings.
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from repolab.interfaces import ChatResult
from repolab.llm.response import extract_text, inspect_message


class NebiusLLMProvider:
    """Thin :class:`~repolab.interfaces.LLMProvider` over the ``openai`` SDK."""

    def __init__(self, api_key: str, base_url: str, *, timeout: float = 60.0) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    async def list_models(self) -> list[str]:
        page = await self._client.models.list()
        return [model.id for model in page.data]

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if timeout is not None:
            # Per-request override of the client-level timeout.
            kwargs["timeout"] = timeout
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        completion = await self._client.chat.completions.create(**kwargs)
        message = completion.choices[0].message if completion.choices else None
        usage = completion.usage

        return ChatResult(
            model=model,
            raw_message=message,
            text=extract_text(message, inspect_message(message)),
            tool_calls=list(getattr(message, "tool_calls", None) or []),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )

    async def close(self) -> None:
        await self._client.close()
