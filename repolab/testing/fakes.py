"""In-memory provider fakes.

These exist so the test suite never touches the network, and so the capability
checker's own logic can be exercised without credentials.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from repolab.interfaces import ChatResult, SearchResultItem
from repolab.llm.response import extract_text, inspect_message


class FakeMessage:
    """A chat message whose populated fields the test decides.

    Mirrors how the openai SDK exposes a message: plain attributes, with
    ``content`` sometimes empty and reasoning sometimes carried elsewhere.
    """

    def __init__(
        self,
        content: str | None = None,
        *,
        reasoning_content: str | None = None,
        tool_calls: list[Any] | None = None,
        refusal: str | None = None,
        **extra: Any,
    ) -> None:
        self.role = "assistant"
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or []
        self.refusal = refusal
        for key, value in extra.items():
            setattr(self, key, value)


class FakeToolCall:
    """A well-formed tool request, shaped like the OpenAI response format."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.id = f"chatcmpl-tool-fake-{name}"
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeFunction:
    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = json.dumps(arguments)


class FakeLLMProvider:
    """Scripted :class:`~repolab.interfaces.LLMProvider`.

    Three scripting styles:

    * ``message`` -- every call returns the same message.
    * ``messages`` -- a queue consumed one call at a time, so a router retry can
      be driven (invalid output first, valid output second). The final entry is
      reused once the queue is exhausted.
    * ``fail_with`` / ``fail_times`` -- raise instead of responding, optionally
      only for the first *n* calls.

    ``delay_seconds`` makes a call slow enough to exercise timeout handling.
    """

    def __init__(
        self,
        *,
        models: list[str] | None = None,
        message: Any | None = None,
        messages: list[Any] | None = None,
        supports_tools: bool = True,
        fail_with: Exception | None = None,
        fail_times: int | None = None,
        delay_seconds: float = 0.0,
        prompt_tokens: int = 11,
        completion_tokens: int = 7,
    ) -> None:
        self.models = ["fake/nano", "fake/super", "fake/ultra"] if models is None else list(models)
        self.message = message
        self.scripted = list(messages) if messages is not None else None
        self.supports_tools = supports_tools
        self.fail_with = fail_with
        self.fail_times = fail_times
        self.delay_seconds = delay_seconds
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def _should_fail(self) -> bool:
        if self.fail_with is None:
            return False
        if self.fail_times is None:
            return True
        return len(self.calls) < self.fail_times

    def _next_scripted(self) -> Any | None:
        """Take the message for this call, reusing the last once exhausted."""
        if not self.scripted:
            return None
        return self.scripted[min(len(self.calls), len(self.scripted) - 1)]

    async def list_models(self) -> list[str]:
        if self.fail_with:
            raise self.fail_with
        return list(self.models)

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
        if self._should_fail():
            assert self.fail_with is not None
            self.calls.append({"model": model, "messages": messages, "failed": True})
            raise self.fail_with

        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        # Chosen before recording the call so the queue index matches this call.
        message = self._next_scripted() or self.message
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "response_format": response_format,
                "tools": tools,
                "timeout": timeout,
            }
        )

        if message is None:
            if tools is not None:
                message = (
                    FakeMessage(
                        content=None,
                        tool_calls=[FakeToolCall("get_current_temperature", {"city": "Berlin"})],
                    )
                    if self.supports_tools
                    else FakeMessage(content="I cannot call tools.")
                )
            elif response_format is not None:
                message = FakeMessage(content='{"status": "ok"}')
            else:
                message = FakeMessage(content="hello from the fake model")

        return ChatResult(
            model=model,
            raw_message=message,
            text=extract_text(message, inspect_message(message)),
            tool_calls=list(getattr(message, "tool_calls", None) or []),
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


class FakeResearchProvider:
    """Scripted :class:`~repolab.interfaces.ResearchProvider`."""

    def __init__(
        self,
        *,
        results: list[SearchResultItem] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.results = (
            [
                SearchResultItem(
                    url="https://spark.apache.org/docs/latest/sql-performance-tuning.html",
                    title="Performance Tuning - Spark",
                    content="spark.sql.shuffle.partitions configures the number of partitions.",
                )
            ]
            if results is None
            else list(results)
        )
        self.fail_with = fail_with
        self.queries: list[tuple[str, list[str] | None]] = []

    async def search(
        self,
        query: str,
        *,
        include_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> list[SearchResultItem]:
        if self.fail_with:
            raise self.fail_with
        self.queries.append((query, include_domains))
        return list(self.results[:max_results])


# The sandbox fake lives with the provider it models, so the two cannot drift
# apart. Re-exported here because the provider fakes are conventionally imported
# from this module.
from repolab.sandbox.fake import FakeSandboxProvider, ScriptedCommand  # noqa: E402

__all__ = [
    "FakeLLMProvider",
    "FakeMessage",
    "FakeResearchProvider",
    "FakeSandboxProvider",
    "FakeToolCall",
    "ScriptedCommand",
]
