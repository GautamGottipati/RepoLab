"""Job submission and progress streaming.

Slow operations never run inside a request handler: submitting a repository
creates a job row and enqueues it, and the handler returns immediately.

Progress is streamed with SSE. Replay and live streaming use the *same* query
against Postgres, ordered by the per-job sequence number, so a browser that
refreshes mid-run receives its history and then continues with no gap and no
duplicate. Each frame carries ``id:``, which ``EventSource`` sends back as
``Last-Event-ID`` after a reconnect.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api import deps
from api.deps import get_session
from repolab.architecture import load_graph
from repolab.architecture.actions import NodeActions, concept_actions, node_actions
from repolab.architecture.runtime import RuntimeOverlay
from repolab.artifacts.overlay import architecture_sections, with_refinements
from repolab.db.builds import load_builds
from repolab.db.jobs import (
    count_events,
    create_job,
    get_job,
    last_event_at,
    read_events_after,
)
from repolab.db.models import (
    ArchitectureRecord,
    Job,
    RepoManifestRecord,
)
from repolab.db.store import load_runtime_overlay
from repolab.events import (
    EventType,
    JobEvent,
    JobStatus,
    LivenessRead,
    job_key,
    liveness,
    read_liveness,
    task_health,
)
from repolab.events.liveness import Activity, Health
from repolab.experiences.catalog import ExperienceCatalog, experiences_for
from repolab.ingest.url import InvalidRepoUrlError, validate_repo_url
from repolab.manifest.models import RepoManifest

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: How often the stream checks Postgres for new events.
POLL_INTERVAL_SECONDS = 0.25

#: How long a finished job's stream stays open before closing, so a client that
#: connects just after completion still receives the tail.
LINGER_SECONDS = 1.0


class SubmitRepository(BaseModel):
    """A repository to investigate."""

    repo_url: str = Field(description="Public https repository URL")


class JobCreated(BaseModel):
    job_id: str
    status: JobStatus
    repo_url: str
    events_url: str


class JobState(BaseModel):
    """Everything a client needs to render progress."""

    job_id: str
    status: JobStatus
    repo_url: str
    attempts: int
    last_seq: int
    event_count: int
    elapsed_seconds: float | None = None
    health: Health | None = None
    """``working``, ``waiting`` or ``interrupted`` -- from the worker's heartbeat,
    never from how long it has been since the last event. ``None`` once finished."""
    activity: Activity | None = None
    """What the worker is doing now, as a stage and a machine key; the screen
    owns the words. ``None`` when unknown."""
    heartbeat_at: datetime | None = None
    worker: str | None = None
    """Which worker holds the job. Diagnostics only."""
    last_progress_at: datetime | None = None
    """When the last milestone event was recorded. For diagnostics only: a long
    model call legitimately records none."""
    error: str | None = None
    ingestion_id: int | None = None


def _state(session: Session, job: Job) -> JobState:
    started = job.started_at or job.created_at
    finished = job.finished_at
    elapsed: float | None = None
    if started is not None:
        from datetime import UTC, datetime

        end = finished or datetime.now(UTC)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        elapsed = round((end - started).total_seconds(), 3)

    job_status = JobStatus(job.status)
    live = (
        LivenessRead(known=True)
        if job_status.is_terminal
        else read_liveness(liveness.default_store(), job_key(job.id))
    )
    snapshot = live.snapshot

    return JobState(
        job_id=job.id,
        status=job_status,
        repo_url=job.repo_url,
        attempts=job.attempts,
        last_seq=job.last_seq,
        event_count=count_events(session, job.id),
        elapsed_seconds=elapsed,
        health=task_health(job_status, live, job.started_at or job.created_at),
        activity=snapshot.activity if snapshot else None,
        heartbeat_at=snapshot.heartbeat_at if snapshot else None,
        worker=snapshot.worker if snapshot else None,
        last_progress_at=last_event_at(session, job.id),
        error=job.error,
        ingestion_id=job.ingestion_id,
    )


@router.post("", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
def submit_repository(
    payload: SubmitRepository,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> JobCreated:
    """Accept a repository and queue its investigation.

    Returns 202 immediately: the work happens in a worker.
    """
    try:
        repo_url = validate_repo_url(payload.repo_url)
    except InvalidRepoUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    job = create_job(session, repo_url.normalized)
    session.commit()

    from repolab.analytics import AnalyticsEventType, DbAnalyticsWriter
    from repolab.analytics.events import AnalyticsEvent

    DbAnalyticsWriter(lambda: deps.session_factory()()).record(
        AnalyticsEvent(
            type=AnalyticsEventType.REPO_SUBMITTED,
            job_id=job.id,
            repo_url=repo_url.normalized,
        )
    )

    try:
        from worker.jobs import enqueue_investigation

        enqueue_investigation(job.id, repo_url.normalized)
    except Exception as exc:  # noqa: BLE001 - reported, and the job row survives
        log.warning("jobs.enqueue_failed", job_id=job.id, error=str(exc))

    response.headers["Location"] = f"/jobs/{job.id}"
    return JobCreated(
        job_id=job.id,
        status=JobStatus.QUEUED,
        repo_url=repo_url.normalized,
        events_url=f"/jobs/{job.id}/events",
    )


@router.get("/{job_id}", response_model=JobState)
def job_state(job_id: str, session: Annotated[Session, Depends(get_session)]) -> JobState:
    """Status, elapsed time, retry state and stall detection."""
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")
    return _state(session, job)


@router.get("/{job_id}/events")
async def stream_events(
    job_id: str,
    request: Request,
    after: Annotated[int, Query(ge=0, description="Resume after this sequence")] = 0,
    follow: Annotated[bool, Query(description="Keep the connection open for live events")] = True,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream this job's events, replaying history first.

    ``Last-Event-ID`` takes precedence over ``after`` so a browser reconnect needs
    no cooperation from the page.

    With ``follow=false`` the stream drains whatever has been recorded and closes,
    which suits a client that wants the current history without holding a
    connection open.
    """
    with deps.session_factory()() as session:
        if get_job(session, job_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")

    start = _resume_point(after, last_event_id)
    return StreamingResponse(
        _event_stream(job_id, start, request, follow=follow),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx and friends buffer SSE by default, which stalls the stream.
            "X-Accel-Buffering": "no",
        },
    )


def _resume_point(after: int, last_event_id: str | None) -> int:
    if last_event_id:
        try:
            return max(0, int(last_event_id))
        except ValueError:
            return after
    return after


async def _event_stream(
    job_id: str, after: int, request: Request, *, follow: bool = True
) -> AsyncIterator[str]:
    """Replay, then follow. One ordering, so no gaps and no duplicates."""
    cursor = after
    factory = deps.session_factory()

    while True:
        if await request.is_disconnected():
            return

        with factory() as session:
            events = read_events_after(session, job_id, cursor)
            job = get_job(session, job_id)
            job_status = JobStatus(job.status) if job is not None else None

        for event in events:
            cursor = event.seq
            yield event.to_sse()

        drained = not events
        if drained and job_status is not None and job_status.is_terminal:
            # Nothing more is coming; tell the client to stop reconnecting.
            yield _terminal_frame(job_id, cursor, job_status)
            return
        if drained and not follow:
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def _terminal_frame(job_id: str, cursor: int, job_status: JobStatus) -> str:
    """A final frame telling the client to stop reconnecting."""
    closing = JobEvent(
        job_id=job_id,
        seq=max(1, cursor + 1),
        type=EventType.STEP_DONE if job_status is JobStatus.COMPLETED else EventType.FAILED,
        message=f"job {job_status.value}",
        step="job",
        data={"terminal": True, "status": job_status.value},
    )
    return closing.to_sse()


class ArchitectureView(BaseModel):
    """The architecture an ingestion produced, and what can be done with each part.

    The graph is served in the current schema whatever version stored it. Every
    node already carries the source references that justify it, which is what
    lets the screen show *why* a component is claimed to exist.

    ``node_actions`` is decided here, deterministically: Ask is always possible;
    Experiment only where RepoLab can really run this repository and the
    component is one that run exercises.
    """

    job_id: str
    repo_url: str
    commit_sha: str
    generated_by: str
    node_count: int
    edge_count: int
    summary: str | None = None
    graph: dict[str, Any]
    node_actions: dict[str, NodeActions] = Field(default_factory=dict)
    concept_actions: dict[str, NodeActions] = Field(default_factory=dict)
    """The same, for system-model concepts and subsystems (by their ids)."""
    sections: dict[str, str] = Field(default_factory=dict)
    """Each architecture artifact's status (``structure``, ``runtime_naming``...)."""


@router.get("/{job_id}/architecture", response_model=ArchitectureView)
def job_architecture(
    job_id: str, session: Annotated[Session, Depends(get_session)]
) -> ArchitectureView:
    """The architecture graph this job produced. 404 until it is ready."""
    job = _require_ingested(session, job_id, wanted="architecture")
    record = _architecture_record(session, job)
    graph = load_graph(record.graph)

    commit_sha = record.ingestion.commit_sha if record.ingestion is not None else ""
    sections: dict[str, str] = {}
    if commit_sha:
        graph = with_refinements(session, job.repo_url, commit_sha, graph)
        sections = architecture_sections(session, job.repo_url, commit_sha)
    manifest_record = (
        session.query(RepoManifestRecord)
        .filter(RepoManifestRecord.ingestion_id == job.ingestion_id)
        .one_or_none()
    )
    manifest = RepoManifest.model_validate(manifest_record.manifest) if manifest_record else None
    artifacts = load_builds(session, job.repo_url, commit_sha) if commit_sha else []

    actions = node_actions(graph, manifest, artifacts)
    return ArchitectureView(
        job_id=job_id,
        repo_url=job.repo_url,
        commit_sha=commit_sha,
        generated_by=graph.generated_by.value,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        summary=graph.summary or record.summary,
        graph=graph.model_dump(mode="json"),
        node_actions=actions,
        concept_actions=concept_actions(graph, actions),
        sections=sections,
    )


@router.get("/{job_id}/architecture/runtime", response_model=RuntimeOverlay)
def job_architecture_runtime(
    job_id: str, session: Annotated[Session, Depends(get_session)]
) -> RuntimeOverlay:
    """Measurements from real runs of this repository, placed on its components.

    Empty until something has run. Nothing here is estimated: each value names
    the run that produced it.
    """
    job = _require_ingested(session, job_id, wanted="architecture")
    graph = load_graph(_architecture_record(session, job).graph)
    return load_runtime_overlay(session, job.repo_url, graph)


def _architecture_record(session: Session, job: Job) -> ArchitectureRecord:
    record = (
        session.query(ArchitectureRecord)
        .filter(ArchitectureRecord.ingestion_id == job.ingestion_id)
        .one_or_none()
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "architecture was not recorded")
    return record


@router.get("/{job_id}/experiences", response_model=ExperienceCatalog)
def job_experiences(
    job_id: str, session: Annotated[Session, Depends(get_session)]
) -> ExperienceCatalog:
    """Every interaction surface this repository offers, and which can be opened.

    A repository is never reduced to one application type, so this is a list
    rather than a single answer -- and each entry says plainly whether RepoLab
    can run it. The runnability is decided by the code that would actually
    refuse, not by a list of supported repositories kept here.

    Deterministic and fast: it reads the stored manifest and asks which build
    candidates exist, which executes nothing.
    """
    manifest = _manifest_for(session, job_id, wanted="experiences")
    return experiences_for(manifest)


def _require_ingested(session: Session, job_id: str, *, wanted: str) -> Job:
    """The job, once it has an ingestion to read from.

    *wanted* names what the caller was after, so the 404 says which thing is not
    ready rather than making a client guess from the path it requested.
    """
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")
    if job.ingestion_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"job {job_id} has no {wanted} yet (status {job.status})",
        )
    return job


@router.get("/{job_id}/manifest", response_model=RepoManifest)
def job_manifest(job_id: str, session: Annotated[Session, Depends(get_session)]) -> Any:
    """The manifest this job produced. 404 until it is ready.

    Returns the stored JSON rather than a re-parsed model, so a manifest written
    by an older version is served as it was written instead of failing
    validation on a field that has since changed.
    """
    job = _require_ingested(session, job_id, wanted="manifest")
    return _manifest_record(session, job).manifest


def _manifest_record(session: Session, job: Job) -> RepoManifestRecord:
    record = (
        session.query(RepoManifestRecord)
        .filter(RepoManifestRecord.ingestion_id == job.ingestion_id)
        .one_or_none()
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "manifest was not recorded")
    return record


def _manifest_for(session: Session, job_id: str, *, wanted: str) -> RepoManifest:
    """The job's manifest as a model, for endpoints that reason over it."""
    job = _require_ingested(session, job_id, wanted=wanted)
    return RepoManifest.model_validate(_manifest_record(session, job).manifest)
