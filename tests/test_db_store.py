"""Persistence of ingestion results.

These run on in-memory SQLite so the offline suite needs no infrastructure. The
same models run on Postgres with real JSONB columns (see `make db-upgrade`).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from repolab.db import Base, Ingestion, Repository, load_latest_ingestion, save_ingestion
from repolab.ingest import ingest_clone
from tests.test_detectors import SPARK_REPO, WEB_REPO


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture
async def result(make_repo, local_clone, fake_repo_url):
    root, sha = make_repo(SPARK_REPO)
    return await ingest_clone(fake_repo_url, local_clone(root, sha))


class TestSavingAnIngestion:
    async def test_all_five_records_are_written(self, session: Session, result) -> None:
        ingestion = save_ingestion(session, result)
        session.commit()

        assert ingestion.id is not None
        assert ingestion.repository.url == "https://github.com/example/demo"
        assert ingestion.manifest is not None
        assert ingestion.architecture is not None
        assert len(ingestion.evidence) == 1

    async def test_the_commit_is_recorded(self, session: Session, result) -> None:
        ingestion = save_ingestion(session, result)
        assert ingestion.commit_sha == result.commit_sha
        assert ingestion.branch == "main"

    async def test_the_manifest_round_trips_through_the_database(
        self, session: Session, result
    ) -> None:
        from repolab.manifest.models import RepoManifest

        ingestion = save_ingestion(session, result)
        session.commit()

        restored = RepoManifest.model_validate(ingestion.manifest.manifest)
        assert restored == result.manifest

    async def test_the_architecture_round_trips(self, session: Session, result) -> None:
        from repolab.architecture import load_graph

        ingestion = save_ingestion(session, result)
        session.commit()

        restored = load_graph(ingestion.architecture.graph)
        assert len(restored.nodes) == len(result.architecture.nodes)
        assert all(node.is_supported for node in restored.nodes)

    async def test_the_evidence_round_trips(self, session: Session, result) -> None:
        from repolab.evidence.models import EvidenceBundle

        ingestion = save_ingestion(session, result)
        session.commit()

        restored = EvidenceBundle.model_validate(ingestion.evidence[0].bundle)
        assert restored.code_references
        assert restored.simulated is False

    async def test_queryable_summary_columns_are_populated(self, session: Session, result) -> None:
        """Denormalised columns let the API filter without parsing JSON."""
        ingestion = save_ingestion(session, result)
        session.commit()

        assert ingestion.manifest.primary_surface == "data_playground"
        assert ingestion.manifest.confidence == result.manifest.confidence
        assert ingestion.architecture.node_count == len(result.architecture.nodes)
        assert ingestion.architecture.edge_count == len(result.architecture.edges)

    async def test_ingestion_metadata_is_recorded(self, session: Session, result) -> None:
        ingestion = save_ingestion(session, result)
        assert ingestion.file_count == result.file_count
        assert ingestion.files_read == result.files_read
        assert ingestion.llm_enriched is False
        assert ingestion.languages == {
            "languages": [s.model_dump(mode="json") for s in result.languages]
        }


class TestReIngestion:
    async def test_the_same_commit_updates_rather_than_duplicates(
        self, session: Session, result
    ) -> None:
        first = save_ingestion(session, result)
        session.commit()
        second = save_ingestion(session, result)
        session.commit()

        assert first.id == second.id
        assert session.query(Ingestion).count() == 1
        assert session.query(Repository).count() == 1

    async def test_re_ingesting_replaces_the_manifest(self, session: Session, result) -> None:
        save_ingestion(session, result)
        session.commit()

        result.manifest.confidence = 0.42
        save_ingestion(session, result)
        session.commit()

        assert session.query(Ingestion).count() == 1
        assert session.query(Ingestion).one().manifest.confidence == 0.42

    async def test_a_different_commit_creates_a_second_ingestion(
        self, session: Session, result
    ) -> None:
        save_ingestion(session, result)
        result.commit_sha = "b" * 40
        save_ingestion(session, result)
        session.commit()

        assert session.query(Ingestion).count() == 2
        assert session.query(Repository).count() == 1

    async def test_a_second_repository_is_separate(
        self, session: Session, result, make_repo, local_clone
    ) -> None:
        from repolab.ingest.url import RepoUrl

        save_ingestion(session, result)
        root, sha = make_repo(WEB_REPO)
        other_url = RepoUrl(
            normalized="https://github.com/example/other",
            host="github.com",
            owner="example",
            name="other",
        )
        other = await ingest_clone(other_url, local_clone(root, sha))
        save_ingestion(session, other)
        session.commit()

        assert session.query(Repository).count() == 2


class TestLoading:
    async def test_the_latest_ingestion_can_be_loaded(self, session: Session, result) -> None:
        save_ingestion(session, result)
        session.commit()

        loaded = load_latest_ingestion(session, "https://github.com/example/demo")
        assert loaded is not None
        assert loaded.commit_sha == result.commit_sha

    def test_an_unknown_repository_loads_as_none(self, session: Session) -> None:
        assert load_latest_ingestion(session, "https://github.com/no/such") is None


class TestCascades:
    async def test_deleting_a_repository_removes_its_records(
        self, session: Session, result
    ) -> None:
        save_ingestion(session, result)
        session.commit()

        session.delete(session.query(Repository).one())
        session.commit()

        assert session.query(Ingestion).count() == 0
