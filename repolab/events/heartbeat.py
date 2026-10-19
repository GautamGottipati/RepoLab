"""Heartbeats, elapsed time and stall detection.

A job that has gone quiet is indistinguishable from a job that is working hard,
unless it says otherwise. Heartbeats carry elapsed time so a reader can tell the
difference, and :func:`detect_stall` turns "no events for a while" into an
explicit signal rather than something a user has to infer from a spinner.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from pydantic import BaseModel

from repolab.events.emitter import EventEmitter
from repolab.events.models import EventType

#: How often a running job reports that it is still alive.
HEARTBEAT_INTERVAL_SECONDS = 5.0

#: Silence beyond this is reported as a stall.
STALL_THRESHOLD_SECONDS = 30.0


class StallReport(BaseModel):
    """Whether a job has gone quiet, and for how long."""

    stalled: bool
    seconds_since_last_event: float | None = None
    threshold_seconds: float = STALL_THRESHOLD_SECONDS

    @property
    def summary(self) -> str:
        if self.seconds_since_last_event is None:
            return "no events recorded yet"
        if self.stalled:
            return f"no progress for {self.seconds_since_last_event:.0f}s"
        return f"last progress {self.seconds_since_last_event:.0f}s ago"


def detect_stall(
    last_event_at: datetime | None,
    *,
    now: datetime | None = None,
    threshold_seconds: float = STALL_THRESHOLD_SECONDS,
) -> StallReport:
    """Whether the gap since *last_event_at* exceeds *threshold_seconds*."""
    if last_event_at is None:
        return StallReport(stalled=False, threshold_seconds=threshold_seconds)

    moment = now or datetime.now(UTC)
    if last_event_at.tzinfo is None:
        last_event_at = last_event_at.replace(tzinfo=UTC)

    elapsed = max(0.0, (moment - last_event_at).total_seconds())
    return StallReport(
        stalled=elapsed > threshold_seconds,
        seconds_since_last_event=round(elapsed, 3),
        threshold_seconds=threshold_seconds,
    )


class Heartbeat:
    """Emits a periodic heartbeat for as long as it is running."""

    def __init__(
        self,
        emitter: EventEmitter,
        *,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        step: str | None = None,
    ) -> None:
        self._emitter = emitter
        self._interval = interval_seconds
        self._step = step
        self._task: asyncio.Task[None] | None = None

    def beat(self, elapsed_seconds: float) -> None:
        """Emit a single heartbeat."""
        self._emitter.emit(
            EventType.HEARTBEAT,
            f"still working ({elapsed_seconds:.0f}s elapsed)",
            step=self._step,
            data={"elapsed_seconds": round(elapsed_seconds, 3)},
        )

    async def _loop(self) -> None:
        started = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(self._interval)
            self.beat(asyncio.get_running_loop().time() - started)

    async def __aenter__(self) -> Heartbeat:
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
