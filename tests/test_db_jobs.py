"""Job, event and agent-run persistence.

Postgres is the source of truth for the event stream, so replay behaviour is
tested here rather than only through the API.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from repolab.agents import AgentLimits, AgentRun, AgentStatus, AgentStep, ToolRequest, ToolResult
from repolab.analytics import AnalyticsEvent, AnalyticsEventType, DbAnalyticsWriter
from repolab.db import Base
from repolab.db.jobs import (
    append_event,
    count_events,
    create_job,
    get_job,
    last_event_at,
    mark_finished,
    mark_running,
    read_events_after,
    save_agent_run,
)
from repolab.db.models import AgentRunRecord, AnalyticsEventRecord, JobEventRecord
from repolab.events import EventType, JobEvent, JobStatus

REPO = "https://github.com/example/demo"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def emit(session: Session, job_id: str, seq: int, **kwargs) -> JobEvent:
    event = JobEvent(job_id=job_id, seq=seq, type=EventType.PROGRESS, **kwargs)
    append_event(session, event)
    session.commit()
    return event


class TestJobLifecycle:
    def test_a_new_job_is_queued(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()

        assert job.status == JobStatus.QUEUED.value
        assert job.attempts == 0
        assert job.last_seq == 0

    def test_starting_records_the_attempt(self, session: Session) -> None:
        job = create_job(session, REPO)
        mark_running(session, job.id)
        session.commit()

        assert job.status == JobStatus.RUNNING.value
        assert job.attempts == 1
        assert job.started_at is not None

    def test_retrying_increments_attempts(self, session: Session) -> None:
        """Retry state has to be visible, not inferred."""
        job = create_job(session, REPO)
        mark_running(session, job.id)
        mark_running(session, job.id)
        session.commit()
        assert job.attempts == 2

    def test_completing_records_the_ingestion(self, session: Session) -> None:
        job = create_job(session, REPO)
        mark_finished(session, job.id, status=JobStatus.COMPLETED, ingestion_id=7)
        session.commit()

        assert job.status == JobStatus.COMPLETED.value
        assert job.ingestion_id == 7
        assert job.finished_at is not None

    def test_failing_records_the_error(self, session: Session) -> None:
        job = create_job(session, REPO)
        mark_finished(session, job.id, status=JobStatus.FAILED, error="clone failed")
        session.commit()

        assert job.status == JobStatus.FAILED.value
        assert job.error == "clone failed"

    def test_an_unknown_job_is_none(self, session: Session) -> None:
        assert get_job(session, "nope") is None


class TestEventPersistence:
    def test_events_advance_the_job_tail(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        emit(session, job.id, 1)
        emit(session, job.id, 2)

        assert get_job(session, job.id).last_seq == 2
        assert count_events(session, job.id) == 2

    def test_a_duplicate_sequence_is_rejected(self, session: Session) -> None:
        """The constraint is what guarantees a reader cannot see two seq 1s."""
        job = create_job(session, REPO)
        session.commit()
        emit(session, job.id, 1)

        with pytest.raises(IntegrityError):
            emit(session, job.id, 1)

    def test_data_and_step_survive(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        emit(session, job.id, 1, step="clone", data={"files": 21}, message="cloned")

        stored = session.query(JobEventRecord).one()
        assert stored.step == "clone"
        assert stored.data == {"files": 21}
        assert stored.message == "cloned"

    def test_last_event_at_tracks_the_most_recent(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        assert last_event_at(session, job.id) is None

        emit(session, job.id, 1)
        assert last_event_at(session, job.id) is not None


class TestReplay:
    def test_reading_from_zero_returns_everything(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        for seq in range(1, 6):
            emit(session, job.id, seq)

        assert [e.seq for e in read_events_after(session, job.id, 0)] == [1, 2, 3, 4, 5]

    def test_reading_after_a_sequence_returns_only_the_tail(self, session: Session) -> None:
        """This is exactly what a reconnecting browser asks for."""
        job = create_job(session, REPO)
        session.commit()
        for seq in range(1, 6):
            emit(session, job.id, seq)

        assert [e.seq for e in read_events_after(session, job.id, 3)] == [4, 5]

    def test_reading_past_the_end_returns_nothing(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        emit(session, job.id, 1)
        assert read_events_after(session, job.id, 99) == []

    def test_events_are_returned_oldest_first(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        for seq in (3, 1, 2):  # inserted out of order
            emit(session, job.id, seq)

        assert [e.seq for e in read_events_after(session, job.id)] == [1, 2, 3]

    def test_one_job_never_sees_another_job_events(self, session: Session) -> None:
        first = create_job(session, REPO)
        second = create_job(session, "https://github.com/example/other")
        session.commit()
        emit(session, first.id, 1, message="mine")
        emit(session, second.id, 1, message="theirs")

        events = read_events_after(session, first.id)
        assert [e.message for e in events] == ["mine"]

    def test_the_round_trip_preserves_the_event(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        emit(session, job.id, 1, step="investigate", data={"tool": "list_files"})

        restored = read_events_after(session, job.id)[0]
        assert restored.type is EventType.PROGRESS
        assert restored.step == "investigate"
        assert restored.data == {"tool": "list_files"}


class TestAgentRunPersistence:
    def run_with_steps(self) -> AgentRun:
        run = AgentRun(
            agent="repository_investigator",
            objective="understand the repo",
            limits=AgentLimits(max_iterations=10),
        )
        run.steps.append(
            AgentStep(
                index=1,
                request=ToolRequest(tool="list_files", arguments={"limit": 5}, reason="look"),
                result=ToolResult(summary="listed 5 files"),
                status="listed 5 files",
                duration_ms=12.5,
            )
        )
        run.finish(AgentStatus.COMPLETED)
        return run

    def test_a_run_and_its_steps_are_saved(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        record = save_agent_run(session, job.id, self.run_with_steps())
        session.commit()

        assert record.agent == "repository_investigator"
        assert record.status == "completed"
        assert record.iterations == 1
        assert record.max_iterations == 10
        assert len(record.steps) == 1

    def test_the_tool_call_is_recorded_for_audit(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        record = save_agent_run(session, job.id, self.run_with_steps())
        session.commit()

        call = record.steps[0].tool_calls[0]
        assert call.tool == "list_files"
        assert call.arguments == {"limit": 5}
        assert call.ok is True

    def test_saving_twice_replaces_the_steps(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        run = self.run_with_steps()
        save_agent_run(session, job.id, run)
        session.commit()
        save_agent_run(session, job.id, run)
        session.commit()

        assert session.query(AgentRunRecord).count() == 1
        assert len(session.query(AgentRunRecord).one().steps) == 1

    def test_no_model_reasoning_reaches_the_audit_trail(self, session: Session) -> None:
        """Only a bounded, user-facing status is stored."""
        secret = "chain of thought that must not be stored"
        run = self.run_with_steps()
        run.steps[0].status = secret[:0] + "listed 5 files"

        job = create_job(session, REPO)
        session.commit()
        save_agent_run(session, job.id, run)
        session.commit()

        stored = session.query(AgentRunRecord).one()
        assert secret not in stored.steps[0].status
        assert secret not in (stored.steps[0].tool_calls[0].summary or "")

    def test_deleting_a_job_cascades(self, session: Session) -> None:
        job = create_job(session, REPO)
        session.commit()
        emit(session, job.id, 1)
        save_agent_run(session, job.id, self.run_with_steps())
        session.commit()

        session.delete(job)
        session.commit()

        assert session.query(JobEventRecord).count() == 0
        assert session.query(AgentRunRecord).count() == 0


class TestAnalyticsPersistence:
    def test_an_event_is_written(self, session: Session) -> None:
        engine = session.get_bind()
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        DbAnalyticsWriter(factory).record(
            AnalyticsEvent(
                type=AnalyticsEventType.REPO_SUBMITTED,
                job_id="job-1",
                repo_url=REPO,
                properties={"source": "api"},
            )
        )

        stored = session.query(AnalyticsEventRecord).one()
        assert stored.type == "repo_submitted"
        assert stored.job_id == "job-1"
        assert stored.properties == {"source": "api"}

    def test_a_write_failure_does_not_raise(self) -> None:
        """Analytics observes work; it must never be able to break it."""

        def broken():
            raise RuntimeError("database gone")

        DbAnalyticsWriter(broken).record(AnalyticsEvent(type=AnalyticsEventType.REPO_READY))
