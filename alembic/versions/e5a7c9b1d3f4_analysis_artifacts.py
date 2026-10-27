"""analysis artifacts

Each piece of repository analysis -- facts, manifest, architecture sections --
with its own status, so it can be generated, verified, persisted and shown on
its own, and reused for the same repository and commit. Adds ``jobs.commit_sha``
so a job's artifacts can be found before its ingestion exists. Additive.

Revision ID: e5a7c9b1d3f4
Revises: d4f6a8c0e2b3
Create Date: 2026-10-26 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5a7c9b1d3f4"
down_revision: str | None = "d4f6a8c0e2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("jobs", sa.Column("commit_sha", sa.String(length=64), nullable=True))
    op.create_table(
        "analysis_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo_url", sa.String(length=1024), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("artifact_type", sa.String(length=128), nullable=False),
        sa.Column("analysis_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="not_requested"),
        sa.Column("priority", sa.String(length=8), nullable=False, server_default="P1"),
        sa.Column("payload", _json(), nullable=True),
        sa.Column("error", _json(), nullable=True),
        sa.Column("dependencies", _json(), nullable=False, server_default="[]"),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "repo_url",
            "commit_sha",
            "artifact_type",
            "analysis_version",
            name="uq_analysis_artifact",
        ),
    )
    op.create_index("ix_analysis_artifacts_repo_url", "analysis_artifacts", ["repo_url"])
    op.create_index("ix_analysis_artifacts_commit_sha", "analysis_artifacts", ["commit_sha"])
    op.create_index("ix_analysis_artifacts_status", "analysis_artifacts", ["status"])
    op.create_index("ix_analysis_artifacts_job_id", "analysis_artifacts", ["job_id"])


def downgrade() -> None:
    for name in ("job_id", "status", "commit_sha", "repo_url"):
        op.drop_index(f"ix_analysis_artifacts_{name}", table_name="analysis_artifacts")
    op.drop_table("analysis_artifacts")
    op.drop_column("jobs", "commit_sha")
