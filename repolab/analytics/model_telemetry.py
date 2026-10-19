"""Persisting what each model call cost.

The router has built a :class:`~repolab.llm.telemetry.CallRecord` on every call
since Phase 1 and only logged it. A log is not a data source an API can query,
so four of the operational metrics CLAUDE.md names -- model latency, token
usage, estimated cost, schema-validation failures -- had no answer.

This sink writes them down, with the same discipline as
:class:`~repolab.analytics.events.DbAnalyticsWriter`: it swallows its own
failures. Telemetry that can break the work it measures is worse than no
telemetry.

**No prompt and no output text passes through here.** ``CallRecord`` carries
none and ``model_calls`` has no column for any, so there is nowhere for it to go
even by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from repolab.llm.telemetry import CallRecord

log = structlog.get_logger(__name__)


class DbModelTelemetry:
    """Appends one row per routed model call."""

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_factory = session_factory

    def __call__(self, record: CallRecord) -> None:
        self.record(record)

    def record(self, record: CallRecord) -> None:
        from repolab.db.models import ModelCallRecord

        try:
            with self._session_factory() as session:
                session.add(
                    ModelCallRecord(
                        tier=record.tier.value,
                        model=record.model,
                        purpose=str(record.purpose),
                        schema_name=record.schema_name,
                        latency_ms=record.latency_ms,
                        prompt_tokens=record.prompt_tokens,
                        completion_tokens=record.completion_tokens,
                        estimated_cost_usd=record.estimated_cost_usd,
                        success=record.success,
                        retries=record.retries,
                        error_type=record.error_type,
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - never fail the measured work
            log.warning("model_telemetry.write_failed", model=record.model, error=str(exc))


def db_model_telemetry(
    session_factory: Callable[[], AbstractContextManager[Session]] | None,
) -> DbModelTelemetry | None:
    """A sink writing through *session_factory*, or ``None`` without one.

    A ``sessionmaker`` satisfies the signature directly -- calling it returns a
    session that is itself a context manager -- so every task that already holds
    one can pass it unchanged.
    """
    return None if session_factory is None else DbModelTelemetry(session_factory)
