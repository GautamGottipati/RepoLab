"""Live model verification.

Marked ``live`` and therefore deselected by default: ``make test`` stays offline.
Run with ``make smoke``.

Each configured tier must return exactly one validated Pydantic object through
the router. This is the only place RepoLab asserts anything about the real
models, and it skips loudly rather than passing vacuously when credentials or
model IDs are absent.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from repolab.config import get_settings
from repolab.llm import ModelTier, Purpose, build_router

pytestmark = pytest.mark.live


class RepoSummary(BaseModel):
    """A small but non-trivial schema: two types, an enum-ish field, and a list."""

    language: str = Field(description="The main programming language")
    is_data_framework: bool = Field(description="Whether this is a data processing framework")
    concepts: list[str] = Field(description="Two key concepts, as short phrases")


PROMPT = (
    "Apache Spark is a unified analytics engine for large-scale data processing. "
    "Summarise it: the main language, whether it is a data processing framework, "
    "and two key concepts."
)


@pytest.fixture(scope="module")
def settings():
    resolved = get_settings()
    if resolved.nebius_api_key is None:
        pytest.skip("NEBIUS_API_KEY is not set")
    return resolved


@pytest.mark.parametrize("tier", list(ModelTier))
async def test_tier_returns_one_validated_object(tier: ModelTier, settings) -> None:
    model_id = settings.configured_models[tier.value]
    if not model_id:
        pytest.skip(f"{tier.env_var} is not set")

    router = build_router(settings)
    completion = await getattr(router, tier.value)(
        purpose=Purpose.SMOKE_TEST,
        schema=RepoSummary,
        prompt=PROMPT,
        max_tokens=2048,
    )

    # One validated object of the requested type.
    assert isinstance(completion.value, RepoSummary)
    assert completion.value.language.strip()
    assert len(completion.value.concepts) >= 1

    # And the accounting the router promises.
    record = completion.record
    assert record.success is True
    assert record.tier is tier
    assert record.model == model_id
    assert record.latency_ms > 0
    assert record.retries <= 1

    # Report what the live model actually did, so Phase 0's open question about
    # reasoning-field placement gets a recorded answer.
    print(
        f"\n{tier.value}: model={record.model} "
        f"latency={record.latency_ms:.0f}ms retries={record.retries} "
        f"tokens={record.prompt_tokens}/{record.completion_tokens} "
        f"cost={record.estimated_cost_usd} "
        f"content_populated={record.content_populated} "
        f"text_field={record.primary_text_field} "
        f"reasoning_fields={record.reasoning_fields}"
    )
