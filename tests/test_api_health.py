"""The API must boot and answer health without touching any external service."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from repolab import __version__


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
