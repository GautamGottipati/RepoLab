"""Runtimes in Postgres: the source of truth the proxy and the screen read."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.db.models import RuntimeRecord
from repolab.runtime.models import RuntimePlan, RuntimeState


def create_runtime(
    session: Session, state: RuntimeState, plan: RuntimePlan, *, job_id: str | None
) -> RuntimeRecord:
    record = RuntimeRecord(
        id=state.id,
        repo_url=state.repo_url,
        commit_sha=state.commit_sha,
        job_id=job_id,
        status=state.status.value,
        plan=plan.model_dump(mode="json"),
        state=state.model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return record


def save_runtime(session: Session, state: RuntimeState) -> None:
    record = session.get(RuntimeRecord, state.id)
    if record is None:
        return
    record.state = state.model_dump(mode="json")
    record.status = state.status.value
    record.updated_at = datetime.now(UTC)


def load_runtime(session: Session, runtime_id: str) -> tuple[RuntimeRecord, RuntimeState] | None:
    record = session.get(RuntimeRecord, runtime_id)
    if record is None or record.state is None:
        return None
    return record, RuntimeState.model_validate(record.state)


def latest_runtime(session: Session, repo_url: str) -> RuntimeRecord | None:
    return session.scalar(
        select(RuntimeRecord)
        .where(RuntimeRecord.repo_url == repo_url)
        .order_by(RuntimeRecord.created_at.desc())
        .limit(1)
    )


def touch_runtime(session: Session, runtime_id: str) -> None:
    record = session.get(RuntimeRecord, runtime_id)
    if record is not None:
        record.last_used_at = datetime.now(UTC)


def active_runtimes(session: Session) -> list[RuntimeRecord]:
    stopped = {"stopped"}
    return [
        record
        for record in session.scalars(select(RuntimeRecord)).all()
        if record.status not in stopped
    ]
