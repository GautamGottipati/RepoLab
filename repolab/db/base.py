"""Database engine, session factory and declarative base.

Postgres is the source of truth. JSON columns are declared so that the same
models also work on SQLite, which lets the test suite exercise persistence
without requiring any running infrastructure.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

from sqlalchemy import JSON, Engine, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from repolab.config import Settings, get_settings

#: JSON that becomes real JSONB on Postgres and plain JSON elsewhere.
JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base for every RepoLab table."""

    # SQLAlchemy reads this mapping at class-construction time; it is not a
    # mutable default in the usual sense.
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JsonColumn}


def build_engine(settings: Settings | None = None, **kwargs: Any) -> Engine:
    """Create an engine from settings."""
    resolved = settings or get_settings()
    url = resolved.database_url
    # SQLAlchemy needs the psycopg3 driver named explicitly.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, future=True, **kwargs)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """A transactional session, committed on success and rolled back on error."""
    factory = build_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
