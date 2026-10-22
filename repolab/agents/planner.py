"""Planners: how an agent chooses its next action.

A planner only ever *proposes*. Whether the proposal is a real tool, whether its
arguments are valid, and whether it is permitted are decided afterwards by
:class:`~repolab.agents.tools.ToolRegistry`, so a planner -- model-driven or not --
has no capability of its own.

Two implementations:

* :class:`DeterministicPlanner` chooses from what has been learned so far. It is
  evidence-driven rather than a fixed script: it reads the config files that were
  actually found, and searches documentation only for a framework that was
  actually detected. It needs no credentials, so investigation works without a
  model.
* :class:`SuperPlanner` asks a model for a structured action request. Model output
  is data to be validated, never code to be run.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from repolab.agents.models import MAX_REASON_LENGTH, AgentRun, ToolRequest
from repolab.llm import LLMError, ModelRouter, Purpose


@runtime_checkable
class Planner(Protocol):
    """Chooses the next action, or ``None`` when the objective is met."""

    name: str

    async def next_request(self, run: AgentRun, context: dict[str, Any]) -> ToolRequest | None: ...


class DeterministicPlanner:
    """Evidence-driven planning with no model involved.

    The order reflects what is cheap and broadly informative first: see the tree,
    then what the repository declares, then what it contains, then -- only if a
    framework was actually identified -- what its official documentation says.
    """

    name = "deterministic"

    #: The opening sequence. Every repository benefits from these.
    BASE_PLAN: tuple[tuple[str, dict[str, Any], str], ...] = (
        ("list_files", {}, "See what the repository contains"),
        ("inspect_dependencies", {}, "Find what the project declares it needs"),
        ("detect_entrypoints", {}, "Find how the software is started"),
        ("detect_services", {}, "Find external services it expects"),
        ("detect_surfaces", {}, "Look for interaction-surface signals in the source"),
        ("inspect_config", {}, "Read configuration that governs how it runs"),
    )

    def __init__(self, *, research_available: bool = False) -> None:
        self._research_available = research_available

    async def next_request(self, run: AgentRun, context: dict[str, Any]) -> ToolRequest | None:
        done = {step.request.tool for step in run.steps if step.request is not None}

        for tool, arguments, reason in self.BASE_PLAN:
            if tool not in done:
                return ToolRequest(tool=tool, arguments=arguments, reason=reason)

        # Follow up on what was found rather than guessing: read a configuration
        # file only because one was detected.
        config_paths: list[str] = context.get("config_paths", [])
        read_already: set[str] = {
            str(step.request.arguments.get("path"))
            for step in run.steps
            if step.request is not None and step.request.tool == "read_file"
        }
        for path in config_paths[:3]:
            if path not in read_already:
                return ToolRequest(
                    tool="read_file",
                    arguments={"path": path},
                    reason=f"Inspect the settings declared in {path}",
                )

        # Search official documentation only for a framework actually detected.
        frameworks: list[str] = context.get("frameworks", [])
        if self._research_available and frameworks and "search_official_docs" not in done:
            return ToolRequest(
                tool="search_official_docs",
                arguments={"query": f"{frameworks[0]} getting started configuration"},
                reason=f"Check official {frameworks[0]} documentation",
            )

        return None


class PlannedAction(BaseModel):
    """The structured action a model is asked for.

    Deliberately narrow: a tool name, its arguments, and a short user-facing
    reason. No free-form instruction, and nothing that could be executed.
    """

    action: str = Field(description="Exactly one of the tool names listed in the prompt")
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", description="One short sentence on why this action helps")
    done: bool = Field(
        default=False, description="True when the repository is understood well enough"
    )


class SuperPlanner:
    """Asks Super for the next action.

    Falls back to the deterministic planner whenever the model is unavailable or
    proposes something unusable, so investigation always makes progress.
    """

    name = "super"

    def __init__(
        self,
        router: ModelRouter,
        tool_descriptions: list[dict[str, Any]],
        *,
        fallback: Planner | None = None,
    ) -> None:
        self._router = router
        self._tools = tool_descriptions
        self._names = {tool["name"] for tool in tool_descriptions}
        self._fallback = fallback or DeterministicPlanner()

    async def next_request(self, run: AgentRun, context: dict[str, Any]) -> ToolRequest | None:
        prompt = self._build_prompt(run, context)
        try:
            completion = await self._router.super(
                purpose=Purpose.REPO_INVESTIGATION,
                schema=PlannedAction,
                system=(
                    "You investigate a software repository by requesting one tool at "
                    "a time. You may only request the tools listed. Never invent a "
                    "tool or an argument name."
                ),
                prompt=prompt,
                max_tokens=512,
            )
        except LLMError:
            # A model failure must not stall the investigation.
            return await self._fallback.next_request(run, context)

        action = completion.value
        if action.done:
            return None
        if action.action not in self._names:
            # The model named something that does not exist; fall back rather than
            # pass an invalid request down the chain.
            return await self._fallback.next_request(run, context)

        request = ToolRequest(
            tool=action.action,
            arguments=action.arguments,
            reason=action.reason[:MAX_REASON_LENGTH],
        )

        # Asking again for something that already succeeded learns nothing new.
        # Observed: Super re-ran list_files() until the runtime's stall guard
        # failed the whole investigation, which had all the evidence it needed.
        # The deterministic planner moves on instead -- it only proposes tools not
        # yet run, and says "done" when there are none.
        if request.signature in _succeeded(run):
            return await self._fallback.next_request(run, context)
        return request

    def _build_prompt(self, run: AgentRun, context: dict[str, Any]) -> str:
        tool_lines = "\n".join(
            f"- {tool['name']}({', '.join(tool['arguments'])}): {tool['description']}"
            for tool in self._tools
        )
        history = "\n".join(
            f"- {step.request.signature}: {step.status}{_digest(step)}"
            for step in run.steps[-10:]
            if step.request is not None
        )
        known = ", ".join(
            f"{key}={value}"
            for key, value in context.items()
            if isinstance(value, (int, str)) or (isinstance(value, list) and len(value) < 12)
        )
        return (
            f"Objective: {run.objective}\n\n"
            f"Tools available:\n{tool_lines}\n\n"
            f"Actions already taken:\n{history or '(none yet)'}\n\n"
            f"What is known so far: {known or '(nothing yet)'}\n\n"
            "Request the single most useful next action, or set done=true if the "
            "repository is understood well enough to build a manifest. Never repeat "
            "an action already taken with the same arguments: its result is shown "
            "above. Use file paths exactly as listed."
        )


#: How much of a tool's result reaches the planner's prompt.
_MAX_DIGEST_PATHS = 60
_MAX_DIGEST_MATCHES = 8


def _succeeded(run: AgentRun) -> set[str]:
    """Signatures of the requests that already ran successfully."""
    return {step.request.signature for step in run.steps if step.request is not None and step.ok}


def _digest(step: Any) -> str:
    """What a step returned, compactly, so the planner can act on it.

    A status line such as "listed 21 of 21 matching files" tells a model that
    files exist but not which, and a model that cannot see the paths guesses
    them -- this one asked to read ``etl_job`` when the file is
    ``jobs/etl_job.py``, then listed the files again to find out.
    """
    result = getattr(step, "result", None)
    if result is None or not result.ok:
        return ""
    data = result.data or {}
    paths = data.get("paths")
    if isinstance(paths, list) and paths:
        shown = ", ".join(str(path) for path in paths[:_MAX_DIGEST_PATHS])
        more = (
            f" (+{len(paths) - _MAX_DIGEST_PATHS} more)" if len(paths) > _MAX_DIGEST_PATHS else ""
        )
        return f"\n    files: {shown}{more}"
    matches = data.get("matches")
    if isinstance(matches, list) and matches:
        shown = ", ".join(
            f"{match.get('path')}:{match.get('line')}"
            for match in matches[:_MAX_DIGEST_MATCHES]
            if isinstance(match, dict)
        )
        return f"\n    matches: {shown}"
    return ""
