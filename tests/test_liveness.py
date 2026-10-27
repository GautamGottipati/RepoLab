"""Liveness: a heartbeat says the worker is alive; silence in events says nothing."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from repolab.events import Health, Liveness, LivenessRead, job_key, read_liveness, task_health
from repolab.events.liveness import LivenessSnapshot, MemoryLivenessStore

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
ALIVE = LivenessRead(known=True, snapshot=LivenessSnapshot(heartbeat_at=NOW))
SILENT = LivenessRead(known=True)
UNKNOWN = LivenessRead(known=False)


def ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


class TestHealth:
    @pytest.mark.parametrize(
        ("status", "live", "started", "expected"),
        [
            # A five-minute model call with no milestone, but the worker beating.
            ("running", ALIVE, ago(300), Health.WORKING),
            # No heartbeat yet, but only just started: the grace period.
            ("running", SILENT, ago(30), Health.WORKING),
            # No heartbeat, and running long enough that a live worker would beat.
            ("running", SILENT, ago(120), Health.INTERRUPTED),
            # The store cannot be read: nothing can be concluded.
            ("running", UNKNOWN, ago(3600), Health.WORKING),
            ("queued", SILENT, None, Health.WAITING),
            ("completed", SILENT, ago(3600), None),
            ("failed", SILENT, ago(3600), None),
        ],
    )
    def test_health_comes_from_the_heartbeat(self, status, live, started, expected) -> None:
        assert task_health(status, live, started, now=NOW) is expected

    def test_a_naive_start_time_is_utc(self) -> None:
        naive = ago(120).replace(tzinfo=None)
        assert task_health("running", SILENT, naive, now=NOW) is Health.INTERRUPTED


class TestLiveness:
    def test_beats_record_the_worker_and_disappear_on_exit(self) -> None:
        store = MemoryLivenessStore()
        with Liveness(store, job_key("j"), interval_seconds=0.02):
            read = read_liveness(store, job_key("j"))
            assert read.known and read.snapshot and read.snapshot.heartbeat_at
            assert read.snapshot.worker
        assert read_liveness(store, job_key("j")).snapshot is None

    def test_the_activity_clock_restarts_only_when_the_activity_changes(self) -> None:
        store = MemoryLivenessStore()
        clock = iter(range(1_000_000, 2_000_000, 10)).__next__
        live = Liveness(store, "k", interval_seconds=60, clock=lambda: float(clock()))
        with live:
            live.set_activity("architecture", "architecture.components")
            first = read_liveness(store, "k").snapshot.activity
            live.set_activity("architecture", "architecture.components")
            again = read_liveness(store, "k").snapshot.activity
            live.set_activity("architecture", "architecture.summary")
            changed = read_liveness(store, "k").snapshot.activity
        assert first.key == "architecture.components" and first.stage == "architecture"
        assert again.started_at == first.started_at
        assert changed.key == "architecture.summary" and changed.started_at > first.started_at

    def test_it_keeps_beating_while_the_task_blocks(self) -> None:
        """Blocking sync code (a clone, verification) must not silence the heartbeat."""
        store = MemoryLivenessStore()
        beats: list[str] = []
        original = store.write

        def counting(key, fields, ttl):
            beats.append(fields["heartbeat_at"])
            original(key, fields, ttl)

        store.write = counting  # type: ignore[method-assign]
        with Liveness(store, "k", interval_seconds=0.02):
            time.sleep(0.15)  # the "task", holding the thread
        assert len(beats) >= 4

    def test_an_expired_heartbeat_reads_as_absent(self) -> None:
        now = [0.0]
        store = MemoryLivenessStore(clock=lambda: now[0])
        store.write("k", {"heartbeat_at": NOW.isoformat()}, ttl_seconds=45)
        assert read_liveness(store, "k").snapshot is not None
        now[0] = 46
        read = read_liveness(store, "k")
        assert read.known and read.snapshot is None

    def test_a_failing_store_never_fails_the_task(self) -> None:
        class Broken(MemoryLivenessStore):
            def write(self, *args, **kwargs):
                raise ConnectionError("redis is down")

            def delete(self, *args, **kwargs):
                raise ConnectionError("redis is down")

        with Liveness(Broken(), "k", interval_seconds=0.01) as live:
            live.set_activity("architecture", "architecture.runtime")
            time.sleep(0.03)
