"""The experiment API, and the approval gate it enforces.

The gate is server-side. A client cannot run variants by asking nicely: the only
path to execution is an approval of a plan that was actually presented, and the
test that matters most is that a second approval does not queue a second run.

Nothing slow happens in a handler, so the queue is faked and the tests assert
what was enqueued rather than waiting for it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api import deps
from api.main import app
from repolab.db import Base
from repolab.db.experiments import save_experiment
from repolab.db.jobs import mark_finished, read_events_after
from repolab.db.models import AnalyticsEventRecord, Job
from repolab.events import EventType, JobStatus
from repolab.experiments.experiment import Experiment
from repolab.experiments.models import ExperimentStatus
from tests.support.experiments import plan, variant_run

REPO = "https://github.com/AlexIoannides/pyspark-example-project"
QUESTION = "Why does this job use 200 shuffle partitions?"


@pytest.fixture
def enqueued() -> dict[str, list[tuple]]:
    return {"propose": [], "run": []}


@pytest.fixture
def db(
    monkeypatch: pytest.MonkeyPatch, enqueued: dict[str, list[tuple]]
) -> Iterator[sessionmaker[Session]]:
    """An in-memory database, with the queue recorded instead of used."""
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
        "enqueue_experiment_proposal",
        lambda *a, **k: enqueued["propose"].append((a, k)) or "fake-rq-id",
    )
    monkeypatch.setattr(
        worker.jobs,
        "enqueue_experiment_run",
        lambda *a, **k: enqueued["run"].append((a, k)) or "fake-rq-id",
    )
    yield factory
    engine.dispose()


@pytest.fixture
def client(db: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(app) as handle:
        yield handle


def stored_experiment(
    factory: sessionmaker[Session],
    *,
    status: ExperimentStatus = ExperimentStatus.AWAITING_APPROVAL,
    job_id: str | None = None,
    runs: list | None = None,
    explanation: dict | None = None,
) -> Experiment:
    """An experiment already in the database, in a chosen state."""
    from repolab.db.jobs import create_job

    experiment = Experiment(
        question=QUESTION,
        repo_url=REPO,
        commit_sha="c" * 40,
        plan=plan(values=[50, 200], repeats=2),
        runs=runs or [],
    )
    experiment.status = status
    with factory() as session:
        if job_id is None:
            job = create_job(session, REPO, kind="experiment_design")
            session.flush()
            experiment.job_id = job.id
        else:
            experiment.job_id = job_id
        record = save_experiment(session, experiment)
        if explanation is not None:
            record.explanation = explanation
        session.commit()
    return experiment


class TestAsking:
    def test_a_question_is_accepted_and_queued(self, client: TestClient, enqueued: dict) -> None:
        response = client.post("/experiments", json={"repo_url": REPO, "question": QUESTION})

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "proposed"
        assert body["experiment_id"] and body["job_id"]
        assert body["events_url"].endswith("/events")
        assert body["approve_url"].endswith("/approve")
        assert response.headers["Location"] == f"/experiments/{body['experiment_id']}"

        assert len(enqueued["propose"]) == 1
        assert enqueued["run"] == [], "nothing may be queued for execution yet"

    def test_the_experiment_is_pollable_immediately(self, client: TestClient) -> None:
        """The id is allocated before the design starts, so a client can watch."""
        created = client.post("/experiments", json={"repo_url": REPO, "question": QUESTION}).json()

        state = client.get(f"/experiments/{created['experiment_id']}")
        assert state.status_code == 200
        assert state.json()["status"] == "proposed"
        assert state.json()["awaiting_approval"] is False

    def test_an_invalid_repository_url_is_refused(self, client: TestClient) -> None:
        response = client.post("/experiments", json={"repo_url": "not-a-url", "question": QUESTION})
        assert response.status_code == 400

    def test_an_internal_host_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/experiments", json={"repo_url": "https://localhost/a/b", "question": QUESTION}
        )
        assert response.status_code == 400

    def test_too_short_a_question_is_refused(self, client: TestClient) -> None:
        response = client.post("/experiments", json={"repo_url": REPO, "question": "why"})
        assert response.status_code == 422

    def test_concurrency_is_capped(self, client: TestClient) -> None:
        """Above the cap every timing would be contention, not measurement."""
        response = client.post(
            "/experiments",
            json={"repo_url": REPO, "question": QUESTION, "concurrency": 99},
        )
        assert response.status_code == 422

    def test_concurrency_is_passed_to_the_worker(self, client: TestClient, enqueued: dict) -> None:
        client.post(
            "/experiments",
            json={"repo_url": REPO, "question": QUESTION, "concurrency": 2, "rows": 1000},
        )
        _, kwargs = enqueued["propose"][0]
        assert kwargs["concurrency"] == 2
        assert kwargs["rows"] == 1000

    def test_the_question_is_recorded_as_a_product_event(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        client.post("/experiments", json={"repo_url": REPO, "question": QUESTION})
        with db() as session:
            types = {row.type for row in session.query(AnalyticsEventRecord).all()}
        assert "question_asked" in types


class TestState:
    def test_an_unknown_experiment_is_404(self, client: TestClient) -> None:
        assert client.get("/experiments/nope").status_code == 404

    def test_a_presented_plan_is_visible(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        experiment = stored_experiment(db)
        body = client.get(f"/experiments/{experiment.id}").json()

        assert body["awaiting_approval"] is True
        assert body["plan"]["values"] == [50, 200]
        assert body["plan"]["estimated_runs"] == 4
        assert body["reproducibility"] is None

    def test_runs_and_measurements_are_counted(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        experiment = stored_experiment(
            db,
            status=ExperimentStatus.COMPLETED,
            runs=[variant_run(value=200, repeat=1), variant_run(value=50, repeat=1)],
        )
        body = client.get(f"/experiments/{experiment.id}").json()

        assert body["variant_runs"] == 2
        assert body["measurements"] > 0

    def test_experiments_are_listed(self, client: TestClient, db: sessionmaker[Session]) -> None:
        stored_experiment(db)
        stored_experiment(db)
        body = client.get("/experiments", params={"repo_url": REPO}).json()
        assert len(body) == 2


class TestApproval:
    def test_approving_queues_the_run(
        self, client: TestClient, db: sessionmaker[Session], enqueued: dict
    ) -> None:
        experiment = stored_experiment(db)
        response = client.post(
            f"/experiments/{experiment.id}/approve", json={"approved_by": "tester"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["job_id"], "execution gets its own job"
        assert "4 runs" in body["message"]

        assert len(enqueued["run"]) == 1
        args, _ = enqueued["run"][0]
        assert args[1] == experiment.id

    def test_approving_records_who(self, client: TestClient, db: sessionmaker[Session]) -> None:
        experiment = stored_experiment(db)
        client.post(f"/experiments/{experiment.id}/approve", json={"approved_by": "tester"})

        state = client.get(f"/experiments/{experiment.id}").json()
        assert state["status"] == "approved"

    def test_approving_twice_is_refused_and_queues_nothing_more(
        self, client: TestClient, db: sessionmaker[Session], enqueued: dict
    ) -> None:
        """A second approval must not run the variants a second time."""
        experiment = stored_experiment(db)
        assert client.post(f"/experiments/{experiment.id}/approve", json={}).status_code == 200

        second = client.post(f"/experiments/{experiment.id}/approve", json={})
        assert second.status_code == 409
        assert "not awaiting approval" in second.json()["detail"]
        assert len(enqueued["run"]) == 1

    def test_approving_something_never_presented_is_refused(
        self, client: TestClient, db: sessionmaker[Session], enqueued: dict
    ) -> None:
        experiment = stored_experiment(db, status=ExperimentStatus.PROPOSED)
        response = client.post(f"/experiments/{experiment.id}/approve", json={})

        assert response.status_code == 409
        assert enqueued["run"] == []

    def test_approving_an_unknown_experiment_is_404(self, client: TestClient) -> None:
        assert client.post("/experiments/nope/approve", json={}).status_code == 404

    def test_approval_is_announced_on_the_event_stream(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        experiment = stored_experiment(db)
        body = client.post(f"/experiments/{experiment.id}/approve", json={}).json()

        with db() as session:
            events = read_events_after(session, body["job_id"], 0)
        assert [event.type for event in events] == [EventType.APPROVAL_RECEIVED]
        assert events[0].data["approved"] is True


class TestRejection:
    def test_rejecting_runs_nothing(
        self, client: TestClient, db: sessionmaker[Session], enqueued: dict
    ) -> None:
        experiment = stored_experiment(db)
        response = client.post(
            f"/experiments/{experiment.id}/reject", json={"approved_by": "tester"}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert enqueued["run"] == []

    def test_a_rejected_experiment_cannot_then_be_approved(
        self, client: TestClient, db: sessionmaker[Session], enqueued: dict
    ) -> None:
        experiment = stored_experiment(db)
        client.post(f"/experiments/{experiment.id}/reject", json={})

        assert client.post(f"/experiments/{experiment.id}/approve", json={}).status_code == 409
        assert enqueued["run"] == []

    def test_rejection_closes_the_event_stream(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """A client must not be left reconnecting to a stream that is finished."""
        experiment = stored_experiment(db)
        client.post(f"/experiments/{experiment.id}/reject", json={})

        with db() as session:
            job_id = session.get(Job, experiment.job_id)
            assert job_id is not None
            assert JobStatus(job_id.status).is_terminal


class TestExplanation:
    def test_it_is_404_until_the_experiment_has_run(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """There is no honest explanation before there are measurements."""
        experiment = stored_experiment(db)
        response = client.get(f"/experiments/{experiment.id}/explanation")

        assert response.status_code == 404
        assert "no explanation yet" in response.json()["detail"]

    def test_it_returns_the_claims_with_their_kinds(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        explanation = {
            "explanation": {
                "answer": "200 is the default.",
                "claims": [
                    {
                        "kind": "measured",
                        "statement": "193 partitions were empty.",
                        "support": "empty partitions",
                    }
                ],
                "caveats": [],
            },
            "unsupported": [],
            "checked_claims": 1,
            "grounded": True,
        }
        experiment = stored_experiment(
            db,
            status=ExperimentStatus.COMPLETED,
            runs=[variant_run(value=200, repeat=1)],
            explanation=explanation,
        )

        body = client.get(f"/experiments/{experiment.id}/explanation").json()
        assert body["question"] == QUESTION
        assert body["explanation"]["grounded"] is True
        assert body["explanation"]["explanation"]["claims"][0]["kind"] == "measured"


class TestEventStream:
    def test_it_replays_the_experiment_job_history(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        experiment = stored_experiment(db)
        from repolab.db.jobs import append_event
        from repolab.events import JobEvent

        with db() as session:
            for seq in range(1, 4):
                append_event(
                    session,
                    JobEvent(
                        job_id=experiment.job_id,
                        seq=seq,
                        type=EventType.EXPERIMENT_VARIANT_STARTED,
                        message=f"variant {seq}",
                    ),
                )
            mark_finished(session, experiment.job_id, status=JobStatus.COMPLETED)
            session.commit()

        response = client.get(f"/experiments/{experiment.id}/events", params={"follow": False})
        assert response.status_code == 200

        payloads = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [p["seq"] for p in payloads[:3]] == [1, 2, 3]
        assert payloads[0]["type"] == "experiment_variant_started"

    def test_an_unknown_experiment_is_404(self, client: TestClient) -> None:
        assert client.get("/experiments/nope/events").status_code == 404

    def test_an_experiment_without_a_job_is_404(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        experiment = stored_experiment(db, job_id="")
        with db() as session:
            from repolab.db.models import ExperimentRecord

            session.get(ExperimentRecord, experiment.id).job_id = None
            session.commit()

        response = client.get(f"/experiments/{experiment.id}/events")
        assert response.status_code == 404
        assert "no event stream" in response.json()["detail"]


class TestTriagedQuestions:
    """An answered question and a CLI experiment travel through the same endpoints."""

    def test_a_new_question_is_pending_until_triaged(self, client: TestClient) -> None:
        created = client.post(
            "/experiments", json={"repo_url": REPO, "question": "What is Apache Spark?"}
        ).json()

        state = client.get(f"/experiments/{created['experiment_id']}").json()
        assert state["kind"] == "pending"
        assert state["status"] == "proposed"

    def test_an_answered_question_carries_its_answer_and_nothing_to_approve(
        self, client: TestClient, db: sessionmaker[Session], enqueued: dict
    ) -> None:
        from repolab.db.experiments import save_answer, save_triage
        from repolab.questions.models import AnswerResult, QuestionKind, Route, Triage

        created = client.post(
            "/experiments", json={"repo_url": REPO, "question": "What is Apache Spark?"}
        ).json()
        experiment_id = created["experiment_id"]
        with db() as session:
            save_triage(
                session,
                experiment_id,
                Triage(
                    kind=QuestionKind.CONCEPTUAL,
                    reason="an idea",
                    decided_by="rules",
                    route=Route.ANSWER,
                ),
            )
            save_answer(
                session,
                experiment_id,
                AnswerResult(question="What is Apache Spark?", kind=QuestionKind.CONCEPTUAL),
            )
            session.commit()

        state = client.get(f"/experiments/{experiment_id}").json()
        assert state["status"] == "answered"
        assert state["kind"] == "answer"
        assert state["triage"]["kind"] == "conceptual"
        assert state["answer"]["question"] == "What is Apache Spark?"

        refused = client.post(f"/experiments/{experiment_id}/approve", json={})
        assert refused.status_code == 409
        assert enqueued["run"] == []

    def test_a_structured_answer_reaches_the_client_whole(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        from repolab.db.experiments import save_answer
        from repolab.questions.models import (
            AnswerResult,
            AnswerStep,
            AnswerVisual,
            ExplanationShape,
            GroundedAnswer,
            QuestionKind,
            VerifiedAnswer,
            VisualEdge,
            VisualNode,
        )

        created = client.post(
            "/experiments", json={"repo_url": REPO, "question": "How does the job read data?"}
        ).json()
        produced = GroundedAnswer(
            answer="It reads a CSV.",
            summary="The job reads its input with a reader.",
            claims=[],
            caveats=[],
            shape=ExplanationShape.FLOW,
            visual=AnswerVisual(
                nodes=[VisualNode(id="a", label="Job"), VisualNode(id="b", label="Reader")],
                edges=[VisualEdge(source="a", target="b", support="jobs/etl.py")],
            ),
            steps=[AnswerStep(title="Read", description="It reads.", sources=["jobs/etl.py"])],
        )
        with db() as session:
            save_answer(
                session,
                created["experiment_id"],
                AnswerResult(
                    question="How does the job read data?",
                    kind=QuestionKind.REPOSITORY,
                    shape=ExplanationShape.FLOW,
                    answer=VerifiedAnswer(answer=produced, not_established=["Reader → Disk"]),
                    follow_ups=["Where is the reader configured?"],
                ),
            )
            session.commit()

        answer = client.get(f"/experiments/{created['experiment_id']}").json()["answer"]
        assert answer["shape"] == "flow"
        assert answer["follow_ups"] == ["Where is the reader configured?"]
        assert answer["answer"]["not_established"] == ["Reader → Disk"]
        laid_out = answer["answer"]["answer"]
        assert laid_out["summary"] == "The job reads its input with a reader."
        assert laid_out["visual"]["edges"][0]["support"] == "jobs/etl.py"
        assert laid_out["steps"][0]["sources"] == ["jobs/etl.py"]

    def test_a_cli_experiment_is_approved_through_the_same_gate(
        self, client: TestClient, db: sessionmaker[Session], enqueued: dict
    ) -> None:
        from repolab.db.experiments import save_cli_experiment
        from repolab.db.jobs import create_job
        from repolab.experiments.cli.models import CliExperiment
        from repolab.experiments.cli.validation import validate_cli_plan
        from tests.test_cli_experiment import artifact, proposal

        experiment = CliExperiment(
            question="What happens if I filter on a non-key column?",
            repo_url="https://github.com/apache/cassandra",
            commit_sha="c" * 40,
            plan=validate_cli_plan(proposal(), artifact=artifact()).require(),
        )
        experiment.await_approval()
        with db() as session:
            experiment.job_id = create_job(session, experiment.repo_url).id
            save_cli_experiment(session, experiment)
            session.commit()

        state = client.get(f"/experiments/{experiment.id}").json()
        assert state["kind"] == "cli"
        assert state["awaiting_approval"]
        assert state["plan"]["variants"][0]["label"] == "filter on partition key"

        approved = client.post(f"/experiments/{experiment.id}/approve", json={})
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert len(enqueued["run"]) == 1

        again = client.post(f"/experiments/{experiment.id}/approve", json={})
        assert again.status_code == 409
        assert len(enqueued["run"]) == 1


CONTEXT: dict = {
    "node_id": "step:jobs:join",
    "label": "Join Customers",
    "category": "transform",
    "source_references": [{"path": "jobs/daily_sales.py", "start_line": 6}],
    "relationships": ["Read Orders -data_flow-> Join Customers"],
}


class TestArchitectureContext:
    """Ask Why from the Architecture page sends the component with the question."""

    def test_the_context_is_stored_and_survives_triage(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        from repolab.db.experiments import question_context, save_triage
        from repolab.questions.models import QuestionKind, Route, Triage

        created = client.post(
            "/experiments",
            json={"repo_url": REPO, "question": "Why join here?", "context": CONTEXT},
        ).json()
        experiment_id = created["experiment_id"]

        with db() as session:
            save_triage(
                session,
                experiment_id,
                Triage(
                    kind=QuestionKind.REPOSITORY, reason="x", decided_by="rules", route=Route.ANSWER
                ),
            )
            session.commit()
            focus = question_context(session, experiment_id)

        assert focus is not None and focus.label == "Join Customers"
        state = client.get(f"/experiments/{experiment_id}").json()
        assert state["triage"]["context"]["node_id"] == "step:jobs:join"

    def test_an_oversized_context_is_refused(self, client: TestClient) -> None:
        context = {**CONTEXT, "source_references": [{"path": f"f{i}.py"} for i in range(50)]}
        response = client.post(
            "/experiments",
            json={"repo_url": REPO, "question": "Why join here?", "context": context},
        )
        assert response.status_code == 422

    def test_the_level_and_subsystem_reach_the_answer(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        from repolab.db.experiments import question_context

        context = {
            **CONTEXT,
            "node_id": "concept:scheduler",
            "label": "Scheduler",
            "category": "concept",
            "level": "system",
            "subsystem": "Core engine",
            "directories": ["core/src/main/scala/org/example/engine/scheduler"],
        }
        created = client.post(
            "/experiments",
            json={"repo_url": REPO, "question": "What does it do?", "context": context},
        ).json()
        with db() as session:
            focus = question_context(session, created["experiment_id"])
        assert focus is not None and focus.level == "system"
        described = focus.describe()
        assert "the concept 'Scheduler'" in described
        assert "'Core engine'" in described
        assert "core/src/main/scala/org/example/engine/scheduler" in described
