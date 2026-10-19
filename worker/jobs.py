"""Enqueuing background work.

The API creates a job row and enqueues it; the worker does the work. A request
handler never blocks on anything slow.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue

from repolab.config import Settings, get_settings

QUEUE_NAME = "repolab"

#: Generous: a large repository clone plus investigation can take minutes.
JOB_TIMEOUT_SECONDS = 1800

#: An experiment runs its variants sequentially, so its budget is the sum of
#: them: up to 24 Spark runs at roughly 14 s each, with room to spare.
EXPERIMENT_TIMEOUT_SECONDS = 3600

#: The ladder races every rung, and the slowest is a real `ant jar` build of
#: Cassandra -- measured at roughly four minutes on an unloaded machine, with
#: dependency resolution over the network. The budget is for the whole ladder,
#: not one rung.
BUILD_LADDER_TIMEOUT_SECONDS = 5400


def build_queue(settings: Settings | None = None) -> Queue:
    resolved = settings or get_settings()
    return Queue(QUEUE_NAME, connection=Redis.from_url(resolved.redis_url))


def enqueue_investigation(job_id: str, url: str, *, queue: Queue | None = None) -> str:
    """Queue an investigation and return the RQ job id."""
    target = queue or build_queue()
    enqueued = target.enqueue(
        "worker.tasks.investigate_repository",
        job_id,
        url,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=3600,
    )
    return str(enqueued.id)


def enqueue_experiment_proposal(
    job_id: str,
    experiment_id: str,
    url: str,
    question: str,
    *,
    concurrency: int = 1,
    rows: int | None = None,
    queue: Queue | None = None,
) -> str:
    """Queue the design of an experiment. Stops at the approval gate."""
    target = queue or build_queue()
    enqueued = target.enqueue(
        "worker.experiment_tasks.propose_experiment",
        job_id,
        experiment_id,
        url,
        question,
        concurrency,
        rows,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=3600,
    )
    return str(enqueued.id)


def enqueue_experiment_run(
    job_id: str,
    experiment_id: str,
    *,
    rows: int | None = None,
    queue: Queue | None = None,
) -> str:
    """Queue the execution of an **already approved** experiment.

    Enqueued only by an approval, which is what keeps the gate meaningful: there
    is no path from proposing to executing that does not pass through it.
    """
    target = queue or build_queue()
    enqueued = target.enqueue(
        "worker.experiment_tasks.run_experiment",
        job_id,
        experiment_id,
        rows,
        job_timeout=EXPERIMENT_TIMEOUT_SECONDS,
        result_ttl=3600,
    )
    return str(enqueued.id)


def enqueue_spark_run(
    job_id: str,
    url: str,
    *,
    shuffle_partitions: int = 200,
    rows: int | None = None,
    queue: Queue | None = None,
) -> str:
    """Queue a Spark pipeline run for the playground."""
    target = queue or build_queue()
    enqueued = target.enqueue(
        "worker.spark_tasks.run_spark_pipeline",
        job_id,
        url,
        shuffle_partitions,
        rows,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=3600,
    )
    return str(enqueued.id)


def enqueue_environment_resolution(
    job_id: str,
    url: str,
    *,
    slow_source_seconds: float = 0.0,
    queue: Queue | None = None,
) -> str:
    """Queue a full ladder resolution: cache, source build, release, partial, Mini Lab."""
    target = queue or build_queue()
    enqueued = target.enqueue(
        "worker.environment_tasks.resolve_environment",
        job_id,
        url,
        slow_source_seconds,
        job_timeout=BUILD_LADDER_TIMEOUT_SECONDS,
        result_ttl=3600,
    )
    return str(enqueued.id)


def enqueue_lesson_generation(job_id: str, url: str, *, queue: Queue | None = None) -> str:
    """Queue lesson generation for a repository whose environment is ready."""
    target = queue or build_queue()
    enqueued = target.enqueue(
        "worker.lesson_tasks.generate_lesson",
        job_id,
        url,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=3600,
    )
    return str(enqueued.id)
