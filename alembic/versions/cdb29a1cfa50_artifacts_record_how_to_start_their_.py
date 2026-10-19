"""artifacts record how to start their server

A cached artifact held its checkpoint but not what makes that checkpoint a
running Cassandra, so nothing could restore it into a server for a terminal
session to connect to. Additive and nullable: a build with no service -- Spark"s
-- simply has none.


Revision ID: cdb29a1cfa50
Revises: 9c4d80805831
Create Date: 2026-10-04 16:08:25.714685
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - used by JSONB variants

revision: str = "cdb29a1cfa50"
down_revision: str | None = "9c4d80805831"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("builds", sa.Column("service_command", sa.Text(), nullable=True))
    op.add_column("builds", sa.Column("cli_command", sa.String(length=512), nullable=True))
    op.add_column("builds", sa.Column("memory", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("builds", "memory")
    op.drop_column("builds", "cli_command")
    op.drop_column("builds", "service_command")
