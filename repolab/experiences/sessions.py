"""Experience sessions and the environments they run in, in Postgres.

A session holds what a person did -- each run's input, its real output and
measurements, what it was mapped to, and any explanation -- so a refresh
reconnects to it and an experiment can repeat exactly what they ran.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.db.models import ExperienceBuildRecord, ExperienceSessionRecord

READY = "ready"
PREPARING = "preparing"
FAILED = "failed"
STOPPED = "stopped"


class SessionRuntime(BaseModel):
    """Where this session's runs execute."""

    image: str | None = None
    spark_submit: str | None = None
    workdir: str | None = None
    python: str | None = None
    version: str | None = None
    """The software version the runtime reports (e.g. ``spark.version``)."""

    disclosure: str | None = None
    label: str = ""


class RunRecord(BaseModel):
    id: str
    status: str = "running"
    """``running``, ``done`` or ``failed``."""

    code: str = ""
    action_id: str | None = None
    shuffle_partitions: int | None = None
    result: dict[str, Any] | None = None
    mapping: list[dict[str, Any]] = Field(default_factory=list)
    explanation: dict[str, Any] | None = None
    explanation_error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    events_job_id: str | None = None


class SessionState(BaseModel):
    runtime: SessionRuntime = Field(default_factory=SessionRuntime)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    failure: str | None = None
    failure_log: str = ""
    runs: list[RunRecord] = Field(default_factory=list)
    dataset: dict[str, Any] | None = None
    """The sample dataset runs read (schema, preview, sha256), for Spark sessions."""

    minilab: dict[str, Any] | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)

    def run(self, run_id: str) -> RunRecord | None:
        return next((r for r in self.runs if r.id == run_id), None)


def new_id() -> str:
    return os.urandom(6).hex()


def create_session(
    session: Session,
    *,
    job_id: str,
    repo_url: str,
    commit_sha: str,
    experience_id: str,
    adapter: str,
    strategy: str,
    reality: str,
    events_job_id: str | None,
) -> ExperienceSessionRecord:
    record = ExperienceSessionRecord(
        id=new_id(),
        job_id=job_id,
        repo_url=repo_url,
        commit_sha=commit_sha,
        experience_id=experience_id,
        adapter=adapter,
        strategy=strategy,
        reality=reality,
        status=PREPARING,
        events_job_id=events_job_id,
        state=SessionState().model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return record


def load_session(
    session: Session, session_id: str
) -> tuple[ExperienceSessionRecord, SessionState] | None:
    record = session.get(ExperienceSessionRecord, session_id)
    if record is None:
        return None
    return record, SessionState.model_validate(record.state or {})


def save_state(
    session: Session, session_id: str, state: SessionState, *, status: str | None = None
) -> None:
    record = session.get(ExperienceSessionRecord, session_id)
    if record is None:
        return
    record.state = state.model_dump(mode="json")
    if status is not None:
        record.status = status
    record.last_activity = datetime.now(UTC)


def active_sessions(session: Session, job_id: str) -> list[ExperienceSessionRecord]:
    return list(
        session.scalars(
            select(ExperienceSessionRecord)
            .where(
                ExperienceSessionRecord.job_id == job_id,
                ExperienceSessionRecord.status.in_([PREPARING, READY]),
            )
            .order_by(ExperienceSessionRecord.started_at.desc())
        ).all()
    )


def find_build(
    session: Session, repo_url: str, commit_sha: str, adapter: str, strategy: str
) -> ExperienceBuildRecord | None:
    return session.scalar(
        select(ExperienceBuildRecord).where(
            ExperienceBuildRecord.repo_url == repo_url,
            ExperienceBuildRecord.commit_sha == commit_sha,
            ExperienceBuildRecord.adapter == adapter,
            ExperienceBuildRecord.strategy == strategy,
        )
    )


def builds_for(session: Session, repo_url: str, commit_sha: str) -> list[ExperienceBuildRecord]:
    return list(
        session.scalars(
            select(ExperienceBuildRecord).where(
                ExperienceBuildRecord.repo_url == repo_url,
                ExperienceBuildRecord.commit_sha == commit_sha,
            )
        ).all()
    )


def record_build(
    session: Session,
    *,
    repo_url: str,
    commit_sha: str,
    adapter: str,
    strategy: str,
    status: str,
    image: str | None = None,
    job_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> ExperienceBuildRecord:
    record = find_build(session, repo_url, commit_sha, adapter, strategy)
    if record is None:
        record = ExperienceBuildRecord(
            repo_url=repo_url, commit_sha=commit_sha, adapter=adapter, strategy=strategy
        )
        session.add(record)
    record.status = status
    record.image = image if image is not None else record.image
    record.job_id = job_id if job_id is not None else record.job_id
    record.detail = detail if detail is not None else record.detail
    record.updated_at = datetime.now(UTC)
    session.flush()
    return record
