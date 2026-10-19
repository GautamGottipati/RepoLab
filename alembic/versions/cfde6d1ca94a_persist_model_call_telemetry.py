"""persist model call telemetry

The router has built a `CallRecord` on every model call since Phase 1 and only
logged it, so model latency, token usage, estimated cost and schema-validation
failures had no queryable source -- four of the operational metrics CLAUDE.md
names. One new table, nothing altered.

Deliberately no column for prompt or output text: `CallRecord` carries none, and
what a model was asked is not a metric.

Revision ID: cfde6d1ca94a
Revises: 4aa878ba31da
Create Date: 2026-10-04 23:49:35.879006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - used by JSONB variants

revision: str = "cfde6d1ca94a"
down_revision: str | None = "4aa878ba31da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_calls_created_at"), "model_calls", ["created_at"], unique=False)
    op.create_index(op.f("ix_model_calls_model"), "model_calls", ["model"], unique=False)
    op.create_index(op.f("ix_model_calls_purpose"), "model_calls", ["purpose"], unique=False)
    op.create_index(op.f("ix_model_calls_success"), "model_calls", ["success"], unique=False)
    op.create_index(op.f("ix_model_calls_tier"), "model_calls", ["tier"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_calls_tier"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_success"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_purpose"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_model"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_created_at"), table_name="model_calls")
    op.drop_table("model_calls")
