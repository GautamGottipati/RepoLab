"""Build caching.

A cache hit hands an environment straight to a caller, so what may be stored
matters as much as what is retrieved: an unready build must never be cached, or the
next lookup returns something broken.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from repolab.builds import (
    BuildArtifact,
    BuildCache,
    BuildCapabilities,
    DbBuildCache,
    InMemoryBuildCache,
    NullBuildCache,
    SmokeTestResult,
)
from repolab.builds.models import BuildSource
from repolab.db import Base
from repolab.db.builds import count_builds, load_build, save_build
from repolab.manifest.models import Fidelity

REPO = "https://github.com/AlexIoannides/pyspark-example-project"
SHA = "e" * 40


def ready_artifact(**overrides) -> BuildArtifact:
    defaults = {
        "repo_url": REPO,
        "commit_sha": SHA,
        "provider": "fake",
        "base_image": "docker://apache/spark-py:v3.4.0",
        "checkpoint_id": "ckpt-1",
        "checkpoint_tag": "repolab/demo:eeeeeeeeeeee",
        "smoke": SmokeTestResult(
            name="a tiny Spark job completes",
            command="python3 /work/repolab_smoke.py",
            passed=True,
            exit_code=0,
            stdout="REPOLAB_SPARK_OK 3.5.1\n",
        ),
        "capabilities": BuildCapabilities(
            capabilities=["run pyspark jobs locally"],
            deviations=["pyspark 2.4.0 -> 3.5.1"],
            versions={"pyspark": "3.5.1"},
        ),
    }
    return BuildArtifact(**{**defaults, **overrides})


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


class TestConformance:
    @pytest.mark.parametrize(
        "cache", [InMemoryBuildCache(), NullBuildCache()], ids=["memory", "null"]
    )
    def test_the_caches_satisfy_the_protocol(self, cache: object) -> None:
        assert isinstance(cache, BuildCache)


class TestInMemoryCache:
    def test_a_ready_artifact_round_trips(self) -> None:
        cache = InMemoryBuildCache()
        assert cache.put(ready_artifact()) is True

        found = cache.get(REPO, SHA)
        assert found is not None
        assert found.checkpoint_id == "ckpt-1"

    def test_an_unready_artifact_is_refused(self) -> None:
        """Caching a broken build would hand it out on the next lookup."""
        cache = InMemoryBuildCache()
        assert cache.put(ready_artifact(smoke=None)) is False
        assert cache.get(REPO, SHA) is None

    def test_a_retrieved_artifact_reports_itself_as_cached(self) -> None:
        cache = InMemoryBuildCache()
        cache.put(ready_artifact())
        assert cache.get(REPO, SHA).source is BuildSource.CACHE

    def test_a_different_commit_misses(self) -> None:
        cache = InMemoryBuildCache()
        cache.put(ready_artifact())
        assert cache.get(REPO, "f" * 40) is None

    def test_a_different_repository_misses(self) -> None:
        cache = InMemoryBuildCache()
        cache.put(ready_artifact())
        assert cache.get("https://github.com/other/repo", SHA) is None

    def test_the_null_cache_stores_nothing(self) -> None:
        cache = NullBuildCache()
        assert cache.put(ready_artifact()) is False
        assert cache.get(REPO, SHA) is None


class TestDatabaseCache:
    def test_a_ready_build_is_stored_and_loaded(self, session: Session) -> None:
        save_build(session, ready_artifact())
        session.commit()

        loaded = load_build(session, REPO, SHA)
        assert loaded is not None
        assert loaded.checkpoint_id == "ckpt-1"
        assert loaded.checkpoint_tag == "repolab/demo:eeeeeeeeeeee"
        assert loaded.ready is True

    def test_an_unready_build_is_not_stored(self, session: Session) -> None:
        assert save_build(session, ready_artifact(smoke=None)) is None
        session.commit()
        assert count_builds(session) == 0

    def test_capabilities_and_deviations_survive(self, session: Session) -> None:
        save_build(session, ready_artifact())
        session.commit()

        loaded = load_build(session, REPO, SHA)
        assert loaded.capabilities.deviations == ["pyspark 2.4.0 -> 3.5.1"]
        assert loaded.capabilities.versions == {"pyspark": "3.5.1"}

    def test_fidelity_is_rederived_not_stored_blindly(self, session: Session) -> None:
        """Fidelity comes from the deviations, so a load cannot overstate it."""
        save_build(session, ready_artifact())
        session.commit()
        assert load_build(session, REPO, SHA).fidelity is Fidelity.HIGH

    def test_an_exact_build_loads_as_exact(self, session: Session) -> None:
        save_build(session, ready_artifact(capabilities=BuildCapabilities()))
        session.commit()
        assert load_build(session, REPO, SHA).fidelity is Fidelity.EXACT

    def test_the_smoke_result_survives(self, session: Session) -> None:
        save_build(session, ready_artifact())
        session.commit()

        smoke = load_build(session, REPO, SHA).smoke
        assert smoke is not None
        assert smoke.passed is True
        assert "REPOLAB_SPARK_OK" in smoke.stdout

    def test_resaving_the_same_commit_updates(self, session: Session) -> None:
        save_build(session, ready_artifact())
        session.commit()
        save_build(session, ready_artifact(checkpoint_id="ckpt-2"))
        session.commit()

        assert count_builds(session) == 1
        assert load_build(session, REPO, SHA).checkpoint_id == "ckpt-2"

    def test_a_second_commit_is_a_second_row(self, session: Session) -> None:
        save_build(session, ready_artifact())
        save_build(session, ready_artifact(commit_sha="f" * 40))
        session.commit()
        assert count_builds(session) == 2

    def test_a_miss_returns_none(self, session: Session) -> None:
        assert load_build(session, REPO, SHA) is None

    def test_the_wrapper_reads_and_writes(self, session: Session) -> None:
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        cache = DbBuildCache(factory)

        assert cache.put(ready_artifact()) is True
        found = cache.get(REPO, SHA)
        assert found is not None
        assert found.source is BuildSource.CACHE

    def test_a_write_failure_does_not_raise(self) -> None:
        """Failing to cache must never fail a build that otherwise succeeded."""

        def broken():
            raise RuntimeError("database gone")

        assert DbBuildCache(broken).put(ready_artifact()) is False

    def test_a_read_failure_is_a_miss(self) -> None:
        def broken():
            raise RuntimeError("database gone")

        assert DbBuildCache(broken).get(REPO, SHA) is None
