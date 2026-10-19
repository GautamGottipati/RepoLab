"""The model router.

All model access passes through this layer. Callers pick a logical tier and pass
a Pydantic schema; they receive a validated, typed object plus the telemetry for
the call. Model IDs are configuration and never appear in a call site.

The router is built on Phase 0's finding that reasoning models do not reliably
populate ``content``: it reads the answer through
:func:`repolab.llm.response.extract_text`, which discovers whichever field
carried it, and records only the *shape* of the response so reasoning text never
reaches a log.

Failure handling follows CLAUDE.md: request JSON, validate it, and retry exactly
once on a schema failure. The retry both feeds the validation error back to the
model and downgrades ``json_schema`` to ``json_object``, which also covers models
whose serving stack rejects strict schemas outright.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any, Generic, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from repolab.interfaces import ChatResult, LLMProvider
from repolab.llm.errors import (
    EmptyResponseError,
    LLMTimeoutError,
    LLMTransportError,
    ModelNotConfiguredError,
    SchemaValidationError,
)
from repolab.llm.parsing import extract_json_object
from repolab.llm.response import MessageShape, extract_text, inspect_message
from repolab.llm.schema import (
    JSON_OBJECT_RESPONSE_FORMAT,
    describe_schema_for_prompt,
    response_format_for,
)
from repolab.llm.telemetry import CallRecord, ModelPricing
from repolab.llm.tiers import ModelTier, Purpose

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_TIMEOUT_SECONDS = 60.0

#: Appended to the system prompt. Nebius documentation recommends restating the
#: schema rather than relying on `response_format` alone.
_SCHEMA_INSTRUCTION = (
    "Respond with a single JSON object conforming exactly to this JSON Schema. "
    "Emit no prose, no explanation and no code fences.\n\nSchema:\n{schema}"
)


class Completion(BaseModel, Generic[T]):
    """A validated model response together with its accounting."""

    value: T
    record: CallRecord

    @property
    def model(self) -> str:
        return self.record.model

    @property
    def retries(self) -> int:
        return self.record.retries


class ModelRouter:
    """Routes logical tiers to configured models and validates their output."""

    def __init__(
        self,
        provider: LLMProvider,
        models: Mapping[ModelTier, str | None],
        *,
        pricing: ModelPricing | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        telemetry: Callable[[CallRecord], None] | None = None,
    ) -> None:
        self._provider = provider
        self._models = dict(models)
        self._pricing = pricing or ModelPricing()
        self._timeout_seconds = timeout_seconds

        # Where call records go besides the log. Optional: a router built without
        # one behaves exactly as it did before, which is what keeps every test
        # and every offline path unchanged.
        self._telemetry = telemetry

    # -- tier helpers -----------------------------------------------------
    #
    # Deliberately identical signatures, and deliberately no `model` parameter:
    # a caller cannot address a model directly.

    async def nano(
        self,
        *,
        purpose: Purpose | str,
        schema: type[T],
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Completion[T]:
        """Cheap, fast work: summaries, log parsing, quick answers, classification."""
        return await self.complete(
            ModelTier.NANO,
            purpose=purpose,
            schema=schema,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    async def super(
        self,
        *,
        purpose: Purpose | str,
        schema: type[T],
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Completion[T]:
        """Reasoning about repositories, lessons, builds and architecture."""
        return await self.complete(
            ModelTier.SUPER,
            purpose=purpose,
            schema=schema,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    async def ultra(
        self,
        *,
        purpose: Purpose | str,
        schema: type[T],
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Completion[T]:
        """Experiment design, hypothesis generation, result interpretation."""
        return await self.complete(
            ModelTier.ULTRA,
            purpose=purpose,
            schema=schema,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    # -- core -------------------------------------------------------------

    def model_for(self, tier: ModelTier) -> str:
        """The configured model ID for *tier*.

        Raises:
            ModelNotConfiguredError: if the tier has no configured model.
        """
        model = self._models.get(tier)
        if not model:
            raise ModelNotConfiguredError(tier)
        return model

    async def complete(
        self,
        tier: ModelTier,
        *,
        purpose: Purpose | str,
        schema: type[T],
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Completion[T]:
        """Ask *tier* for one instance of *schema*.

        Raises:
            ModelNotConfiguredError: the tier has no model configured.
            LLMTimeoutError: the budget elapsed.
            LLMTransportError: the service failed the request.
            EmptyResponseError: no usable text arrived in any field.
            SchemaValidationError: output was still invalid after one retry.
        """
        model = self.model_for(tier)
        budget = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        purpose_value = str(purpose)

        messages = self._initial_messages(prompt, system, schema)
        started = time.perf_counter()

        shape = MessageShape()
        result: ChatResult | None = None
        retries = 0
        last_text: str | None = None
        last_errors: Any = None

        # Attempt 1 requests a strict schema; the single retry relaxes to a plain
        # JSON object and feeds the validation failure back to the model.
        for attempt in range(2):
            retries = attempt
            response_format = (
                response_format_for(schema) if attempt == 0 else JSON_OBJECT_RESPONSE_FORMAT
            )
            try:
                result = await self._call(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    max_tokens=max_tokens,
                    budget=budget,
                )
            except (LLMTimeoutError, LLMTransportError) as exc:
                # A transport failure on the first attempt may be the service
                # rejecting strict json_schema, so the retry is still worth it.
                if attempt == 1 or isinstance(exc, LLMTimeoutError):
                    self._publish(
                        self._record(
                            tier, model, purpose_value, schema, started, None, shape, retries, exc
                        )
                    )
                    raise
                messages = self._retry_messages(messages, None, str(exc), schema)
                continue

            shape = inspect_message(result.raw_message)
            last_text = extract_text(result.raw_message, shape)

            if last_text is None:
                error = EmptyResponseError(model, shape.summary)
                if attempt == 1:
                    self._publish(
                        self._record(
                            tier,
                            model,
                            purpose_value,
                            schema,
                            started,
                            result,
                            shape,
                            retries,
                            error,
                        )
                    )
                    raise error
                messages = self._retry_messages(messages, None, str(error), schema)
                continue

            payload = extract_json_object(last_text)
            if payload is None:
                reason = "output was not a JSON object"
            else:
                try:
                    value = schema.model_validate(payload)
                except ValidationError as exc:
                    last_errors = exc.errors()
                    reason = f"schema validation failed: {exc.error_count()} error(s)"
                else:
                    record = self._publish(
                        self._record(
                            tier,
                            model,
                            purpose_value,
                            schema,
                            started,
                            result,
                            shape,
                            retries,
                            None,
                        )
                    )
                    return Completion[schema](value=value, record=record)  # type: ignore[valid-type]

            if attempt == 1:
                break
            messages = self._retry_messages(messages, last_text, reason, schema)

        error = SchemaValidationError(
            model,
            schema.__name__,
            retries=retries,
            errors=last_errors,
            excerpt=last_text,
        )
        self._publish(
            self._record(tier, model, purpose_value, schema, started, result, shape, retries, error)
        )
        raise error

    # -- internals --------------------------------------------------------

    def _publish(self, record: CallRecord) -> CallRecord:
        """Log the record, and hand it to the telemetry sink if there is one.

        A sink failure is swallowed here as well as in the sink itself: this is
        the path every agent in the product runs through, and an unwritable
        metrics table must not be able to fail a model call.
        """
        record.emit()
        if self._telemetry is not None:
            try:
                self._telemetry(record)
            except Exception as exc:  # noqa: BLE001 - never fail the measured work
                log.warning("llm.telemetry_failed", model=record.model, error=str(exc))
        return record

    async def _call(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any],
        max_tokens: int | None,
        budget: float,
    ) -> ChatResult:
        """One provider call, bounded by *budget* seconds.

        The timeout is enforced twice: the provider receives it, and
        ``asyncio.wait_for`` caps the total, so a hung client cannot outlive it.
        """
        try:
            return await asyncio.wait_for(
                self._provider.chat(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    max_tokens=max_tokens,
                    timeout=budget,
                ),
                timeout=budget,
            )
        except TimeoutError as exc:
            raise LLMTimeoutError(model, budget) from exc
        except Exception as exc:
            # Every provider failure is normalised into a RepoLab error type so
            # vendor exceptions never leak to callers.
            raise LLMTransportError(model, exc) from exc

    @staticmethod
    def _initial_messages(
        prompt: str,
        system: str | None,
        schema: type[BaseModel],
    ) -> list[dict[str, Any]]:
        instruction = _SCHEMA_INSTRUCTION.format(schema=describe_schema_for_prompt(schema))
        system_content = f"{system}\n\n{instruction}" if system else instruction
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]

    @staticmethod
    def _retry_messages(
        messages: list[dict[str, Any]],
        previous_output: str | None,
        reason: str,
        schema: type[BaseModel],
    ) -> list[dict[str, Any]]:
        """Add the failed attempt and a correction request."""
        retried = list(messages)
        if previous_output is not None:
            retried.append({"role": "assistant", "content": previous_output})
        retried.append(
            {
                "role": "user",
                "content": (
                    f"That response was rejected: {reason}. "
                    f"Reply with only a JSON object valid against the "
                    f"{schema.__name__} schema given above."
                ),
            }
        )
        return retried

    def _record(
        self,
        tier: ModelTier,
        model: str,
        purpose: str,
        schema: type[BaseModel],
        started: float,
        result: ChatResult | None,
        shape: MessageShape,
        retries: int,
        error: BaseException | None,
    ) -> CallRecord:
        prompt_tokens = result.prompt_tokens if result else None
        completion_tokens = result.completion_tokens if result else None
        return CallRecord(
            tier=tier,
            model=model,
            purpose=purpose,
            schema_name=schema.__name__,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=self._pricing.estimate(model, prompt_tokens, completion_tokens),
            success=error is None,
            retries=retries,
            error_type=type(error).__name__ if error else None,
            content_populated=shape.content_populated,
            primary_text_field=shape.primary_text_field,
            reasoning_fields=list(shape.reasoning_fields),
        )
