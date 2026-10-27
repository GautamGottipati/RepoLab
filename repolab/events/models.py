"""Job and agent progress events.

Every long-running action emits events. They are persisted so that refreshing a
browser replays history rather than losing it, and streamed so progress is
visible while work is happening.

Events carry **concise, user-facing** information only. Private model
chain-of-thought is never placed in an event (CLAUDE.md), so ``message`` is a
short status line and ``data`` holds structured facts, never raw model output.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

#: Longest message an event may carry. Keeps the stream readable and makes it
#: impossible to smuggle a wall of model output through a status line.
MAX_MESSAGE_LENGTH = 500


class EventType(StrEnum):
    """Every event RepoLab emits."""

    # Agent lifecycle
    AGENT_STARTED = "agent_started"
    AGENT_STATUS = "agent_status"

    # Tool lifecycle
    TOOL_REQUESTED = "tool_requested"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"

    # Job progress
    STEP_STARTED = "step_started"
    PROGRESS = "progress"
    LOG_LINE = "log_line"
    PARTIAL_RESULT = "partial_result"
    STEP_DONE = "step_done"
    FAILED = "failed"
    HEARTBEAT = "heartbeat"

    # Approval gates. An experiment waits in `approval_required` until the user
    # answers; nothing executes in between.
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RECEIVED = "approval_received"

    # Experiment variants
    EXPERIMENT_VARIANT_STARTED = "experiment_variant_started"
    EXPERIMENT_VARIANT_COMPLETED = "experiment_variant_completed"

    # Environment upgrades. An upgrade is *offered* and only applied when the
    # learner is not mid-command, so the two are distinct events.
    UPGRADE_AVAILABLE = "upgrade_available"
    UPGRADE_APPLIED = "upgrade_applied"

    # Analysis artifacts. Each carries ``{artifact_type, status, version,
    # cache_hit}`` -- never the payload, which is fetched by type.
    ARTIFACT_QUEUED = "artifact_queued"
    ARTIFACT_STARTED = "artifact_started"
    ARTIFACT_VERIFYING = "artifact_verifying"
    ARTIFACT_READY = "artifact_ready"
    ARTIFACT_UPDATED = "artifact_updated"
    ARTIFACT_FAILED = "artifact_failed"
    ARTIFACT_BLOCKED = "artifact_blocked"


class JobStatus(StrEnum):
    """Where a job is in its lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.FAILED}


class JobEvent(BaseModel):
    """One event in a job's stream.

    ``seq`` is assigned by the emitter and is contiguous from 1 within a job, which
    is what lets a reader resume exactly where it left off.
    """

    job_id: str
    seq: int = Field(ge=1)
    type: EventType
    message: str = ""
    step: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def model_post_init(self, _context: object) -> None:
        if len(self.message) > MAX_MESSAGE_LENGTH:
            object.__setattr__(self, "message", self.message[:MAX_MESSAGE_LENGTH])

    def to_sse(self) -> str:
        """Render as an SSE frame.

        The ``id:`` field is what makes browser resumption work: ``EventSource``
        sends it back as ``Last-Event-ID`` after a reconnect.
        """
        payload = self.model_dump_json()
        return f"id: {self.seq}\nevent: {self.type.value}\ndata: {payload}\n\n"
