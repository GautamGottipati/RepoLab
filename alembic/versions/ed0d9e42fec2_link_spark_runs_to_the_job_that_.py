"""link spark runs to the job that produced them

Additive and nullable: a run started from the command line has no job, and
existing rows keep working. It exists so a browser watching a job can find the
measurements that job produced.

Revision ID: ed0d9e42fec2
Revises: adec7ab8f0b0
Create Date: 2026-10-04 10:27:55.068227
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ed0d9e42fec2"
down_revision: str | None = "adec7ab8f0b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Named explicitly. Autogenerate emitted an unnamed constraint and warned that
#: the downgrade would fail as rendered, since there would be nothing to drop by
#: name.
FOREIGN_KEY = "fk_spark_runs_job_id"
INDEX = "ix_spark_runs_job_id"


def upgrade() -> None:
    op.add_column("spark_runs", sa.Column("job_id", sa.String(length=36), nullable=True))
    op.create_index(INDEX, "spark_runs", ["job_id"], unique=False)
    op.create_foreign_key(
        FOREIGN_KEY, "spark_runs", "jobs", ["job_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(FOREIGN_KEY, "spark_runs", type_="foreignkey")
    op.drop_index(INDEX, table_name="spark_runs")
    op.drop_column("spark_runs", "job_id")
