"""The event stream stays alive through a reverse proxy.

nginx closes a proxied connection that is silent for its read timeout. A long
model call can leave a job with nothing to report for longer than that, so the
stream sends an SSE comment while idle -- and only while idle.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api import deps
from api.routes import jobs
from repolab.db import Base
from repolab.db.jobs import append_event, create_job, mark_running
from repolab.events import EventType, JobEvent

REPO = "https://github.com/AlexIoannides/pyspark-example-project"


class FakeRequest:
    """Disconnects after a fixed number of polls."""

    def __init__(self, polls: int) -> None:
        self.remaining = polls

    async def is_disconnected(self) -> bool:
        self.remaining -= 1
        return self.remaining < 0


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(deps, "session_factory", lambda: made)
    monkeypatch.setattr(jobs, "POLL_INTERVAL_SECONDS", 0.0)
    yield made
    engine.dispose()


def running_job(factory: sessionmaker[Session], events: int = 0) -> str:
    with factory() as session:
        job = create_job(session, REPO)
        mark_running(session, job.id)
        for seq in range(1, events + 1):
            append_event(
                session,
                JobEvent(job_id=job.id, seq=seq, type=EventType.PROGRESS, message=f"s{seq}"),
            )
        session.commit()
        return job.id


async def collect(job_id: str, polls: int) -> list[str]:
    return [frame async for frame in jobs._event_stream(job_id, 0, FakeRequest(polls))]


async def test_an_idle_stream_sends_keepalive_comments(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "KEEPALIVE_SECONDS", 0.0)
    job_id = running_job(factory)

    frames = await collect(job_id, polls=3)

    assert frames
    assert all(frame == jobs.KEEPALIVE_FRAME for frame in frames)
    # A comment line: EventSource ignores it, so the page sees no event.
    assert jobs.KEEPALIVE_FRAME.startswith(":")


async def test_a_stream_with_events_sends_no_keepalive_before_the_timeout(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "KEEPALIVE_SECONDS", 3600.0)
    job_id = running_job(factory, events=2)

    frames = await collect(job_id, polls=3)

    assert len(frames) == 2
    assert jobs.KEEPALIVE_FRAME not in frames
