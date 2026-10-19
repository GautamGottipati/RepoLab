"""FastAPI application.

Slow operations never run inside request handlers -- those belong to the worker.
This process accepts repository submissions, reports job state, and streams
progress events.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.routes import cli, experiments, jobs, lessons, metrics, runs
from repolab import __version__

#: The local web app during development. Tightened for any real deployment.
DEV_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own the terminal reaper for the life of the process.

    A terminal session is a live container. Nothing else would ever release one
    belonging to a learner who closed their tab, and each is sized for Cassandra.
    On shutdown every session is stopped, so a restart does not leave containers
    behind with nobody tracking them.
    """
    reaper = asyncio.create_task(cli.reap_idle_sessions())
    try:
        yield
    finally:
        reaper.cancel()
        manager = cli.current_manager()
        if manager is not None:
            await manager.close()


app = FastAPI(title="RepoLab API", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(DEV_ORIGINS),
    # DELETE is here for terminal sessions: a learner closing a lesson releases
    # a container, and leaving that to the idle reaper would hold 4 GB for
    # twenty minutes after they left.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(experiments.router)
app.include_router(runs.router)
app.include_router(cli.router)
app.include_router(lessons.router)
app.include_router(metrics.router)


class Health(BaseModel):
    status: str
    version: str


@app.get("/health", response_model=Health)
async def health() -> Health:
    """Liveness only: deliberately touches no external service."""
    return Health(status="ok", version=__version__)
