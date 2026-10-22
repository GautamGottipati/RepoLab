"""questions are triaged, and answered or tested on the repository's own surface

Ask RepoLab used to turn every question into a Spark experiment. A question is
now triaged first: conceptual and repository questions are answered from
evidence, and testable ones run on whatever this repository really is -- a Spark
job, or CQL through a built node's cqlsh. ``kind`` says which, ``triage`` how
that was decided, ``answer`` holds an answered question, and ``runs`` holds a
CLI experiment's statement-by-statement results, which do not fit the Spark
variant table. Additive; existing rows are Spark experiments.

Revision ID: a1c3e5f7b9d2
Revises: cfde6d1ca94a
Create Date: 2026-10-18 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c3e5f7b9d2"
down_revision: str | None = "cfde6d1ca94a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="spark"),
    )
    op.add_column("experiments", sa.Column("triage", _json(), nullable=True))
    op.add_column("experiments", sa.Column("answer", _json(), nullable=True))
    op.add_column("experiments", sa.Column("runs", _json(), nullable=True))


def downgrade() -> None:
    op.drop_column("experiments", "runs")
    op.drop_column("experiments", "answer")
    op.drop_column("experiments", "triage")
    op.drop_column("experiments", "kind")
