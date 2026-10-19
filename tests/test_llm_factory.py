"""``build_router`` wiring.

The factory is how the API and worker obtain a router, so its dependency
injection and its refusal to run without credentials are pinned here.
"""

from __future__ import annotations

import json

import pytest

from repolab.config import Settings
from repolab.llm import ModelTier, build_router
from repolab.testing.fakes import FakeLLMProvider


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "MODEL_NANO": "vendor/nano",
        "MODEL_SUPER": "vendor/super",
        "MODEL_ULTRA": "vendor/ultra",
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


class TestProviderInjection:
    def test_an_injected_provider_needs_no_credentials(self) -> None:
        """Tests and agents must be able to build a router offline."""
        router = build_router(settings(), provider=FakeLLMProvider())
        assert router.model_for(ModelTier.SUPER) == "vendor/super"

    def test_without_a_provider_or_key_it_refuses(self) -> None:
        with pytest.raises(ValueError, match="NEBIUS_API_KEY"):
            build_router(settings())

    def test_tiers_come_from_settings(self) -> None:
        router = build_router(settings(MODEL_NANO="vendor/other-nano"), provider=FakeLLMProvider())
        assert router.model_for(ModelTier.NANO) == "vendor/other-nano"


class TestPricingWiring:
    async def test_pricing_flows_from_settings_into_the_record(self) -> None:
        from pydantic import BaseModel

        from repolab.llm import Purpose
        from repolab.testing.fakes import FakeMessage

        class Answer(BaseModel):
            status: str

        pricing = json.dumps({"vendor/nano": {"input": 1.0, "output": 2.0}})
        router = build_router(
            settings(MODEL_PRICING=pricing),
            provider=FakeLLMProvider(
                message=FakeMessage(content='{"status": "ok"}'),
                prompt_tokens=1_000_000,
                completion_tokens=1_000_000,
            ),
        )
        completion = await router.nano(purpose=Purpose.SMOKE_TEST, schema=Answer, prompt="go")
        assert completion.record.estimated_cost_usd == pytest.approx(3.0)

    def test_absent_pricing_is_not_an_error(self) -> None:
        router = build_router(settings(), provider=FakeLLMProvider())
        assert router is not None


class TestTimeoutWiring:
    async def test_the_configured_timeout_reaches_the_provider(self) -> None:
        from pydantic import BaseModel

        from repolab.llm import Purpose
        from repolab.testing.fakes import FakeMessage

        class Answer(BaseModel):
            status: str

        provider = FakeLLMProvider(message=FakeMessage(content='{"status": "ok"}'))
        router = build_router(settings(LLM_TIMEOUT_SECONDS=7.5), provider=provider)
        await router.nano(purpose=Purpose.SMOKE_TEST, schema=Answer, prompt="go")
        assert provider.calls[0]["timeout"] == 7.5
