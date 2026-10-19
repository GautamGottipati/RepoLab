"""Background task for the Spark playground.

Running the pipeline is what makes the playground a playground rather than a
report, and it takes ten to fifteen seconds of real Spark in a container -- so it
belongs here, never in a request handler.

The task reuses the Phase 6 runner unchanged: this is orchestration around
:class:`SparkDemoRunner`, not a second way to run Spark. What it adds is the job
plumbing a browser needs -- a progress stream, and a row linking the job to the
measurements it produced.

Like the experiment run task, it **verifies the commit** it clones against the
commit the repository was investigated at. Measuring different code than the
architecture screen is describing would make every figure unattributable.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AbstractContextManager
from pathlib import Path

import structlog
from sqlalchemy.orm import Session, sessionmaker

from repolab.config import Settings, get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.jobs import get_job, mark_finished, mark_running
from repolab.db.models import Ingestion
from repolab.db.runs import save_spark_run
from repolab.events import DbEventEmitter, EventType, JobStatus
from repolab.experiences.spark import SparkDemoRunner, SparkRunConfig
from repolab.failures import describe, explain
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.limits import DEFAULT_LIMITS
from repolab.ingest.url import validate_repo_url
from repolab.sandbox.docker import DockerEnvironment, docker_available, ensure_image
from repolab.sandbox.models import SandboxError

log = structlog.get_logger(__name__)

STEP_LABEL = "pipeline"

#: Pinned: the event-log field names were verified against this Spark version.
DEFAULT_IMAGE = os.environ.get("REPOLAB_SPARK_IMAGE", "apache/spark:3.5.1-python3")


class CommitMovedError(RuntimeError):
    """The repository's HEAD is no longer the commit that was investigated."""


def run_spark_pipeline(
    job_id: str,
    repo_url: str,
    shuffle_partitions: int = 200,
    rows: int | None = None,
) -> str:
    """Run the repository's job on real Spark and measure it."""
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
        run_id = asyncio.run(
            _run(job_id, repo_url, shuffle_partitions, rows, settings, emitter, factory)
        )
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
        log.warning("pipeline.failed", job_id=job_id, error=detail)
        engine.dispose()
        raise

    engine.dispose()
    return run_id


def expected_commit(factory: sessionmaker[Session], repo_url: str) -> str | None:
    """The commit this repository was most recently investigated at.

    ``None`` when it has never been investigated, in which case there is nothing
    to check against and the run proceeds on whatever HEAD is.
    """
    from repolab.db.store import load_latest_ingestion

    with factory() as session:
        ingestion: Ingestion | None = load_latest_ingestion(session, repo_url)
        return ingestion.commit_sha if ingestion is not None else None


async def _run(
    job_id: str,
    repo_url: str,
    shuffle_partitions: int,
    rows: int | None,
    settings: Settings,
    emitter: DbEventEmitter,
    factory: sessionmaker[Session],
) -> str:
    url = validate_repo_url(repo_url)

    # An environment problem, not a failed run: raising SparkRunFailed here told
    # a user "the repository's own job failed when it ran" when nothing had run.
    # Found by pointing DOCKER_HOST at a socket that does not exist.
    if not docker_available():
        raise SandboxError(
            "Docker is not available, and repository code must not run on the API host"
        )

    emitter.emit(
        EventType.STEP_STARTED,
        f"preparing {DEFAULT_IMAGE}",
        step="environment",
        data={"image": DEFAULT_IMAGE},
    )
    status = ensure_image(DEFAULT_IMAGE)
    if not status.ready:
        raise SandboxError(status.describe())
    emitter.emit(
        EventType.STEP_DONE,
        status.describe(),
        step="environment",
        data={"image": DEFAULT_IMAGE, "pulled": status.pulled},
    )

    emitter.emit(
        EventType.STEP_STARTED, f"cloning {url.slug}", step="clone", data={"repo": url.slug}
    )
    with TemporaryClone(url, limits=DEFAULT_LIMITS) as clone:
        wanted = expected_commit(factory, url.normalized)
        if wanted is not None and wanted != clone.commit_sha:
            # The architecture screen describes the investigated commit. Measuring
            # a different one would make every figure unattributable to the code
            # the user is looking at.
            raise CommitMovedError(
                f"this repository was investigated at {wanted[:12]} but the default "
                f"branch is now {clone.commit_sha[:12]}; import it again"
            )

        emitter.emit(
            EventType.STEP_DONE,
            f"cloned at {clone.commit_sha[:12]}",
            step="clone",
            data={"commit_sha": clone.commit_sha},
        )

        config = SparkRunConfig(shuffle_partitions=shuffle_partitions)
        runner = SparkDemoRunner(DockerEnvironment(), emitter=emitter)
        result = await runner.run(
            repo_url=url.normalized,
            commit_sha=clone.commit_sha,
            repository_root=Path(clone.path),
            image=DEFAULT_IMAGE,
            config=config,
            rows=rows,
        )

    with factory() as session:
        record = save_spark_run(session, result, job_id=job_id)
        mark_finished(session, job_id, status=JobStatus.COMPLETED)
        session.commit()
        stored = record.id

    emitter.emit(
        EventType.STEP_DONE,
        (
            f"measured {result.measurements.task_count} tasks across "
            f"{result.measurements.stage_count} stages"
        ),
        step=STEP_LABEL,
        data={
            "run_id": result.run_id,
            "spark_run": stored,
            "shuffle_partitions": shuffle_partitions,
            "tasks": result.measurements.task_count,
            "partitions": result.measurements.distribution.partitions,
            "empty_partitions": result.measurements.distribution.empty_partitions,
            "total_runtime_ms": result.measurements.total_runtime_ms,
        },
    )
    log.info("pipeline.completed", job_id=job_id, run_id=result.run_id)
    return result.run_id
