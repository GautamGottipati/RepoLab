"""On-demand (P2) artifacts: generated only when someone asks for them.

``architecture.runtime_naming`` -- Nemotron Super names the runtime components,
writes their responsibilities, labels links and names flows. It needs no clone:
everything it reasons over (verified types, relations, their lines) is in the
persisted architecture, and its output is re-verified against the commit's
stored source files before it is shown.

Its result lives in the artifact's payload and is overlaid on the architecture
when read, so it never races the investigation that owns the graph.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.analytics import Analytics, AnalyticsEventType, DbAnalyticsWriter
from repolab.architecture.code_model import reading_path
from repolab.architecture.models import Provenance, load_graph
from repolab.architecture.runtime_synthesis import MIN_COMPONENTS, name_runtime
from repolab.architecture.verify import verify_graph
from repolab.artifacts import ARCH_RUNTIME_NAMING, Status
from repolab.artifacts import store as artifacts
from repolab.config import get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.jobs import get_job
from repolab.db.models import ArchitectureRecord, Ingestion
from repolab.events import DbEventEmitter
from repolab.events.liveness import ActivitySink, NoLiveness, job_key, liveness_for
from repolab.failures import explain
from repolab.sources import StoredSnapshot

log = structlog.get_logger(__name__)


class ModelUnavailable(RuntimeError):
    """No model could be asked, or it returned nothing usable."""


def generate_artifact(job_id: str, artifact_type: str) -> str:
    """Generate one on-demand artifact for *job_id*'s commit."""
    if artifact_type != ARCH_RUNTIME_NAMING:
        raise ValueError(f"{artifact_type} is not generated on demand")
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    def scope() -> AbstractContextManager[Session]:
        return session_scope(engine)

    try:
        with factory() as session:
            job = get_job(session, job_id)
            if job is None or job.ingestion_id is None:
                raise ValueError(f"job {job_id} has no analysed commit yet")
            ingestion = session.get(Ingestion, job.ingestion_id)
            assert ingestion is not None
            repo_url, commit, ingestion_id = job.repo_url, ingestion.commit_sha, ingestion.id
            start = job.last_seq
        emitter = DbEventEmitter(job_id, scope, start_seq=start)
        analytics = Analytics(DbAnalyticsWriter(scope), job_id=job_id, repo_url=repo_url)
        with liveness_for(job_key(job_id)) as liveness:
            asyncio.run(
                _runtime_naming(
                    job_id,
                    repo_url,
                    commit,
                    ingestion_id,
                    factory,
                    emitter,
                    analytics,
                    settings,
                    liveness,
                )
            )
    finally:
        engine.dispose()
    return job_id


async def _runtime_naming(
    job_id: str,
    repo_url: str,
    commit: str,
    ingestion_id: int,
    factory: Any,
    emitter: DbEventEmitter,
    analytics: Analytics,
    settings: Any,
    liveness: ActivitySink | None = None,
) -> None:
    live = liveness or NoLiveness()
    live.set_activity("architecture", ARCH_RUNTIME_NAMING)
    with factory() as session:
        record = artifacts.ensure(session, repo_url, commit, ARCH_RUNTIME_NAMING)
        if Status(record.status) is not Status.QUEUED:
            return  # someone else ran it, or it is no longer wanted
        record.job_id = job_id
        artifacts.transition(session, record, Status.GENERATING)
        session.commit()
        artifacts.announce(emitter, record, message="naming the runtime components")

    try:
        with factory() as session:
            stored = session.scalar(
                select(ArchitectureRecord).where(ArchitectureRecord.ingestion_id == ingestion_id)
            )
            if stored is None:
                raise ValueError("the architecture has not been saved yet")
            graph = load_graph(stored.graph)

        if not nameable(graph):
            # Built from detected components, or too small: already named as it is.
            with factory() as session:
                record = artifacts.ensure(session, repo_url, commit, ARCH_RUNTIME_NAMING)
                artifacts.transition(session, record, Status.READY, payload={"unchanged": True})
                session.commit()
                artifacts.announce(emitter, record, message="nothing to name")
            return

        router = _router(settings, factory)
        if router is None:
            raise ModelUnavailable("no model is configured to name the runtime components")
        graph = await name_runtime(graph, router)
        if graph.runtime.provenance is not Provenance.INFERRED:
            raise ModelUnavailable(
                graph.gaps[-1] if graph.gaps else "Nemotron could not name the runtime components"
            )

        with factory() as session:
            record = artifacts.ensure(session, repo_url, commit, ARCH_RUNTIME_NAMING)
            artifacts.transition(session, record, Status.VERIFYING)
            session.commit()
            live.set_activity("architecture", f"{ARCH_RUNTIME_NAMING}:verifying")
            artifacts.announce(
                emitter, record, message="verifying the named runtime against the source"
            )
            graph = verify_graph(graph, StoredSnapshot(session, repo_url, commit))
            graph.reading_path = reading_path(graph)
            payload = {
                "runtime": graph.runtime.model_dump(mode="json"),
                "flows": [f.model_dump(mode="json") for f in graph.flows],
                "reading_path": [r.model_dump(mode="json") for r in graph.reading_path],
                "dropped": {
                    k: v for k, v in graph.dropped.items() if "runtime" in k or "flow" in k
                },
            }
            artifacts.transition(session, record, Status.READY, payload=payload)
            session.commit()
            artifacts.announce(emitter, record, message="runtime components named")
            duration = record.duration_ms
        analytics.record(
            AnalyticsEventType.ARTIFACT_READY,
            artifact_type=ARCH_RUNTIME_NAMING,
            duration_ms=duration,
            cache_hit=False,
            commit=commit[:12],
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the artifact, retryable
        with factory() as session:
            record = artifacts.ensure(session, repo_url, commit, ARCH_RUNTIME_NAMING)
            artifacts.fail(
                session,
                record,
                code=type(exc).__name__,
                stage="runtime naming",
                message=str(exc) if isinstance(exc, ModelUnavailable) else explain(exc).headline,
            )
            session.commit()
            artifacts.announce(emitter, record)
        analytics.record(
            AnalyticsEventType.ARTIFACT_FAILED,
            artifact_type=ARCH_RUNTIME_NAMING,
            error=type(exc).__name__,
        )
        log.warning("artifact.runtime_naming_failed", job_id=job_id, error=str(exc)[:300])


def nameable(graph: Any) -> bool:
    """Whether Super has anything to name: grouped types, enough of them."""
    return len(graph.abstractions) >= MIN_COMPONENTS and any(
        c.members for c in graph.runtime.components
    )


def _router(settings: Any, factory: Any) -> Any:
    from worker.tasks import _providers

    router, _ = _providers(settings, factory)
    return router
