"""The architecture and Spark playground endpoints.

These are the two screens whose data the backend held but never served. The
behaviour that matters: nothing slow happens in a handler, a run is linked to
the job that produced it, and the dataset preview shows the *real* bytes rather
than a plausible sample.
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
from repolab.db.models import ArchitectureRecord, Ingestion, Job, Repository, SparkRun
from repolab.events import JobStatus
from repolab.experiences.spark.dataset import generate_dataset

REPO = "https://github.com/AlexIoannides/pyspark-example-project"


@pytest.fixture
def enqueued() -> list[tuple]:
    return []


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, enqueued: list[tuple]) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(deps, "engine", lambda: engine)
    monkeypatch.setattr(deps, "session_factory", lambda: factory)

    import worker.jobs

    monkeypatch.setattr(
        worker.jobs,
        "enqueue_spark_run",
        lambda *a, **k: enqueued.append((a, k)) or "fake-rq-id",
    )
    yield factory
    engine.dispose()


@pytest.fixture
def client(db: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(app) as handle:
        yield handle


def ingested_job(factory: sessionmaker[Session], *, with_architecture: bool = True) -> str:
    """A completed investigation, so the architecture endpoint has something to serve."""
    with factory() as session:
        job = create_job(session, REPO)
        repository = Repository(url=REPO, host="github.com", owner="a", name="b")
        session.add(repository)
        session.flush()

        ingestion = Ingestion(repository_id=repository.id, commit_sha="c" * 40)
        session.add(ingestion)
        session.flush()

        if with_architecture:
            session.add(
                ArchitectureRecord(
                    ingestion_id=ingestion.id,
                    generated_by="deterministic",
                    node_count=2,
                    edge_count=1,
                    graph={
                        "nodes": [
                            {
                                "id": "entrypoint:etl_job",
                                "kind": "entrypoint",
                                "name": "etl_job",
                                "file_references": [{"path": "jobs/etl_job.py"}],
                            },
                            {
                                "id": "external:Spark",
                                "kind": "external",
                                "name": "Spark",
                                "file_references": [{"path": "Pipfile"}],
                            },
                        ],
                        "edges": [
                            {
                                "source": "entrypoint:etl_job",
                                "target": "external:Spark",
                                "kind": "depends_on",
                            }
                        ],
                    },
                )
            )

        job.ingestion_id = ingestion.id
        job.status = JobStatus.COMPLETED.value
        session.commit()
        return job.id


class TestArchitecture:
    def test_an_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/nope/architecture").status_code == 404

    def test_it_is_404_until_the_investigation_finishes(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        with db() as session:
            job = create_job(session, REPO)
            session.commit()
            job_id = job.id

        response = client.get(f"/jobs/{job_id}/architecture")
        assert response.status_code == 404
        assert "has no architecture yet" in response.json()["detail"]

    def test_it_serves_the_stored_graph(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = ingested_job(db)
        body = client.get(f"/jobs/{job_id}/architecture").json()

        assert body["node_count"] == 2
        assert body["edge_count"] == 1
        assert body["generated_by"] == "deterministic"
        assert {node["name"] for node in body["graph"]["nodes"]} == {"etl_job", "Spark"}
        assert body["graph"]["edges"][0]["kind"] == "depends_on"

    def test_every_component_cites_a_file(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """A component without evidence is a diagram, not an architecture."""
        job_id = ingested_job(db)
        body = client.get(f"/jobs/{job_id}/architecture").json()

        for node in body["graph"]["nodes"]:
            assert node["file_references"], f"{node['name']} cites nothing"

    def test_a_missing_record_is_404_rather_than_an_empty_graph(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = ingested_job(db, with_architecture=False)
        assert client.get(f"/jobs/{job_id}/architecture").status_code == 404

    def test_the_manifest_endpoint_still_guards_the_same_way(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        with db() as session:
            job = create_job(session, REPO)
            session.commit()
            job_id = job.id
        assert client.get(f"/jobs/{job_id}/manifest").status_code == 404


class TestStartingARun:
    def test_a_run_is_queued_not_executed(self, client: TestClient, enqueued: list[tuple]) -> None:
        response = client.post("/runs", json={"repo_url": REPO, "shuffle_partitions": 50})

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["shuffle_partitions"] == 50
        assert body["events_url"].endswith("/events")
        assert body["run_url"].endswith("/run")
        assert len(enqueued) == 1

    def test_the_partition_count_reaches_the_worker(
        self, client: TestClient, enqueued: list[tuple]
    ) -> None:
        client.post("/runs", json={"repo_url": REPO, "shuffle_partitions": 8, "rows": 20_000})
        _, kwargs = enqueued[0]
        assert kwargs["shuffle_partitions"] == 8
        assert kwargs["rows"] == 20_000

    def test_the_default_is_the_question_under_study(self, client: TestClient) -> None:
        body = client.post("/runs", json={"repo_url": REPO}).json()
        assert body["shuffle_partitions"] == 200

    def test_an_invalid_url_is_refused(self, client: TestClient, enqueued: list[tuple]) -> None:
        assert client.post("/runs", json={"repo_url": "not-a-url"}).status_code == 400
        assert enqueued == []

    def test_an_internal_host_is_refused(self, client: TestClient) -> None:
        assert client.post("/runs", json={"repo_url": "https://localhost/a/b"}).status_code == 400

    @pytest.mark.parametrize("partitions", [0, -1, 5000])
    def test_partitions_outside_the_allowed_range_are_refused(
        self, client: TestClient, partitions: int
    ) -> None:
        response = client.post("/runs", json={"repo_url": REPO, "shuffle_partitions": partitions})
        assert response.status_code == 422

    def test_an_unbounded_dataset_is_refused(self, client: TestClient) -> None:
        """A browser must not be able to request arbitrary compute."""
        response = client.post("/runs", json={"repo_url": REPO, "rows": 10_000_000})
        assert response.status_code == 422

    def test_the_job_is_recorded_with_its_kind(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = client.post("/runs", json={"repo_url": REPO}).json()["job_id"]
        with db() as session:
            assert session.get(Job, job_id).kind == "spark_run"


class TestReadingRuns:
    def stored_run(self, factory: sessionmaker[Session], *, job_id: str | None = None) -> str:
        with factory() as session:
            session.add(
                SparkRun(
                    run_id="run-abc",
                    job_id=job_id,
                    repo_url=REPO,
                    commit_sha="c" * 40,
                    dataset_hash="d" * 64,
                    measurements={"task_count": 404, "stage_count": 6},
                    configuration={"shuffle_partitions": 200},
                    event_log_path="/tmp/x",
                    event_log_sha256="e" * 64,
                    event_log_bytes=1024,
                    task_count=404,
                    stage_count=6,
                    total_runtime_ms=6011,
                    wall_clock_seconds=12.5,
                )
            )
            session.commit()
        return "run-abc"

    def test_a_run_is_readable_by_id(self, client: TestClient, db: sessionmaker[Session]) -> None:
        run_id = self.stored_run(db)
        body = client.get(f"/runs/{run_id}").json()

        assert body["task_count"] == 404
        assert body["total_runtime_ms"] == 6011
        assert body["dataset_hash"] == "d" * 64

    def test_every_run_discloses_what_repolab_added(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """The repository's job performs no shuffle; the additions must be visible."""
        run_id = self.stored_run(db)
        assert client.get(f"/runs/{run_id}").json()["added_steps"]

    def test_an_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get("/runs/nope").status_code == 404

    def test_a_job_resolves_to_the_run_it_produced(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        with db() as session:
            job = create_job(session, REPO, kind="spark_run")
            session.commit()
            job_id = job.id

        self.stored_run(db, job_id=job_id)
        assert client.get(f"/jobs/{job_id}/run").json()["run_id"] == "run-abc"

    def test_it_is_404_until_the_run_is_measured(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        with db() as session:
            job = create_job(session, REPO, kind="spark_run")
            session.commit()
            job_id = job.id

        response = client.get(f"/jobs/{job_id}/run")
        assert response.status_code == 404
        assert "has not produced a run" in response.json()["detail"]

    def test_runs_are_listed_for_a_repository(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        self.stored_run(db)
        body = client.get("/runs", params={"repo_url": REPO}).json()
        assert [run["run_id"] for run in body] == ["run-abc"]


class TestDatasetPreview:
    def test_the_preview_is_cut_from_the_real_bytes(self, client: TestClient) -> None:
        """A sample invented for display would be the one unevidenced thing on screen."""
        body = client.get("/runs/dataset", params={"rows": 5_000}).json()
        _, spec = generate_dataset(rows=5_000)

        assert body["sha256"] == spec.sha256
        assert body["total_rows"] == 5_000
        assert body["spec"]["skewed_key"] == spec.skewed_key

    def test_the_distinct_key_count_is_served_not_assumed(self, client: TestClient) -> None:
        """`DatasetSpec` exposes it as a property, which pydantic does not
        serialise — so without this the screen would hardcode the number."""
        body = client.get("/runs/dataset", params={"rows": 5_000}).json()
        _, spec = generate_dataset(rows=5_000)
        assert body["distinct_keys"] == spec.distinct_keys

    def test_it_returns_the_columns_the_pipeline_reads(self, client: TestClient) -> None:
        body = client.get("/runs/dataset", params={"rows": 5_000}).json()
        assert body["columns"] == ["id", "first_name", "second_name", "floor", "dept"]
        assert body["rows"]
        assert all(len(row) == len(body["columns"]) for row in body["rows"])

    def test_the_preview_is_bounded(self, client: TestClient) -> None:
        body = client.get("/runs/dataset", params={"rows": 5_000}).json()
        assert body["preview_rows"] <= 8

    def test_the_same_request_gives_the_same_bytes(self, client: TestClient) -> None:
        """Determinism is what lets a later run claim it saw this input."""
        first = client.get("/runs/dataset", params={"rows": 5_000}).json()
        second = client.get("/runs/dataset", params={"rows": 5_000}).json()
        assert first == second

    def test_the_route_is_not_shadowed_by_the_run_id_route(self, client: TestClient) -> None:
        """`/runs/dataset` must not be read as a run called "dataset"."""
        assert client.get("/runs/dataset", params={"rows": 5_000}).status_code == 200

    def test_an_unbounded_preview_is_refused(self, client: TestClient) -> None:
        assert client.get("/runs/dataset", params={"rows": 10_000_000}).status_code == 422
