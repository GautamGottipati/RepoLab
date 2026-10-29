"""Settings a deployment changes: CORS origins, readiness and live-app origins."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as main
from repolab.config import Settings
from repolab.runtime import proxy


class TestReadiness:
    def test_ready_when_database_and_redis_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main, "database_ready", lambda: True)
        monkeypatch.setattr(main, "redis_ready", lambda: True)
        with TestClient(main.app) as client:
            response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": True, "redis": True}

    @pytest.mark.parametrize(("database", "redis"), [(False, True), (True, False), (False, False)])
    def test_unavailable_when_either_is_down(
        self, monkeypatch: pytest.MonkeyPatch, database: bool, redis: bool
    ) -> None:
        monkeypatch.setattr(main, "database_ready", lambda: database)
        monkeypatch.setattr(main, "redis_ready", lambda: redis)
        with TestClient(main.app) as client:
            response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable", "database": database, "redis": redis}

    def test_liveness_still_touches_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main, "database_ready", lambda: False)
        with TestClient(main.app) as client:
            assert client.get("/health").status_code == 200


class TestCorsOrigins:
    def test_defaults_to_the_local_web_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        settings = Settings(_env_file=None)
        assert settings.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]

    def test_read_from_the_environment_as_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ORIGINS", '["https://repolab.example"]')
        assert Settings(_env_file=None).cors_origins == ["https://repolab.example"]


class TestProxyOrigin:
    def use(self, monkeypatch: pytest.MonkeyPatch, **values: object) -> None:
        settings = Settings(_env_file=None, **values)
        monkeypatch.setattr(proxy, "get_settings", lambda: settings)

    def test_local_development_keeps_the_api_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.use(monkeypatch)
        assert proxy.proxy_origin("abcdef012345", "web") == (
            "http://rt-abcdef012345-web.localhost:8000"
        )

    def test_https_behind_a_proxy_has_no_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.use(
            monkeypatch,
            runtime_proxy_scheme="https",
            runtime_proxy_domain="repolab.example",
            runtime_proxy_origin_port=443,
        )
        assert proxy.proxy_origin("abcdef012345", "web") == (
            "https://rt-abcdef012345-web.repolab.example"
        )

    def test_no_port_configured_means_none_in_the_origin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.use(monkeypatch, runtime_proxy_origin_port=None)
        assert proxy.proxy_origin("abcdef012345", "web") == "http://rt-abcdef012345-web.localhost"
