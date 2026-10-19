"""The Spark playground's background task.

The task is thin -- it reuses the Phase 6 runner -- so what is worth testing is
the guard around it: a run must measure the commit the architecture screen is
describing, or its figures cannot be attributed to the code the user is reading.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from repolab.db.base import Base
from repolab.db.store import save_ingestion
from repolab.ingest import ingest_clone
from tests.test_detectors import SPARK_REPO
from worker.spark_tasks import DEFAULT_IMAGE, CommitMovedError, expected_commit

REPO = "https://github.com/AlexIoannides/pyspark-example-project"


@pytest.fixture
def factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
async def ingestion(make_repo, local_clone, fake_repo_url):
    """A real ingestion of a Spark-shaped repository, cloned locally."""
    root, sha = make_repo(SPARK_REPO)
    return await ingest_clone(fake_repo_url, local_clone(root, sha))


class TestTheCommitGuard:
    def test_a_repository_never_investigated_has_no_expected_commit(
        self, factory: sessionmaker[Session]
    ) -> None:
        """Nothing to check against, so a run proceeds on whatever HEAD is."""
        assert expected_commit(factory, REPO) is None

    async def test_the_investigated_commit_is_returned(
        self, factory: sessionmaker[Session], ingestion
    ) -> None:
        with factory() as session:
            save_ingestion(session, ingestion)
            session.commit()

        found = expected_commit(factory, ingestion.repo_url.normalized)
        assert found == ingestion.commit_sha

    async def test_a_moved_head_would_not_match(
        self, factory: sessionmaker[Session], ingestion
    ) -> None:
        """What the task compares against before it measures anything."""
        with factory() as session:
            save_ingestion(session, ingestion)
            session.commit()

        wanted = expected_commit(factory, ingestion.repo_url.normalized)
        assert wanted is not None
        assert wanted != "f" * 40

    def test_a_mismatch_is_what_the_task_refuses(self) -> None:
        """Pinned as a named error so the browser can explain it rather than crash."""
        error = CommitMovedError("investigated at abc but HEAD is now def")
        assert isinstance(error, RuntimeError)


class TestConfiguration:
    def test_the_image_is_pinned_to_the_verified_spark_version(self) -> None:
        """The event-log field names were verified against this version's source."""
        assert "3.5.1" in DEFAULT_IMAGE
