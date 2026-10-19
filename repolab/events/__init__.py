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
from repolab.events.heartbeat import (
    HEARTBEAT_INTERVAL_SECONDS,
    STALL_THRESHOLD_SECONDS,
    Heartbeat,
    StallReport,
    detect_stall,
)
from repolab.events.models import EventType, JobEvent, JobStatus

__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "PHASES",
    "PHASE_COUNT",
    "STALL_THRESHOLD_SECONDS",
    "ActivityPhase",
    "ActivityReporter",
    "CollectingEmitter",
    "DbEventEmitter",
    "EventEmitter",
    "EventType",
    "Heartbeat",
    "JobEvent",
    "JobStatus",
    "NullEmitter",
    "StallReport",
    "detect_stall",
    "phase_by_key",
    "phase_for",
    "phase_index",
]
