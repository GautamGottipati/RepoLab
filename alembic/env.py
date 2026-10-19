"""Alembic environment.

The database URL is read from RepoLab settings rather than ``alembic.ini`` so
credentials live only in the environment.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from repolab.config import get_settings

# Importing the models module registers every table on Base.metadata, which is
# what autogenerate compares against.
from repolab.db import models  # noqa: F401
from repolab.db.base import Base, build_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    engine = build_engine()
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
