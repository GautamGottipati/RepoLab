"""Persistence for measured Spark runs.

Stores the parsed measurements alongside everything needed to interpret them --
commit, dataset hash, configuration, environment -- and a reference to the
preserved raw event log the figures came from.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.db.models import SparkRun
from repolab.experiences.spark.runner import SparkRunResult


def save_spark_run(
    session: Session, result: SparkRunResult, *, job_id: str | None = None
) -> SparkRun:
    """Persist one run, replacing a previous record of the same run id.

    *job_id* links the run to the job that produced it, which is how a browser
    watching a job finds the measurements it was waiting for. A run started from
    the command line passes nothing and stays unlinked.
    """
    record = session.scalar(select(SparkRun).where(SparkRun.run_id == result.run_id))
    if record is None:
        record = SparkRun(run_id=result.run_id)
        session.add(record)

    if job_id is not None:
        record.job_id = job_id
    record.repo_url = result.repo_url
    record.commit_sha = result.commit_sha
    record.dataset_hash = result.dataset.sha256

    record.configuration = result.config.model_dump(mode="json")
    record.environment = result.environment.model_dump(mode="json")
    record.dataset = result.dataset.model_dump(mode="json")
    record.measurements = result.measurements.model_dump(mode="json")
    record.evidence = result.evidence.model_dump(mode="json")

    record.event_log_path = result.event_log_path
    record.event_log_sha256 = result.event_log_sha256
    record.event_log_bytes = result.event_log_bytes

    record.task_count = result.measurements.task_count
    record.stage_count = result.measurements.stage_count
    record.total_runtime_ms = result.measurements.total_runtime_ms
    record.wall_clock_seconds = result.wall_clock_seconds

    session.flush()
    return record


def load_spark_runs(
    session: Session, repo_url: str, commit_sha: str | None = None
) -> list[SparkRun]:
    """Runs recorded for a repository, newest first."""
    query = select(SparkRun).where(SparkRun.repo_url == repo_url)
    if commit_sha is not None:
        query = query.where(SparkRun.commit_sha == commit_sha)
    return list(session.scalars(query.order_by(SparkRun.created_at.desc())).all())


def count_spark_runs(session: Session) -> int:
    return len(session.scalars(select(SparkRun.id)).all())


def load_spark_run(session: Session, run_id: str) -> SparkRun | None:
    return session.scalar(select(SparkRun).where(SparkRun.run_id == run_id))


def load_run_for_job(session: Session, job_id: str) -> SparkRun | None:
    """The run a job produced, if it has finished measuring one."""
    return session.scalar(
        select(SparkRun).where(SparkRun.job_id == job_id).order_by(SparkRun.created_at.desc())
    )
