"""`GET /jobs/{id}/experiences` — the Available Experiences screen's data.

The screen renders whatever this returns, so the properties that matter are the
ones a screen cannot repair: every surface arrives with its evidence, nothing
unsupported arrives with a route to open, and the specification's sentence
arrives verbatim rather than paraphrased on the way out.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api import deps
from api.main import app
from repolab.db import Base
from repolab.db.jobs import create_job
from repolab.db.models import Ingestion, RepoManifestRecord, Repository
from repolab.events import JobStatus
from repolab.experiences.catalog import NOT_RUNNABLE
from repolab.manifest.models import RepoManifest
from tests.support.manifests import AIRFLOW_REPO, airflow_manifest, spark_manifest


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
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
def client(db: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(app) as handle:
        yield handle


def investigated(factory: sessionmaker[Session], manifest: RepoManifest) -> str:
    """A completed investigation holding *manifest*. Returns the job id."""
    with factory() as session:
        job = create_job(session, manifest.repo_url)
        owner, name = manifest.repo_url.rstrip("/").rsplit("/", 2)[-2:]
        repository = Repository(url=manifest.repo_url, host="github.com", owner=owner, name=name)
        session.add(repository)
        session.flush()
        ingestion = Ingestion(repository_id=repository.id, commit_sha=manifest.commit_sha)
        session.add(ingestion)
        session.flush()
        session.add(
            RepoManifestRecord(
                ingestion_id=ingestion.id,
                manifest=manifest.model_dump(mode="json"),
                confidence=manifest.confidence,
                primary_surface="cli",
            )
        )
        job.ingestion_id = ingestion.id
        job.status = JobStatus.COMPLETED.value
        session.commit()
        return job.id


def unfinished(factory: sessionmaker[Session]) -> str:
    with factory() as session:
        job = create_job(session, AIRFLOW_REPO)
        session.commit()
        return job.id


class TestAvailability:
    def test_an_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/nope/experiences").status_code == 404

    def test_it_is_404_until_the_investigation_finishes(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        response = client.get(f"/jobs/{unfinished(db)}/experiences")

        assert response.status_code == 404
        assert "has no experiences yet" in response.json()["detail"]


class TestTheHybridCatalog:
    def test_it_serves_every_detected_surface(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = investigated(db, airflow_manifest())

        body = client.get(f"/jobs/{job_id}/experiences").json()

        assert body["surface_count"] == 4
        assert [item["label"] for item in body["experiences"]] == [
            "CLI",
            "Web",
            "API",
            "Code / Notebook",
        ]
        assert body["hybrid"] is True

    def test_every_surface_arrives_with_its_evidence_and_reason(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = investigated(db, airflow_manifest())

        for item in client.get(f"/jobs/{job_id}/experiences").json()["experiences"]:
            assert item["evidence"], f"{item['label']} arrived citing nothing"
            assert item["reason"]
            assert all(reference["path"] for reference in item["evidence"])

    def test_the_sentence_survives_serialisation_verbatim(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = investigated(db, airflow_manifest())

        for item in client.get(f"/jobs/{job_id}/experiences").json()["experiences"]:
            assert item["status"] == "detected_only"
            assert item["unavailable_reason"].startswith(NOT_RUNNABLE)

    def test_nothing_unsupported_arrives_with_a_route(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = investigated(db, airflow_manifest())

        body = client.get(f"/jobs/{job_id}/experiences").json()

        assert body["runnable_count"] == 0
        assert body["runnable_surfaces"] == []
        assert all(item["route"] is None for item in body["experiences"])

    def test_the_primary_and_its_rationale_are_served(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = investigated(db, airflow_manifest())

        body = client.get(f"/jobs/{job_id}/experiences").json()

        assert body["primary_type"] == "cli"
        assert "scored highest" in body["rationale"]


class TestARunnableCatalog:
    def test_spark_arrives_with_a_route_to_its_playground(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = investigated(db, spark_manifest())

        body = client.get(f"/jobs/{job_id}/experiences").json()

        assert body["runnable_count"] == 1
        assert body["runnable_surfaces"] == ["data_playground"]
        playground = body["experiences"][0]
        assert playground["route"] == "/playground"
        assert playground["unavailable_reason"] == ""

    def test_the_weak_secondary_surface_is_still_listed(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """A repository is never reduced to one type, including on this screen."""
        job_id = investigated(db, spark_manifest())

        body = client.get(f"/jobs/{job_id}/experiences").json()

        cli = next(item for item in body["experiences"] if item["label"] == "CLI")
        assert cli["weak"] is True
        assert cli["status"] == "detected_only"

    def test_the_manifest_endpoint_still_serves_the_stored_json(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """The two endpoints read the same record; one parses it, one does not."""
        job_id = investigated(db, spark_manifest())

        body = client.get(f"/jobs/{job_id}/manifest").json()

        assert body["repo_url"] == spark_manifest().repo_url
        assert len(body["interaction_surfaces"]) == 2
