"""Job, event and agent-run persistence.

Postgres is the source of truth for the event stream: the SSE endpoint replays
from here and then keeps reading, so there is exactly one ordering and a reader
that reconnects cannot miss or duplicate an event.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repolab.agents.models import AgentRun, AgentStep
from repolab.db.models import (
    AgentRunRecord,
    AgentStepRecord,
    Job,
    JobEventRecord,
    ToolCallRecord,
)
from repolab.events.models import EventType, JobEvent, JobStatus

#: Tool results are summarised before storage; the repository's contents are never
#: copied into the audit trail.
MAX_SUMMARY_LENGTH = 1000


def new_job_id() -> str:
    return str(uuid.uuid4())


def create_job(session: Session, repo_url: str, *, kind: str = "investigate") -> Job:
    """Record a queued job."""
    job = Job(id=new_job_id(), kind=kind, status=JobStatus.QUEUED.value, repo_url=repo_url)
    session.add(job)
    session.flush()
    return job


def get_job(session: Session, job_id: str) -> Job | None:
    return session.get(Job, job_id)


def mark_running(session: Session, job_id: str) -> Job | None:
    job = get_job(session, job_id)
    if job is None:
        return None
    job.status = JobStatus.RUNNING.value
    job.started_at = datetime.now(UTC)
    job.attempts += 1
    session.flush()
    return job


def mark_finished(
    session: Session,
    job_id: str,
    *,
    status: JobStatus,
    ingestion_id: int | None = None,
    error: str | None = None,
) -> Job | None:
    job = get_job(session, job_id)
    if job is None:
        return None
    job.status = status.value
    job.finished_at = datetime.now(UTC)
    if ingestion_id is not None:
        job.ingestion_id = ingestion_id
    if error is not None:
        job.error = error[:2000]
    session.flush()
    return job


def append_event(session: Session, event: JobEvent) -> JobEventRecord:
    """Persist one event and advance the job's tail marker."""
    record = JobEventRecord(
        job_id=event.job_id,
        seq=event.seq,
        type=event.type.value,
        message=event.message,
        step=event.step,
        data=event.data or None,
        created_at=event.created_at,
    )
    session.add(record)

    job = session.get(Job, event.job_id)
    if job is not None and event.seq > job.last_seq:
        job.last_seq = event.seq
    session.flush()
    return record


def read_events_after(
    session: Session, job_id: str, after_seq: int = 0, *, limit: int = 500
) -> list[JobEvent]:
    """Events with ``seq > after_seq``, oldest first.

    One query serves both replay and live streaming, which is why a reconnecting
    client cannot see a gap.
    """
    rows = session.scalars(
        select(JobEventRecord)
        .where(JobEventRecord.job_id == job_id, JobEventRecord.seq > after_seq)
        .order_by(JobEventRecord.seq)
        .limit(limit)
    ).all()
    return [
        JobEvent(
            job_id=row.job_id,
            seq=row.seq,
            type=EventType(row.type),
            message=row.message,
            step=row.step,
            data=row.data or {},
            created_at=row.created_at,
        )
        for row in rows
    ]


def last_event_at(session: Session, job_id: str) -> datetime | None:
    """When the most recent event for *job_id* was recorded."""
    return session.scalar(
        select(func.max(JobEventRecord.created_at)).where(JobEventRecord.job_id == job_id)
    )


def count_events(session: Session, job_id: str) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(JobEventRecord).where(JobEventRecord.job_id == job_id)
        )
        or 0
    )


def save_agent_run(session: Session, job_id: str, run: AgentRun) -> AgentRunRecord:
    """Persist an agent run and its steps.

    Only concise, user-facing statuses are stored; model reasoning never is.
    """
    record = session.get(AgentRunRecord, run.id)
    if record is None:
        record = AgentRunRecord(id=run.id, job_id=job_id)
        session.add(record)

    record.agent = run.agent
    record.status = run.status.value
    record.objective = run.objective
    record.iterations = run.iterations
    record.max_iterations = run.limits.max_iterations
    record.planner = run.planner
    record.error = run.error
    record.started_at = run.started_at
    record.finished_at = run.finished_at
    session.flush()

    # Assigned through the relationship so the ORM keeps both sides in sync;
    # `delete-orphan` removes any steps from a previous save of the same run.
    record.steps = [_step_record(step) for step in run.steps]
    session.flush()
    return record


def _step_record(step: AgentStep) -> AgentStepRecord:
    record = AgentStepRecord(
        index=step.index,
        tool=step.request.tool if step.request else None,
        status=step.status[:2000],
        ok=step.ok,
        duration_ms=step.duration_ms,
        attempts=step.attempts,
        error=step.error,
    )
    if step.request is not None:
        record.tool_calls = [
            ToolCallRecord(
                tool=step.request.tool,
                arguments=step.request.arguments or None,
                ok=step.ok,
                summary=(step.result.summary if step.result else "")[:MAX_SUMMARY_LENGTH],
                duration_ms=step.duration_ms,
                error=step.error,
            )
        ]
    return record


#: How long a `running` job may go without an event before a restarting worker
#: treats it as abandoned.
#:
#: Ten minutes, against a measured worst case of well under one: the longest
#: legitimate silence in RepoLab is a Cassandra source build, and that emits
#: throughout. Generous on purpose -- failing a job that is merely slow would be
#: worse than leaving a dead one listed, and a second worker starting up must
#: not be able to kill the first worker's live job.
ABANDONED_AFTER_SECONDS = 600.0


def fail_abandoned_jobs(
    session: Session,
    *,
    reason: str,
    older_than_seconds: float = ABANDONED_AFTER_SECONDS,
) -> list[str]:
    """Mark long-silent `running` jobs as failed, returning their ids.

    A worker killed mid-job leaves its row `running` forever: the browser shows
    *stalled* and nothing ever resolves it. Called when a worker starts, which
    is the moment something is known to have restarted.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)

    running = session.scalars(select(Job).where(Job.status == JobStatus.RUNNING.value)).all()
    abandoned: list[str] = []

    for job in running:
        last = last_event_at(session, job.id) or job.started_at or job.created_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is not None and last > cutoff:
            continue  # recent activity: someone is still working on it

        job.status = JobStatus.FAILED.value
        job.finished_at = datetime.now(UTC)
        job.error = reason[:2000]
        abandoned.append(job.id)

    session.flush()
    return abandoned
