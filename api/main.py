"""FastAPI application.

Slow operations never run inside request handlers -- those belong to the worker.
This process accepts repository submissions, reports job state, and streams
progress events.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from redis import Redis
from sqlalchemy import text

from api import deps
from api.routes import (
    artifacts,
    cli,
    experience_sessions,
    experiments,
    jobs,
    lessons,
    metrics,
    runs,
    runtimes,
    sources,
)
from api.runtime_proxy import RuntimeProxyMiddleware
from repolab import __version__
from repolab.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own the terminal reaper for the life of the process.

    A terminal session is a live container. Nothing else would ever release one
    belonging to a learner who closed their tab, and each is sized for Cassandra.
    On shutdown every session is stopped, so a restart does not leave containers
    behind with nobody tracking them.
    """
    reaper = asyncio.create_task(cli.reap_idle_sessions())
    runtime_reaper = asyncio.create_task(_reap_idle_runtimes())
    try:
        yield
    finally:
        reaper.cancel()
        runtime_reaper.cancel()
        manager = cli.current_manager()
        if manager is not None:
            await manager.close()


#: How often idle live apps are looked for. The worker does the stopping.
RUNTIME_REAP_SECONDS = 600


async def _reap_idle_runtimes() -> None:
    """Ask the worker, periodically, to stop live apps nobody has used lately.

    The API never touches Docker itself; it only queues the job.
    """
    while True:
        await asyncio.sleep(RUNTIME_REAP_SECONDS)
        try:
            from worker.jobs import build_queue

            build_queue().enqueue("worker.runtime_tasks.reap_idle_runtimes", job_timeout=900)
        except Exception:  # noqa: BLE001 - Redis down; try again next round
            pass


app = FastAPI(title="RepoLab API", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    # DELETE is here for terminal sessions: a learner closing a lesson releases
    # a container, and leaving that to the idle reaper would hold 4 GB for
    # twenty minutes after they left.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
# Added last so it runs first: a live app's host never reaches RepoLab's routes.
app.add_middleware(RuntimeProxyMiddleware)

app.include_router(jobs.router)
app.include_router(artifacts.router)
app.include_router(experiments.router)
app.include_router(runs.router)
app.include_router(cli.router)
app.include_router(lessons.router)
app.include_router(metrics.router)
app.include_router(runtimes.router)
app.include_router(experience_sessions.router)
app.include_router(sources.router)


class Health(BaseModel):
    status: str
    version: str


@app.get("/health", response_model=Health)
async def health() -> Health:
    """Liveness only: deliberately touches no external service."""
    return Health(status="ok", version=__version__)


class Readiness(BaseModel):
    status: str
    database: bool
    redis: bool


def database_ready() -> bool:
    try:
        with deps.engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure means "not ready", reported as such
        return False
    return True


def redis_ready() -> bool:
    try:
        client = Redis.from_url(
            get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        return bool(client.ping())
    except Exception:  # noqa: BLE001 - any failure means "not ready", reported as such
        return False


@app.get("/health/ready", response_model=Readiness)
async def ready(response: Response) -> Readiness:
    """Readiness: can this process reach Postgres and Redis right now?

    503 when either is unreachable, so a proxy or orchestrator holds traffic back
    instead of serving errors. Says which, never why: no connection strings.
    """
    database, queue = await asyncio.gather(
        asyncio.to_thread(database_ready), asyncio.to_thread(redis_ready)
    )
    healthy = database and queue
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ok" if healthy else "unavailable", database=database, redis=queue)
