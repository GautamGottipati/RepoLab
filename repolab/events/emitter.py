"""Event emission.

The sequence number is assigned **here**, not by a database serial. A
``BIGSERIAL`` can become visible to a reader out of order relative to its value,
which would let a poller skip an event permanently. Exactly one worker owns a job,
so an in-process counter is contiguous, and the database enforces that with a
uniqueness constraint.

Emitters are synchronous on purpose: they are called from both async agent code
and the synchronous RQ task, and a sync call works on either side of that seam.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

from repolab.events.models import EventType, JobEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)


@runtime_checkable
class EventEmitter(Protocol):
    """Records progress for one job."""

    def emit(
        self,
        event_type: EventType,
        message: str = "",
        *,
        step: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> JobEvent: ...


class _SequencedEmitter:
    """Shared sequence handling for emitters that belong to a single job."""

    def __init__(self, job_id: str, *, start_seq: int = 0) -> None:
        self.job_id = job_id
        self._seq = start_seq
        self._lock = threading.Lock()
        self.started_at = time.monotonic()

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def elapsed_seconds(self) -> float:
        return round(time.monotonic() - self.started_at, 3)

    def _next(
        self,
        event_type: EventType,
        message: str,
        step: str | None,
        data: dict[str, Any] | None,
    ) -> JobEvent:
        with self._lock:
            self._seq += 1
            seq = self._seq
        return JobEvent(
            job_id=self.job_id,
            seq=seq,
            type=event_type,
            message=message,
            step=step,
            data=data or {},
        )


class CollectingEmitter(_SequencedEmitter):
    """Keeps events in memory. Used by tests and by dry runs."""

    def __init__(self, job_id: str = "test-job", *, start_seq: int = 0) -> None:
        super().__init__(job_id, start_seq=start_seq)
        self.events: list[JobEvent] = []

    def emit(
        self,
        event_type: EventType,
        message: str = "",
        *,
        step: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> JobEvent:
        event = self._next(event_type, message, step, data)
        self.events.append(event)
        return event

    def types(self) -> list[EventType]:
        return [event.type for event in self.events]

    def of_type(self, event_type: EventType) -> list[JobEvent]:
        return [event for event in self.events if event.type is event_type]


class NullEmitter(_SequencedEmitter):
    """Discards events. For code paths where progress reporting is not wanted."""

    def __init__(self, job_id: str = "null") -> None:
        super().__init__(job_id)

    def emit(
        self,
        event_type: EventType,
        message: str = "",
        *,
        step: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> JobEvent:
        return self._next(event_type, message, step, data)


class DbEventEmitter(_SequencedEmitter):
    """Persists each event immediately so a reader can see it while work runs.

    Committing per event is deliberate: batching would mean a progress stream that
    only appears once the job is already finished.
    """

    def __init__(
        self,
        job_id: str,
        session_factory: Callable[[], AbstractContextManager[Session]],
        *,
        start_seq: int = 0,
    ) -> None:
        super().__init__(job_id, start_seq=start_seq)
        self._session_factory = session_factory

    def emit(
        self,
        event_type: EventType,
        message: str = "",
        *,
        step: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> JobEvent:
        event = self._next(event_type, message, step, data)
        from repolab.db.jobs import append_event

        try:
            with self._session_factory() as session:
                append_event(session, event)
                session.commit()
        except Exception as exc:  # noqa: BLE001 - progress must not break the job
            log.warning("events.persist_failed", job_id=self.job_id, seq=event.seq, error=str(exc))
        return event
