"""experiments, variants and measurements

Three tables, each answering a different question: ``experiments`` holds the plan,
statistics, evidence, explanation and reproducibility record; ``experiment_variants``
holds one row per run with a pointer and hash for the raw event log it was measured
from; ``measurements`` holds one row per measured quantity so a metric can be
queried across experiments without unpacking JSON.

Revision ID: adec7ab8f0b0
Revises: 9ffff8d77a84
Create Date: 2026-10-03 23:42:16.587905
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - used by JSONB variants

revision: str = 'adec7ab8f0b0'
down_revision: str | None = '9ffff8d77a84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('experiments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('repo_url', sa.String(length=1024), nullable=False),
    sa.Column('commit_sha', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('variable', sa.String(length=255), nullable=True),
    sa.Column('baseline_value', sa.Integer(), nullable=True),
    sa.Column('repeats', sa.Integer(), nullable=False),
    sa.Column('estimated_runs', sa.Integer(), nullable=False),
    sa.Column('estimated_seconds', sa.Float(), nullable=False),
    sa.Column('image', sa.String(length=512), nullable=False),
    sa.Column('dataset_hash', sa.String(length=64), nullable=True),
    sa.Column('proposal', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('plan', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('rejection', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('statistics', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('evidence', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('explanation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('reproducibility', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('approved_by', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_experiments_dataset_hash'), 'experiments', ['dataset_hash'], unique=False)
    op.create_index(op.f('ix_experiments_job_id'), 'experiments', ['job_id'], unique=False)
    op.create_index(op.f('ix_experiments_repo_url'), 'experiments', ['repo_url'], unique=False)
    op.create_index(op.f('ix_experiments_status'), 'experiments', ['status'], unique=False)
    op.create_table('experiment_variants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('experiment_id', sa.String(length=36), nullable=False),
    sa.Column('variant_id', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.Column('repeat', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('dataset_hash', sa.String(length=64), nullable=False),
    sa.Column('image', sa.String(length=512), nullable=False),
    sa.Column('checkpoint_id', sa.String(length=128), nullable=True),
    sa.Column('measurements', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('event_log_path', sa.String(length=1024), nullable=False),
    sa.Column('event_log_sha256', sa.String(length=64), nullable=False),
    sa.Column('task_count', sa.Integer(), nullable=False),
    sa.Column('total_runtime_ms', sa.Integer(), nullable=True),
    sa.Column('wall_clock_seconds', sa.Float(), nullable=False),
    sa.Column('timing_contended', sa.Boolean(), nullable=False),
    sa.Column('ran_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('experiment_id', 'variant_id', 'repeat', name='uq_experiment_variant_repeat')
    )
    op.create_index(op.f('ix_experiment_variants_experiment_id'), 'experiment_variants', ['experiment_id'], unique=False)
    op.create_index(op.f('ix_experiment_variants_run_id'), 'experiment_variants', ['run_id'], unique=False)
    op.create_index(op.f('ix_experiment_variants_variant_id'), 'experiment_variants', ['variant_id'], unique=False)
    op.create_table('measurements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('experiment_id', sa.String(length=36), nullable=False),
    sa.Column('variant_pk', sa.Integer(), nullable=True),
    sa.Column('variant_id', sa.String(length=64), nullable=True),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['variant_pk'], ['experiment_variants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_measurements_experiment_id'), 'measurements', ['experiment_id'], unique=False)
    op.create_index(op.f('ix_measurements_name'), 'measurements', ['name'], unique=False)
    op.create_index(op.f('ix_measurements_variant_id'), 'measurements', ['variant_id'], unique=False)
    op.create_index(op.f('ix_measurements_variant_pk'), 'measurements', ['variant_pk'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_measurements_variant_pk'), table_name='measurements')
    op.drop_index(op.f('ix_measurements_variant_id'), table_name='measurements')
    op.drop_index(op.f('ix_measurements_name'), table_name='measurements')
    op.drop_index(op.f('ix_measurements_experiment_id'), table_name='measurements')
    op.drop_table('measurements')
    op.drop_index(op.f('ix_experiment_variants_variant_id'), table_name='experiment_variants')
    op.drop_index(op.f('ix_experiment_variants_run_id'), table_name='experiment_variants')
    op.drop_index(op.f('ix_experiment_variants_experiment_id'), table_name='experiment_variants')
    op.drop_table('experiment_variants')
    op.drop_index(op.f('ix_experiments_status'), table_name='experiments')
    op.drop_index(op.f('ix_experiments_repo_url'), table_name='experiments')
    op.drop_index(op.f('ix_experiments_job_id'), table_name='experiments')
    op.drop_index(op.f('ix_experiments_dataset_hash'), table_name='experiments')
    op.drop_table('experiments')
