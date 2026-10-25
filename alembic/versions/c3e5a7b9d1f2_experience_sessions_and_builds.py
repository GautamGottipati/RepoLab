"""experience sessions and experience builds

An experience session is one person's use of one experience -- its runtime and
every run they made -- persisted so a refresh reconnects. An experience build is
an environment built once per commit (a Spark source build, an installed
Python package) and reused by later sessions. Additive.

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-10-24 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e5a7b9d1f2"
down_revision: str | None = "b2d4f6a8c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "experience_builds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo_url", sa.String(length=1024), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("adapter", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="building"),
        sa.Column("image", sa.String(length=512), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("detail", _json(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repo_url", "commit_sha", "adapter", "strategy", name="uq_experience_build"),
    )
    op.create_index("ix_experience_builds_repo_url", "experience_builds", ["repo_url"])
    op.create_table(
        "experience_sessions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("repo_url", sa.String(length=1024), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("experience_id", sa.String(length=64), nullable=False),
        sa.Column("adapter", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("reality", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="preparing"),
        sa.Column("events_job_id", sa.String(length=64), nullable=True),
        sa.Column("state", _json(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("job_id", "repo_url", "experience_id", "status"):
        op.create_index(f"ix_experience_sessions_{column}", "experience_sessions", [column])


def downgrade() -> None:
    for column in ("job_id", "repo_url", "experience_id", "status"):
        op.drop_index(f"ix_experience_sessions_{column}", table_name="experience_sessions")
    op.drop_table("experience_sessions")
    op.drop_index("ix_experience_builds_repo_url", table_name="experience_builds")
    op.drop_table("experience_builds")
