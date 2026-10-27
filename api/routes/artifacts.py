"""Analysis artifacts: what is known about a job's repository, piece by piece.

    GET  /jobs/{id}/artifacts           every artifact's status -- the whole state
                                        a refreshed page needs, without SSE history
    GET  /jobs/{id}/artifacts/{type}    one artifact, with its payload when small
    POST /jobs/{id}/artifacts/{type}    make sure it exists: ready -> returned;
                                        in progress -> its status; never asked for
                                        -> claimed and queued (once, however many
                                        callers); failed + ?retry=true -> re-queued

The backend is authoritative for every status. Generation never runs in a
request handler.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_session
from repolab.artifacts import SPECS, Status, spec
from repolab.artifacts import store as artifacts
from repolab.db.jobs import get_job
from repolab.db.models import AnalysisArtifactRecord, Ingestion, Job
from repolab.events import DbEventEmitter

log = structlog.get_logger(__name__)

router = APIRouter(tags=["artifacts"])


class ArtifactState(BaseModel):
    type: str
    status: str
    priority: str
    version: str
    lazy: bool
    depends_on: list[str]
    description: str
    updated_at: datetime | None = None
    started_at: datetime | None = None
    """When generation began: what a pending section times its step from."""
    completed_at: datetime | None = None
    duration_ms: int | None = None
    cache_hits: int = 0
    error: dict[str, Any] | None = None


class ArtifactDetail(ArtifactState):
    payload: dict[str, Any] | None = None


class ArtifactList(BaseModel):
    job_id: str
    commit_sha: str | None
    """``None`` until the job has resolved which commit it analyses."""
    artifacts: list[ArtifactState]


def _state(record: AnalysisArtifactRecord, cls: type[ArtifactState] = ArtifactState) -> Any:
    definition = spec(record.artifact_type)
    data: dict[str, Any] = {
        "type": record.artifact_type,
        "status": record.status,
        "priority": record.priority,
        "version": record.analysis_version,
        "lazy": definition.lazy,
        "depends_on": list(definition.depends_on),
        "description": definition.description,
        "updated_at": record.updated_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "duration_ms": record.duration_ms,
        "cache_hits": record.cache_hits or 0,
        "error": record.error,
    }
    if cls is ArtifactDetail:
        data["payload"] = record.payload
    return cls(**data)


def job_commit(session: Session, job: Job) -> str | None:
    if job.commit_sha:
        return job.commit_sha
    if job.ingestion_id is not None:
        ingestion = session.get(Ingestion, job.ingestion_id)
        return ingestion.commit_sha if ingestion else None
    return None


def _require(session: Session, job_id: str) -> Job:
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")
    return job


def _known(artifact_type: str) -> None:
    if artifact_type not in SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no artifact type {artifact_type!r}")


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactList)
def list_artifacts(job_id: str, session: Annotated[Session, Depends(get_session)]) -> ArtifactList:
    job = _require(session, job_id)
    commit = job_commit(session, job)
    if commit is None:
        return ArtifactList(job_id=job_id, commit_sha=None, artifacts=[])
    return ArtifactList(
        job_id=job_id,
        commit_sha=commit,
        artifacts=[_state(r) for r in artifacts.all_for(session, job.repo_url, commit)],
    )


@router.get("/jobs/{job_id}/artifacts/{artifact_type}", response_model=ArtifactDetail)
def get_artifact(
    job_id: str, artifact_type: str, session: Annotated[Session, Depends(get_session)]
) -> ArtifactDetail:
    _known(artifact_type)
    job = _require(session, job_id)
    commit = job_commit(session, job)
    if commit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the commit is not resolved yet")
    record = artifacts.get(session, job.repo_url, commit, artifact_type)
    if record is None:
        record = next(
            r
            for r in artifacts.all_for(session, job.repo_url, commit)
            if r.artifact_type == artifact_type
        )
    return _state(record, ArtifactDetail)


@router.post(
    "/jobs/{job_id}/artifacts/{artifact_type}",
    response_model=ArtifactDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_artifact(
    job_id: str,
    artifact_type: str,
    session: Annotated[Session, Depends(get_session)],
    retry: Annotated[bool, Query()] = False,
) -> ArtifactDetail:
    """Ensure an artifact is (being) generated. Idempotent; never duplicates work."""
    _known(artifact_type)
    job = _require(session, job_id)
    commit = job_commit(session, job)
    if commit is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "the repository is still being resolved")
    definition = spec(artifact_type)
    record = artifacts.ensure(session, job.repo_url, commit, artifact_type)
    current = Status(record.status)
    if current.usable or current.active:
        session.commit()
        return _state(record, ArtifactDetail)

    if definition.needs_clone:
        if not retry:
            # Generated by the investigation itself, never on request.
            session.commit()
            return _state(record, ArtifactDetail)
        # A retry of repository-reading work re-runs the job's analysis, which
        # reuses everything already ready for this commit.
        if artifacts.claim(session, job.repo_url, commit, artifact_type, job_id=job.id, retry=True):
            session.commit()
            _enqueue_resume(job)
        else:
            session.commit()
        session.refresh(record)
        return _state(record, ArtifactDetail)

    if artifacts.claim(session, job.repo_url, commit, artifact_type, job_id=job.id, retry=retry):
        session.refresh(record)
        artifacts.transition(session, record, Status.QUEUED)
        session.commit()
        _announce(job, record)
        _record_request(job, artifact_type, retry=retry)
        if artifacts.dependencies_ready(session, job.repo_url, commit, artifact_type):
            _enqueue_artifact(job.id, artifact_type)
        # Otherwise the orchestrator starts it when its dependencies are ready.
    else:
        session.commit()
        session.refresh(record)
    return _state(record, ArtifactDetail)


def _announce(job: Job, record: AnalysisArtifactRecord) -> None:
    """Emit the artifact's state on the job's stream, after it is committed."""
    from api import deps

    factory = deps.session_factory()
    with factory() as session:
        fresh = get_job(session, job.id)
        start = fresh.last_seq if fresh is not None else 0
    artifacts.announce(DbEventEmitter(job.id, lambda: factory(), start_seq=start), record)


def _record_request(job: Job, artifact_type: str, *, retry: bool) -> None:
    from api import deps
    from repolab.analytics import AnalyticsEventType, DbAnalyticsWriter
    from repolab.analytics.events import AnalyticsEvent

    DbAnalyticsWriter(lambda: deps.session_factory()()).record(
        AnalyticsEvent(
            type=AnalyticsEventType.ARTIFACT_REQUESTED,
            job_id=job.id,
            repo_url=job.repo_url,
            properties={"artifact_type": artifact_type, "retry": retry},
        )
    )


def _enqueue_artifact(job_id: str, artifact_type: str) -> None:
    try:
        from worker.jobs import enqueue_artifact

        enqueue_artifact(job_id, artifact_type)
    except Exception as exc:  # noqa: BLE001 - recorded; the row stays queued
        log.warning(
            "artifact.enqueue_failed", job_id=job_id, artifact_type=artifact_type, error=str(exc)
        )


def _enqueue_resume(job: Job) -> None:
    try:
        from worker.jobs import enqueue_investigation

        enqueue_investigation(job.id, job.repo_url)
    except Exception as exc:  # noqa: BLE001 - recorded; the row stays queued
        log.warning("artifact.resume_enqueue_failed", job_id=job.id, error=str(exc))
