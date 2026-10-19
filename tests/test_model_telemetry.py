"""Persisting model-call telemetry.

The router has built a `CallRecord` on every call since Phase 1 and only logged
it. This sink writes it down, and the three properties that matter are: it
records what the dashboard needs, it never breaks the call it is measuring, and
it cannot accumulate prompts.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.analytics.model_telemetry import DbModelTelemetry, db_model_telemetry
from repolab.db import Base
from repolab.db.models import ModelCallRecord
from repolab.llm.telemetry import CallRecord
from repolab.llm.tiers import ModelTier


@pytest.fixture
def db() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def call(**overrides: object) -> CallRecord:
    payload: dict[str, object] = {
        "tier": ModelTier.SUPER,
        "model": "nvidia/nemotron-3-super",
        "purpose": "repo_investigation",
        "schema_name": "PlannedAction",
        "latency_ms": 1904.4,
        "prompt_tokens": 670,
        "completion_tokens": 241,
        "success": True,
        "retries": 0,
    }
    payload.update(overrides)
    return CallRecord(**payload)  # type: ignore[arg-type]


class TestWhatIsRecorded:
    def test_a_call_becomes_a_row(self, db: sessionmaker[Session]) -> None:
        DbModelTelemetry(db).record(call())

        with db() as session:
            stored = session.scalars(select(ModelCallRecord)).one()

        assert stored.tier == "super"
        assert stored.model == "nvidia/nemotron-3-super"
        assert stored.purpose == "repo_investigation"
        assert stored.latency_ms == pytest.approx(1904.4)
        assert stored.prompt_tokens == 670
        assert stored.completion_tokens == 241
        assert stored.success is True
        assert stored.retries == 0

    def test_a_failed_call_records_its_error_type(self, db: sessionmaker[Session]) -> None:
        DbModelTelemetry(db).record(
            call(success=False, retries=1, error_type="SchemaValidationError")
        )

        with db() as session:
            stored = session.scalars(select(ModelCallRecord)).one()

        assert stored.success is False
        assert stored.error_type == "SchemaValidationError"
        assert stored.retries == 1

    def test_an_unpriced_call_stores_no_cost(self, db: sessionmaker[Session]) -> None:
        """Null, not zero: without MODEL_PRICING there is no price to record."""
        DbModelTelemetry(db).record(call())

        with db() as session:
            assert session.scalars(select(ModelCallRecord)).one().estimated_cost_usd is None

    def test_the_sink_is_callable(self, db: sessionmaker[Session]) -> None:
        """So it can be handed to the router as a plain function."""
        sink = DbModelTelemetry(db)
        sink(call())

        with db() as session:
            assert session.scalar(select(ModelCallRecord.id)) is not None


class TestItNeverBreaksTheCallItMeasures:
    def test_a_broken_database_is_swallowed(self, capture_logs: list[dict]) -> None:
        def broken():
            raise RuntimeError("no database")

        DbModelTelemetry(broken).record(call())  # must not raise

        assert any(entry["event"] == "model_telemetry.write_failed" for entry in capture_logs)

    def test_no_session_factory_means_no_sink(self) -> None:
        assert db_model_telemetry(None) is None

    def test_a_factory_yields_one(self, db: sessionmaker[Session]) -> None:
        assert isinstance(db_model_telemetry(db), DbModelTelemetry)


class TestNoPromptsAreStored:
    def test_the_table_has_no_column_that_could_hold_one(self) -> None:
        """Checked structurally: what a model was asked is not a metric."""
        columns = {column.name for column in ModelCallRecord.__table__.columns}
        forbidden = {"prompt", "messages", "content", "output", "response", "text", "excerpt"}

        assert not columns & forbidden
        assert "properties" not in columns, "no free-form blob to hide one in either"

    def test_the_record_itself_carries_none(self) -> None:
        assert not {"prompt", "messages", "output"} & set(CallRecord.model_fields)
