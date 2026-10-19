"""one build row per ladder rung

The build cache held one row per commit, which was right while there was one way
to build something. The fidelity ladder produces several candidates for the same
commit -- an official release can serve while a source build compiles, and both
are worth keeping -- so the key becomes (repo_url, commit_sha, kind).

Existing rows survive: each already has a kind, so the wider constraint is
satisfied by whatever is already there.

Revision ID: 9c4d80805831
Revises: 5785833899cf
Create Date: 2026-10-04 12:09:56.047198
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4d80805831"
down_revision: str | None = "5785833899cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CONSTRAINT = "uq_build_commit"
NEW_CONSTRAINT = "uq_build_commit_kind"


def upgrade() -> None:
    op.add_column("builds", sa.Column("version", sa.String(length=128), nullable=True))

    # A server default, not just a Python one: without it this fails on any table
    # that already has rows, which every machine that has run P0 does.
    op.add_column(
        "builds",
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.drop_constraint(OLD_CONSTRAINT, "builds", type_="unique")
    op.create_unique_constraint(
        NEW_CONSTRAINT, "builds", ["repo_url", "commit_sha", "kind"]
    )


def downgrade() -> None:
    # Narrowing the constraint again would fail if a commit has more than one
    # rung stored, which is exactly what this revision exists to allow. The
    # duplicates are removed first, keeping one row per commit.
    op.execute(
        """
        DELETE FROM builds a
        USING builds b
        WHERE a.repo_url = b.repo_url
          AND a.commit_sha = b.commit_sha
          AND a.id > b.id
        """
    )
    op.drop_constraint(NEW_CONSTRAINT, "builds", type_="unique")
    op.create_unique_constraint(OLD_CONSTRAINT, "builds", ["repo_url", "commit_sha"])
    op.drop_column("builds", "simulated")
    op.drop_column("builds", "version")
