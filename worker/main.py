"""RQ worker entrypoint.

Run with ``python -m worker.main`` (or ``make dev-worker``).
"""

from __future__ import annotations

import os
import platform
import sys

import structlog
from redis import Redis
from rq import Queue, Worker

from repolab.config import Settings, get_settings


class WorkerRestarted(RuntimeError):
    """A job was interrupted by the worker stopping.

    Raised nowhere: it exists so :func:`repolab.failures.explain` has something
    to recognise, and so the sentence a user reads lives with every other one.
    """


QUEUE_NAME = "repolab"

#: macOS refuses to ``fork()`` a process that has initialised Objective-C state
#: unless this is set.
#:
#: RQ forks a work-horse per job, and every RepoLab job then forks again -- `git
#: clone` does, through subprocess. Without this the work-horse dies with signal
#: 6 ("+[NSCharacterSet initialize] may have been in progress in another thread
#: when fork() was called") and RQ reports only "Work-horse terminated
#: unexpectedly", so a job appears to vanish with no error anywhere.
FORK_SAFETY = "OBJC_DISABLE_INITIALIZE_FORK_SAFETY"

log = structlog.get_logger(__name__)


def ensure_fork_safety() -> bool:
    """Re-exec with :data:`FORK_SAFETY` set, if macOS needs it and it is absent.

    Setting ``os.environ`` is not enough: the Objective-C runtime reads this
    variable when it initialises, which happens before any of this module runs.
    The variable has to be present in the environment the process *starts* with,
    so the only way to fix it from inside is to start again.

    Returns True if a re-exec was performed (in which case this call does not
    return at all).
    """
    if platform.system() != "Darwin" or os.environ.get(FORK_SAFETY) == "YES":
        return False

    log.info("worker.reexec_for_fork_safety", variable=FORK_SAFETY)
    environment = {**os.environ, FORK_SAFETY: "YES"}
    os.execve(sys.executable, [sys.executable, "-m", "worker.main"], environment)
    return True  # unreachable: execve replaces the process


def resolve_abandoned_jobs(settings: Settings) -> list[str]:
    """Fail jobs that a previous worker was running when it stopped.

    Without this a killed worker leaves its job `running` forever: the browser
    shows *stalled* and nothing ever resolves it, so the only way out is for
    someone to notice. Best-effort -- a worker that cannot reach Postgres should
    still start and do its job.
    """
    from repolab.db import build_engine, session_scope
    from repolab.db.jobs import fail_abandoned_jobs
    from repolab.failures import explain

    reason = explain(WorkerRestarted()).summary
    engine = None
    try:
        engine = build_engine(settings)
        with session_scope(engine) as session:
            abandoned = fail_abandoned_jobs(session, reason=reason)
        if abandoned:
            log.info("worker.resolved_abandoned_jobs", jobs=abandoned)
        return abandoned
    except Exception as exc:  # noqa: BLE001 - never stop the worker from starting
        log.warning("worker.abandoned_job_sweep_failed", error=str(exc))
        return []
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    ensure_fork_safety()

    settings = get_settings()
    resolve_abandoned_jobs(settings)

    connection = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)
    log.info("worker.starting", queue=QUEUE_NAME)
    Worker([queue], connection=connection).work(with_scheduler=True)


if __name__ == "__main__":
    main()
