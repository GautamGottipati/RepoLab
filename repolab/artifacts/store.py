"""Artifact state in Postgres: one row per (repository, commit, type, version).

Postgres is the source of truth; Redis only carries the queue. Transitions are
explicit, and the one that matters for duplicate work -- *claiming* an
artifact for generation -- is a single conditional UPDATE, so of five
concurrent requests exactly one wins and enqueues.

Every transition can emit an ``artifact_*`` event on the job's stream (which is
persisted and replayed), carrying the type and status -- never the payload.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repolab.artifacts.registry import SPECS, Status, dependants, spec, version
from repolab.db.models import AnalysisArtifactRecord
from repolab.events import EventType

log = structlog.get_logger(__name__)

#: States from which an artifact may be (re)claimed for generation.
CLAIMABLE = (Status.NOT_REQUESTED, Status.FAILED, Status.BLOCKED)

_EVENT: dict[Status, EventType] = {
    Status.QUEUED: EventType.ARTIFACT_QUEUED,
    Status.GENERATING: EventType.ARTIFACT_STARTED,
    Status.VERIFYING: EventType.ARTIFACT_VERIFYING,
    Status.READY: EventType.ARTIFACT_READY,
    Status.PARTIAL: EventType.ARTIFACT_READY,
    Status.FAILED: EventType.ARTIFACT_FAILED,
    Status.BLOCKED: EventType.ARTIFACT_BLOCKED,
}


class Emitter(Protocol):
    def emit(self, event_type: EventType, message: str = "", **kwargs: Any) -> Any: ...


def _now() -> datetime:
    return datetime.now(UTC)


def get(
    session: Session, repo_url: str, commit_sha: str, artifact_type: str
) -> AnalysisArtifactRecord | None:
    return session.execute(
        select(AnalysisArtifactRecord).where(
            AnalysisArtifactRecord.repo_url == repo_url,
            AnalysisArtifactRecord.commit_sha == commit_sha,
            AnalysisArtifactRecord.artifact_type == artifact_type,
            AnalysisArtifactRecord.analysis_version == version(artifact_type),
        )
    ).scalar_one_or_none()


def ensure(
    session: Session, repo_url: str, commit_sha: str, artifact_type: str
) -> AnalysisArtifactRecord:
    """The row for this artifact at this commit and version, created if absent."""
    found = get(session, repo_url, commit_sha, artifact_type)
    if found is not None:
        return found
    definition = spec(artifact_type)
    record = AnalysisArtifactRecord(
        repo_url=repo_url,
        commit_sha=commit_sha,
        artifact_type=artifact_type,
        analysis_version=definition.version,
        status=Status.NOT_REQUESTED.value,
        priority=definition.priority.value,
        dependencies=list(definition.depends_on),
    )
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        # Created concurrently by someone else: theirs is the row.
        found = get(session, repo_url, commit_sha, artifact_type)
        assert found is not None
        return found
    return record


def all_for(session: Session, repo_url: str, commit_sha: str) -> list[AnalysisArtifactRecord]:
    """Every known artifact type for this commit, at its current version.

    Types never requested are returned as ``not_requested`` rows that are not
    persisted, so a client sees the whole picture from one call.
    """
    rows = {
        r.artifact_type: r
        for r in session.execute(
            select(AnalysisArtifactRecord).where(
                AnalysisArtifactRecord.repo_url == repo_url,
                AnalysisArtifactRecord.commit_sha == commit_sha,
            )
        ).scalars()
        if r.analysis_version == version(r.artifact_type)
        if r.artifact_type in SPECS
    }
    result: list[AnalysisArtifactRecord] = []
    for artifact_type, definition in SPECS.items():
        result.append(
            rows.get(artifact_type)
            or AnalysisArtifactRecord(
                repo_url=repo_url,
                commit_sha=commit_sha,
                artifact_type=artifact_type,
                analysis_version=definition.version,
                status=Status.NOT_REQUESTED.value,
                priority=definition.priority.value,
                dependencies=list(definition.depends_on),
            )
        )
    return result


def claim(
    session: Session,
    repo_url: str,
    commit_sha: str,
    artifact_type: str,
    *,
    job_id: str | None,
    retry: bool = False,
) -> bool:
    """Atomically move an artifact to ``queued`` if no one else owns it.

    True only for the single caller that should generate it. A ready artifact
    is never claimed; a failed or blocked one only when ``retry`` (or when it
    was never attempted).
    """
    record = ensure(session, repo_url, commit_sha, artifact_type)
    allowed = [Status.NOT_REQUESTED.value]
    if retry:
        allowed += [Status.FAILED.value, Status.BLOCKED.value]
    result = session.execute(
        update(AnalysisArtifactRecord)
        .where(
            AnalysisArtifactRecord.id == record.id,
            AnalysisArtifactRecord.status.in_(allowed),
        )
        .values(status=Status.QUEUED.value, job_id=job_id, error=None, updated_at=_now())
        .execution_options(synchronize_session=False)
    )
    won = bool(result.rowcount)
    if won:
        session.expire(record)
    return won


def transition(
    session: Session,
    record: AnalysisArtifactRecord,
    status: Status,
    *,
    emitter: Emitter | None = None,
    payload: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    model: str | None = None,
    message: str = "",
    cache_hit: bool = False,
    updated: bool = False,
) -> AnalysisArtifactRecord:
    """Set *record*'s status (and payload/error), stamp times, and emit."""
    now = _now()
    previous = Status(record.status)
    record.status = status.value
    record.updated_at = now
    if status is Status.GENERATING:
        record.started_at = now
        record.attempts = (record.attempts or 0) + 1
    if status.usable:
        record.completed_at = now
        if record.started_at is not None:
            started = (
                record.started_at
                if record.started_at.tzinfo
                else record.started_at.replace(tzinfo=UTC)
            )
            record.duration_ms = int((now - started).total_seconds() * 1000)
        record.error = None
    if payload is not None:
        record.payload = payload
    if error is not None:
        record.error = error
    if model is not None:
        record.model = model
    session.flush()
    log.info(
        "artifact.transition",
        artifact_type=record.artifact_type,
        commit=record.commit_sha[:12],
        status=status.value,
        previous=previous.value,
        duration_ms=record.duration_ms if status.usable else None,
        cache_hit=cache_hit,
        error_code=(error or {}).get("error_code"),
    )
    if emitter is not None:
        announce(emitter, record, message=message, cache_hit=cache_hit, updated=updated)
    return record


def announce(
    emitter: Emitter,
    record: AnalysisArtifactRecord,
    *,
    message: str = "",
    cache_hit: bool = False,
    updated: bool = False,
) -> None:
    """Tell the job's stream about *record*'s current state. Never the payload."""
    status = Status(record.status)
    event = EventType.ARTIFACT_UPDATED if updated else _EVENT.get(status)
    if event is None:
        return
    error = record.error or {}
    emitter.emit(
        event,
        message or f"{record.artifact_type} {status.value}",
        step="artifacts",
        data={
            "artifact_type": record.artifact_type,
            "status": status.value,
            "version": record.analysis_version,
            "cache_hit": cache_hit,
            **({"error_code": error.get("error_code")} if error.get("error_code") else {}),
        },
    )


def fail(
    session: Session,
    record: AnalysisArtifactRecord,
    *,
    code: str,
    stage: str,
    message: str,
    retryable: bool = True,
    emitter: Emitter | None = None,
) -> list[str]:
    """Mark *record* failed and every not-yet-usable dependant blocked.

    Returns the dependants that were blocked.
    """
    transition(
        session,
        record,
        Status.FAILED,
        emitter=emitter,
        error={
            "error_code": code,
            "stage": stage,
            "message": message[:500],
            "retryable": retryable,
            "timestamp": _now().isoformat(),
        },
        message=message[:200],
    )
    blocked: list[str] = []
    pending = list(dependants(record.artifact_type))
    while pending:
        artifact_type = pending.pop()
        dependant = ensure(session, record.repo_url, record.commit_sha, artifact_type)
        if Status(dependant.status).usable or dependant.status == Status.BLOCKED.value:
            continue
        transition(
            session,
            dependant,
            Status.BLOCKED,
            emitter=emitter,
            error={
                "error_code": "dependency_failed",
                "stage": record.artifact_type,
                "message": f"needs {record.artifact_type}, which failed",
                "retryable": True,
                "timestamp": _now().isoformat(),
            },
        )
        blocked.append(artifact_type)
        pending.extend(dependants(artifact_type))
    return blocked


def dependencies_ready(
    session: Session, repo_url: str, commit_sha: str, artifact_type: str
) -> bool:
    for needed in spec(artifact_type).depends_on:
        found = get(session, repo_url, commit_sha, needed)
        if found is None or not Status(found.status).usable:
            return False
    return True


def all_ready(session: Session, repo_url: str, commit_sha: str, types: Iterable[str]) -> bool:
    for artifact_type in types:
        found = get(session, repo_url, commit_sha, artifact_type)
        if found is None or not Status(found.status).usable:
            return False
    return True


def mark_cache_hits(
    session: Session,
    repo_url: str,
    commit_sha: str,
    types: Iterable[str],
    *,
    emitter: Emitter | None,
) -> None:
    """Announce reused artifacts to a new job's stream and count the reuse."""
    for artifact_type in types:
        found = get(session, repo_url, commit_sha, artifact_type)
        if found is None or not Status(found.status).usable:
            continue
        found.cache_hits = (found.cache_hits or 0) + 1
        transition(
            session,
            found,
            Status(found.status),
            emitter=emitter,
            cache_hit=True,
            message=f"{artifact_type} reused for this commit",
        )
