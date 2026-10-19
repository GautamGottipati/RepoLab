"""The agent execution model.

Agents reason and choose actions. Tools perform actions. Deterministic
application code validates state and enforces permissions (CLAUDE.md), so the
types here describe *requests* and *results* rather than giving an agent any
capability of its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

#: Longest status line kept for a step. Bounded so a status can never become a
#: channel for pasting model output.
MAX_STATUS_LENGTH = 500

#: Longest reason a planner may give for an action.
MAX_REASON_LENGTH = 300


class AgentStatus(StrEnum):
    """Where an agent run stands."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    """Hit its iteration budget before finishing."""

    @property
    def is_terminal(self) -> bool:
        return self is not AgentStatus.RUNNING


class AgentLimits(BaseModel):
    """Bounds on one agent run. Every agent runs inside a budget."""

    max_iterations: int = Field(default=24, gt=0)
    max_tool_retries: int = Field(default=2, ge=0)
    """Retries per tool call, after the first attempt."""

    step_timeout_seconds: float = Field(default=60.0, gt=0)
    max_consecutive_repeats: int = Field(default=2, gt=0)
    """A planner repeating the same request this many times has stalled."""


class ToolRequest(BaseModel):
    """An action an agent has asked for. Not yet validated, not yet executed."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    """Why the agent wants this. User-facing and length-capped."""

    def model_post_init(self, _context: object) -> None:
        if len(self.reason) > MAX_REASON_LENGTH:
            object.__setattr__(self, "reason", self.reason[:MAX_REASON_LENGTH])

    @property
    def signature(self) -> str:
        """Identity of this request, for detecting a planner that is looping."""
        rendered = ",".join(f"{k}={v}" for k, v in sorted(self.arguments.items()))
        return f"{self.tool}({rendered})"


class ToolResult(BaseModel):
    """What a tool produced."""

    ok: bool = True
    summary: str = ""
    """A concise, user-facing description of what happened."""

    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @classmethod
    def failure(cls, error: str) -> ToolResult:
        return cls(ok=False, summary=f"failed: {error}", error=error)


class AgentStep(BaseModel):
    """One iteration of an agent loop."""

    index: int
    request: ToolRequest | None = None
    result: ToolResult | None = None
    status: str = ""
    """Concise user-facing status. Never model chain-of-thought."""

    ok: bool = True
    attempts: int = 1
    duration_ms: float = 0.0
    error: str | None = None

    def model_post_init(self, _context: object) -> None:
        if len(self.status) > MAX_STATUS_LENGTH:
            object.__setattr__(self, "status", self.status[:MAX_STATUS_LENGTH])


class AgentRun(BaseModel):
    """One complete agent execution, and its audit trail."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent: str
    objective: str = ""
    status: AgentStatus = AgentStatus.RUNNING
    planner: str = "deterministic"

    limits: AgentLimits = Field(default_factory=AgentLimits)
    steps: list[AgentStep] = Field(default_factory=list)
    error: str | None = None

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def iterations(self) -> int:
        return len(self.steps)

    @property
    def tool_calls(self) -> int:
        return sum(1 for step in self.steps if step.request is not None)

    @property
    def failed_steps(self) -> list[AgentStep]:
        return [step for step in self.steps if not step.ok]

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    def finish(self, status: AgentStatus, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.finished_at = datetime.now(UTC)
