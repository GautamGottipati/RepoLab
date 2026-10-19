"""The agent execution loop.

Every property here is a safety property: an agent runs inside a budget, cannot
name a capability it was not given, cannot loop forever, and cannot write model
reasoning into the audit trail.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from repolab.agents import (
    AgentLimits,
    AgentRuntime,
    AgentStatus,
    ToolRegistry,
    ToolRequest,
    ToolResult,
)
from repolab.events import CollectingEmitter, EventType

SECRET_COT = "Let me think about which tool would be cleverest here."


class EchoArgs(BaseModel):
    value: str = "x"


class EchoTool:
    """A tool that succeeds, and counts how often it was run."""

    name = "echo"
    description = "Echo a value back."
    requires_approval = False
    arguments_model = EchoArgs

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        args = EchoArgs.model_validate(arguments)
        return ToolResult(summary=f"echoed {args.value}", data={"value": args.value})


class RaisingTool:
    """A tool that raises, to exercise retries."""

    name = "flaky"
    description = "Raises until it has been called enough times."
    requires_approval = False
    arguments_model = EchoArgs

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("transient failure")
        return ToolResult(summary="recovered")


class SlowTool:
    name = "slow"
    description = "Takes longer than its budget."
    requires_approval = False
    arguments_model = EchoArgs

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(summary="never reached")  # pragma: no cover


class ScriptedPlanner:
    """Returns a fixed list of requests, then stops."""

    name = "scripted"

    def __init__(self, requests: list[ToolRequest]) -> None:
        self._requests = list(requests)
        self.contexts: list[dict[str, Any]] = []

    async def next_request(self, run, context):
        self.contexts.append(dict(context))
        if not self._requests:
            return None
        return self._requests.pop(0)


class EndlessPlanner:
    """Always asks for the same thing. Used to test loop protection."""

    name = "endless"

    def __init__(self, request: ToolRequest) -> None:
        self._request = request

    async def next_request(self, run, context):
        return self._request


class VaryingEndlessPlanner:
    """Always asks for something new, so only the iteration cap can stop it."""

    name = "varying"

    def __init__(self) -> None:
        self.count = 0

    async def next_request(self, run, context):
        self.count += 1
        return ToolRequest(tool="echo", arguments={"value": str(self.count)})


class BrokenPlanner:
    name = "broken"

    async def next_request(self, run, context):
        raise RuntimeError("planner exploded")


def runtime_for(
    planner: Any, tools: list[Any], *, limits: AgentLimits | None = None
) -> tuple[AgentRuntime, CollectingEmitter]:
    emitter = CollectingEmitter("job-1")
    runtime = AgentRuntime(
        agent="test_agent",
        objective="exercise the loop",
        registry=ToolRegistry(tools),
        planner=planner,
        emitter=emitter,
        limits=limits or AgentLimits(),
        step_label="testing",
    )
    return runtime, emitter


class TestHappyPath:
    async def test_a_planner_that_finishes_completes_the_run(self) -> None:
        tool = EchoTool()
        runtime, _ = runtime_for(
            ScriptedPlanner([ToolRequest(tool="echo", arguments={"value": "a"})]), [tool]
        )
        run = await runtime.run()

        assert run.status is AgentStatus.COMPLETED
        assert run.iterations == 1
        assert tool.calls == 1
        assert run.duration_seconds is not None

    async def test_steps_record_what_happened(self) -> None:
        runtime, _ = runtime_for(
            ScriptedPlanner([ToolRequest(tool="echo", arguments={"value": "a"})]),
            [EchoTool()],
        )
        run = await runtime.run()
        step = run.steps[0]

        assert step.index == 1
        assert step.ok is True
        assert step.request is not None and step.request.tool == "echo"
        assert step.status == "echoed a"
        assert step.duration_ms >= 0

    async def test_the_planner_sees_what_has_been_learned(self) -> None:
        planner = ScriptedPlanner([ToolRequest(tool="echo")])
        runtime, _ = runtime_for(planner, [EchoTool()])
        await runtime.run(lambda: {"frameworks": ["Spark"]})

        assert planner.contexts[0] == {"frameworks": ["Spark"]}

    async def test_a_failing_context_provider_does_not_break_the_run(self) -> None:
        def broken() -> dict[str, Any]:
            raise RuntimeError("no context")

        runtime, _ = runtime_for(ScriptedPlanner([ToolRequest(tool="echo")]), [EchoTool()])
        run = await runtime.run(broken)
        assert run.status is AgentStatus.COMPLETED


class TestEventSequence:
    async def test_each_step_emits_requested_started_completed(self) -> None:
        runtime, emitter = runtime_for(ScriptedPlanner([ToolRequest(tool="echo")]), [EchoTool()])
        await runtime.run()

        assert emitter.types() == [
            EventType.AGENT_STARTED,
            EventType.TOOL_REQUESTED,
            EventType.TOOL_STARTED,
            EventType.TOOL_COMPLETED,
            EventType.PROGRESS,
            EventType.AGENT_STATUS,
        ]

    async def test_the_reason_reaches_the_requested_event(self) -> None:
        runtime, emitter = runtime_for(
            ScriptedPlanner([ToolRequest(tool="echo", reason="Because it is informative")]),
            [EchoTool()],
        )
        await runtime.run()
        assert "Because it is informative" in emitter.of_type(EventType.TOOL_REQUESTED)[0].message

    async def test_progress_reports_position_in_the_budget(self) -> None:
        runtime, emitter = runtime_for(
            ScriptedPlanner([ToolRequest(tool="echo")]),
            [EchoTool()],
            limits=AgentLimits(max_iterations=9),
        )
        await runtime.run()
        progress = emitter.of_type(EventType.PROGRESS)[0]
        assert progress.data == {"completed_steps": 1, "max_steps": 9}

    async def test_events_are_sequenced_contiguously(self) -> None:
        runtime, emitter = runtime_for(
            ScriptedPlanner([ToolRequest(tool="echo"), ToolRequest(tool="echo")]),
            [EchoTool()],
        )
        await runtime.run()
        assert [e.seq for e in emitter.events] == list(range(1, len(emitter.events) + 1))


class TestLimits:
    async def test_the_iteration_cap_terminates_an_endless_planner(self) -> None:
        planner = VaryingEndlessPlanner()
        runtime, emitter = runtime_for(planner, [EchoTool()], limits=AgentLimits(max_iterations=4))
        run = await runtime.run()

        assert run.status is AgentStatus.EXHAUSTED
        assert run.iterations == 4
        assert "4-step limit" in (run.error or "")
        assert emitter.of_type(EventType.AGENT_STATUS)

    async def test_a_repeating_planner_is_stopped_as_stuck(self) -> None:
        """Repeating the identical request is a stall, not progress."""
        runtime, _ = runtime_for(
            EndlessPlanner(ToolRequest(tool="echo", arguments={"value": "same"})),
            [EchoTool()],
            limits=AgentLimits(max_iterations=50, max_consecutive_repeats=2),
        )
        run = await runtime.run()

        assert run.status is AgentStatus.FAILED
        assert "repeated" in (run.error or "")
        assert run.iterations < 50

    async def test_a_slow_tool_hits_its_timeout(self) -> None:
        runtime, _ = runtime_for(
            ScriptedPlanner([ToolRequest(tool="slow")]),
            [SlowTool()],
            limits=AgentLimits(step_timeout_seconds=0.05, max_tool_retries=0),
        )
        run = await runtime.run()

        assert run.steps[0].ok is False
        assert "timed out" in (run.steps[0].error or "")


class TestRetries:
    async def test_a_transient_failure_is_retried_then_succeeds(self) -> None:
        tool = RaisingTool(fail_times=1)
        runtime, emitter = runtime_for(
            ScriptedPlanner([ToolRequest(tool="flaky")]),
            [tool],
            limits=AgentLimits(max_tool_retries=2),
        )
        run = await runtime.run()

        assert run.steps[0].ok is True
        assert run.steps[0].attempts == 2
        assert tool.calls == 2
        assert emitter.of_type(EventType.LOG_LINE)

    async def test_retries_are_bounded(self) -> None:
        tool = RaisingTool(fail_times=99)
        runtime, _ = runtime_for(
            ScriptedPlanner([ToolRequest(tool="flaky")]),
            [tool],
            limits=AgentLimits(max_tool_retries=2),
        )
        run = await runtime.run()

        assert run.steps[0].ok is False
        assert tool.calls == 3  # the first attempt plus two retries

    async def test_a_definite_negative_is_not_retried(self) -> None:
        """A missing file will still be missing; retrying wastes the budget."""

        class MissingTool:
            name = "missing"
            description = "Always reports a definite negative."
            requires_approval = False
            arguments_model = EchoArgs

            def __init__(self) -> None:
                self.calls = 0

            async def execute(self, arguments: dict[str, Any]) -> ToolResult:
                self.calls += 1
                return ToolResult.failure("no such file")

        tool = MissingTool()
        runtime, _ = runtime_for(
            ScriptedPlanner([ToolRequest(tool="missing")]),
            [tool],
            limits=AgentLimits(max_tool_retries=3),
        )
        await runtime.run()
        assert tool.calls == 1


class TestRefusals:
    async def test_an_unregistered_tool_is_refused_and_ends_the_run(self) -> None:
        tool = EchoTool()
        runtime, emitter = runtime_for(
            ScriptedPlanner([ToolRequest(tool="run_shell_command")]), [tool]
        )
        run = await runtime.run()

        assert run.status is AgentStatus.FAILED
        assert "unknown tool" in (run.error or "")
        assert tool.calls == 0
        # Refused before it could start.
        assert emitter.of_type(EventType.TOOL_STARTED) == []

    async def test_malformed_arguments_are_refused_without_running(self) -> None:
        """A wrong argument type is caught by the schema, before the tool runs."""
        tool = EchoTool()
        runtime, emitter = runtime_for(
            ScriptedPlanner(
                [
                    ToolRequest(tool="echo", arguments={"value": 123}),
                    ToolRequest(tool="echo", arguments={"value": "ok"}),
                ]
            ),
            [tool],
        )
        run = await runtime.run()

        assert run.steps[0].ok is False
        assert "invalid arguments" in (run.steps[0].error or "")
        # It never started, and the run recovered on the next, valid request.
        assert emitter.of_type(EventType.TOOL_STARTED) and tool.calls == 1
        assert run.steps[1].ok is True
        assert run.status is AgentStatus.COMPLETED

    async def test_a_planner_failure_ends_the_run_cleanly(self) -> None:
        runtime, _ = runtime_for(BrokenPlanner(), [EchoTool()])
        run = await runtime.run()

        assert run.status is AgentStatus.FAILED
        assert "planner failed" in (run.error or "")


class TestNoChainOfThoughtIsPersisted:
    async def test_a_long_reason_is_truncated(self) -> None:
        from repolab.agents.models import MAX_REASON_LENGTH

        request = ToolRequest(tool="echo", reason=SECRET_COT * 50)
        assert len(request.reason) == MAX_REASON_LENGTH

    async def test_step_status_is_bounded(self) -> None:
        from repolab.agents.models import MAX_STATUS_LENGTH

        class VerboseTool:
            name = "verbose"
            description = "Returns a very long summary."
            requires_approval = False
            arguments_model = EchoArgs

            async def execute(self, arguments: dict[str, Any]) -> ToolResult:
                return ToolResult(summary="y" * 10_000)

        runtime, _ = runtime_for(ScriptedPlanner([ToolRequest(tool="verbose")]), [VerboseTool()])
        run = await runtime.run()
        assert len(run.steps[0].status) <= MAX_STATUS_LENGTH

    async def test_step_records_hold_no_free_form_model_text(self) -> None:
        """A step carries a tool name, a status and a duration -- nothing else."""
        runtime, _ = runtime_for(ScriptedPlanner([ToolRequest(tool="echo")]), [EchoTool()])
        run = await runtime.run()

        allowed = {
            "index",
            "request",
            "result",
            "status",
            "ok",
            "attempts",
            "duration_ms",
            "error",
        }
        assert set(run.steps[0].model_dump()) == allowed


class TestRunSummary:
    async def test_counts_and_failures_are_reported(self) -> None:
        runtime, _ = runtime_for(
            ScriptedPlanner([ToolRequest(tool="echo"), ToolRequest(tool="does_not_exist")]),
            [EchoTool()],
        )
        run = await runtime.run()

        assert run.tool_calls == 2
        assert len(run.failed_steps) == 1

    @pytest.mark.parametrize(
        ("status", "terminal"),
        [
            (AgentStatus.RUNNING, False),
            (AgentStatus.COMPLETED, True),
            (AgentStatus.FAILED, True),
            (AgentStatus.EXHAUSTED, True),
        ],
    )
    def test_terminality(self, status: AgentStatus, terminal: bool) -> None:
        assert status.is_terminal is terminal
