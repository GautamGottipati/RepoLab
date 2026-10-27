"""Job and agent event emission.

Every long-running action emits events; they are persisted in Postgres and
streamed over SSE so that refreshing a browser replays history and continues.
"""

from __future__ import annotations

from repolab.events.activity import (
    PHASE_COUNT,
    PHASES,
    ActivityPhase,
    ActivityReporter,
    phase_by_key,
    phase_for,
    phase_index,
)
from repolab.events.emitter import (
    CollectingEmitter,
    DbEventEmitter,
    EventEmitter,
    NullEmitter,
)
from repolab.events.liveness import (
    HEARTBEAT_INTERVAL_SECONDS,
    INTERRUPTED_AFTER_SECONDS,
    Health,
    Liveness,
    LivenessRead,
    NoLiveness,
    job_key,
    read_liveness,
    session_key,
    task_health,
)
from repolab.events.models import EventType, JobEvent, JobStatus

__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "INTERRUPTED_AFTER_SECONDS",
    "PHASES",
    "PHASE_COUNT",
    "ActivityPhase",
    "ActivityReporter",
    "CollectingEmitter",
    "DbEventEmitter",
    "EventEmitter",
    "EventType",
    "Health",
    "JobEvent",
    "JobStatus",
    "Liveness",
    "LivenessRead",
    "NoLiveness",
    "NullEmitter",
    "job_key",
    "phase_by_key",
    "phase_for",
    "phase_index",
    "read_liveness",
    "session_key",
    "task_health",
]
