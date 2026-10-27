"""The investigation, published artifact by artifact -- against a real local repository.

No network, no model: a local git repository over ``file://``, SQLite, and no
router, so every pass that would ask Nemotron runs its deterministic path.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.analytics import Analytics
from repolab.analytics.events import CollectingAnalyticsWriter
from repolab.artifacts import (
    ARCH_COMPONENTS,
    ARCH_RUNTIME,
    ARCH_SOURCE,
    ARCH_STRUCTURE,
    ARCH_SUMMARY,
    EAGER,
    REPO_FACTS,
    REPO_MANIFEST,
)
from repolab.artifacts import store as artifacts
from repolab.db.models import Job, JobEventRecord
from repolab.events import DbEventEmitter
from repolab.ingest.url import RepoUrl

PIPELINE = {
    "README.md": "# Sales ETL\n\nA small Spark job that totals sales by region.\n",
    "jobs/etl_job.py": (
        "from pyspark.sql import SparkSession\n\n\n"
        "def main():\n"
        "    spark = SparkSession.builder.getOrCreate()\n"
        "    data = extract_data(spark)\n"
        "    totals = transform_data(data)\n"
        "    load_data(totals)\n\n\n"
        "def extract_data(spark):\n"
        "    return spark.read.parquet('tests/test_data/employees')\n\n\n"
        "def transform_data(df):\n"
        "    return df.groupBy('region').count()\n\n\n"
        "def load_data(df):\n"
        "    df.write.csv('out')\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
    "requirements.txt": "pyspark==3.5.1\n",
}


@pytest.fixture
def db() -> Iterator[sessionmaker[Session]]:
    from repolab.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def local_url(path: Path) -> RepoUrl:
    return RepoUrl(
        normalized=f"file://{path.resolve()}", host="github.com", owner="local", name=path.name
    )


def new_job(db, job_id: str, url: RepoUrl) -> None:
    with db() as session:
        session.add(Job(id=job_id, repo_url=url.normalized, status="running"))
        session.commit()


def run(db, job_id: str, url: RepoUrl, *, providers=None, enqueue=None, liveness=None) -> int:
    from worker.analysis import analyse

    emitter = DbEventEmitter(job_id, lambda: db())
    analytics = Analytics(CollectingAnalyticsWriter(), job_id=job_id)
    return asyncio.run(
        analyse(
            job_id,
            url,
            settings=None,
            emitter=emitter,
            analytics=analytics,
            factory=db,
            providers=providers or (lambda: (None, None)),
            enqueue=enqueue,
            allow_local_source=True,
            liveness=liveness,
        )
    )


def ready_order(db, job_id: str) -> list[str]:
    with db() as session:
        rows = (
            session.query(JobEventRecord)
            .filter(JobEventRecord.job_id == job_id, JobEventRecord.type == "artifact_ready")
            .order_by(JobEventRecord.seq)
            .all()
        )
        return [r.data["artifact_type"] for r in rows]


@pytest.fixture
def repo(make_repo) -> tuple[Path, str]:
    root, sha = make_repo(PIPELINE)
    subprocess.run(
        ["git", "config", "uploadpack.allowReachableSHA1InWant", "true"], cwd=root, check=True
    )
    return root, sha


class TestProgressiveInvestigation:
    def test_artifacts_are_published_in_dependency_order_and_the_job_is_linked_early(
        self, db, repo
    ) -> None:
        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        seen_before_models: dict[str, object] = {}

        def providers():
            # Called just before the first model pass: P0 must already be out.
            with db() as session:
                job = session.get(Job, "job-1")
                seen_before_models["ingestion_id"] = job.ingestion_id
                seen_before_models["commit"] = job.commit_sha
                seen_before_models["ready"] = {
                    t
                    for t in EAGER
                    if (r := artifacts.get(session, url.normalized, sha, t)) and r.status == "ready"
                }
            return None, None

        ingestion_id = run(db, "job-1", url, providers=providers)
        assert seen_before_models["ingestion_id"] == ingestion_id
        assert seen_before_models["commit"] == sha
        assert {
            REPO_FACTS,
            REPO_MANIFEST,
            ARCH_STRUCTURE,
            ARCH_RUNTIME,
            ARCH_SOURCE,
        } <= seen_before_models["ready"]
        assert ready_order(db, "job-1") == [
            REPO_FACTS,
            REPO_MANIFEST,
            ARCH_STRUCTURE,
            ARCH_RUNTIME,
            ARCH_SOURCE,
            ARCH_COMPONENTS,
            ARCH_SUMMARY,
        ]
        with db() as session:
            facts = artifacts.get(session, url.normalized, sha, REPO_FACTS)
            assert facts.payload["readme"]["title"] == "Sales ETL"
            assert facts.payload["readme"]["first_paragraph"].startswith("A small Spark job")
            assert "Python" in facts.payload["languages"]

    def test_the_live_activity_follows_the_real_steps_in_order(self, db, repo) -> None:
        """What the screen says it is doing is set where that work begins -- never invented."""

        class Recorder:
            def __init__(self) -> None:
                self.seen: list[tuple[str, str]] = []

            def set_activity(self, stage: str, activity: str) -> None:
                if not self.seen or self.seen[-1] != (stage, activity):
                    self.seen.append((stage, activity))

        root, _ = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        recorder = Recorder()
        run(db, "job-1", url, liveness=recorder)
        keys = [key for _, key in recorder.seen]
        assert keys[0] == "inspecting_repository"
        expected = [
            REPO_FACTS,
            REPO_MANIFEST,
            ARCH_STRUCTURE,
            ARCH_RUNTIME,
            "finding_entrypoints",
            ARCH_COMPONENTS,
            ARCH_SUMMARY,
        ]
        positions = [keys.index(key) for key in expected]
        assert positions == sorted(positions)
        assert ("architecture", f"{ARCH_COMPONENTS}:verifying") in recorder.seen

    def test_the_same_commit_is_reused_without_cloning(self, db, repo, monkeypatch) -> None:
        root, _ = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        first = run(db, "job-1", url)

        import worker.analysis as analysis

        def no_clone(*args, **kwargs):
            raise AssertionError("a cached commit must not be cloned again")

        monkeypatch.setattr(analysis, "TemporaryClone", no_clone)
        new_job(db, "job-2", url)
        assert run(db, "job-2", url) == first
        with db() as session:
            assert session.get(Job, "job-2").ingestion_id == first
            events = [
                r.data
                for r in session.query(JobEventRecord)
                .filter(JobEventRecord.job_id == "job-2")
                .all()
                if r.type == "artifact_ready"
            ]
        assert events and all(e["cache_hit"] for e in events)
        assert {e["artifact_type"] for e in events} == set(EAGER)

    def test_an_interrupted_analysis_is_not_followed(self, db, repo, monkeypatch) -> None:
        """Its artifacts still say "generating", but its worker stopped beating."""
        from datetime import UTC, datetime, timedelta

        import worker.analysis as analysis
        from repolab.artifacts import Status

        root, sha = repo
        url = local_url(root)
        with db() as session:
            session.add(
                Job(
                    id="dead",
                    repo_url=url.normalized,
                    status="running",
                    commit_sha=sha,
                    started_at=datetime.now(UTC) - timedelta(minutes=5),
                )
            )
            record = artifacts.ensure(session, url.normalized, sha, REPO_MANIFEST)
            record.job_id = "dead"
            artifacts.transition(session, record, Status.GENERATING)
            session.commit()
        monkeypatch.setattr(analysis, "FOLLOW_SECONDS", 2)
        new_job(db, "job-2", url)
        run(db, "job-2", url)
        with db() as session:
            messages = [
                r.message
                for r in session.query(JobEventRecord)
                .filter(JobEventRecord.job_id == "job-2")
                .all()
            ]
        assert not any("following it" in m for m in messages)
        assert REPO_MANIFEST in ready_order(db, "job-2")

    def test_a_new_commit_never_reuses_the_old_one(self, db, repo) -> None:
        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        run(db, "job-1", url)
        (root / "jobs/etl_job.py").write_text(PIPELINE["jobs/etl_job.py"] + "\n# changed\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@repolab.invalid",
                "-c",
                "user.name=T",
                "commit",
                "-qm",
                "two",
            ],
            cwd=root,
            check=True,
        )
        new_job(db, "job-2", url)
        run(db, "job-2", url)
        with db() as session:
            new_commit = session.get(Job, "job-2").commit_sha
        assert new_commit != sha
        order = ready_order(db, "job-2")
        assert order[0] == REPO_FACTS  # generated afresh, not reused

    def test_one_failing_pass_does_not_fail_the_job(self, db, repo, monkeypatch) -> None:
        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        import worker.analysis as analysis

        async def broken(*args, **kwargs):
            raise RuntimeError("model unavailable")

        monkeypatch.setattr(analysis, "synthesise", broken)
        ingestion_id = run(db, "job-1", url)
        assert ingestion_id
        with db() as session:
            states = {
                r.artifact_type: r.status for r in artifacts.all_for(session, url.normalized, sha)
            }
        assert states[ARCH_COMPONENTS] == "failed"
        assert states[ARCH_SUMMARY] == "blocked"
        for artifact_type in (REPO_FACTS, REPO_MANIFEST, ARCH_STRUCTURE, ARCH_RUNTIME, ARCH_SOURCE):
            assert states[artifact_type] == "ready"

    def test_a_lazy_artifact_requested_during_the_job_starts_when_its_dependency_is_ready(
        self, db, repo
    ) -> None:
        from repolab.artifacts import ARCH_RUNTIME_NAMING

        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        with db() as session:
            # Someone opened Architecture before the runtime model existed.
            artifacts.claim(session, url.normalized, sha, ARCH_RUNTIME_NAMING, job_id="job-1")
            session.commit()
        started: list[tuple[str, str]] = []
        run(db, "job-1", url, enqueue=lambda job, t: started.append((job, t)))
        assert started == [("job-1", ARCH_RUNTIME_NAMING)]


# -- the on-demand runtime naming pass -----------------------------------------------


def name_on_demand(db, job_id: str, url: RepoUrl, sha: str, monkeypatch, *, router, renamed=None):
    """Claim runtime naming as the API would, then run the worker's pass."""
    import worker.artifact_tasks as tasks
    from repolab.artifacts import ARCH_RUNTIME_NAMING, Status

    ingestion_id = run(db, job_id, url)
    with db() as session:
        assert artifacts.claim(session, url.normalized, sha, ARCH_RUNTIME_NAMING, job_id=job_id)
        record = artifacts.get(session, url.normalized, sha, ARCH_RUNTIME_NAMING)
        artifacts.transition(session, record, Status.QUEUED)
        session.commit()
    monkeypatch.setattr(tasks, "_router", lambda settings, factory: router)
    if renamed is not None:
        monkeypatch.setattr(tasks, "nameable", lambda graph: True)

        async def fake_name_runtime(graph, router):
            return renamed(graph)

        monkeypatch.setattr(tasks, "name_runtime", fake_name_runtime)
    analytics = Analytics(CollectingAnalyticsWriter(), job_id=job_id)
    asyncio.run(
        tasks._runtime_naming(
            job_id,
            url.normalized,
            sha,
            ingestion_id,
            db,
            DbEventEmitter(job_id, lambda: db(), start_seq=last_seq(db, job_id)),
            analytics,
            None,
        )
    )
    with db() as session:
        return artifacts.get(session, url.normalized, sha, ARCH_RUNTIME_NAMING)


def last_seq(db, job_id: str) -> int:
    with db() as session:
        return session.get(Job, job_id).last_seq


class TestRuntimeNamingOnDemand:
    def test_a_named_runtime_is_stored_on_the_artifact_and_overlaid_on_read(
        self, db, repo, monkeypatch
    ) -> None:
        from repolab.architecture import load_graph
        from repolab.architecture.models import Provenance
        from repolab.artifacts.overlay import architecture_sections, with_refinements
        from repolab.db.models import ArchitectureRecord

        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)

        def rename(graph):
            graph.runtime.provenance = Provenance.INFERRED
            return graph

        record = name_on_demand(db, "job-1", url, sha, monkeypatch, router=object(), renamed=rename)
        assert record.status == "ready"
        assert record.payload["runtime"]["provenance"] == "inferred"
        with db() as session:
            stored = session.query(ArchitectureRecord).one()
            assert load_graph(stored.graph).runtime.provenance != Provenance.INFERRED  # untouched
            shown = with_refinements(session, url.normalized, sha, load_graph(stored.graph))
            assert shown.runtime.provenance == Provenance.INFERRED
            assert architecture_sections(session, url.normalized, sha)["runtime_naming"] == "ready"
        assert ready_order(db, "job-1")[-1] == "architecture.runtime_naming"

    def test_without_a_model_it_fails_retryably_and_nothing_else_changes(
        self, db, repo, monkeypatch
    ) -> None:
        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        record = name_on_demand(
            db, "job-1", url, sha, monkeypatch, router=None, renamed=lambda g: g
        )
        assert record.status == "failed"
        assert record.error["retryable"] and record.error["stage"] == "runtime naming"
        with db() as session:
            states = {
                r.artifact_type: r.status for r in artifacts.all_for(session, url.normalized, sha)
            }
        assert states[ARCH_RUNTIME] == "ready" and states[ARCH_SUMMARY] == "ready"

    def test_a_runtime_with_nothing_to_name_is_ready_unchanged(self, db, repo, monkeypatch) -> None:
        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        record = name_on_demand(db, "job-1", url, sha, monkeypatch, router=None)
        assert record.status == "ready" and record.payload == {"unchanged": True}

    def test_an_artifact_that_is_no_longer_queued_is_not_run_twice(
        self, db, repo, monkeypatch
    ) -> None:
        import worker.artifact_tasks as tasks
        from repolab.artifacts import ARCH_RUNTIME_NAMING

        root, sha = repo
        url = local_url(root)
        new_job(db, "job-1", url)
        ingestion_id = run(db, "job-1", url)
        calls: list[object] = []
        monkeypatch.setattr(tasks, "_router", lambda *a: calls.append(a))
        asyncio.run(
            tasks._runtime_naming(
                "job-1",
                url.normalized,
                sha,
                ingestion_id,
                db,
                DbEventEmitter("job-1", lambda: db()),
                Analytics(CollectingAnalyticsWriter()),
                None,
            )
        )
        assert calls == []
        with db() as session:
            assert (
                artifacts.get(session, url.normalized, sha, ARCH_RUNTIME_NAMING).status
                == "not_requested"
            )
