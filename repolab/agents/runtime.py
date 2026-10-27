"""The agent execution loop.

This is where "agentic" is made safe. The loop is explicit and bounded: ask the
planner for one action, validate it in deterministic code, execute it through the
tool layer, record what happened, repeat until the objective is met or a budget
is spent.

Nothing here lets an agent act directly. It cannot reach a shell, the filesystem
outside its repository context, sandbox infrastructure, a database or the network
except by naming a registered tool, and every invocation emits an event.

What is recorded is concise and user-facing: a tool name, a short status, a
duration. Private model chain-of-thought is never persisted (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from repolab.agents.models import (
    AgentLimits,
    AgentRun,
    AgentStatus,
    AgentStep,
    ToolRequest,
    ToolResult,
)
from repolab.agents.planner import Planner
from repolab.agents.tools import (
    ApprovalRequiredError,
    InvalidArgumentsError,
    ToolRegistry,
    UnknownToolError,
)
from repolab.events import ActivityReporter, EventEmitter, EventType, NullEmitter

log = structlog.get_logger(__name__)


class AgentRuntime:
    """Runs one agent to completion within its limits."""

    def __init__(
        self,
        *,
        agent: str,
        objective: str,
        registry: ToolRegistry,
        planner: Planner,
        emitter: EventEmitter | None = None,
        limits: AgentLimits | None = None,
        step_label: str | None = None,
        activity: ActivityReporter | None = None,
    ) -> None:
        self.agent = agent
        self.objective = objective
        self.registry = registry
        self.planner = planner
        self.emitter = emitter if emitter is not None else NullEmitter()
        self.limits = limits if limits is not None else AgentLimits()
        self.step_label = step_label or agent
        self.activity = activity
        """Optional. When present, each tool also announces its user-facing phase."""

    async def run(self, context_provider: Any = None) -> AgentRun:
        """Execute the agent loop.

        *context_provider* is an optional callable returning what the agent has
        learned so far, which the planner uses to choose evidence-driven actions.
        """
        run = AgentRun(
            agent=self.agent,
            objective=self.objective,
            planner=self.planner.name,
            limits=self.limits,
        )
        self.emitter.emit(
            EventType.AGENT_STARTED,
            f"{self.agent} started",
            step=self.step_label,
            data={"objective": self.objective, "planner": self.planner.name},
        )

        repeats = 0
        last_signature: str | None = None

        while True:
            if run.iterations >= self.limits.max_iterations:
                return self._exhausted(run)

            context = self._context(context_provider)
            try:
                request = await self.planner.next_request(run, context)
            except Exception as exc:  # noqa: BLE001 - a planner failure ends the run cleanly
                return self._failed(run, f"planner failed: {type(exc).__name__}: {exc}")

            if request is None:
                return self._completed(run)

            # A planner asking for the same thing repeatedly is stuck, and would
            # otherwise burn the whole iteration budget.
            if request.signature == last_signature:
                repeats += 1
                if repeats >= self.limits.max_consecutive_repeats:
                    return self._failed(
                        run, f"planner repeated {request.signature} without progress"
                    )
            else:
                repeats = 0
            last_signature = request.signature

            step = await self._execute(run, request)
            run.steps.append(step)
            if not step.ok and step.error and _is_fatal(step.error):
                return self._failed(run, step.error)

    # -- one iteration ----------------------------------------------------

    async def _execute(self, run: AgentRun, request: ToolRequest) -> AgentStep:
        index = run.iterations + 1

        # Announce the user-facing phase this tool belongs to, before the tool
        # events themselves. The vocabulary lives in repolab.events.activity, so
        # a watcher gets "Finding entry points" rather than
        # "detect_entrypoints" without the runtime knowing any labels.
        if self.activity is not None:
            self.activity.observe(tool=request.tool)

        self.emitter.emit(
            EventType.TOOL_REQUESTED,
            f"{request.tool}: {request.reason}" if request.reason else request.tool,
            step=self.step_label,
            data={"tool": request.tool, "arguments": request.arguments, "index": index},
        )

        try:
            self.registry.validate(request)
        except (UnknownToolError, InvalidArgumentsError, ApprovalRequiredError) as exc:
            # Refused before anything ran.
            message = str(exc)
            self.emitter.emit(
                EventType.TOOL_COMPLETED,
                f"{request.tool} refused: {message}",
                step=self.step_label,
                data={"tool": request.tool, "ok": False, "refused": True},
            )
            return AgentStep(
                index=index,
                request=request,
                result=ToolResult.failure(message),
                status=f"refused: {message}",
                ok=False,
                error=message,
            )

        self.emitter.emit(
            EventType.TOOL_STARTED,
            f"running {request.tool}",
            step=self.step_label,
            data={"tool": request.tool, "index": index},
        )

        started = time.perf_counter()
        result, attempts, error = await self._with_retries(request)
        duration_ms = (time.perf_counter() - started) * 1000

        status = result.summary if result.ok else f"{request.tool} failed: {error}"
        self.emitter.emit(
            EventType.TOOL_COMPLETED,
            status,
            step=self.step_label,
            data={
                "tool": request.tool,
                "ok": result.ok,
                "attempts": attempts,
                "duration_ms": round(duration_ms, 1),
            },
        )
        if result.ok:
            self.emitter.emit(
                EventType.PROGRESS,
                status,
                step=self.step_label,
                data={"completed_steps": index, "max_steps": self.limits.max_iterations},
            )

        return AgentStep(
            index=index,
            request=request,
            result=result,
            status=status,
            ok=result.ok,
            attempts=attempts,
            duration_ms=round(duration_ms, 3),
            error=error,
        )

    async def _with_retries(self, request: ToolRequest) -> tuple[ToolResult, int, str | None]:
        """Execute *request*, retrying a transient failure within the budget."""
        attempts = 0
        last_error: str | None = None

        while attempts <= self.limits.max_tool_retries:
            attempts += 1
            try:
                result = await asyncio.wait_for(
                    self.registry.execute(request),
                    timeout=self.limits.step_timeout_seconds,
                )
            except TimeoutError:
                last_error = f"timed out after {self.limits.step_timeout_seconds:.0f}s"
            except Exception as exc:  # noqa: BLE001 - normalised into a failed result
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if result.ok:
                    return result, attempts, None
                last_error = result.error or result.summary
                # A tool that reports a definite negative (a missing file, a
                # refused path) will report the same thing again, so do not retry.
                return result, attempts, last_error

            if attempts <= self.limits.max_tool_retries:
                self.emitter.emit(
                    EventType.LOG_LINE,
                    f"retrying {request.tool} after: {last_error}",
                    step=self.step_label,
                    data={"tool": request.tool, "attempt": attempts},
                )

        return ToolResult.failure(last_error or "unknown error"), attempts, last_error

    # -- terminal states --------------------------------------------------

    def _context(self, context_provider: Any) -> dict[str, Any]:
        if context_provider is None:
            return {}
        try:
            return dict(context_provider())
        except Exception:  # noqa: BLE001 - context is advisory
            return {}

    def _completed(self, run: AgentRun) -> AgentRun:
        run.finish(AgentStatus.COMPLETED)
        self.emitter.emit(
            EventType.AGENT_STATUS,
            f"{self.agent} finished after {run.iterations} step(s)",
            step=self.step_label,
            data={"status": run.status.value, "iterations": run.iterations},
        )
        return run

    def _exhausted(self, run: AgentRun) -> AgentRun:
        message = f"reached the {self.limits.max_iterations}-step limit"
        run.finish(AgentStatus.EXHAUSTED, message)
        self.emitter.emit(
            EventType.AGENT_STATUS,
            f"{self.agent} stopped: {message}",
            step=self.step_label,
            data={"status": run.status.value, "iterations": run.iterations},
        )
        log.info("agent.exhausted", agent=self.agent, iterations=run.iterations)
        return run

    def _failed(self, run: AgentRun, error: str) -> AgentRun:
        run.finish(AgentStatus.FAILED, error)
        self.emitter.emit(
            EventType.FAILED,
            f"{self.agent} failed: {error}",
            step=self.step_label,
            data={"status": run.status.value, "iterations": run.iterations},
        )
        return run


#: Failures that mean the run cannot usefully continue.
_FATAL_FRAGMENTS = ("unknown tool", "requires user approval")


def _is_fatal(error: str) -> bool:
    lowered = error.lower()
    return any(fragment in lowered for fragment in _FATAL_FRAGMENTS)
