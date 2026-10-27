"""Analysis artifacts: state, claims, dependencies, failure isolation, the API.

Offline, on SQLite. Enqueuing is captured, never sent to a real queue.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.artifacts import (
    ARCH_COMPONENTS,
    ARCH_RUNTIME,
    ARCH_RUNTIME_NAMING,
    ARCH_STRUCTURE,
    ARCH_SUMMARY,
    EAGER,
    REPO_FACTS,
    REPO_MANIFEST,
    SPECS,
    Priority,
    Status,
    registry,
)
from repolab.artifacts import store as artifacts
from repolab.events import CollectingEmitter, EventType

REPO = "https://github.com/example/engine"
COMMIT = "a" * 40
OTHER = "b" * 40


@pytest.fixture
def engine():
    from repolab.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def ready(session: Session, artifact_type: str, commit: str = COMMIT, **payload) -> None:
    record = artifacts.ensure(session, REPO, commit, artifact_type)
    artifacts.transition(session, record, Status.GENERATING)
    artifacts.transition(session, record, Status.READY, payload=payload or None)


class TestRegistry:
    def test_every_dependency_is_a_known_type_and_the_graph_has_no_cycles(self) -> None:
        for definition in SPECS.values():
            assert set(definition.depends_on) <= set(SPECS)
        seen: set[str] = set()

        def visit(artifact_type: str, path: tuple[str, ...]) -> None:
            assert artifact_type not in path, f"cycle through {artifact_type}"
            for needed in SPECS[artifact_type].depends_on:
                visit(needed, (*path, artifact_type))
            seen.add(artifact_type)

        for artifact_type in SPECS:
            visit(artifact_type, ())

    def test_priorities_and_laziness(self) -> None:
        assert SPECS[REPO_FACTS].priority is Priority.P0
        assert SPECS[ARCH_SUMMARY].priority is Priority.P1
        assert SPECS[ARCH_RUNTIME_NAMING].lazy and ARCH_RUNTIME_NAMING not in EAGER
        # Nothing user-blocking waits on a model.
        assert all(s.model_tier is None for s in SPECS.values() if s.priority is Priority.P0)


class TestState:
    def test_an_artifact_moves_through_its_lifecycle_and_records_timing(self, session) -> None:
        record = artifacts.ensure(session, REPO, COMMIT, REPO_FACTS)
        assert record.status == "not_requested"
        assert artifacts.claim(session, REPO, COMMIT, REPO_FACTS, job_id="j1")
        session.refresh(record)
        assert record.status == "queued"
        artifacts.transition(session, record, Status.GENERATING)
        artifacts.transition(session, record, Status.VERIFYING)
        artifacts.transition(session, record, Status.READY, payload={"languages": ["Scala"]})
        assert record.status == "ready" and record.payload == {"languages": ["Scala"]}
        assert record.completed_at is not None and record.duration_ms is not None
        assert record.attempts == 1

    def test_the_same_commit_is_a_cache_hit_another_commit_and_another_version_are_not(
        self, session, monkeypatch
    ) -> None:
        ready(session, REPO_FACTS, languages=["Scala"])
        assert artifacts.get(session, REPO, COMMIT, REPO_FACTS).status == "ready"
        assert artifacts.get(session, REPO, OTHER, REPO_FACTS) is None
        bumped = registry.ArtifactSpec(REPO_FACTS, Priority.P0, "2")
        monkeypatch.setitem(registry.SPECS, REPO_FACTS, bumped)
        assert (
            artifacts.get(session, REPO, COMMIT, REPO_FACTS) is None
        )  # a new algorithm, nothing reused

    def test_all_for_lists_every_type_including_never_requested(self, session) -> None:
        ready(session, REPO_FACTS)
        states = {r.artifact_type: r.status for r in artifacts.all_for(session, REPO, COMMIT)}
        assert set(states) == set(SPECS)
        assert states[REPO_FACTS] == "ready" and states[ARCH_RUNTIME_NAMING] == "not_requested"


class TestClaims:
    def test_only_one_of_many_claims_wins(self, engine) -> None:
        """Five requests, five sessions: the conditional UPDATE lets exactly one through.

        (Run in sequence: SQLite's shared in-memory connection cannot host real
        parallel transactions. Postgres is exercised by the acceptance run.)
        """
        factory = sessionmaker(bind=engine)
        with factory() as session:
            artifacts.ensure(session, REPO, COMMIT, ARCH_RUNTIME_NAMING)
            session.commit()
        results = []
        for _ in range(5):
            with factory() as session:
                results.append(
                    artifacts.claim(session, REPO, COMMIT, ARCH_RUNTIME_NAMING, job_id="j1")
                )
                session.commit()
        assert results == [True, False, False, False, False]

    def test_a_ready_artifact_is_never_reclaimed_and_a_failed_one_only_on_retry(
        self, session
    ) -> None:
        ready(session, REPO_FACTS)
        assert not artifacts.claim(session, REPO, COMMIT, REPO_FACTS, job_id="j1", retry=True)
        record = artifacts.ensure(session, REPO, COMMIT, ARCH_SUMMARY)
        artifacts.fail(
            session, record, code="model_unavailable", stage="orientation", message="timeout"
        )
        assert not artifacts.claim(session, REPO, COMMIT, ARCH_SUMMARY, job_id="j1")
        assert artifacts.claim(session, REPO, COMMIT, ARCH_SUMMARY, job_id="j1", retry=True)


class TestFailureIsolation:
    def test_a_failure_blocks_only_its_dependants(self, session) -> None:
        for artifact_type in (REPO_FACTS, REPO_MANIFEST, ARCH_STRUCTURE):
            ready(session, artifact_type)
        emitter = CollectingEmitter("job-1")
        components = artifacts.ensure(session, REPO, COMMIT, ARCH_COMPONENTS)
        blocked = artifacts.fail(
            session,
            components,
            code="schema",
            stage="synthesis",
            message="invalid output",
            emitter=emitter,
        )
        assert blocked == [ARCH_SUMMARY]
        states = {r.artifact_type: r.status for r in artifacts.all_for(session, REPO, COMMIT)}
        assert states[ARCH_COMPONENTS] == "failed" and states[ARCH_SUMMARY] == "blocked"
        assert states[ARCH_STRUCTURE] == "ready"  # untouched
        assert states[ARCH_RUNTIME] == "not_requested"  # independent work continues
        failed = artifacts.get(session, REPO, COMMIT, ARCH_COMPONENTS)
        assert set(failed.error) >= {"error_code", "stage", "message", "retryable"}
        types = [e.type for e in emitter.events]
        assert types == [EventType.ARTIFACT_FAILED, EventType.ARTIFACT_BLOCKED]
        assert emitter.events[0].data["artifact_type"] == ARCH_COMPONENTS
        assert "payload" not in emitter.events[0].data

    def test_dependencies_ready(self, session) -> None:
        assert not artifacts.dependencies_ready(session, REPO, COMMIT, ARCH_RUNTIME_NAMING)
        for artifact_type in (REPO_FACTS, REPO_MANIFEST, ARCH_STRUCTURE, ARCH_RUNTIME):
            ready(session, artifact_type)
        assert artifacts.dependencies_ready(session, REPO, COMMIT, ARCH_RUNTIME_NAMING)


# -- the API -------------------------------------------------------------------------


@pytest.fixture
def api(engine, monkeypatch) -> Iterator[tuple[TestClient, sessionmaker, list]]:
    import api.routes.artifacts as routes
    from api import deps
    from api.main import app

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(deps, "engine", lambda: engine)
    monkeypatch.setattr(deps, "session_factory", lambda: factory)
    queued: list = []
    monkeypatch.setattr(
        routes, "_enqueue_artifact", lambda job_id, t: queued.append(("artifact", job_id, t))
    )
    monkeypatch.setattr(routes, "_enqueue_resume", lambda job: queued.append(("resume", job.id)))
    with TestClient(app) as client:
        yield client, factory, queued


def make_job(factory, *, commit: str | None = COMMIT) -> str:
    from repolab.db.models import Job

    with factory() as session:
        session.add(Job(id="job-1", repo_url=REPO, status="running", commit_sha=commit))
        session.commit()
    return "job-1"


class TestApi:
    def test_a_job_without_a_commit_has_no_artifacts_yet(self, api) -> None:
        client, factory, _ = api
        job = make_job(factory, commit=None)
        body = client.get(f"/jobs/{job}/artifacts").json()
        assert body == {"job_id": job, "commit_sha": None, "artifacts": []}

    def test_refresh_reconstructs_every_artifacts_state(self, api) -> None:
        client, factory, _ = api
        job = make_job(factory)
        with factory() as session:
            ready(session, REPO_FACTS, languages=["Scala"])
            session.commit()
        body = client.get(f"/jobs/{job}/artifacts").json()
        states = {a["type"]: a["status"] for a in body["artifacts"]}
        assert states[REPO_FACTS] == "ready" and states[ARCH_RUNTIME_NAMING] == "not_requested"
        detail = client.get(f"/jobs/{job}/artifacts/{REPO_FACTS}").json()
        assert detail["payload"] == {"languages": ["Scala"]}

    def test_a_lazy_artifact_is_queued_once_however_many_times_it_is_requested(self, api) -> None:
        client, factory, queued = api
        job = make_job(factory)
        with factory() as session:
            for artifact_type in (REPO_FACTS, REPO_MANIFEST, ARCH_STRUCTURE, ARCH_RUNTIME):
                ready(session, artifact_type)
            session.commit()
        first = client.post(f"/jobs/{job}/artifacts/{ARCH_RUNTIME_NAMING}")
        assert first.status_code == 202 and first.json()["status"] == "queued"
        for _ in range(4):
            assert (
                client.post(f"/jobs/{job}/artifacts/{ARCH_RUNTIME_NAMING}").json()["status"]
                == "queued"
            )
        assert queued == [("artifact", job, ARCH_RUNTIME_NAMING)]
        events = client.get(f"/jobs/{job}/events", params={"follow": "false"}).text
        assert "artifact_queued" in events

    def test_a_lazy_artifact_waits_for_its_dependencies_without_busy_waiting(self, api) -> None:
        client, factory, queued = api
        job = make_job(factory)
        assert (
            client.post(f"/jobs/{job}/artifacts/{ARCH_RUNTIME_NAMING}").json()["status"] == "queued"
        )
        assert queued == []  # the orchestrator starts it when architecture.runtime is ready

    def test_repository_reading_artifacts_are_not_generated_on_request_but_can_be_retried(
        self, api
    ) -> None:
        client, factory, queued = api
        job = make_job(factory)
        assert (
            client.post(f"/jobs/{job}/artifacts/{ARCH_SUMMARY}").json()["status"] == "not_requested"
        )
        assert queued == []
        with factory() as session:
            record = artifacts.ensure(session, REPO, COMMIT, ARCH_SUMMARY)
            artifacts.fail(session, record, code="x", stage="y", message="z")
            session.commit()
        retried = client.post(
            f"/jobs/{job}/artifacts/{ARCH_SUMMARY}", params={"retry": "true"}
        ).json()
        assert retried["status"] == "queued" and queued == [("resume", job)]

    def test_unknown_types_and_jobs_are_404(self, api) -> None:
        client, factory, _ = api
        job = make_job(factory)
        assert client.get(f"/jobs/{job}/artifacts/nope").status_code == 404
        assert client.get("/jobs/missing/artifacts").status_code == 404
