"""Background task for lesson generation.

Writing a lesson means reading the repository, reading its own documentation,
searching official documentation and calling Super. That is a minute of work on
a good day, so it belongs here -- and because the API serves what this writes,
the result goes to Postgres rather than to memory.

The lesson is generated **for a specific ready environment**. A lesson that asks
a learner to create a table is only a lesson if there is a server that will
create one, and the artifact is what says which commands the environment
supports. Without a ready build there is nothing to generate against, and the
task says so instead of writing a lesson nobody can run.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from pathlib import Path

import structlog
from sqlalchemy.orm import Session, sessionmaker

from repolab.config import Settings, get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.builds import load_builds
from repolab.db.jobs import get_job, mark_finished, mark_running
from repolab.db.lessons import save_lesson
from repolab.db.store import load_latest_ingestion, load_manifest
from repolab.events import DbEventEmitter, EventType, JobStatus
from repolab.failures import describe, explain
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.limits import DEFAULT_LIMITS
from repolab.ingest.url import validate_repo_url
from repolab.interfaces import ResearchProvider
from repolab.lessons.agent import LearningAgent
from repolab.lessons.models import Lesson

log = structlog.get_logger(__name__)

STEP_LABEL = "lesson"


class NoEnvironmentError(RuntimeError):
    """No ready environment exists, so there is nothing to write a lesson for."""


def generate_lesson(job_id: str, repo_url: str) -> str:
    """Generate and store one lesson for *repo_url*. Returns the lesson id."""
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    def session_factory() -> AbstractContextManager[Session]:
        return session_scope(engine)

    with factory() as session:
        job = get_job(session, job_id)
        start_seq = job.last_seq if job is not None else 0
        mark_running(session, job_id)
        session.commit()

    emitter = DbEventEmitter(job_id, session_factory, start_seq=start_seq)

    try:
        lesson = asyncio.run(_generate(repo_url, settings, emitter, factory))
    except Exception as exc:
        failure = explain(exc)
        detail = describe(exc)
        emitter.emit(
            EventType.FAILED,
            failure.summary,
            step=STEP_LABEL,
            data=failure.model_dump_public(),
        )
        with factory() as session:
            mark_finished(session, job_id, status=JobStatus.FAILED, error=detail)
            session.commit()
        log.warning("lesson.failed", job_id=job_id, error=detail)
        engine.dispose()
        raise

    with factory() as session:
        record = save_lesson(session, lesson)
        mark_finished(session, job_id, status=JobStatus.COMPLETED)
        session.commit()
        stored = record.id

    emitter.emit(
        EventType.STEP_DONE,
        f"{lesson.title} is ready: {lesson.step_count} steps",
        step=STEP_LABEL,
        data={
            "lesson_id": stored,
            "steps": lesson.step_count,
            "generated_by": lesson.generated_by,
            "fidelity": lesson.fidelity,
            "ungrounded_steps": lesson.ungrounded_steps,
        },
    )
    log.info("lesson.generated", job_id=job_id, lesson_id=stored, by=lesson.generated_by)
    return stored


async def _generate(
    repo_url: str,
    settings: Settings,
    emitter: DbEventEmitter,
    factory: sessionmaker[Session],
) -> Lesson:
    url = validate_repo_url(repo_url)

    with factory() as session:
        manifest = load_manifest(session, url.normalized)
        ingestion = load_latest_ingestion(session, url.normalized)
        artifacts = (
            load_builds(session, url.normalized, ingestion.commit_sha)
            if ingestion is not None
            else []
        )

    if manifest is None:
        raise NoEnvironmentError(f"{url.slug} has not been investigated yet")
    if not artifacts:
        raise NoEnvironmentError(
            f"{url.slug} has no ready environment; a lesson is written for the "
            "environment a learner will actually use"
        )

    artifact = artifacts[0]
    router, research = _providers(settings, factory)

    emitter.emit(
        EventType.STEP_STARTED,
        f"writing a lesson for the {artifact.fidelity.value}-fidelity environment",
        step=STEP_LABEL,
        data={"fidelity": artifact.fidelity.value, "kind": artifact.kind.value},
    )

    agent = LearningAgent(router=router, research=research, emitter=emitter)

    # The clone is what makes the references real: every file a step cites is
    # resolved here, against this commit, and dropped if it is not there.
    with TemporaryClone(url, limits=DEFAULT_LIMITS) as clone:
        return await agent.generate(manifest, artifact, Path(clone.path))


def _providers(
    settings: Settings, session_factory: object | None = None
) -> tuple[object | None, ResearchProvider | None]:
    """The model router and research provider, both optional.

    With neither, the deterministic lesson is generated instead -- a complete
    lesson, not a placeholder, which is why this task works with no credentials.
    """
    router = None
    research = None

    if settings.nebius_api_key is not None and any(settings.configured_models.values()):
        from repolab.analytics.model_telemetry import db_model_telemetry
        from repolab.llm import build_router

        router = build_router(
            settings,
            telemetry=db_model_telemetry(session_factory),  # type: ignore[arg-type]
        )
    if settings.tavily_api_key is not None:
        from repolab.research.tavily import TavilyResearchProvider

        research = TavilyResearchProvider(api_key=settings.tavily_api_key.get_secret_value())
    return router, research
