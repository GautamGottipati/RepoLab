"""`GET /metrics` — the KPI dashboard's data.

The screen renders whatever this returns, so what matters here is that the
serialised form keeps the distinction the metric model enforces: a measured
number arrives as a number, and everything else arrives as a status and a
reason, never as a zero.
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
from repolab.analytics.events import AnalyticsEventType
from repolab.analytics.metrics import NORTH_STAR_LABEL
from repolab.db import Base
from repolab.db.models import AnalyticsEventRecord, ExperimentRecord, Job

REPO = "https://github.com/apache/cassandra"


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


def seed(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        session.add(Job(id="job-1", kind="investigate", status="completed", repo_url=REPO))
        session.add(
            AnalyticsEventRecord(
                type=AnalyticsEventType.REPO_SUBMITTED.value, job_id="job-1", repo_url=REPO
            )
        )
        session.add(
            AnalyticsEventRecord(
                type=AnalyticsEventType.REPO_READY.value, job_id="job-1", repo_url=REPO
            )
        )
        session.add(
            ExperimentRecord(id="exp-1", question="why 200?", repo_url=REPO, status="completed")
        )
        session.commit()


class TestTheEndpoint:
    def test_an_empty_database_serves_a_dashboard_of_unknowns(self, client: TestClient) -> None:
        body = client.get("/metrics").json()

        assert body["learning"], "the sections exist even with nothing in them"
        assert body["operations"]
        assert body["measured_count"] == 0
        for group in body["learning"] + body["operations"]:
            for metric in group["metrics"]:
                assert metric["value"] is None
                assert metric["status"] in {"no_data", "not_instrumented"}

    def test_it_serves_both_sections(self, client: TestClient, db: sessionmaker[Session]) -> None:
        """The phase's done-when: learning activity *and* operational performance."""
        seed(db)

        body = client.get("/metrics").json()

        assert [group["title"] for group in body["learning"]] == [
            "Repositories",
            "Time to value",
            "Lessons",
            "Experiments",
        ]
        assert [group["title"] for group in body["operations"]] == [
            "Agent performance",
            "Tool layer",
            "Model usage",
            "Compute and cost",
        ]

    def test_measured_metrics_arrive_with_values_and_definitions(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        seed(db)

        body = client.get("/metrics").json()

        metrics = {
            metric["key"]: metric
            for group in body["learning"] + body["operations"]
            for metric in group["metrics"]
        }
        assert metrics["repositories_analyzed"]["value"] == 1
        assert metrics["experiments_proposed"]["value"] == 1
        for metric in metrics.values():
            assert metric["definition"]
            assert metric["source"]

    def test_the_north_star_is_labelled_and_never_numbered(self, client: TestClient) -> None:
        body = client.get("/metrics").json()

        north_star = body["north_star"]
        assert north_star["label"] == NORTH_STAR_LABEL
        assert north_star["status"] == "north_star"
        assert north_star["value"] is None
        assert "longitudinal" in north_star["detail"]

    def test_the_dataset_provenance_is_served(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        seed(db)

        body = client.get("/metrics").json()

        assert body["provenance"]["analytics_events"] == 2
        assert body["provenance"]["repositories"] == 1

    def test_challenges_are_declared_uninstrumented_over_http(self, client: TestClient) -> None:
        body = client.get("/metrics").json()

        challenge = next(
            metric
            for group in body["learning"]
            for metric in group["metrics"]
            if metric["key"] == "challenge_success_rate"
        )
        assert challenge["status"] == "not_instrumented"
        assert challenge["value"] is None
