"""Liveness: whether a worker is still on a task, and what it is doing.

Two different questions, kept apart on purpose:

- **Progress** -- a meaningful milestone completed. Persisted as events in
  ``job_events``; that is the history a refreshed page replays.
- **Heartbeat** -- the worker process is alive. Ephemeral: a Redis hash with a
  short TTL, refreshed every few seconds by a thread, never written to the
  event table.

A model call can run for a minute without a single milestone. Silence in the
event table is therefore not evidence of anything; only a heartbeat that has
stopped is. :func:`task_health` encodes exactly that.

The hash also carries the task's *current activity*: a stage and a machine key
(``architecture.components``, ``finding_entrypoints``, ``image``) set where the
work actually begins. Keys, never prose -- the words a person reads live in the
frontend's copy map, so nothing internal or model-generated reaches the screen.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Protocol

import structlog
from pydantic import BaseModel

from repolab.events.models import JobStatus

log = structlog.get_logger(__name__)

#: How often a running task's thread refreshes its heartbeat.
HEARTBEAT_INTERVAL_SECONDS = 5.0

#: A heartbeat not refreshed for this long disappears. Several missed beats,
#: so a briefly busy machine never looks dead.
HEARTBEAT_TTL_SECONDS = 45

#: A running task with no heartbeat is only called interrupted once it has
#: been running this long: before that the worker may simply not have started
#: beating yet (or be an older worker that never beats).
INTERRUPTED_AFTER_SECONDS = 90.0

PREFIX = "repolab:live:"


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


def session_key(session_id: str) -> str:
    return f"session:{session_id}"


# -- storage ------------------------------------------------------------------


class LivenessUnavailable(RuntimeError):
    """The store could not be read. Means *unknown*, never *interrupted*."""


class LivenessStore(Protocol):
    def write(self, key: str, fields: dict[str, str], ttl_seconds: int) -> None: ...

    def read(self, key: str) -> dict[str, str] | None:
        """The fields, ``None`` when absent. Raises :class:`LivenessUnavailable`."""
        ...

    def delete(self, key: str) -> None: ...


class RedisLivenessStore:
    def __init__(self, url: str) -> None:
        from redis import Redis

        self._redis = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    def write(self, key: str, fields: dict[str, str], ttl_seconds: int) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(PREFIX + key)
        pipe.hset(PREFIX + key, mapping=fields)
        pipe.expire(PREFIX + key, ttl_seconds)
        pipe.execute()

    def read(self, key: str) -> dict[str, str] | None:
        try:
            raw = self._redis.hgetall(PREFIX + key)
        except Exception as exc:
            raise LivenessUnavailable(str(exc)) from exc
        if not raw:
            return None
        return {k.decode(): v.decode() for k, v in raw.items()}

    def delete(self, key: str) -> None:
        self._redis.delete(PREFIX + key)


class MemoryLivenessStore:
    """In-process store with real TTLs. For tests, and for running without Redis."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[dict[str, str], float]] = {}
        self._lock = threading.Lock()
        self.unavailable = False

    def write(self, key: str, fields: dict[str, str], ttl_seconds: int) -> None:
        with self._lock:
            self._data[key] = (dict(fields), self._clock() + ttl_seconds)

    def read(self, key: str) -> dict[str, str] | None:
        if self.unavailable:
            raise LivenessUnavailable("store marked unavailable")
        with self._lock:
            entry = self._data.get(key)
            if entry is None or entry[1] <= self._clock():
                self._data.pop(key, None)
                return None
            return dict(entry[0])

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


@lru_cache
def redis_store(url: str) -> RedisLivenessStore:
    return RedisLivenessStore(url)


def default_store() -> LivenessStore:
    from repolab.config import get_settings

    return redis_store(get_settings().redis_url)


# -- the worker side -------------------------------------------------------------


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat()


def _worker_name() -> str:
    try:
        from rq import get_current_job

        job = get_current_job()
        if job is not None and getattr(job, "worker_name", None):
            return str(job.worker_name)
    except Exception:  # noqa: BLE001 - a label for diagnostics, nothing more
        pass
    return f"{socket.gethostname()}:{os.getpid()}"


class Liveness:
    """Beats for as long as a task runs, and records what it is doing.

    A thread, not an asyncio task: an investigation also runs blocking code
    (a clone, verification), which would freeze an event loop -- and with it
    every heartbeat, which is exactly the false alarm this exists to prevent.
    Store failures are logged and ignored: liveness must never fail a task.
    """

    def __init__(
        self,
        store: LivenessStore,
        key: str,
        *,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
        clock=time.time,
    ) -> None:
        self._store = store
        self.key = key
        self._interval = interval_seconds
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fields: dict[str, str] = {"worker": _worker_name()}
        self._warned = False

    def __enter__(self) -> Liveness:
        self._beat()
        self._thread = threading.Thread(target=self._run, name=f"liveness:{self.key}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
        try:
            self._store.delete(self.key)
        except Exception as error:  # noqa: BLE001
            self._warn(error)

    def set_activity(self, stage: str, activity: str) -> None:
        """What the task is doing now. The clock restarts only when it changes."""
        with self._lock:
            if self._fields.get("stage") == stage and self._fields.get("activity") == activity:
                return
            self._fields.update(
                stage=stage, activity=activity, activity_started_at=_iso(self._clock())
            )
        self._beat()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._beat()

    def _beat(self) -> None:
        with self._lock:
            fields = {**self._fields, "heartbeat_at": _iso(self._clock())}
        try:
            self._store.write(self.key, fields, self._ttl)
        except Exception as error:  # noqa: BLE001 - never fail the task over this
            self._warn(error)

    def _warn(self, error: Exception) -> None:
        if not self._warned:
            self._warned = True
            log.warning("liveness.write_failed", key=self.key, error=str(error)[:200])


def liveness_for(key: str) -> Liveness | NoLiveness:
    """A heartbeat for *key* on the configured store, or nothing if none can be made."""
    try:
        return Liveness(default_store(), key)
    except Exception as error:  # noqa: BLE001 - a task runs without liveness rather than not at all
        log.warning("liveness.unavailable", key=key, error=str(error)[:200])
        return NoLiveness()


class NoLiveness:
    """For callers without a store (tests, scripts): records nothing."""

    key = ""

    def __enter__(self) -> NoLiveness:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def set_activity(self, stage: str, activity: str) -> None:
        return None


ActivitySink = Liveness | NoLiveness


# -- the reading side ------------------------------------------------------------


class Activity(BaseModel):
    stage: str
    key: str
    started_at: datetime | None = None


class LivenessSnapshot(BaseModel):
    heartbeat_at: datetime | None = None
    worker: str | None = None
    activity: Activity | None = None


class Health(StrEnum):
    WAITING = "waiting"
    """Queued: no worker has picked it up yet."""

    WORKING = "working"
    """Running, and either beating or not yet expected to."""

    INTERRUPTED = "interrupted"
    """Running, yet no heartbeat for longer than a live worker could be silent."""


class LivenessRead(BaseModel):
    known: bool
    """False when the store could not be read: nothing can be concluded."""

    snapshot: LivenessSnapshot | None = None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_liveness(store: LivenessStore, key: str) -> LivenessRead:
    try:
        fields = store.read(key)
    except LivenessUnavailable:
        return LivenessRead(known=False)
    except Exception:  # noqa: BLE001 - same: unknown, not interrupted
        return LivenessRead(known=False)
    if fields is None:
        return LivenessRead(known=True)
    activity = None
    if fields.get("activity"):
        activity = Activity(
            stage=fields.get("stage") or "",
            key=fields["activity"],
            started_at=_parse(fields.get("activity_started_at")),
        )
    return LivenessRead(
        known=True,
        snapshot=LivenessSnapshot(
            heartbeat_at=_parse(fields.get("heartbeat_at")),
            worker=fields.get("worker"),
            activity=activity,
        ),
    )


def task_health(
    status: JobStatus | str,
    liveness: LivenessRead,
    started_at: datetime | None,
    *,
    now: datetime | None = None,
    interrupted_after_seconds: float = INTERRUPTED_AFTER_SECONDS,
) -> Health | None:
    """How a task is doing, from its heartbeat -- never from event silence.

    ``None`` for a finished task. ``INTERRUPTED`` needs both a readable store
    with no heartbeat in it *and* a task old enough that a live worker would
    certainly have beaten by now.
    """
    status = JobStatus(status)
    if status.is_terminal:
        return None
    if status is JobStatus.QUEUED:
        return Health.WAITING
    if not liveness.known or liveness.snapshot is not None:
        return Health.WORKING
    if started_at is None:
        return Health.WORKING
    moment = now or datetime.now(UTC)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    running_for = (moment - started_at).total_seconds()
    return Health.INTERRUPTED if running_for > interrupted_after_seconds else Health.WORKING
