"""A real live experience, end to end, on the local Docker daemon. Opt-in.

A tiny Express app with a MongoDB connection read from the environment is
planned from its own files, installed in a build container, started on a
no-egress network next to an ephemeral MongoDB, exposed through RepoLab's
sidecar, health-checked, and then reached through the API's Host-routed proxy.

Run with ``pytest -m docker tests/test_runtime_docker.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.runtime_proxy import RuntimeProxyMiddleware
from repolab.builds.resolver import archive_repository
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.tree import build_file_tree
from repolab.manifest.models import (
    BuildKind,
    BuildStrategy,
    Fidelity,
    InteractionSurface,
    RepoManifest,
    SurfaceType,
)
from repolab.runtime.backend import DockerRuntimeBackend
from repolab.runtime.launcher import Launcher, stop
from repolab.runtime.models import RuntimeState, RuntimeStatus
from repolab.runtime.plan import plan_runtime
from repolab.sandbox.docker import docker_available

pytestmark = pytest.mark.docker

APP = {
    "package.json": json.dumps(
        {
            "name": "tiny-api",
            "scripts": {"start": "node server.js"},
            "dependencies": {"express": "4.19.2", "mongodb": "6.8.0"},
        }
    ),
    "server.js": """
const express = require('express');
const { MongoClient } = require('mongodb');
const app = express();
const client = new MongoClient(process.env.MONGODB_URI);
app.get('/health', (req, res) => res.json({ ok: true }));
app.get('/api/items', async (req, res) => {
  await client.connect();
  const items = client.db().collection('items');
  await items.insertOne({ at: Date.now() });
  res.json({ count: await items.countDocuments() });
});
app.listen(process.env.PORT || 4100);
""",
}


def manifest() -> RepoManifest:
    return RepoManifest(
        repo_url="https://github.com/example/tiny-api",
        commit_sha="a" * 40,
        interaction_surfaces=[
            InteractionSurface(
                type=SurfaceType.API, name="HTTP API", confidence=0.9, reason="test", primary=True
            )
        ],
        build_strategy=BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=Fidelity.EXACT),
        confidence=0.9,
    )


@pytest.mark.skipif(not docker_available(), reason="needs a Docker daemon")
def test_an_express_api_with_mongodb_goes_live(tmp_path: Path) -> None:
    root = tmp_path / "tiny"
    root.mkdir()
    for name, text in APP.items():
        (root / name).write_text(text)
    # A lockfile-free install: `npm install`, resolved from the registry.
    context = DetectionContext(root=root, tree=build_file_tree(root), commit_sha="a" * 40)
    plan = plan_runtime(context, manifest())
    assert [s.name for s in plan.services] == ["tiny-api"]
    assert plan.services[0].port == 4100
    assert plan.dependencies[0].state.value == "provisionable"

    state = RuntimeState(id="ab12cd34ef56", repo_url="https://github.com/example/tiny-api",
                         commit_sha="a" * 40)  # fmt: skip
    backend = DockerRuntimeBackend()
    events: list[str] = []
    launcher = Launcher(backend, emit=lambda k, m, d: events.append(m), save=lambda s: None)
    try:
        state = asyncio.run(launcher.launch(state, plan, archive_repository(root)))
        service = state.services[0]
        assert service.status is RuntimeStatus.RUNNING, (service.failure, events)
        assert service.health and service.health.status_code == 200

        app = FastAPI()
        app.add_middleware(RuntimeProxyMiddleware, load=lambda _id: state)
        client = TestClient(app)
        host = {"host": f"rt-{state.id}-tiny-api.localhost:8000"}
        assert client.get("/health", headers=host).json() == {"ok": True}
        # The provisioned MongoDB really answered, from inside the no-egress network.
        assert client.get("/api/items", headers=host).json()["count"] >= 1
        assert (
            client.get("/health", headers={"host": "rt-000000000000-x.localhost"}).status_code
            == 404
        )
    finally:
        asyncio.run(stop(state, backend))
