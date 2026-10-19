"""record why an explanation is missing

"No model was configured" and "the model failed" are different situations, and
an empty explanation panel reported as the first when it was the second would
misdescribe what happened. Additive and nullable.


Revision ID: 5785833899cf
Revises: ed0d9e42fec2
Create Date: 2026-10-04 11:20:39.888793
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - used by JSONB variants

revision: str = "5785833899cf"
down_revision: str | None = "ed0d9e42fec2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("explanation_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("experiments", "explanation_error")
