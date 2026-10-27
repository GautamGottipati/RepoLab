"""Experiences: what can be done with a repository, and doing it.

    GET    /jobs/{job_id}/experience-list                    ranked experiences + why not
    POST   /jobs/{job_id}/experiences/{experience_id}/sessions   start one (202)
    GET    /experience-sessions/{id}                         reconnect
    POST   /experience-sessions/{id}/runs                    run code in it (202)
    POST   /experience-sessions/{id}/runs/{run_id}/explain   explain a run (202)
    POST   /experience-sessions/{id}/minilab                 a Mini Lab command (simulated)
    DELETE /experience-sessions/{id}                         end it

Starting and running are explicit user actions; everything slow is a worker job
whose progress arrives on that job's event stream. A Mini Lab answers here
because it runs nothing -- every response is a lookup in a generated, validated
table, labelled as simulated.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_session
from repolab.db.jobs import create_job, get_job
from repolab.db.runtimes import latest_runtime
from repolab.db.store import load_architecture, load_manifest
from repolab.events import liveness, read_liveness, session_key, task_health
from repolab.events.liveness import Activity, Health
from repolab.experiences import sessions as store
from repolab.experiences.adapters import DiscoveryContext
from repolab.experiences.catalog import experiences_for
from repolab.experiences.discovery import discover
from repolab.experiences.model import ExperienceDefinition, ExperienceList, Strategy

log = structlog.get_logger(__name__)

router = APIRouter(tags=["experiences"])

MAX_CODE = 20_000


def _discover(session: Session, job_id: str) -> tuple[Any, ExperienceList]:
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")
    manifest = load_manifest(session, job.repo_url)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this repository has not been investigated")
    catalog = experiences_for(manifest)
    builds = store.builds_for(session, manifest.repo_url, manifest.commit_sha)
    runtime = latest_runtime(session, manifest.repo_url)
    live: dict[str, str] = {}
    if runtime is not None and runtime.state:
        live = {s["name"]: s["status"] for s in runtime.state.get("services", [])}
    context = DiscoveryContext(
        manifest=manifest,
        catalog=catalog,
        architecture=load_architecture(session, manifest.repo_url),
        builds={(b.adapter, b.strategy): b.status for b in builds},
        build_images={(b.adapter, b.strategy): b.image or "" for b in builds},
        build_reasons={
            # The first paragraph is the plain reason; the raw log follows it.
            (b.adapter, b.strategy): str((b.detail or {}).get("log_tail", "")).split("\n\n")[0][
                :600
            ]
            for b in builds
            if b.status == "failed"
        },
        sessions={s.experience_id: s.id for s in reversed(store.active_sessions(session, job_id))},
        live_services=live,
        surface_confidence={e.surface.value: e.confidence for e in catalog.experiences},
    )
    return manifest, discover(context)


@router.get("/jobs/{job_id}/experience-list", response_model=ExperienceList)
def experience_list(
    job_id: str, session: Annotated[Session, Depends(get_session)]
) -> ExperienceList:
    return _discover(session, job_id)[1]


class StartRequest(BaseModel):
    strategy: Strategy | None = None
    """Which runtime option to use. Defaults to the experience's first ready one."""


class SessionView(BaseModel):
    id: str
    job_id: str
    experience_id: str
    adapter: str
    strategy: str
    reality: str
    status: str
    events_url: str | None
    state: store.SessionState
    experience: ExperienceDefinition | None = None
    health: Health | None = None
    """While preparing: ``waiting``, ``working`` or ``interrupted``, from the
    preparing worker's heartbeat. ``None`` otherwise."""
    activity: Activity | None = None
    """While preparing: the step the worker is on (a machine key)."""


def _liveness(session: Session, record: Any) -> tuple[Health | None, Activity | None]:
    if record.status != store.PREPARING or not record.events_job_id:
        return None, None
    job = get_job(session, record.events_job_id)
    if job is None:
        return None, None
    live = read_liveness(liveness.default_store(), session_key(record.id))
    health = task_health(job.status, live, job.started_at or job.created_at)
    return health, live.snapshot.activity if live.snapshot else None


def _view(
    record: Any,
    state: store.SessionState,
    experience: ExperienceDefinition | None = None,
    session: Session | None = None,
) -> SessionView:
    health, activity = _liveness(session, record) if session is not None else (None, None)
    return SessionView(
        id=record.id,
        job_id=record.job_id,
        experience_id=record.experience_id,
        adapter=record.adapter,
        strategy=record.strategy,
        reality=record.reality,
        status=record.status,
        events_url=f"/jobs/{record.events_job_id}/events" if record.events_job_id else None,
        state=state,
        experience=experience,
        health=health,
        activity=activity,
    )


def _find(listing: ExperienceList, experience_id: str) -> ExperienceDefinition | None:
    pool = [*listing.experiences, *listing.also, *([listing.fallback] if listing.fallback else [])]
    return next((e for e in pool if e.id == experience_id), None)


@router.post(
    "/jobs/{job_id}/experiences/{experience_id}/sessions",
    response_model=SessionView,
    status_code=202,
)
def start(
    job_id: str,
    experience_id: str,
    payload: StartRequest,
    session: Annotated[Session, Depends(get_session)],
) -> SessionView:
    manifest, listing = _discover(session, job_id)
    experience = _find(listing, experience_id)
    if experience is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no experience {experience_id}")
    if experience.route is not None or experience.adapter in {"live", "cli"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{experience.title} opens in its own screen: {experience.route}",
        )
    option = next(
        (
            o
            for o in experience.options
            if payload.strategy is None or o.strategy == payload.strategy
        ),
        None,
    )
    if option is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{experience.title} cannot run that way")

    for existing in store.active_sessions(session, job_id):
        if existing.experience_id == experience_id and existing.strategy == option.strategy.value:
            loaded = store.load_session(session, existing.id)
            assert loaded is not None
            health, _ = _liveness(session, existing)
            if health is not Health.INTERRUPTED:
                return _view(*loaded, experience, session)
            # Its worker stopped beating: retire it and prepare afresh, rather
            # than hand back a preparation nobody is working on.
            _, state = loaded
            state.failure = "preparation was interrupted; it was started again"
            store.save_state(session, existing.id, state, status=store.FAILED)

    events = create_job(session, manifest.repo_url, kind="experience")
    record = store.create_session(
        session,
        job_id=job_id,
        repo_url=manifest.repo_url,
        commit_sha=manifest.commit_sha,
        experience_id=experience_id,
        adapter=experience.adapter,
        strategy=option.strategy.value,
        reality=option.reality.value,
        events_job_id=events.id,
    )
    session.commit()
    _enqueue("worker.experience_tasks.prepare_experience", events.id, record.id, timeout=4 * 3600)
    loaded = store.load_session(session, record.id)
    assert loaded is not None
    return _view(*loaded, experience, session)


def _load(session: Session, session_id: str) -> tuple[Any, store.SessionState]:
    loaded = store.load_session(session, session_id)
    if loaded is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no experience session {session_id}")
    return loaded


@router.get("/experience-sessions/{session_id}", response_model=SessionView)
def session_view(session_id: str, session: Annotated[Session, Depends(get_session)]) -> SessionView:
    record, state = _load(session, session_id)
    _, listing = _discover(session, record.job_id)
    return _view(record, state, _find(listing, record.experience_id), session)


class RunRequest(BaseModel):
    code: str = Field(min_length=1, max_length=MAX_CODE)
    action_id: str | None = Field(default=None, max_length=80)
    shuffle_partitions: int | None = Field(default=None, ge=1, le=64)


class RunCreated(BaseModel):
    run_id: str
    events_url: str


@router.post("/experience-sessions/{session_id}/runs", response_model=RunCreated, status_code=202)
def run(
    session_id: str, payload: RunRequest, session: Annotated[Session, Depends(get_session)]
) -> RunCreated:
    record, state = _load(session, session_id)
    if record.status != store.READY:
        raise HTTPException(status.HTTP_409_CONFLICT, f"the session is {record.status}, not ready")
    if record.adapter not in {"spark", "library"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "this experience does not run code")
    if any(r.status == "running" for r in state.runs):
        raise HTTPException(status.HTTP_409_CONFLICT, "a run is already in progress")
    events = create_job(session, record.repo_url, kind="experience_run")
    entry = store.RunRecord(
        id=store.new_id(),
        code=payload.code,
        action_id=payload.action_id,
        shuffle_partitions=payload.shuffle_partitions,
        events_job_id=events.id,
    )
    state.runs.append(entry)
    store.save_state(session, session_id, state)
    session.commit()
    _enqueue("worker.experience_tasks.run_experience", events.id, session_id, entry.id, timeout=900)
    return RunCreated(run_id=entry.id, events_url=f"/jobs/{events.id}/events")


@router.post(
    "/experience-sessions/{session_id}/runs/{run_id}/explain",
    response_model=RunCreated,
    status_code=202,
)
def explain_run(
    session_id: str, run_id: str, session: Annotated[Session, Depends(get_session)]
) -> RunCreated:
    record, state = _load(session, session_id)
    entry = state.run(run_id)
    if entry is None or entry.result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing has run to explain")
    if record.adapter != "spark":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "only Spark runs carry measurements to explain"
        )
    events = create_job(session, record.repo_url, kind="experience_explain")
    session.commit()
    _enqueue(
        "worker.experience_tasks.explain_experience", events.id, session_id, run_id, timeout=600
    )
    return RunCreated(run_id=run_id, events_url=f"/jobs/{events.id}/events")


class MiniLabCommand(BaseModel):
    command: str = Field(min_length=1, max_length=500)


@router.post("/experience-sessions/{session_id}/minilab")
def minilab(
    session_id: str, payload: MiniLabCommand, session: Annotated[Session, Depends(get_session)]
) -> dict[str, Any]:
    from repolab.builds.minilab import MiniLab

    record, state = _load(session, session_id)
    if record.adapter != "minilab" or state.minilab is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this session is not a ready Mini Lab")
    response = MiniLab.model_validate(state.minilab).respond(payload.command)
    entry = {
        "command": payload.command,
        **response.model_dump(mode="json"),
        "label": response.label,
    }
    state.transcript.append(entry)
    state.transcript = state.transcript[-100:]
    store.save_state(session, session_id, state)
    session.commit()
    return entry


@router.delete("/experience-sessions/{session_id}", status_code=202)
def stop(session_id: str, session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    _, state = _load(session, session_id)
    store.save_state(session, session_id, state, status=store.STOPPED)
    session.commit()
    return {"id": session_id, "status": store.STOPPED}


def _enqueue(task: str, *args: str, timeout: int) -> None:
    try:
        from worker.jobs import BACKGROUND, INTERACTIVE, build_queue

        # Preparation builds images and compiles; runs and explanations have a
        # learner waiting on them.
        name = BACKGROUND if task.endswith("prepare_experience") else INTERACTIVE
        build_queue(name=name).enqueue(task, *args, job_timeout=timeout, result_ttl=3600)
    except Exception as exc:  # noqa: BLE001 - recorded; the rows survive
        log.warning("experience.enqueue_failed", task=task, error=str(exc))
