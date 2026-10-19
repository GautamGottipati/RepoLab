"""Every probe is exercised against fakes, so the checker's logic is verified
without credentials or network access."""

from __future__ import annotations

from repolab.capability import (
    ProbeStatus,
    guarded,
    probe_inference,
    probe_model_catalog,
    probe_research,
    probe_sandbox,
    probe_structured_output,
    probe_tool_calling,
)
from repolab.interfaces import CommandResult, SearchResultItem
from repolab.sandbox import FakeSandboxProvider, ScriptedCommand
from repolab.testing.fakes import FakeLLMProvider, FakeMessage, FakeResearchProvider

SMOKE_OUTPUTS = {
    "echo hello": CommandResult(stdout="hello\n", exit_code=0),
    "python --version": CommandResult(stdout="Python 3.11.9\n", exit_code=0),
}


class TestModelCatalog:
    async def test_configured_ids_present_in_catalog_pass(self) -> None:
        llm = FakeLLMProvider(models=["nvidia/nano-x", "nvidia/super-y"])
        rows = await probe_model_catalog(
            llm, {"nano": "nvidia/nano-x", "super": "nvidia/super-y", "ultra": None}
        )
        by_capability = {r.capability: r for r in rows}

        assert by_capability["Model catalog listing"].status is ProbeStatus.PASS
        assert by_capability["Nano model ID resolves"].status is ProbeStatus.PASS
        assert by_capability["Super model ID resolves"].status is ProbeStatus.PASS
        # An unset tier is untested, not broken.
        assert by_capability["Ultra model ID resolves"].status is ProbeStatus.SKIP

    async def test_missing_id_fails_and_suggests_close_matches(self) -> None:
        """Nemotron IDs are inconsistently cased upstream, so suggestions matter."""
        llm = FakeLLMProvider(models=["nvidia/nemotron-3-super-120b-a12b"])
        rows = await probe_model_catalog(llm, {"super": "nvidia/Nemotron-3-Super-120B-A12B"})
        row = next(r for r in rows if r.capability == "Super model ID resolves")

        assert row.status is ProbeStatus.FAIL
        assert "not in catalog" in row.notes
        assert "nvidia/nemotron-3-super-120b-a12b" in row.notes

    async def test_empty_catalog_fails(self) -> None:
        rows = await probe_model_catalog(FakeLLMProvider(models=[]), {})
        assert rows[0].status is ProbeStatus.FAIL


class TestInference:
    async def test_content_populated_passes(self) -> None:
        result = await probe_inference(FakeLLMProvider(), "nano", "fake/nano")
        assert result.status is ProbeStatus.PASS
        assert result.detail["content_populated"] is True
        assert result.detail["primary_text_field"] == "content"

    async def test_empty_content_warns_and_names_the_carrier(self) -> None:
        """A reasoning model that leaves content empty is usable, but noteworthy."""
        llm = FakeLLMProvider(message=FakeMessage(content=None, output_text="ready"))
        result = await probe_inference(llm, "super", "fake/super")

        assert result.status is ProbeStatus.WARN
        assert "output_text" in result.notes
        assert result.detail["primary_text_field"] == "output_text"

    async def test_reasoning_only_response_fails(self) -> None:
        llm = FakeLLMProvider(message=FakeMessage(content=None, reasoning_content="thinking..."))
        result = await probe_inference(llm, "ultra", "fake/ultra")

        assert result.status is ProbeStatus.FAIL
        assert result.detail["reasoning_fields"] == ["reasoning_content"]

    async def test_detail_never_contains_reasoning_text(self) -> None:
        secret = "the private chain of thought"
        llm = FakeLLMProvider(message=FakeMessage(content="ready", reasoning_content=secret))
        result = await probe_inference(llm, "nano", "fake/nano")
        assert secret not in result.model_dump_json()

    async def test_records_token_usage(self) -> None:
        result = await probe_inference(FakeLLMProvider(), "nano", "fake/nano")
        assert result.detail["prompt_tokens"] == 11
        assert result.detail["completion_tokens"] == 7
        assert result.latency_ms is not None


class TestStructuredOutput:
    async def test_valid_json_passes(self) -> None:
        result = await probe_structured_output(FakeLLMProvider(), "nano", "fake/nano")
        assert result.status is ProbeStatus.PASS
        assert result.detail["status"] == "ok"

    async def test_prose_fails(self) -> None:
        llm = FakeLLMProvider(message=FakeMessage(content="Sure! Here is the JSON you wanted."))
        result = await probe_structured_output(llm, "nano", "fake/nano")
        assert result.status is ProbeStatus.FAIL
        assert "unparseable" in result.notes

    async def test_schema_violation_fails(self) -> None:
        llm = FakeLLMProvider(message=FakeMessage(content='{"state": "ok"}'))
        result = await probe_structured_output(llm, "nano", "fake/nano")
        assert result.status is ProbeStatus.FAIL

    async def test_empty_response_fails(self) -> None:
        llm = FakeLLMProvider(message=FakeMessage(content=None))
        result = await probe_structured_output(llm, "nano", "fake/nano")
        assert result.status is ProbeStatus.FAIL
        assert result.notes == "empty response"

    async def test_schema_is_restated_in_the_prompt(self) -> None:
        """Nebius documentation recommends this, so the probe must actually do it."""
        llm = FakeLLMProvider()
        await probe_structured_output(llm, "nano", "fake/nano")
        call = llm.calls[0]
        assert call["response_format"]["type"] == "json_schema"
        assert '{"status": "ok"}' in call["messages"][0]["content"]


class TestToolCalling:
    async def test_well_formed_request_passes(self) -> None:
        result = await probe_tool_calling(FakeLLMProvider(), "super", "fake/super")
        assert result.status is ProbeStatus.PASS
        assert result.detail["tool_name"] == "get_current_temperature"
        assert result.detail["argument_keys"] == ["city"]

    async def test_absence_only_warns_and_is_optional(self) -> None:
        result = await probe_tool_calling(
            FakeLLMProvider(supports_tools=False), "super", "fake/super"
        )
        assert result.status is ProbeStatus.WARN
        assert result.required is False

    async def test_a_failure_here_is_never_required(self) -> None:
        result = await guarded(
            "Super native tool calling",
            lambda: probe_tool_calling(
                FakeLLMProvider(fail_with=RuntimeError("400 tools unsupported")),
                "super",
                "fake/super",
            ),
            required=False,
        )
        assert result.status is ProbeStatus.FAIL
        assert result.required is False


class TestSandbox:
    """The capability probe against the new provider lifecycle."""

    def provider(self, **kwargs: object) -> FakeSandboxProvider:
        scripted = {
            "echo hello": ScriptedCommand(stdout="hello\n"),
            "python --version": ScriptedCommand(stdout="Python 3.11.9\n"),
        }
        return FakeSandboxProvider(scripted=scripted, **kwargs)  # type: ignore[arg-type]

    async def test_smoke_commands_pass(self) -> None:
        rows = {r.capability: r for r in await probe_sandbox(self.provider())}

        assert rows["Sandbox creation"].status is ProbeStatus.PASS
        assert rows["Sandbox command"].status is ProbeStatus.PASS
        assert rows["Sandbox checkpoint"].status is ProbeStatus.PASS
        assert rows["Sandbox python present"].status is ProbeStatus.PASS

    async def test_a_checkpoint_is_taken(self) -> None:
        """Checkpointing is how experiment variants will start from one state."""
        sandbox = self.provider()
        rows = {r.capability: r for r in await probe_sandbox(sandbox)}

        assert sandbox.checkpoint_count == 1
        assert rows["Sandbox checkpoint"].detail["checkpoint_id"]

    async def test_missing_python_only_warns(self) -> None:
        """busybox legitimately has no python; that is an image fact, not a failure."""
        sandbox = FakeSandboxProvider(
            scripted={
                "echo hello": ScriptedCommand(stdout="hello\n"),
                "python --version": ScriptedCommand(stderr="not found", exit_code=127),
            }
        )
        rows = {r.capability: r for r in await probe_sandbox(sandbox)}

        assert rows["Sandbox command"].status is ProbeStatus.PASS
        assert rows["Sandbox python present"].status is ProbeStatus.WARN
        assert rows["Sandbox python present"].required is False

    async def test_wrong_echo_output_fails(self) -> None:
        sandbox = FakeSandboxProvider(
            scripted={"echo hello": ScriptedCommand(stdout="", exit_code=1)}
        )
        rows = {r.capability: r for r in await probe_sandbox(sandbox)}
        assert rows["Sandbox command"].status is ProbeStatus.FAIL

    async def test_creation_failure_is_captured_as_a_finding(self) -> None:
        result = await guarded(
            "Sandbox creation",
            lambda: probe_sandbox(
                FakeSandboxProvider(fail_on_create=RuntimeError("401 unauthorized"))
            ),
        )
        assert result.status is ProbeStatus.FAIL
        assert "401 unauthorized" in result.notes


class TestResearch:
    async def test_official_domain_results_pass(self) -> None:
        result = await probe_research(FakeResearchProvider())
        assert result.status is ProbeStatus.PASS
        assert result.detail["with_content"] == 1

    async def test_no_results_fails(self) -> None:
        result = await probe_research(FakeResearchProvider(results=[]))
        assert result.status is ProbeStatus.FAIL

    async def test_results_without_snippets_fail(self) -> None:
        """URLs alone are not enough: RepoLab cites excerpts as evidence."""
        research = FakeResearchProvider(
            results=[SearchResultItem(url="https://spark.apache.org/docs/latest/", content=None)]
        )
        result = await probe_research(research)
        assert result.status is ProbeStatus.FAIL
        assert "no snippet content" in result.notes

    async def test_off_domain_results_warn(self) -> None:
        research = FakeResearchProvider(
            results=[SearchResultItem(url="https://blog.example.com/spark", content="...")]
        )
        result = await probe_research(research)
        assert result.status is ProbeStatus.WARN
        assert "outside spark.apache.org" in result.notes

    async def test_restricts_to_the_official_domain(self) -> None:
        research = FakeResearchProvider()
        await probe_research(research)
        assert research.queries[0][1] == ["spark.apache.org"]


class TestGuard:
    async def test_exceptions_become_failed_rows_not_crashes(self) -> None:
        async def boom():
            raise ValueError("catalog exploded")

        result = await guarded("Model catalog listing", boom)
        assert result.status is ProbeStatus.FAIL
        assert result.notes == "ValueError: catalog exploded"
        assert result.latency_ms is not None

    async def test_multiline_errors_are_reduced_to_one_line(self) -> None:
        async def boom():
            raise RuntimeError("first line\nsecond line")

        result = await guarded("x", boom)
        assert result.notes == "RuntimeError: first line"
