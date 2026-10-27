"""The source API serves RepoLab's own copies, at exactly the cited commit."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.sources import MemorySnapshot, capture
from tests.test_api_experiences import investigated
from tests.test_experiences_discovery import manifest

REPO = "https://github.com/apache/spark"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    from api import deps
    from repolab.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(deps, "engine", lambda: engine)
    monkeypatch.setattr(deps, "session_factory", lambda: factory)
    yield factory
    engine.dispose()


@pytest.fixture
def client(db) -> Iterator[TestClient]:
    from api.main import app

    with TestClient(app) as handle:
        yield handle


def job_with_source(db) -> tuple[str, str]:
    job = investigated(db, manifest(url=REPO))
    from repolab.db.jobs import get_job
    from repolab.db.models import Ingestion

    with db() as session:
        commit = session.get(Ingestion, get_job(session, job).ingestion_id).commit_sha
        capture(
            session,
            repo_url=REPO,
            commit_sha=commit,
            snapshot=MemorySnapshot(commit_sha=commit, files={"core/A.scala": "class A {\n}\n"}),
            paths=["core/A.scala"],
        )
        session.commit()
    return job, commit


def test_a_captured_file_is_served_at_the_investigated_commit(client, db) -> None:
    job, commit = job_with_source(db)
    body = client.get(f"/jobs/{job}/source", params={"path": "core/A.scala"}).json()
    assert body["captured"] is True and body["commit_sha"] == commit
    assert body["lines"] == ["class A {", "}"] and body["line_count"] == 2
    assert body["host_url"].endswith(f"/blob/{commit}/core/A.scala")


def test_an_uncaptured_file_says_so_and_offers_the_host_at_the_same_commit(client, db) -> None:
    job, commit = job_with_source(db)
    body = client.get(f"/jobs/{job}/source", params={"path": "core/B.scala"}).json()
    assert body["captured"] is False and body["lines"] == []
    assert commit in body["host_url"]


def test_another_commit_is_never_substituted(client, db) -> None:
    _, commit = job_with_source(db)
    other = "f" * 40
    body = client.get(
        "/sources", params={"repo_url": REPO, "commit_sha": other, "path": "core/A.scala"}
    ).json()
    assert body["captured"] is False and body["commit_sha"] == other
    same = client.get(
        "/sources", params={"repo_url": REPO, "commit_sha": commit, "path": "core/A.scala"}
    ).json()
    assert same["captured"] is True


def test_paths_cannot_escape(client, db) -> None:
    job, _ = job_with_source(db)
    assert client.get(f"/jobs/{job}/source", params={"path": "../etc/passwd"}).status_code == 422
