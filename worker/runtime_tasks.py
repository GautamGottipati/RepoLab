"""Launching, stopping and reaping a repository's live services.

The worker orchestrates; containers execute. Nothing here runs repository code:
it clones the commit, plans again from that exact tree (so the commands that run
match the code that is there), archives it, and hands the plan to the launcher,
which drives Docker. Progress goes to the job's event stream, and every change
of state to Postgres, which is what the proxy and the screen read.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from repolab.builds.resolver import archive_repository
from repolab.config import get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.jobs import get_job, mark_finished, mark_running
from repolab.db.runtimes import active_runtimes, load_runtime, save_runtime
from repolab.db.store import load_manifest
from repolab.events import DbEventEmitter, EventType, JobStatus
from repolab.failures import describe, explain
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.limits import DEFAULT_LIMITS
from repolab.ingest.tree import build_file_tree
from repolab.ingest.url import validate_repo_url
from repolab.runtime.backend import DockerRuntimeBackend
from repolab.runtime.launcher import Launcher, stop
from repolab.runtime.models import RuntimeState, RuntimeStatus
from repolab.runtime.plan import plan_runtime
from repolab.runtime.proxy import proxy_origin

log = structlog.get_logger(__name__)

STEP_LABEL = "runtime"

#: A runtime nobody has used for this long is stopped.
IDLE_MINUTES = 30

_EVENTS = {
    "step_started": EventType.STEP_STARTED,
    "step_done": EventType.STEP_DONE,
    "log_line": EventType.LOG_LINE,
    "failed": EventType.LOG_LINE,
}
"""A service failing is a step's outcome, not the job's: the job fails only if
the launch itself cannot proceed."""


def start_runtime(job_id: str, runtime_id: str, repo_url: str) -> str:
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    def sessions() -> AbstractContextManager[Session]:
        return session_scope(engine)

    with factory() as session:
        job = get_job(session, job_id)
        start_seq = job.last_seq if job is not None else 0
        mark_running(session, job_id)
        session.commit()

    emitter = DbEventEmitter(job_id, sessions, start_seq=start_seq)
    try:
        state = asyncio.run(_start(runtime_id, repo_url, emitter, factory))
    except Exception as exc:
        failure = explain(exc)
        emitter.emit(
            EventType.FAILED, failure.summary, step=STEP_LABEL, data=failure.model_dump_public()
        )
        with factory() as session:
            loaded = load_runtime(session, runtime_id)
            if loaded is not None:
                _, state = loaded
                state.status = RuntimeStatus.START_FAILED
                save_runtime(session, state)
            mark_finished(session, job_id, status=JobStatus.FAILED, error=describe(exc))
            session.commit()
        engine.dispose()
        raise

    emitter.emit(
        EventType.STEP_DONE,
        f"runtime {state.status.value}",
        step=STEP_LABEL,
        data={"runtime_id": runtime_id, "status": state.status.value},
    )
    with factory() as session:
        mark_finished(session, job_id, status=JobStatus.COMPLETED)
        session.commit()
    engine.dispose()
    return state.status.value


async def _start(runtime_id: str, repo_url: str, emitter: DbEventEmitter, factory) -> RuntimeState:
    url = validate_repo_url(repo_url)
    with factory() as session:
        manifest = load_manifest(session, url.normalized)
        loaded = load_runtime(session, runtime_id)
    if manifest is None or loaded is None:
        raise RuntimeError("the repository must be investigated before it can be run")
    _, state = loaded

    def save(current: RuntimeState) -> None:
        with factory() as session:
            save_runtime(session, current)
            session.commit()

    def emit(kind: str, message: str, data: dict) -> None:
        emitter.emit(_EVENTS.get(kind, EventType.LOG_LINE), message, step=STEP_LABEL, data=data)

    emitter.emit(EventType.STEP_STARTED, f"cloning {url.slug}", step=STEP_LABEL)
    with TemporaryClone(url, limits=DEFAULT_LIMITS) as clone:
        root = Path(clone.path)
        context = DetectionContext(
            root=root, tree=build_file_tree(root), commit_sha=clone.commit_sha
        )
        plan = plan_runtime(context, manifest)
        state.commit_sha = clone.commit_sha
        emitter.emit(
            EventType.STEP_DONE,
            f"planned {len(plan.services)} service(s) at {clone.commit_sha[:12]}",
            step=STEP_LABEL,
            data={"services": [s.name for s in plan.services]},
        )
        archive = archive_repository(root)

    launcher = Launcher(
        DockerRuntimeBackend(),
        emit=emit,
        save=save,
        origin=lambda service: proxy_origin(state.id, service),
    )
    return await launcher.launch(state, plan, archive)


def stop_runtime(runtime_id: str) -> str:
    """Remove everything a runtime created. Safe to call twice."""
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    with factory() as session:
        loaded = load_runtime(session, runtime_id)
    if loaded is None:
        return "missing"
    _, state = loaded
    state = asyncio.run(stop(state, DockerRuntimeBackend()))
    with factory() as session:
        save_runtime(session, state)
        session.commit()
    engine.dispose()
    return state.status.value


def reap_idle_runtimes(idle_minutes: int = IDLE_MINUTES) -> list[str]:
    """Stop runtimes nobody has used recently, so containers do not accumulate."""
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    cutoff = datetime.now(UTC) - timedelta(minutes=idle_minutes)
    with factory() as session:
        idle = [
            record.id for record in active_runtimes(session) if _aware(record.last_used_at) < cutoff
        ]
    engine.dispose()
    for runtime_id in idle:
        log.info("runtime.reaped", runtime_id=runtime_id)
        stop_runtime(runtime_id)
    return idle


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
