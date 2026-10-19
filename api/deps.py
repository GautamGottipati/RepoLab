"""Shared API dependencies.

The engine is created once for the process and reused; request handlers take a
short-lived session and never hold one across a stream.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from repolab.config import get_settings
from repolab.db import build_engine, build_session_factory


@lru_cache
def engine() -> Engine:
    return build_engine(get_settings())


@lru_cache
def session_factory() -> sessionmaker[Session]:
    return build_session_factory(engine())


def get_session() -> Iterator[Session]:
    """A session per request."""
    with session_factory()() as session:
        yield session
