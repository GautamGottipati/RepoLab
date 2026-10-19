"""Settings must boot without credentials and must never expose them."""

from __future__ import annotations

import pytest

from repolab.config import DEFAULT_NEBIUS_BASE_URL, Settings

SECRET = "super-secret-key-value"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "NEBIUS_API_KEY",
        "NEBIUS_BASE_URL",
        "TAVILY_API_KEY",
        "MODEL_NANO",
        "MODEL_SUPER",
        "MODEL_ULTRA",
        "DATABASE_URL",
        "REDIS_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def build(**env: str) -> Settings:
    # _env_file=None keeps a developer's real .env out of the test run.
    return Settings(_env_file=None, **env)


class TestDefaults:
    def test_boots_with_no_credentials(self) -> None:
        settings = build()
        assert settings.nebius_api_key is None
        assert settings.tavily_api_key is None

    def test_models_default_to_unset(self) -> None:
        assert build().configured_models == {"nano": None, "super": None, "ultra": None}

    def test_base_url_default(self) -> None:
        assert build().nebius_base_url == DEFAULT_NEBIUS_BASE_URL

    def test_local_infra_defaults(self) -> None:
        settings = build()
        assert "localhost:5432" in settings.database_url
        assert "localhost:6379" in settings.redis_url


class TestEnvironmentLoading:
    def test_reads_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_SUPER", "nvidia/some-super-model")
        monkeypatch.setenv("NEBIUS_API_KEY", SECRET)
        settings = Settings(_env_file=None)

        assert settings.model_super == "nvidia/some-super-model"
        assert settings.configured_models["super"] == "nvidia/some-super-model"
        assert settings.nebius_api_key is not None
        assert settings.nebius_api_key.get_secret_value() == SECRET


class TestSecretsDoNotLeak:
    def test_repr_and_str_hide_the_value(self) -> None:
        settings = build(NEBIUS_API_KEY=SECRET, TAVILY_API_KEY=SECRET)
        assert SECRET not in repr(settings)
        assert SECRET not in str(settings)

    def test_serialization_hides_the_value(self) -> None:
        settings = build(NEBIUS_API_KEY=SECRET)
        assert SECRET not in settings.model_dump_json()
