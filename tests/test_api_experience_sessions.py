"""Experience sessions through the API: start, reconnect, run, simulate, experiment.

Offline: worker jobs are captured, not run, and the database is SQLite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.experiences import sessions as store
from repolab.manifest.models import SurfaceType
from repolab.questions.models import ExperimentBackend, Route
from repolab.questions.triage import triage
from tests.support.manifests import airflow_manifest
from tests.test_api_experiences import investigated
from tests.test_experiences_discovery import SPARK_FACTS, manifest


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
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    import api.routes.experience_sessions as routes

    calls: list[tuple] = []
    monkeypatch.setattr(
        routes, "_enqueue", lambda task, *args, timeout: calls.append((task, *args))
    )
    return calls


@pytest.fixture
def client(db: sessionmaker[Session], queued) -> Iterator[TestClient]:
    from api.main import app

    with TestClient(app) as handle:
        yield handle


def spark_job(db) -> str:
    return investigated(
        db,
        manifest(
            (SurfaceType.CLI, 0.95),
            (SurfaceType.DATA_PLAYGROUND, 0.25),
            facts=SPARK_FACTS,
            url="https://github.com/apache/spark",
        ),
    )


def ready(db, session_id: str, **runtime) -> None:
    with db() as session:
        _, state = store.load_session(session, session_id)
        state.runtime.image = runtime.get("image", "apache/spark:3.5.1-python3")
        state.runtime.spark_submit = "/opt/spark/bin/spark-submit"
        store.save_state(session, session_id, state, status=store.READY)
        session.commit()


class TestStarting:
    def test_the_list_is_experiences_with_a_primary(self, client, db) -> None:
        job = spark_job(db)
        listing = client.get(f"/jobs/{job}/experience-list").json()
        assert listing["experiences"][0]["title"] == "Transform a Dataset"
        assert listing["experiences"][0]["primary"] is True

    def test_starting_records_the_chosen_rung_and_queues_preparation(
        self, client, db, queued
    ) -> None:
        job = spark_job(db)
        created = client.post(
            f"/jobs/{job}/experiences/spark:transform/sessions",
            json={"strategy": "official_release"},
        )
        assert created.status_code == 202
        body = created.json()
        assert body["strategy"] == "official_release" and body["reality"] == "official_runtime"
        assert body["status"] == "preparing" and body["events_url"].endswith("/events")
        assert queued[0][0] == "worker.experience_tasks.prepare_experience"
        # Asking again joins the same session instead of building twice.
        again = client.post(
            f"/jobs/{job}/experiences/spark:transform/sessions",
            json={"strategy": "official_release"},
        ).json()
        assert again["id"] == body["id"] and len(queued) == 1

    def test_a_refresh_reconnects_with_the_experience(self, client, db) -> None:
        job = spark_job(db)
        sid = client.post(f"/jobs/{job}/experiences/spark:transform/sessions", json={}).json()["id"]
        view = client.get(f"/experience-sessions/{sid}").json()
        assert view["experience"]["title"] == "Transform a Dataset"
        listing = client.get(f"/jobs/{job}/experience-list").json()
        assert listing["experiences"][0]["state"] == "running"
        assert listing["experiences"][0]["session_id"] == sid

    def test_unknown_or_screen_owning_experiences_are_refused(self, client, db) -> None:
        job = spark_job(db)
        assert client.post(f"/jobs/{job}/experiences/nope/sessions", json={}).status_code == 404


class TestRunning:
    def test_runs_wait_for_a_ready_session(self, client, db, queued) -> None:
        job = spark_job(db)
        sid = client.post(f"/jobs/{job}/experiences/spark:transform/sessions", json={}).json()["id"]
        assert (
            client.post(
                f"/experience-sessions/{sid}/runs", json={"code": "result = df"}
            ).status_code
            == 409
        )
        ready(db, sid)
        created = client.post(
            f"/experience-sessions/{sid}/runs",
            json={"code": "result = df", "shuffle_partitions": 8},
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        assert (
            queued[-1][0] == "worker.experience_tasks.run_experience" and queued[-1][-1] == run_id
        )
        state = client.get(f"/experience-sessions/{sid}").json()["state"]
        assert (
            state["runs"][0]["shuffle_partitions"] == 8 and state["runs"][0]["status"] == "running"
        )
        # One run at a time.
        assert (
            client.post(f"/experience-sessions/{sid}/runs", json={"code": "x"}).status_code == 409
        )

    @pytest.mark.parametrize(
        "payload", [{"code": ""}, {"code": "x" * 20_001}, {"code": "x", "shuffle_partitions": 999}]
    )
    def test_inputs_are_bounded(self, client, db, payload) -> None:
        job = spark_job(db)
        sid = client.post(f"/jobs/{job}/experiences/spark:transform/sessions", json={}).json()["id"]
        ready(db, sid)
        assert client.post(f"/experience-sessions/{sid}/runs", json=payload).status_code == 422


class TestTheMiniLab:
    def test_it_answers_from_its_table_and_says_it_is_simulated(self, client, db) -> None:
        from repolab.builds.minilab import MiniLab, MiniLabEntry
        from repolab.evidence.models import CodeReference

        job = investigated(db, airflow_manifest())
        listing = client.get(f"/jobs/{job}/experience-list").json()
        assert listing["fallback"]["id"] == "minilab:lab"
        sid = client.post(f"/jobs/{job}/experiences/minilab:lab/sessions", json={}).json()["id"]
        lab = MiniLab(
            repo_url="https://github.com/apache/airflow",
            entries=[
                MiniLabEntry(
                    command="airflow dags list",
                    concept="DAGs",
                    explanation="Lists the DAGs it found.",
                    code_references=[CodeReference(path="airflow/cli/commands/dag_command.py")],
                )
            ],
        )
        with db() as session:
            _, state = store.load_session(session, sid)
            state.minilab = lab.model_dump(mode="json")
            store.save_state(session, sid, state, status=store.READY)
            session.commit()
        answer = client.post(
            f"/experience-sessions/{sid}/minilab", json={"command": "airflow dags list"}
        ).json()
        assert answer["simulated"] is True and "SIMULATED" in answer["label"]
        unknown = client.post(
            f"/experience-sessions/{sid}/minilab", json={"command": "rm -rf /"}
        ).json()
        assert unknown["supported"] is False
        assert len(client.get(f"/experience-sessions/{sid}").json()["state"]["transcript"]) == 2


class TestAnExperimentOnTheSession:
    def test_a_ready_spark_session_is_an_experiment_backend(self) -> None:
        decision = asyncio.run(
            triage(
                "What happens if I change the shuffle partitions?",
                manifest=None,
                spark_session="s1",
            )
        )
        assert decision.route is Route.EXPERIMENT and decision.backend is ExperimentBackend.SPARK
        assert decision.experience_session == "s1"

    def test_the_experiment_targets_the_learners_last_good_run(self, client, db) -> None:
        from repolab.db.models import ExperimentRecord
        from worker.experiment_tasks import _experience_target

        job = spark_job(db)
        sid = client.post(f"/jobs/{job}/experiences/spark:transform/sessions", json={}).json()["id"]
        ready(db, sid)
        created = client.post(
            "/experiments",
            json={
                "repo_url": "https://github.com/apache/spark",
                "question": "What happens with 8 partitions?",
                "experience_session_id": sid,
            },
        )
        assert created.status_code == 202
        experiment_id = created.json()["experiment_id"]
        with db() as session:
            assert session.get(ExperimentRecord, experiment_id).triage["experience_session"] == sid
            assert _experience_target(session, experiment_id) is None  # nothing has run yet
            _, state = store.load_session(session, sid)
            state.runs.append(
                store.RunRecord(
                    id="r1",
                    status="done",
                    code="result = df.groupBy('country').count()",
                    shuffle_partitions=4,
                )
            )
            store.save_state(session, sid, state)
            session.commit()
            target = _experience_target(session, experiment_id)
        assert target is not None
        assert target.code.startswith("result = df.groupBy") and target.baseline == 4
        assert target.image == "apache/spark:3.5.1-python3"
