"""Environment-derived settings.

Secret values are held as ``SecretStr`` so that neither ``repr`` nor structured
logging can leak them. Every external credential is optional: RepoLab must be
able to boot, run its test suite, and report capabilities without any keys
configured.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"


class Settings(BaseSettings):
    """Runtime configuration read from the environment (and ``.env`` if present)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `model_` is a protected namespace in pydantic; MODEL_NANO/SUPER/ULTRA
        # are legitimate RepoLab settings, so the protection is lifted here.
        protected_namespaces=(),
    )

    nebius_api_key: SecretStr | None = None
    nebius_base_url: str = DEFAULT_NEBIUS_BASE_URL

    # Sandboxes authenticate with both the API key and a project ID, which the
    # ConTree client sends as a `Project` header. Inference does not need it.
    nebius_project_id: str | None = None

    tavily_api_key: SecretStr | None = None

    model_nano: str | None = None
    model_super: str | None = None
    model_ultra: str | None = None

    model_pricing: str | None = None
    """JSON map of model ID -> {"input": usd_per_1m, "output": usd_per_1m}.

    Absent or malformed pricing means cost is reported as unknown rather than
    estimated from a guessed price.
    """

    llm_timeout_seconds: float = Field(default=60.0, gt=0)

    database_url: str = Field(
        default="postgresql://repolab:repolab@localhost:5432/repolab",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    @property
    def configured_models(self) -> dict[str, str | None]:
        """Model IDs by routing tier. Values are ``None`` when unset."""
        return {
            "nano": self.model_nano,
            "super": self.model_super,
            "ultra": self.model_ultra,
        }


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, loaded once."""
    return Settings()
