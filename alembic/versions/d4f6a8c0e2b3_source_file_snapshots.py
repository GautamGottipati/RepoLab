"""source file snapshots

The exact text of files cited by an architecture, an answer or an experience,
per repository and commit, so a citation can be opened at the commit it was
made against after the investigation's clone is gone. Additive.

Revision ID: d4f6a8c0e2b3
Revises: c3e5a7b9d1f2
Create Date: 2026-10-25 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f6a8c0e2b3"
down_revision: str | None = "c3e5a7b9d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo_url", sa.String(length=1024), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "captured_by", sa.String(length=32), nullable=False, server_default="architecture"
        ),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("repo_url", "commit_sha", "path", name="uq_source_file"),
    )
    op.create_index("ix_source_files_repo_url", "source_files", ["repo_url"])
    op.create_index("ix_source_files_commit_sha", "source_files", ["commit_sha"])


def downgrade() -> None:
    op.drop_index("ix_source_files_commit_sha", table_name="source_files")
    op.drop_index("ix_source_files_repo_url", table_name="source_files")
    op.drop_table("source_files")
