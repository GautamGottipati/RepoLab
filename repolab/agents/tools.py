"""The tool layer.

Agents never execute anything themselves. They name a tool and supply arguments;
deterministic code then decides whether that tool exists, whether the arguments
are valid, and whether the action needs approval. An agent cannot reach the
shell, the filesystem outside its repository context, sandbox infrastructure, a
database, or the network except through a registered tool.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from repolab.agents.models import ToolRequest, ToolResult


class ToolError(Exception):
    """A tool request could not be executed."""


class UnknownToolError(ToolError):
    """The agent named a tool that is not registered.

    Refused before anything runs: an agent cannot invent capabilities.
    """

    def __init__(self, tool: str, available: list[str]) -> None:
        self.tool = tool
        self.available = available
        super().__init__(
            f"unknown tool {tool!r}; available: {', '.join(sorted(available)) or 'none'}"
        )


class InvalidArgumentsError(ToolError):
    """The arguments did not match the tool's schema."""

    def __init__(self, tool: str, detail: str) -> None:
        self.tool = tool
        super().__init__(f"invalid arguments for {tool!r}: {detail}")


class ApprovalRequiredError(ToolError):
    """The tool needs explicit user approval, which has not been given.

    Agents cannot bypass approval gates; the gate lives here, not in a prompt.
    """

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"{tool!r} requires user approval")


@runtime_checkable
class AgentTool(Protocol):
    """A deterministic action an agent may request."""

    name: str
    description: str
    requires_approval: bool
    arguments_model: type[BaseModel]

    async def execute(self, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    """The set of tools one agent is allowed to use.

    Validation happens here so that an agent's request is checked by application
    code before any effect occurs.
    """

    def __init__(self, tools: list[AgentTool] | None = None) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> AgentTool:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(name, self.names)
        return tool

    def describe(self) -> list[dict[str, Any]]:
        """Tool descriptions, for a planner that needs to choose between them."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "arguments": sorted(tool.arguments_model.model_fields),
                "requires_approval": tool.requires_approval,
            }
            for tool in (self._tools[name] for name in self.names)
        ]

    def validate(self, request: ToolRequest, *, approved: bool = False) -> AgentTool:
        """Check a request without executing it.

        Raises:
            UnknownToolError: the tool is not registered.
            InvalidArgumentsError: the arguments do not match the schema.
            ApprovalRequiredError: the tool needs approval that was not given.
        """
        tool = self.get(request.tool)
        if tool.requires_approval and not approved:
            raise ApprovalRequiredError(tool.name)
        try:
            tool.arguments_model.model_validate(request.arguments)
        except ValidationError as exc:
            raise InvalidArgumentsError(tool.name, _brief(exc)) from exc
        return tool

    async def execute(self, request: ToolRequest, *, approved: bool = False) -> ToolResult:
        """Validate then run *request*."""
        tool = self.validate(request, approved=approved)
        return await tool.execute(request.arguments)


def _brief(exc: ValidationError) -> str:
    """A short description of a validation failure."""
    errors = exc.errors()
    if not errors:
        return "validation failed"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "arguments"
    return f"{location}: {first.get('msg', 'invalid')}"
