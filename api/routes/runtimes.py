"""Live experiences: start a repository's services, watch them, use them.

    POST   /jobs/{job_id}/runtime        start (202) -- an explicit user action
    GET    /jobs/{job_id}/runtime        the latest launch for this repository, and its plan
    GET    /runtimes/{id}                one launch
    DELETE /runtimes/{id}                stop it (202)
    POST   /runtimes/{id}/request        send one request to a running API, for the explorer

Starting services is gated on a person asking (CLAUDE.md: starting services
needs approval), and nothing slow happens here: the launch is a worker job whose
progress arrives on the job's event stream.

The live app itself is served by :class:`RuntimeProxyMiddleware`, on its own
origin per service, and only to the port RepoLab's sidecar holds for it.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_session
from repolab.db.jobs import create_job, get_job
from repolab.db.runtimes import create_runtime, latest_runtime, load_runtime
from repolab.db.store import load_manifest
from repolab.runtime.models import RuntimePlan, RuntimeState, RuntimeStatus
from repolab.runtime.proxy import forward_response_headers, proxy_origin, target_port

log = structlog.get_logger(__name__)

router = APIRouter(tags=["runtimes"])

#: How the explorer may talk to an API. Bounded so it cannot become a load tool.
EXPLORER_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
MAX_REQUEST_BODY = 64 * 1024
MAX_RESPONSE_BODY = 256 * 1024


class ServiceView(BaseModel):
    name: str
    origin: str | None
    """Where the live service is, once -- and only once -- it is running."""


class RuntimeView(BaseModel):
    runtime_id: str | None = None
    job_id: str | None = None
    events_url: str | None = None
    status: RuntimeStatus | None = None
    state: RuntimeState | None = None
    plan: RuntimePlan | None = None
    services: list[ServiceView] = Field(default_factory=list)


def _view(record: Any, state: RuntimeState, plan: RuntimePlan | None) -> RuntimeView:
    return RuntimeView(
        runtime_id=record.id,
        job_id=record.job_id,
        events_url=f"/jobs/{record.job_id}/events" if record.job_id else None,
        status=state.status,
        state=state,
        plan=plan,
        services=[
            ServiceView(
                name=service.name,
                origin=proxy_origin(state.id, service.name)
                if service.status is RuntimeStatus.RUNNING
                else None,
            )
            for service in state.services
        ],
    )


@router.get("/jobs/{job_id}/runtime", response_model=RuntimeView)
def job_runtime(job_id: str, session: Annotated[Session, Depends(get_session)]) -> RuntimeView:
    """The latest launch for this job's repository, with the plan it would run."""
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")
    manifest = load_manifest(session, job.repo_url)
    plan = manifest.runtime if manifest is not None else None
    record = latest_runtime(session, job.repo_url)
    if record is None or record.state is None:
        return RuntimeView(plan=plan)
    state = RuntimeState.model_validate(record.state)
    return _view(record, state, plan)


@router.post("/jobs/{job_id}/runtime", response_model=RuntimeView, status_code=202)
def start(job_id: str, session: Annotated[Session, Depends(get_session)]) -> RuntimeView:
    """Start this repository's planned services. Refused if there is no plan."""
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")
    manifest = load_manifest(session, job.repo_url)
    if manifest is None or manifest.runtime is None or not manifest.runtime.services:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "RepoLab has no plan to run this repository; investigate it again first",
        )

    existing = latest_runtime(session, job.repo_url)
    if existing is not None and existing.state is not None:
        current = RuntimeState.model_validate(existing.state)
        if current.status.active:
            return _view(existing, current, manifest.runtime)

    state = RuntimeState(
        id=os.urandom(6).hex(), repo_url=job.repo_url, commit_sha=manifest.commit_sha
    )
    run_job = create_job(session, job.repo_url, kind="runtime")
    record = create_runtime(session, state, manifest.runtime, job_id=run_job.id)
    session.commit()

    try:
        from worker.jobs import enqueue_runtime_start

        enqueue_runtime_start(run_job.id, state.id, job.repo_url)
    except Exception as exc:  # noqa: BLE001 - reported on the record; the row survives
        log.warning("runtime.enqueue_failed", runtime_id=state.id, error=str(exc))
    return _view(record, state, manifest.runtime)


@router.get("/runtimes/{runtime_id}", response_model=RuntimeView)
def runtime(runtime_id: str, session: Annotated[Session, Depends(get_session)]) -> RuntimeView:
    loaded = load_runtime(session, runtime_id)
    if loaded is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no runtime {runtime_id}")
    record, state = loaded
    plan = RuntimePlan.model_validate(record.plan) if record.plan else None
    return _view(record, state, plan)


@router.delete("/runtimes/{runtime_id}", status_code=202)
def stop(runtime_id: str, session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    if load_runtime(session, runtime_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no runtime {runtime_id}")
    try:
        from worker.jobs import enqueue_runtime_stop

        enqueue_runtime_stop(runtime_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"runtime_id": runtime_id, "status": "stopping"}


class ExplorerRequest(BaseModel):
    service: str = Field(max_length=40)
    method: str = Field(max_length=10)
    path: str = Field(min_length=1, max_length=2000)
    body: str | None = Field(default=None, max_length=MAX_REQUEST_BODY)
    content_type: str | None = Field(default="application/json", max_length=100)


class ExplorerResponse(BaseModel):
    status_code: int
    headers: list[tuple[str, str]]
    body: str
    truncated: bool = False
    binary: bool = False
    elapsed_ms: float


@router.post("/runtimes/{runtime_id}/request", response_model=ExplorerResponse)
async def send(
    runtime_id: str,
    payload: ExplorerRequest,
    session: Annotated[Session, Depends(get_session)],
) -> ExplorerResponse:
    """One request to a running API, made server-side through its sidecar."""
    method = payload.method.upper()
    if method not in EXPLORER_METHODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"method {method} is not allowed")
    if (
        not payload.path.startswith("/")
        or payload.path.startswith("//")
        or "://" in payload.path
        or "\\" in payload.path
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "path must be a path on the service")
    loaded = load_runtime(session, runtime_id)
    if loaded is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no runtime {runtime_id}")
    record, state = loaded
    port = target_port(state, payload.service)
    if port is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{payload.service} is not running")

    headers = (
        {"content-type": payload.content_type} if payload.body and payload.content_type else {}
    )
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.request(
                method,
                f"http://127.0.0.1:{port}{payload.path}",
                content=payload.body.encode() if payload.body else None,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, _timeout_detail(record.plan, payload.service)
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"the service did not answer: {type(exc).__name__}"
        ) from exc
    elapsed = (time.monotonic() - started) * 1000
    raw = response.content[:MAX_RESPONSE_BODY]
    try:
        body, binary = raw.decode("utf-8"), False
    except UnicodeDecodeError:
        body, binary = base64.b64encode(raw[:4096]).decode(), True
    await asyncio.sleep(0)
    return ExplorerResponse(
        status_code=response.status_code,
        headers=forward_response_headers(list(response.headers.items()))[:40],
        body=body,
        truncated=len(response.content) > MAX_RESPONSE_BODY,
        binary=binary,
        elapsed_ms=round(elapsed, 1),
    )


def _timeout_detail(plan: dict[str, Any] | None, service: str) -> str:
    """A timeout, with the plan's evidenced reason when it predicted one.

    Never a guess: only a dependency the plan already recorded as unreachable is
    named, and it is named as the likely reason, not as a measured one.
    """
    detail = "the service accepted the request but did not answer within 20 seconds"
    if not plan:
        return detail
    blocked = [
        d
        for d in RuntimePlan.model_validate(plan).dependencies
        if service in d.services and d.state.value != "provisionable"
    ]
    if blocked:
        detail += f". RepoLab's plan expected this for routes that use {blocked[0].kind.value}: "
        detail += blocked[0].reason
    return detail
