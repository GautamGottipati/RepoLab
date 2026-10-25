"""runtimes: launches of a repository's web and API services

A row per launch: the deterministic plan it ran, each service's state (status,
steps, the sidecar port the proxy may reach, health, failure), and when it was
last used so an idle runtime can be reaped. Additive.

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-10-23 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2d4f6a8c0e1"
down_revision: str | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "runtimes",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("repo_url", sa.String(length=1024), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planning"),
        sa.Column("plan", _json(), nullable=True),
        sa.Column("state", _json(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runtimes_repo_url", "runtimes", ["repo_url"])
    op.create_index("ix_runtimes_job_id", "runtimes", ["job_id"])
    op.create_index("ix_runtimes_status", "runtimes", ["status"])


def downgrade() -> None:
    op.drop_index("ix_runtimes_status", table_name="runtimes")
    op.drop_index("ix_runtimes_job_id", table_name="runtimes")
    op.drop_index("ix_runtimes_repo_url", table_name="runtimes")
    op.drop_table("runtimes")
