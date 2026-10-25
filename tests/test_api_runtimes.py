"""Starting a live experience, and reaching it -- only it -- through the proxy.

Offline: the worker queue is replaced, and a tiny in-process ASGI app stands in
for the sidecar's port so the Host-routed proxy can be exercised for real.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from repolab.db.runtimes import create_runtime, save_runtime
from repolab.manifest.models import InteractionSurface, SurfaceType
from repolab.runtime.models import (
    RuntimePlan,
    RuntimeState,
    RuntimeStatus,
    ServicePlan,
    ServiceState,
)
from tests.support.manifests import spark_manifest
from tests.test_api_experiences import investigated


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from api import deps
    from repolab.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(deps, "engine", lambda: engine)
    monkeypatch.setattr(deps, "session_factory", lambda: factory)
    yield factory
    engine.dispose()


@pytest.fixture
def client(db: sessionmaker[Session]) -> Iterator[TestClient]:
    from api.main import app

    with TestClient(app) as handle:
        yield handle


def planned_manifest():
    manifest = spark_manifest()
    manifest.repo_url = "https://github.com/example/board"
    manifest.interaction_surfaces = [
        InteractionSurface(
            type=SurfaceType.API, name="HTTP API", confidence=0.9, reason="t", primary=True
        )
    ]
    manifest.runtime = RuntimePlan(
        services=[
            ServicePlan(
                name="backend", surfaces=["api"], working_directory="backend", image="node:14",
                image_reason="t", package_manager="npm", install="npm ci --no-audit --no-fund",
                start="npm run start", script="start", port=3000, port_source="t",
            )
        ]
    )  # fmt: skip
    return manifest


@pytest.fixture(autouse=True)
def fresh_proxy_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test reads the runtime row anew; the proxy's short cache is for traffic."""
    import api.runtime_proxy as proxy

    monkeypatch.setattr(proxy, "CACHE_SECONDS", 0)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"start": [], "stop": []}
    import worker.jobs as jobs

    monkeypatch.setattr(jobs, "enqueue_runtime_start", lambda *a, **k: calls["start"].append(a))
    monkeypatch.setattr(jobs, "enqueue_runtime_stop", lambda *a, **k: calls["stop"].append(a))
    return calls


@pytest.fixture
def upstream() -> Iterator[int]:
    """A real HTTP server on a free loopback port, standing in for a sidecar."""
    app = FastAPI()

    @app.get("/")
    def index() -> Response:
        return Response("<html><script src='/main.js'></script></html>", media_type="text/html")

    @app.post("/api/items")
    async def create(request: Request) -> JSONResponse:
        response = JSONResponse({"got": await request.json(), "host": request.headers["host"]}, 201)
        response.set_cookie("sid", "1", domain="localhost")
        return response

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield port
    server.should_exit = True
    thread.join(timeout=5)


def running(factory: sessionmaker[Session], port: int, status=RuntimeStatus.RUNNING) -> str:
    state = RuntimeState(
        id="abcdef123456",
        repo_url="https://github.com/example/board",
        commit_sha="c" * 40,
        status=status,
        services=[ServiceState(name="backend", surfaces=["api"], status=status, host_port=port)],
    )
    with factory() as session:
        create_runtime(session, state, planned_manifest().runtime, job_id=None)
        save_runtime(session, state)
        session.commit()
    return state.id


HOST = {"host": "rt-abcdef123456-backend.localhost:8000"}


class TestStarting:
    def test_the_plan_is_shown_before_anything_runs(self, client, db, queued) -> None:
        job = investigated(db, planned_manifest())
        view = client.get(f"/jobs/{job}/runtime").json()
        assert view["runtime_id"] is None
        assert view["plan"]["services"][0]["start"] == "npm run start"
        assert queued["start"] == []

    def test_starting_is_an_explicit_request_and_queues_a_worker_job(
        self, client, db, queued
    ) -> None:
        job = investigated(db, planned_manifest())
        started = client.post(f"/jobs/{job}/runtime")
        assert started.status_code == 202
        body = started.json()
        assert body["status"] == "planning"
        assert body["events_url"].endswith("/events")
        assert queued["start"][0][1] == body["runtime_id"]
        # Asking twice while it is coming up does not start a second one.
        again = client.post(f"/jobs/{job}/runtime").json()
        assert again["runtime_id"] == body["runtime_id"]
        assert len(queued["start"]) == 1

    def test_no_plan_no_start(self, client, db, queued) -> None:
        manifest = planned_manifest()
        manifest.runtime = None
        job = investigated(db, manifest)
        assert client.post(f"/jobs/{job}/runtime").status_code == 409
        assert queued["start"] == []

    def test_stopping_queues_cleanup(self, client, db, queued) -> None:
        runtime = running(db, 1)
        assert client.delete(f"/runtimes/{runtime}").status_code == 202
        assert queued["stop"] == [(runtime,)]

    def test_an_origin_is_given_only_for_a_running_service(self, client, db) -> None:
        runtime = running(db, 1, status=RuntimeStatus.STARTING)
        assert client.get(f"/runtimes/{runtime}").json()["services"][0]["origin"] is None


class TestTheProxy:
    def test_a_live_app_is_served_on_its_own_origin(self, client, db, upstream) -> None:
        running(db, upstream)
        page = client.get("/", headers=HOST)
        assert page.status_code == 200
        assert "/main.js" in page.text

    def test_requests_and_bodies_pass_through_and_cookies_stay_host_only(
        self,
        client,
        db,
        upstream,
    ) -> None:
        running(db, upstream)
        created = client.post("/api/items", json={"title": "x"}, headers=HOST)
        assert created.status_code == 201
        assert created.json()["got"] == {"title": "x"}
        assert created.json()["host"] == f"127.0.0.1:{upstream}"
        assert "domain" not in created.headers["set-cookie"].lower()

    @pytest.mark.parametrize(
        "host",
        [
            "rt-000000000000-backend.localhost:8000",  # unknown runtime
            "rt-abcdef123456-frontend.localhost:8000",  # service it does not list
            "rt-abcdef123456-backend.evil.example",  # another domain
        ],
    )
    def test_anything_not_authorised_is_404_or_not_proxied(
        self, client, db, upstream, host
    ) -> None:
        running(db, upstream)
        response = client.get("/", headers={"host": host})
        assert response.status_code == 404 or "main.js" not in response.text

    def test_a_stopped_runtime_is_unreachable(self, client, db, upstream) -> None:
        running(db, upstream, status=RuntimeStatus.STOPPED)
        assert client.get("/", headers=HOST).status_code == 404

    def test_repolabs_own_routes_are_not_reachable_on_a_live_host(
        self, client, db, upstream
    ) -> None:
        running(db, upstream)
        assert client.get("/health", headers=HOST).status_code == 404  # the app has no /health


class TestTheExplorer:
    def test_a_request_is_made_server_side_through_the_sidecar(self, client, db, upstream) -> None:
        runtime = running(db, upstream)
        response = client.post(
            f"/runtimes/{runtime}/request",
            json={"service": "backend", "method": "POST", "path": "/api/items", "body": '{"a": 1}'},
        ).json()
        assert response["status_code"] == 201
        assert '"a":1' in response["body"].replace(" ", "")

    @pytest.mark.parametrize(
        ("payload", "code"),
        [
            ({"service": "backend", "method": "TRACE", "path": "/"}, 400),
            ({"service": "backend", "method": "GET", "path": "http://169.254.169.254/"}, 400),
            ({"service": "backend", "method": "GET", "path": "//evil.example/"}, 400),
            ({"service": "other", "method": "GET", "path": "/"}, 409),
        ],
    )
    def test_it_cannot_be_pointed_anywhere_else(self, client, db, upstream, payload, code) -> None:
        runtime = running(db, upstream)
        assert client.post(f"/runtimes/{runtime}/request", json=payload).status_code == code


def test_a_timeout_names_only_the_blocker_the_plan_evidenced() -> None:
    from api.routes.runtimes import _timeout_detail
    from repolab.runtime.models import DependencyKind, DependencyState, RuntimeDependency

    plan = planned_manifest().runtime
    assert "expected" not in _timeout_detail(plan.model_dump(mode="json"), "backend")
    plan.dependencies = [
        RuntimeDependency(
            kind=DependencyKind.MONGODB,
            state=DependencyState.EXTERNAL_REQUIRED,
            reason="the MongoDB connection string is written into the code (app.js:19)",
            services=["backend"],
        )
    ]
    detail = _timeout_detail(plan.model_dump(mode="json"), "backend")
    assert "RepoLab's plan expected this" in detail and "app.js:19" in detail
    assert "expected" not in _timeout_detail(plan.model_dump(mode="json"), "frontend")
