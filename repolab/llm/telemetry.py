"""Per-call accounting for the model layer.

Every routed call produces one :class:`CallRecord`, logged as a single structured
event and returned to the caller for later persistence (``agent_steps`` /
``analytics_events``).

**Logging contract.** Prompts, messages, model output, reasoning text and API
keys are never recorded here. Only metadata is: which model, what for, how long,
how many tokens, what it cost, whether it worked, and the *shape* of the
response. The shape fields come from :mod:`repolab.llm.response`, which measures
sizes and never captures text, so private chain-of-thought cannot leak into logs.
"""

from __future__ import annotations

import json

import structlog
from pydantic import BaseModel, Field

from repolab.llm.tiers import ModelTier

log = structlog.get_logger("repolab.llm")

#: Prices are quoted per million tokens, the unit model providers publish.
TOKENS_PER_PRICE_UNIT = 1_000_000


class ModelPrice(BaseModel):
    """USD per million input and output tokens for one model."""

    input: float = Field(ge=0.0)
    output: float = Field(ge=0.0)


class ModelPricing(BaseModel):
    """Per-model prices, keyed by model ID.

    RepoLab never assumes a price. When a model is absent, cost estimation
    returns ``None`` rather than a guess.
    """

    prices: dict[str, ModelPrice] = Field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: str | None) -> ModelPricing:
        """Parse the ``MODEL_PRICING`` value. Invalid or absent input yields empty pricing."""
        if not raw or not raw.strip():
            return cls()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("llm.pricing_unparseable", reason="MODEL_PRICING is not valid JSON")
            return cls()
        if not isinstance(parsed, dict):
            log.warning("llm.pricing_unparseable", reason="MODEL_PRICING is not an object")
            return cls()
        try:
            return cls(prices={str(k): ModelPrice.model_validate(v) for k, v in parsed.items()})
        except Exception:  # noqa: BLE001 - bad pricing config must not break inference
            log.warning("llm.pricing_unparseable", reason="MODEL_PRICING entries are malformed")
            return cls()

    def estimate(
        self,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None:
        """Estimate a call's cost in USD, or ``None`` if it cannot be known."""
        price = self.prices.get(model)
        if price is None or prompt_tokens is None or completion_tokens is None:
            return None
        return (
            prompt_tokens * price.input + completion_tokens * price.output
        ) / TOKENS_PER_PRICE_UNIT


class CallRecord(BaseModel):
    """What happened on one routed model call. Contains no prompt or output text."""

    tier: ModelTier
    model: str
    purpose: str
    schema_name: str

    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost_usd: float | None = None

    success: bool
    retries: int = 0
    error_type: str | None = None

    # Observations about where the response arrived, from Phase 0's findings.
    content_populated: bool = False
    primary_text_field: str | None = None
    reasoning_fields: list[str] = Field(default_factory=list)

    def emit(self) -> None:
        """Log this record as one structured event."""
        log.info("llm.call", **self.model_dump(mode="json"))
