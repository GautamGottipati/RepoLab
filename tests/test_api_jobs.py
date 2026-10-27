"""The job API.

The behaviour that matters is the one the done-when criteria describe: submit a
repository, watch progress, refresh midway, see history restored, and continue
receiving live events with no gap and no duplicate.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api import deps
from api.main import app
from repolab.db import Base
from repolab.db.jobs import append_event, create_job, mark_finished, mark_running
from repolab.db.models import AnalyticsEventRecord, Job, JobEventRecord, RepoManifestRecord
from repolab.events import EventType, JobEvent, JobStatus

REPO = "https://github.com/AlexIoannides/pyspark-example-project"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """Point the API at an in-memory database, and stop it enqueuing real work."""
    # StaticPool keeps every session on the same in-memory connection; the default
    # pool would hand out fresh, empty databases.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(deps, "engine", lambda: engine)
    monkeypatch.setattr(deps, "session_factory", lambda: factory)

    import worker.jobs

    monkeypatch.setattr(worker.jobs, "enqueue_investigation", lambda *a, **k: "fake-rq-id")
    yield factory
    engine.dispose()


@pytest.fixture
def client(db: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def seed_events(factory: sessionmaker[Session], job_id: str, count: int) -> None:
    with factory() as session:
        for seq in range(1, count + 1):
            append_event(
                session,
                JobEvent(
                    job_id=job_id,
                    seq=seq,
                    type=EventType.PROGRESS,
                    message=f"step {seq}",
                    step="investigate",
                ),
            )
        session.commit()


def make_job(factory: sessionmaker[Session], *, status: JobStatus | None = None) -> str:
    with factory() as session:
        job = create_job(session, REPO)
        if status is JobStatus.RUNNING:
            mark_running(session, job.id)
        elif status is not None:
            mark_finished(session, job.id, status=status)
        session.commit()
        return job.id


def parse_frames(body: str) -> list[dict]:
    """Parse an SSE body into its events."""
    frames: list[dict] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        entry: dict = {}
        for line in block.splitlines():
            if line.startswith("id: "):
                entry["id"] = int(line[4:])
            elif line.startswith("event: "):
                entry["event"] = line[7:]
            elif line.startswith("data: "):
                entry["data"] = json.loads(line[6:])
        frames.append(entry)
    return frames


class TestSubmission:
    def test_submitting_returns_a_job_id(self, client: TestClient) -> None:
        response = client.post("/jobs", json={"repo_url": REPO})

        assert response.status_code == 202
        body = response.json()
        assert body["job_id"]
        assert body["status"] == "queued"
        assert body["events_url"] == f"/jobs/{body['job_id']}/events"

    def test_the_url_is_normalised(self, client: TestClient) -> None:
        response = client.post("/jobs", json={"repo_url": f"{REPO}.git/"})
        assert response.json()["repo_url"] == REPO

    def test_a_location_header_points_at_the_job(self, client: TestClient) -> None:
        response = client.post("/jobs", json={"repo_url": REPO})
        assert response.headers["Location"] == f"/jobs/{response.json()['job_id']}"

    @pytest.mark.parametrize(
        "url",
        [
            "not-a-url",
            "http://github.com/a/b",
            "git@github.com:a/b.git",
            "https://localhost/a/b",
            "https://169.254.169.254/a/b",
        ],
    )
    def test_an_unacceptable_url_is_rejected(self, client: TestClient, url: str) -> None:
        assert client.post("/jobs", json={"repo_url": url}).status_code == 400

    def test_the_handler_does_no_slow_work(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """Submission only records and enqueues; the worker does the work."""
        job_id = client.post("/jobs", json={"repo_url": REPO}).json()["job_id"]

        with db() as session:
            from repolab.db.jobs import get_job

            assert get_job(session, job_id).status == JobStatus.QUEUED.value

    def test_submission_is_recorded_in_analytics(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        client.post("/jobs", json={"repo_url": REPO})

        with db() as session:
            stored = session.query(AnalyticsEventRecord).all()
        assert [row.type for row in stored] == ["repo_submitted"]


class TestJobState:
    def test_state_reports_status_and_counts(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.RUNNING)
        seed_events(db, job_id, 3)

        body = client.get(f"/jobs/{job_id}").json()
        assert body["status"] == "running"
        assert body["last_seq"] == 3
        assert body["event_count"] == 3
        assert body["attempts"] == 1
        assert body["elapsed_seconds"] >= 0

    def test_a_running_job_with_a_heartbeat_is_working_however_long_since_progress(
        self, client: TestClient, db: sessionmaker[Session], liveness_store
    ) -> None:
        """The case the old stall banner got wrong: a long model call, no events."""
        from repolab.events import Liveness, job_key

        job_id = make_job(db, status=JobStatus.RUNNING)
        seed_events(db, job_id, 1)
        with db() as session:
            # The last milestone was five minutes ago.
            session.query(JobEventRecord).filter(JobEventRecord.job_id == job_id).update(
                {"created_at": datetime.now(UTC) - timedelta(minutes=5)}
            )
            session.get(Job, job_id).started_at = datetime.now(UTC) - timedelta(minutes=6)
            session.commit()
        live = Liveness(liveness_store, job_key(job_id))
        with live:
            live.set_activity("architecture", "architecture.components")
            body = client.get(f"/jobs/{job_id}").json()
        assert body["health"] == "working"
        assert body["activity"]["key"] == "architecture.components"
        assert body["activity"]["stage"] == "architecture"
        assert body["heartbeat_at"] is not None
        assert "stalled" not in body and "stall_summary" not in body

    def test_a_running_job_whose_heartbeat_stopped_is_interrupted(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.RUNNING)
        with db() as session:
            session.get(Job, job_id).started_at = datetime.now(UTC) - timedelta(minutes=3)
            session.commit()
        body = client.get(f"/jobs/{job_id}").json()
        assert body["health"] == "interrupted"
        assert body["activity"] is None

    def test_a_just_started_job_without_a_heartbeat_yet_is_working(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.RUNNING)
        assert client.get(f"/jobs/{job_id}").json()["health"] == "working"

    def test_an_unreadable_store_never_reports_interruption(
        self, client: TestClient, db: sessionmaker[Session], liveness_store
    ) -> None:
        job_id = make_job(db, status=JobStatus.RUNNING)
        with db() as session:
            session.get(Job, job_id).started_at = datetime.now(UTC) - timedelta(minutes=10)
            session.commit()
        liveness_store.unavailable = True
        assert client.get(f"/jobs/{job_id}").json()["health"] == "working"

    def test_a_finished_job_has_no_health(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.COMPLETED)
        body = client.get(f"/jobs/{job_id}").json()
        assert body["status"] == "completed"
        assert body["health"] is None

    def test_a_failure_reports_its_error(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        with db() as session:
            job = create_job(session, REPO)
            mark_finished(session, job.id, status=JobStatus.FAILED, error="clone failed")
            session.commit()
            job_id = job.id

        body = client.get(f"/jobs/{job_id}").json()
        assert body["status"] == "failed"
        assert body["error"] == "clone failed"

    def test_an_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/nope").status_code == 404


class TestEventReplay:
    def test_history_is_replayed_from_the_start(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.COMPLETED)
        seed_events(db, job_id, 5)

        frames = parse_frames(client.get(f"/jobs/{job_id}/events").text)
        # Five stored events, plus the terminal frame telling the client to stop.
        assert [f["id"] for f in frames[:5]] == [1, 2, 3, 4, 5]
        assert frames[-1]["data"]["data"]["terminal"] is True

    def test_each_frame_carries_id_and_event_name(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.COMPLETED)
        seed_events(db, job_id, 2)

        frames = parse_frames(client.get(f"/jobs/{job_id}/events").text)
        assert frames[0]["event"] == "progress"
        assert frames[0]["data"]["message"] == "step 1"

    def test_the_after_parameter_resumes(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.COMPLETED)
        seed_events(db, job_id, 5)

        frames = parse_frames(client.get(f"/jobs/{job_id}/events?after=3").text)
        assert [f["id"] for f in frames[:2]] == [4, 5]

    def test_last_event_id_resumes_without_the_page_helping(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """This is what a browser sends automatically after a reconnect."""
        job_id = make_job(db, status=JobStatus.COMPLETED)
        seed_events(db, job_id, 5)

        response = client.get(f"/jobs/{job_id}/events", headers={"Last-Event-ID": "3"})
        assert [f["id"] for f in parse_frames(response.text)[:2]] == [4, 5]

    def test_last_event_id_takes_precedence_over_the_query(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.COMPLETED)
        seed_events(db, job_id, 5)

        response = client.get(f"/jobs/{job_id}/events?after=0", headers={"Last-Event-ID": "4"})
        assert [f["id"] for f in parse_frames(response.text)[:1]] == [5]

    def test_a_malformed_last_event_id_falls_back(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.COMPLETED)
        seed_events(db, job_id, 2)

        response = client.get(
            f"/jobs/{job_id}/events?after=1", headers={"Last-Event-ID": "garbage"}
        )
        assert [f["id"] for f in parse_frames(response.text)[:1]] == [2]

    def test_refresh_midway_yields_no_gap_and_no_duplicate(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """The done-when criterion, asserted on sequence continuity."""
        job_id = make_job(db, status=JobStatus.RUNNING)
        seed_events(db, job_id, 4)

        first = parse_frames(client.get(f"/jobs/{job_id}/events?after=0&follow=false").text)
        seen = [f["id"] for f in first if "id" in f]
        resume_from = seen[-1]

        # More work happens while the client is away, then the job finishes.
        with db() as session:
            for seq in range(5, 9):
                append_event(
                    session,
                    JobEvent(
                        job_id=job_id, seq=seq, type=EventType.PROGRESS, message=f"step {seq}"
                    ),
                )
            mark_finished(session, job_id, status=JobStatus.COMPLETED)
            session.commit()

        second = parse_frames(
            client.get(f"/jobs/{job_id}/events", headers={"Last-Event-ID": str(resume_from)}).text
        )
        resumed = [f["id"] for f in second if not f["data"]["data"].get("terminal")]

        assert resumed == [5, 6, 7, 8]
        assert set(seen) & set(resumed) == set()  # no duplicates
        assert seen + resumed == list(range(1, 9))  # no gaps

    def test_an_unknown_job_stream_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/nope/events").status_code == 404

    def test_the_content_type_is_an_event_stream(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.COMPLETED)
        response = client.get(f"/jobs/{job_id}/events")
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"


class TestManifestEndpoint:
    def test_it_is_404_before_the_job_finishes(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        job_id = make_job(db, status=JobStatus.RUNNING)
        response = client.get(f"/jobs/{job_id}/manifest")
        assert response.status_code == 404
        assert "no manifest yet" in response.json()["detail"]

    def test_it_returns_the_manifest_once_ready(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        from tests.test_domain_models import manifest as build_manifest

        expected = build_manifest(languages=["Python"])
        with db() as session:
            from repolab.db.models import Ingestion, Repository

            repository = Repository(url=REPO, host="github.com", owner="a", name="b")
            session.add(repository)
            session.flush()
            ingestion = Ingestion(repository_id=repository.id, commit_sha="a" * 40)
            session.add(ingestion)
            session.flush()
            session.add(
                RepoManifestRecord(
                    ingestion_id=ingestion.id,
                    manifest=expected.model_dump(mode="json"),
                    confidence=expected.confidence,
                )
            )
            job = create_job(session, REPO)
            mark_finished(session, job.id, status=JobStatus.COMPLETED, ingestion_id=ingestion.id)
            session.commit()
            job_id = job.id

        response = client.get(f"/jobs/{job_id}/manifest")
        assert response.status_code == 200
        assert response.json()["languages"] == ["Python"]

    def test_an_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/nope/manifest").status_code == 404


class TestHealthStillWorks:
    def test_health(self, client: TestClient) -> None:
        assert client.get("/health").json()["status"] == "ok"
