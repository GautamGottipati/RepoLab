"""Background tasks.

Slow work runs here, never in an API request handler. The investigation job is
the full flow: clone, investigate with the agent, classify, build the manifest,
persist, and report progress throughout so a browser can watch it happen.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AbstractContextManager

import structlog
from sqlalchemy.orm import Session, sessionmaker

from repolab.agents.models import AgentRun
from repolab.analytics import Analytics, AnalyticsEventType, DbAnalyticsWriter
from repolab.config import Settings, get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.jobs import get_job, mark_finished, mark_running
from repolab.events import ActivityReporter, DbEventEmitter, EventType, JobStatus
from repolab.events.liveness import ActivitySink, job_key, liveness_for
from repolab.evidence.models import DocumentationReference
from repolab.failures import describe, explain
from repolab.ingest.url import validate_repo_url
from repolab.interfaces import ResearchProvider
from repolab.llm import ModelRouter
from repolab.manifest.models import RepoManifest, SurfaceType
from repolab.sandbox.docker import docker_available, ensure_image

log = structlog.get_logger(__name__)

#: The image the Spark playground runs in. Pulled during investigation so the
#: first run in the browser is not an unexplained wait.
SPARK_IMAGE = os.environ.get("REPOLAB_SPARK_IMAGE", "apache/spark:3.5.1-python3")


def ping() -> str:
    """Smallest possible task, used to verify the queue round-trips."""
    return "pong"


def investigate_repository(job_id: str, url: str) -> str:
    """Investigate a repository, reporting progress as it goes.

    The RQ worker is synchronous and the pipeline is asynchronous, so this is the
    seam where the two meet. Event emission is synchronous on purpose, so it works
    on either side of it.
    """
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    def session_factory() -> AbstractContextManager[Session]:
        return session_scope(engine)

    emitter = DbEventEmitter(job_id, session_factory, start_seq=_resume_seq(factory, job_id))
    analytics = Analytics(DbAnalyticsWriter(session_factory), job_id=job_id, repo_url=url)

    with factory() as session:
        mark_running(session, job_id)
        session.commit()

    # The heartbeat says "this worker is alive and on it" for as long as the
    # job runs, independently of whether any milestone has been reached.
    try:
        with liveness_for(job_key(job_id)) as liveness:
            result = asyncio.run(
                _investigate(job_id, url, settings, emitter, analytics, factory, liveness)
            )
    except Exception as exc:
        # What a person reads, with the exception's own words kept behind it.
        failure = explain(exc)
        detail = describe(exc)
        emitter.emit(
            EventType.FAILED,
            failure.summary,
            step="investigate",
            data=failure.model_dump_public(),
        )
        analytics.record(AnalyticsEventType.AGENT_FAILED, error=type(exc).__name__)
        with factory() as session:
            mark_finished(session, job_id, status=JobStatus.FAILED, error=detail)
            session.commit()
        log.warning("job.failed", job_id=job_id, error=detail)
        engine.dispose()
        raise

    engine.dispose()
    return result


def _resume_seq(factory: sessionmaker[Session], job_id: str) -> int:
    """Continue the sequence if this job is being retried."""
    with factory() as session:
        job = get_job(session, job_id)
        return job.last_seq if job is not None else 0


async def _investigate(
    job_id: str,
    url: str,
    settings: Settings,
    emitter: DbEventEmitter,
    analytics: Analytics,
    factory: sessionmaker[Session],
    liveness: ActivitySink | None = None,
) -> str:
    """The investigation, published artifact by artifact. See :mod:`worker.analysis`."""
    from worker.analysis import analyse
    from worker.jobs import enqueue_artifact

    repo_url = validate_repo_url(url)
    ingestion_id = await analyse(
        job_id,
        repo_url,
        settings=settings,
        emitter=emitter,
        analytics=analytics,
        factory=factory,
        providers=lambda: _providers(settings, factory),
        enqueue=_safe_enqueue(enqueue_artifact),
        prepare=lambda manifest, activity: prepare_environment(manifest, emitter, activity),
        liveness=liveness,
    )
    with factory() as session:
        mark_finished(session, job_id, status=JobStatus.COMPLETED, ingestion_id=ingestion_id)
        session.commit()
    emitter.emit(
        EventType.STEP_DONE,
        "investigation complete",
        step="manifest",
        data={"ingestion_id": ingestion_id},
    )
    analytics.record(AnalyticsEventType.REPO_READY, ingestion_id=ingestion_id)
    log.info("job.completed", job_id=job_id, ingestion_id=ingestion_id)
    return job_id


def _safe_enqueue(enqueue: object) -> object:
    def run(job_id: str, artifact_type: str) -> None:
        try:
            enqueue(job_id, artifact_type)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - the artifact stays queued and can be retried
            log.warning("artifact.enqueue_failed", artifact_type=artifact_type, error=str(exc))

    return run


def prepare_environment(
    manifest: RepoManifest, emitter: DbEventEmitter, activity: ActivityReporter
) -> bool:
    """Make the execution environment ready, and say what that took.

    Only for a repository whose primary surface is the Spark data playground:
    there is nothing to prepare for a surface RepoLab cannot yet run.

    **Failure is reported, not fatal.** The manifest is the investigation's
    result and it is already saved; a machine without Docker should still get a
    usable architecture screen, with the playground saying plainly that it cannot
    run. Raising here would throw away work that succeeded.
    """
    primary = manifest.primary_surface
    if primary is None or primary.type is not SurfaceType.DATA_PLAYGROUND:
        return False

    activity.enter("preparing_environment")
    emitter.emit(
        EventType.STEP_STARTED,
        f"checking the execution environment for {SPARK_IMAGE}",
        step="environment",
        data={"image": SPARK_IMAGE},
    )

    if not docker_available():
        emitter.emit(
            EventType.LOG_LINE,
            "Docker is not available, so the pipeline cannot be run on this machine",
            step="environment",
            data={"image": SPARK_IMAGE, "ready": False, "reason": "docker unavailable"},
        )
        return False

    status = ensure_image(SPARK_IMAGE)
    emitter.emit(
        EventType.STEP_DONE if status.ready else EventType.LOG_LINE,
        status.describe(),
        step="environment",
        data={
            "image": SPARK_IMAGE,
            "ready": status.ready,
            "pulled": status.pulled,
            "seconds": round(status.seconds, 1),
        },
    )
    return status.ready


def retrieved_docs(run: AgentRun) -> list[DocumentationReference]:
    """Official documentation the investigation actually retrieved, for citation."""
    found: dict[str, DocumentationReference] = {}
    for step in run.steps:
        if step.request is None or step.request.tool != "search_official_docs" or not step.ok:
            continue
        for hit in step.result.data.get("results", []) if step.result else []:
            url = hit.get("url") if isinstance(hit, dict) else None
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                found.setdefault(
                    url,
                    DocumentationReference(
                        url=url, title=hit.get("title"), excerpt=hit.get("excerpt")
                    ),
                )
    return list(found.values())


def _providers(
    settings: Settings,
    session_factory: object | None = None,
) -> tuple[ModelRouter | None, ResearchProvider | None]:
    """Build the optional model router and research provider.

    Both are optional: investigation is fully deterministic without credentials.

    *session_factory* is where model-call telemetry is written. Passed in
    rather than created here, so one process holds one connection pool.
    """
    router = None
    research = None

    if settings.nebius_api_key is not None and any(settings.configured_models.values()):
        from repolab.llm import build_router

        router = build_router(settings, telemetry=_telemetry(session_factory))
    if settings.tavily_api_key is not None:
        from repolab.research.tavily import TavilyResearchProvider

        research = TavilyResearchProvider(api_key=settings.tavily_api_key.get_secret_value())
    return router, research


def _telemetry(session_factory: object | None) -> object | None:
    """The sink model-call records are written to, when there is a database."""
    from repolab.analytics.model_telemetry import db_model_telemetry

    return db_model_telemetry(session_factory)  # type: ignore[arg-type]
