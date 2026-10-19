"""Model-layer errors.

Errors are explicit so callers can distinguish "this tier was never configured"
from "the model produced something unusable" from "the service was unreachable".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repolab.llm.tiers import ModelTier

#: Model output included in an error message is truncated to this many characters.
#: Enough to debug a schema failure without dumping a whole response into a log.
ERROR_EXCERPT_LIMIT = 500


class LLMError(Exception):
    """Base class for every failure in the model layer."""


class ModelNotConfiguredError(LLMError):
    """A tier was used but no model ID is configured for it."""

    def __init__(self, tier: ModelTier) -> None:
        self.tier = tier
        super().__init__(f"no model configured for the {tier.value} tier; set {tier.env_var}")


class LLMTimeoutError(LLMError):
    """A call exceeded its timeout budget."""

    def __init__(self, model: str, timeout_seconds: float) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        super().__init__(f"{model} did not respond within {timeout_seconds:.1f}s")


class LLMTransportError(LLMError):
    """The provider or service rejected or failed the request."""

    def __init__(self, model: str, cause: BaseException) -> None:
        self.model = model
        self.cause = cause
        detail = str(cause).strip().splitlines()
        first = detail[0] if detail else type(cause).__name__
        super().__init__(f"{model} request failed: {type(cause).__name__}: {first}")


class EmptyResponseError(LLMError):
    """The model returned no usable text in any field.

    Distinct from a schema failure: there was nothing to validate. Typical of a
    reasoning model that produced only reasoning.
    """

    def __init__(self, model: str, shape_summary: str) -> None:
        self.model = model
        self.shape_summary = shape_summary
        super().__init__(f"{model} returned no usable text (message shape: {shape_summary})")


class SchemaValidationError(LLMError):
    """The model's output could not be validated against the requested schema."""

    def __init__(
        self,
        model: str,
        schema_name: str,
        *,
        retries: int,
        errors: Any = None,
        excerpt: str | None = None,
    ) -> None:
        self.model = model
        self.schema_name = schema_name
        self.retries = retries
        self.errors = errors
        # Truncated deliberately: useful for debugging, small enough not to
        # become a dumping ground for model output.
        self.excerpt = excerpt[:ERROR_EXCERPT_LIMIT] if excerpt else None
        attempts = retries + 1
        super().__init__(f"{model} did not produce valid {schema_name} after {attempts} attempt(s)")
