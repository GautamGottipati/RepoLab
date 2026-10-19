"""The fakes must satisfy the protocols, or offline tests prove nothing."""

from __future__ import annotations

import pytest

from repolab.interfaces import LLMProvider, ResearchProvider
from repolab.sandbox.provider import SandboxProvider
from repolab.testing.fakes import (
    FakeLLMProvider,
    FakeResearchProvider,
    FakeSandboxProvider,
)


class TestProtocolConformance:
    def test_llm_fake(self) -> None:
        assert isinstance(FakeLLMProvider(), LLMProvider)

    def test_research_fake(self) -> None:
        assert isinstance(FakeResearchProvider(), ResearchProvider)

    def test_sandbox_fake(self) -> None:
        assert isinstance(FakeSandboxProvider(), SandboxProvider)


class TestFakeLLM:
    async def test_lists_models(self) -> None:
        assert await FakeLLMProvider(models=["a", "b"]).list_models() == ["a", "b"]

    async def test_plain_chat_returns_text(self) -> None:
        result = await FakeLLMProvider().chat(model="fake/nano", messages=[])
        assert result.text
        assert result.prompt_tokens == 11

    async def test_structured_request_returns_json(self) -> None:
        result = await FakeLLMProvider().chat(
            model="fake/nano", messages=[], response_format={"type": "json_object"}
        )
        assert result.text == '{"status": "ok"}'

    async def test_tool_request_is_well_formed(self) -> None:
        result = await FakeLLMProvider().chat(model="fake/nano", messages=[], tools=[{}])
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function.name == "get_current_temperature"

    async def test_tool_support_can_be_disabled(self) -> None:
        result = await FakeLLMProvider(supports_tools=False).chat(
            model="fake/nano", messages=[], tools=[{}]
        )
        assert result.tool_calls == []

    async def test_failure_is_injectable(self) -> None:
        llm = FakeLLMProvider(fail_with=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await llm.chat(model="fake/nano", messages=[])

    async def test_calls_are_recorded(self) -> None:
        llm = FakeLLMProvider()
        await llm.chat(model="fake/super", messages=[{"role": "user", "content": "hi"}])
        assert llm.calls[0]["model"] == "fake/super"


class TestFakeResearch:
    async def test_returns_results_with_urls_and_snippets(self) -> None:
        hits = await FakeResearchProvider().search("x", include_domains=["spark.apache.org"])
        assert hits[0].url.startswith("https://spark.apache.org")
        assert hits[0].content

    async def test_records_the_domain_restriction(self) -> None:
        research = FakeResearchProvider()
        await research.search("shuffle", include_domains=["spark.apache.org"])
        assert research.queries == [("shuffle", ["spark.apache.org"])]

    async def test_respects_max_results(self) -> None:
        assert await FakeResearchProvider().search("x", max_results=0) == []


class TestFakeSandbox:
    """Only conformance here.

    The sandbox fake models create/execute/checkpoint/restore/terminate, and those
    semantics are covered thoroughly in ``test_sandbox_provider.py``.
    """

    async def test_it_satisfies_the_provider_protocol(self) -> None:
        sandbox = FakeSandboxProvider()
        assert isinstance(sandbox, SandboxProvider)
        assert sandbox.name == "fake"

    async def test_close_is_recorded(self) -> None:
        sandbox = FakeSandboxProvider()
        await sandbox.close()
        assert sandbox.closed is True
