"""Tier routing.

CLAUDE.md forbids hardcoded model IDs and requires that all model access go
through this layer, so these tests pin that callers address *tiers* and never
models.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from repolab.llm import ModelNotConfiguredError, ModelRouter, ModelTier, Purpose
from repolab.testing.fakes import FakeLLMProvider, FakeMessage

CONFIGURED = {
    ModelTier.NANO: "vendor/nano-model",
    ModelTier.SUPER: "vendor/super-model",
    ModelTier.ULTRA: "vendor/ultra-model",
}


class Answer(BaseModel):
    status: str


def router(**overrides: object) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(message=FakeMessage(content='{"status": "ok"}'))
    models = {**CONFIGURED, **overrides}  # type: ignore[dict-item]
    return ModelRouter(provider, models), provider


class TestEachTierUsesItsOwnModel:
    @pytest.mark.parametrize(
        ("helper", "expected"),
        [
            ("nano", "vendor/nano-model"),
            ("super", "vendor/super-model"),
            ("ultra", "vendor/ultra-model"),
        ],
    )
    async def test_helper_resolves_the_configured_id(self, helper: str, expected: str) -> None:
        routed, provider = router()
        completion = await getattr(routed, helper)(
            purpose=Purpose.SMOKE_TEST, schema=Answer, prompt="go"
        )
        assert provider.calls[0]["model"] == expected
        assert completion.record.model == expected
        assert completion.record.tier.value == helper

    async def test_complete_accepts_a_tier_directly(self) -> None:
        routed, provider = router()
        await routed.complete(
            ModelTier.SUPER, purpose=Purpose.REPO_INVESTIGATION, schema=Answer, prompt="go"
        )
        assert provider.calls[0]["model"] == "vendor/super-model"

    def test_model_for_exposes_resolution_for_diagnostics(self) -> None:
        routed, _ = router()
        assert routed.model_for(ModelTier.ULTRA) == "vendor/ultra-model"


class TestModelIdsAreNotCallerVisible:
    @pytest.mark.parametrize("helper", ["nano", "super", "ultra", "complete"])
    def test_no_helper_accepts_a_model_argument(self, helper: str) -> None:
        parameters = inspect.signature(getattr(ModelRouter, helper)).parameters
        assert "model" not in parameters
        assert "model_id" not in parameters

    def test_purpose_and_schema_are_keyword_only(self) -> None:
        """Positional args would invite passing a model by accident."""
        parameters = inspect.signature(ModelRouter.nano).parameters
        for name in ("purpose", "schema", "prompt"):
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


class TestUnconfiguredTiers:
    async def test_unset_tier_raises_with_the_env_var_to_set(self) -> None:
        routed, _ = router(**{ModelTier.ULTRA: None})
        with pytest.raises(ModelNotConfiguredError, match="MODEL_ULTRA") as excinfo:
            await routed.ultra(purpose=Purpose.EXPERIMENT_DESIGN, schema=Answer, prompt="go")
        assert excinfo.value.tier is ModelTier.ULTRA

    async def test_an_empty_string_counts_as_unset(self) -> None:
        """An empty env var must not be sent to the API as a model ID."""
        routed, _ = router(**{ModelTier.NANO: ""})
        with pytest.raises(ModelNotConfiguredError):
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")

    async def test_a_missing_tier_entry_raises(self) -> None:
        routed = ModelRouter(FakeLLMProvider(), {})
        with pytest.raises(ModelNotConfiguredError):
            await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")

    async def test_one_unconfigured_tier_does_not_disturb_the_others(self) -> None:
        routed, provider = router(**{ModelTier.ULTRA: None})
        await routed.nano(purpose=Purpose.QUICK_ANSWER, schema=Answer, prompt="go")
        assert provider.calls[0]["model"] == "vendor/nano-model"


class TestTierEnumeration:
    def test_env_var_names_match_claude_md(self) -> None:
        assert [t.env_var for t in ModelTier] == ["MODEL_NANO", "MODEL_SUPER", "MODEL_ULTRA"]

    def test_purpose_accepts_a_plain_string(self) -> None:
        """An unforeseen purpose must still be recordable."""
        assert isinstance(Purpose.EXPERIMENT_DESIGN.value, str)

    async def test_arbitrary_purpose_string_is_recorded(self) -> None:
        routed, _ = router()
        completion = await routed.nano(purpose="some_future_purpose", schema=Answer, prompt="go")
        assert completion.record.purpose == "some_future_purpose"
