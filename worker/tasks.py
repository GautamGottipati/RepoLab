"""Background tasks.

Slow work runs here, never in an API request handler. The investigation job is
the full flow: clone, investigate with the agent, classify, build the manifest,
persist, and report progress throughout so a browser can watch it happen.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AbstractContextManager
from pathlib import Path

import structlog
from sqlalchemy.orm import Session, sessionmaker

from repolab.agents import AgentStatus, RepositoryInvestigatorAgent
from repolab.analytics import Analytics, AnalyticsEventType, DbAnalyticsWriter
from repolab.classify import classify
from repolab.config import Settings, get_settings
from repolab.db import build_engine, build_session_factory, save_ingestion, session_scope
from repolab.db.jobs import get_job, mark_finished, mark_running, save_agent_run
from repolab.events import ActivityReporter, DbEventEmitter, EventType, JobStatus
from repolab.failures import describe, explain
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.languages import detect_languages
from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits, LimitReport
from repolab.ingest.pipeline import IngestionResult
from repolab.ingest.tree import build_file_tree
from repolab.ingest.url import validate_repo_url
from repolab.interfaces import ResearchProvider
from repolab.llm import ModelRouter
from repolab.manifest.builder import build_architecture, build_evidence, build_manifest
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

    try:
        result = asyncio.run(_investigate(job_id, url, settings, emitter, analytics, factory))
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
) -> str:
    limits: IngestLimits = DEFAULT_LIMITS
    repo_url = validate_repo_url(url)

    # The four phases a person following along sees. The reporter emits one
    # user-facing status per phase entered; the detailed events still flow
    # underneath it, so the browser can show both a tracker and a full feed.
    activity = ActivityReporter(emitter)
    activity.enter("inspecting_repository")

    emitter.emit(
        EventType.STEP_STARTED,
        f"cloning {repo_url.slug}",
        step="clone",
        data={"repo": repo_url.slug},
    )
    with TemporaryClone(repo_url, limits=limits) as clone:
        emitter.emit(
            EventType.STEP_DONE,
            f"cloned at {clone.commit_sha[:12]}",
            step="clone",
            data={"commit_sha": clone.commit_sha, "branch": clone.branch},
        )

        report = LimitReport()
        budget = ReadBudget(limits, report)
        tree = build_file_tree(clone.path, limits=limits, report=report)
        languages = detect_languages(tree)
        emitter.emit(
            EventType.PARTIAL_RESULT,
            f"{tree.file_count} files, "
            f"{', '.join(s.language for s in languages) or 'no languages detected'}",
            step="clone",
            data={
                "file_count": tree.file_count,
                "languages": [s.language for s in languages],
            },
        )

        context = DetectionContext(root=Path(clone.path), tree=tree, commit_sha=clone.commit_sha)
        router, research = _providers(settings, factory)

        analytics.record(AnalyticsEventType.AGENT_STARTED, agent="repository_investigator")
        agent = RepositoryInvestigatorAgent(context, budget, research=research, router=router)
        investigation = await agent.investigate(emitter, activity=activity)

        for step in investigation.run.steps:
            if step.request is not None:
                analytics.record(AnalyticsEventType.TOOL_CALLED, tool=step.request.tool, ok=step.ok)

        if investigation.run.status is AgentStatus.FAILED:
            analytics.record(
                AnalyticsEventType.AGENT_FAILED, error=investigation.run.error or "unknown"
            )
            raise RuntimeError(investigation.run.error or "investigation failed")
        analytics.record(
            AnalyticsEventType.AGENT_COMPLETED,
            iterations=investigation.run.iterations,
            findings=investigation.findings,
        )

        activity.enter("understanding_data_flow")
        emitter.emit(EventType.STEP_STARTED, "classifying interaction surfaces", step="classify")
        outcome = investigation.outcome
        classification = await classify(
            outcome.run,
            entrypoints=outcome.entrypoints,
            services=outcome.services,
            router=router,
        )
        primary_label = classification.primary_type.value if classification.primary_type else "none"
        emitter.emit(
            EventType.STEP_DONE,
            f"primary experience: {primary_label}",
            step="classify",
            data={
                "surfaces": [
                    {"type": s.type.value, "confidence": s.confidence}
                    for s in classification.surfaces
                ],
                "hybrid": classification.hybrid,
                "resolved_by": classification.resolved_by.value,
            },
        )

        emitter.emit(EventType.STEP_STARTED, "building the manifest", step="manifest")
        manifest = build_manifest(
            repo_url=repo_url.normalized,
            commit_sha=clone.commit_sha,
            outcome=outcome,
            languages=languages,
            classification=classification,
        )
        result = IngestionResult(
            repo_url=repo_url,
            commit_sha=clone.commit_sha,
            branch=clone.branch,
            manifest=manifest,
            architecture=build_architecture(outcome),
            evidence=build_evidence(outcome),
            classification=classification,
            languages=languages,
            file_count=tree.file_count,
            total_size=tree.total_size,
            tree_truncated=tree.truncated,
            limits_hit=list(report.hit),
            bytes_read=budget.bytes_read,
            files_read=budget.files_read,
        )
        analytics.record(
            AnalyticsEventType.REPO_MANIFEST_CREATED,
            confidence=manifest.confidence,
            surfaces=len(manifest.interaction_surfaces),
        )

    with factory() as session:
        ingestion = save_ingestion(session, result)
        save_agent_run(session, job_id, investigation.run)
        mark_finished(session, job_id, status=JobStatus.COMPLETED, ingestion_id=ingestion.id)
        session.commit()
        ingestion_id = ingestion.id

    emitter.emit(
        EventType.STEP_DONE,
        "investigation complete",
        step="manifest",
        data={"ingestion_id": ingestion_id, "confidence": manifest.confidence},
    )

    prepare_environment(manifest, emitter, activity)

    analytics.record(AnalyticsEventType.REPO_READY, ingestion_id=ingestion_id)
    log.info("job.completed", job_id=job_id, ingestion_id=ingestion_id)
    return job_id


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
