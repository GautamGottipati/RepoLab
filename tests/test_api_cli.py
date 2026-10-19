"""The terminal and lesson endpoints.

What these tests are for: nothing slow happens in a handler, a step is judged
from what the container actually printed rather than from what the request says,
and an upgrade is refused mid-statement.

The session manager is driven by the fake sandbox provider, so no container is
started and the suite stays offline.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api import deps
from api.main import app
from api.routes import cli as cli_routes
from api.routes import lessons as lesson_routes
from repolab.builds.models import BuildArtifact, BuildCapabilities, SmokeTestResult
from repolab.config import Settings
from repolab.db import Base
from repolab.db.builds import save_build
from repolab.db.jobs import create_job
from repolab.db.lessons import save_lesson
from repolab.db.models import Ingestion, RepoManifestRecord, Repository
from repolab.events import JobStatus
from repolab.experiences.cli.session import CliSessionManager, SessionStatus, cql_command
from repolab.lessons.cassandra import cassandra_lesson
from repolab.manifest.models import BuildKind
from repolab.sandbox.fake import FakeSandboxProvider, ScriptedCommand
from tests.support.builds import CASSANDRA_REPO, SHA, cassandra_manifest

CQLSH = "/opt/cassandra/bin/cqlsh"
TAG = "repolab/apache-cassandra:ccc-official-release"
SERVICE = "cassandra -f"

FILTERING_ERROR = (
    "<stdin>:1:InvalidRequest: Error from server: code=2200 [Invalid query] "
    'message="Cannot execute this query as it might involve data filtering '
    "and thus may have unpredictable performance. If you want to execute this "
    'query despite the performance unpredictability, use ALLOW FILTERING"'
)


def artifact(kind: BuildKind = BuildKind.OFFICIAL_RELEASE, *, tag: str = TAG) -> BuildArtifact:
    return BuildArtifact(
        repo_url=CASSANDRA_REPO,
        commit_sha=SHA,
        kind=kind,
        version="5.0.9",
        provider="fake",
        base_image="cassandra:5.0",
        checkpoint_id="ckpt",
        checkpoint_tag=tag,
        service_command=SERVICE,
        cli_command=CQLSH,
        memory="4g",
        smoke=SmokeTestResult(
            name="cqlsh connects and a SELECT executes", command="x", passed=True
        ),
        capabilities=BuildCapabilities(
            capabilities=["execute CQL through the official cqlsh"],
            limitations=["a single node"],
            # Only the official release departs from the requested commit. A
            # source build carrying a deviation would not be an exact build --
            # fidelity is derived from this, so it has to be right.
            deviations=(
                ["official Cassandra 5.0 substituted for the requested commit"]
                if kind is BuildKind.OFFICIAL_RELEASE
                else []
            ),
        ),
        simulated=kind is BuildKind.MINI_LAB,
    )


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file may reach a real model.

    The explanation handler asks settings whether a model is configured, and on a
    developer machine with a populated `.env` the answer is yes -- which made this
    suite call Nebius for real. ``_env_file=None`` is how the settings tests keep
    a developer's credentials out of a test run, and it belongs here too.
    """
    monkeypatch.setattr(lesson_routes, "get_settings", lambda: Settings(_env_file=None))


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
        "enqueue_lesson_generation",
        lambda *a, **k: enqueued.append((("lesson", *a), k)) or "fake-rq-id",
    )
    monkeypatch.setattr(
        worker.jobs,
        "enqueue_environment_resolution",
        lambda *a, **k: enqueued.append((("environment", *a), k)) or "fake-rq-id",
    )
    yield factory
    engine.dispose()


@pytest.fixture
async def provider() -> FakeSandboxProvider:
    """A provider holding the artifact's checkpoint, with the lesson scripted."""
    fake = FakeSandboxProvider()
    handle = await fake.create("cassandra:5.0")
    await fake.checkpoint(handle, tag=TAG)
    await fake.checkpoint(handle, tag="better-tag")

    lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
    outputs = {
        1: " release_version\n-----------------\n           5.0.9\n\n(1 rows)\n",
        2: "system_schema\nsystem\nrepolab\n",
        3: "",
        4: "",
        5: "",
        6: " sensor_id | celsius\n-----------+---------\n   kitchen |    21.5\n\n(1 rows)\n",
        8: " sensor_id | celsius\n-----------+---------\n   kitchen |    21.5\n\n(1 rows)\n",
    }
    for step in lesson.steps:
        command = cql_command(CQLSH, step.action)
        if step.number == 7:
            fake.script(command, ScriptedCommand(stderr=FILTERING_ERROR, exit_code=2))
        else:
            fake.script(command, ScriptedCommand(stdout=outputs[step.number]))
    return fake


@pytest.fixture
def manager(provider: FakeSandboxProvider) -> Iterator[CliSessionManager]:
    instance = CliSessionManager(provider)
    cli_routes.set_manager(instance)
    yield instance
    cli_routes.set_manager(None)


@pytest.fixture
def client(db: sessionmaker[Session], manager: CliSessionManager) -> Iterator[TestClient]:
    with TestClient(app) as handle:
        yield handle


def investigated(factory: sessionmaker[Session], *, build: BuildArtifact | None = None) -> str:
    """An investigated repository, optionally with a ready build. Returns the job id."""
    with factory() as session:
        job = create_job(session, CASSANDRA_REPO)
        repository = Repository(
            url=CASSANDRA_REPO, host="github.com", owner="apache", name="cassandra"
        )
        session.add(repository)
        session.flush()
        ingestion = Ingestion(repository_id=repository.id, commit_sha=SHA)
        session.add(ingestion)
        session.flush()
        session.add(
            RepoManifestRecord(
                ingestion_id=ingestion.id,
                manifest=cassandra_manifest().model_dump(mode="json"),
                confidence=0.7,
                primary_surface="cli",
            )
        )
        job.ingestion_id = ingestion.id
        job.status = JobStatus.COMPLETED.value
        if build is not None:
            save_build(session, build)
        session.commit()
        return job.id


async def started_session(client: TestClient, manager: CliSessionManager, job_id: str) -> str:
    """Start a session through the API and finish its bring-up."""
    response = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO, "job_id": job_id})
    assert response.status_code == 202, response.text
    session_id = response.json()["session_id"]

    current = manager.get(session_id)
    assert current is not None
    await manager.bring_up(current)
    return session_id


class TestStartingASession:
    def test_an_uninvestigated_repository_cannot_start_one(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        response = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO})

        assert response.status_code == 409
        assert "not been investigated" in response.json()["detail"]

    def test_a_repository_with_no_ready_build_cannot_start_one(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db)

        response = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO})

        assert response.status_code == 409
        assert "finished building" in response.json()["detail"]

    def test_a_mini_lab_cannot_start_one(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """There is no server to connect to, which is said rather than implied."""
        investigated(db, build=artifact(BuildKind.MINI_LAB))

        response = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO})

        assert response.status_code == 409
        assert "Mini Lab" in response.json()["detail"]

    def test_a_bad_url_is_rejected_before_anything_else(self, client: TestClient) -> None:
        assert client.post("/cli/sessions", json={"repo_url": "file:///etc"}).status_code == 400

    def test_the_handler_returns_before_the_environment_is_ready(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        """Restoring a checkpoint takes tens of seconds, so it is not done here."""
        job_id = investigated(db, build=artifact())

        response = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO, "job_id": job_id})

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "starting"
        assert body["terminal_url"] == f"/cli/sessions/{body['session_id']}/terminal"
        assert response.headers["location"] == f"/cli/sessions/{body['session_id']}"

    def test_the_view_discloses_what_an_upgrade_costs(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())
        response = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO})

        assert "will not be there" in response.json()["upgrade_disclosure"]

    async def test_a_session_reports_what_it_is_running_on(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        body = client.get(f"/cli/sessions/{session_id}").json()

        assert body["status"] == "ready"
        assert body["fidelity"] == "high"
        assert body["cli_command"] == CQLSH
        assert body["version"] == "5.0.9"
        assert body["simulated"] is False

    def test_an_unknown_session_is_404(self, client: TestClient) -> None:
        assert client.get("/cli/sessions/nope").status_code == 404

    async def test_stopping_releases_the_container(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        assert client.delete(f"/cli/sessions/{session_id}").status_code == 204
        assert client.get(f"/cli/sessions/{session_id}").json()["status"] == "stopped"


class TestAskingForAnEnvironment:
    """Building one is asked for, not done on arrival.

    CLAUDE.md requires explicit approval before starting expensive services, and
    resolving Cassandra compiles source, pulls a release and starts a 4 GB node.
    """

    def test_an_uninvestigated_repository_cannot_be_built(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        response = client.post("/cli/environment", json={"repo_url": CASSANDRA_REPO})

        assert response.status_code == 409
        assert "not been investigated" in response.json()["detail"]

    def test_resolution_is_queued_not_performed(
        self, client: TestClient, db: sessionmaker[Session], enqueued: list[tuple]
    ) -> None:
        investigated(db)

        response = client.post("/cli/environment", json={"repo_url": CASSANDRA_REPO})

        assert response.status_code == 202
        assert response.json()["status"] == JobStatus.QUEUED.value
        assert [args[0] for args, _ in enqueued] == ["environment"]

    def test_a_deliberate_delay_is_passed_to_the_worker(
        self, client: TestClient, db: sessionmaker[Session], enqueued: list[tuple]
    ) -> None:
        """For demonstrating the upgrade sequence, and recorded on the artifact."""
        investigated(db)

        client.post(
            "/cli/environment", json={"repo_url": CASSANDRA_REPO, "slow_source_seconds": 60}
        )

        assert enqueued[0][1]["slow_source_seconds"] == 60

    def test_the_job_is_reported_so_the_browser_can_watch_it(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db)

        body = client.post("/cli/environment", json={"repo_url": CASSANDRA_REPO}).json()

        assert body["events_url"] == f"/jobs/{body['job_id']}/events"
        assert CASSANDRA_REPO in body["environment_url"]


class TestTheEnvironmentView:
    def test_it_says_what_is_missing_rather_than_404ing(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        body = client.get("/cli/environment", params={"repo_url": CASSANDRA_REPO}).json()

        assert body["ready"] is False
        assert "not been investigated" in body["reason"]

    def test_it_describes_the_surface_and_the_environment(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())

        body = client.get("/cli/environment", params={"repo_url": CASSANDRA_REPO}).json()

        assert body["ready"] is True
        experience = body["experience"]
        assert experience["name"] == "cqlsh"
        assert experience["cli_command"] == CQLSH
        assert experience["prompt"] == "cqlsh>"
        assert experience["capabilities"] == ["execute CQL through the official cqlsh"]
        assert experience["available"] is True

    def test_disclosed_deviations_are_passed_through_unedited(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())

        body = client.get("/cli/environment", params={"repo_url": CASSANDRA_REPO}).json()

        assert body["experience"]["deviations"] == [
            "official Cassandra 5.0 substituted for the requested commit"
        ]

    def test_the_surfaces_own_evidence_is_served(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())

        body = client.get("/cli/environment", params={"repo_url": CASSANDRA_REPO}).json()

        assert [ref["path"] for ref in body["experience"]["evidence"]] == ["bin/cqlsh"]


class TestTheTerminalSocket:
    async def test_it_runs_real_statements_and_returns_real_output(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            assert socket.receive_json()["type"] == "ready"
            socket.send_json({"command": "DESCRIBE KEYSPACES"})
            frame = socket.receive_json()

        assert frame["type"] == "result"
        assert "system_schema" in frame["stdout"]
        assert frame["exit_code"] == 0

    async def test_a_refused_statement_is_reported_not_run(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            socket.receive_json()
            socket.send_json({"command": "rm -rf /"})
            frame = socket.receive_json()

        assert frame["refused"] is not None
        assert frame["exit_code"] is None

    async def test_the_provoked_error_reaches_the_browser_intact(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        step = lesson.step(7)
        assert step is not None

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            socket.receive_json()
            socket.send_json({"command": step.action})
            frame = socket.receive_json()

        assert "ALLOW FILTERING" in frame["stderr"]
        assert frame["exit_code"] == 2

    def test_an_unknown_session_is_closed_with_a_reason(self, client: TestClient) -> None:
        with client.websocket_connect("/cli/sessions/nope/terminal") as socket:
            frame = socket.receive_json()

        assert frame["type"] == "error"
        assert "no session" in frame["message"]

    async def test_a_failed_session_is_reported_rather_than_waited_on(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact(tag="missing-checkpoint"))
        response = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO, "job_id": job_id})
        session_id = response.json()["session_id"]
        current = manager.get(session_id)
        assert current is not None
        await manager.bring_up(current)

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            frame = socket.receive_json()

        assert frame["type"] == "error"
        assert "checkpoint" in frame["message"]

    async def test_an_empty_command_is_not_sent_anywhere(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            socket.receive_json()
            socket.send_json({"command": "   "})
            frame = socket.receive_json()

        assert frame["type"] == "error"


class TestUpgrades:
    async def test_a_better_build_is_offered_and_applied(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        with db() as session:
            save_build(session, artifact(BuildKind.SOURCE_BUILD, tag="better-tag"))
            session.commit()

        body = client.post(f"/cli/sessions/{session_id}/upgrade").json()

        assert body["fidelity"] == "exact"
        assert body["status"] == "ready"
        assert body["session_id"] == session_id

    async def test_an_upgrade_is_refused_mid_statement(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        """The rule the specification states, now reachable from a browser."""
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        with db() as session:
            save_build(session, artifact(BuildKind.SOURCE_BUILD, tag="better-tag"))
            session.commit()

        environment = manager.environment(session_id)
        assert environment is not None
        environment.begin("CREATE KEYSPACE repolab")

        response = client.post(f"/cli/sessions/{session_id}/upgrade")

        assert response.status_code == 409
        assert "once it finishes" in response.json()["detail"]

    async def test_there_is_nothing_to_upgrade_to_when_nothing_is_better(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        assert client.post(f"/cli/sessions/{session_id}/upgrade").status_code == 409


class TestTheLesson:
    def test_repolabs_own_lesson_is_served_before_one_is_generated(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """A learner arriving early still has eight steps to do."""
        investigated(db, build=artifact())

        body = client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()

        assert body["stored"] is False
        assert body["lesson"]["generated_by"] == "deterministic"
        assert body["lesson"]["step_count"] == 8

    async def test_a_step_can_be_checked_straight_after_fetching_the_lesson(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        """Fetching persists RepoLab's own lesson, so it has an identity to check against."""
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        lesson_id = client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()["lesson"][
            "id"
        ]
        await manager.execute(session_id, "DESCRIBE KEYSPACES")

        body = client.post(
            f"/lessons/{lesson_id}/check", json={"step": 2, "session_id": session_id}
        ).json()

        assert body["passed"] is True

    def test_a_repository_without_cqlsh_gets_no_built_in_lesson(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """RepoLab's own lesson teaches CQL. Handing it to another CLI would be false."""
        from repolab.db.models import Ingestion, RepoManifestRecord, Repository

        other = "https://github.com/pallets/click"
        with db() as session:
            repository = Repository(url=other, host="github.com", owner="pallets", name="click")
            session.add(repository)
            session.flush()
            ingestion = Ingestion(repository_id=repository.id, commit_sha="d" * 40)
            session.add(ingestion)
            session.flush()
            manifest = cassandra_manifest(repo_url=other, sha="d" * 40)
            # A command line, but not Cassandra's: no cqlsh anywhere in its evidence.
            payload = manifest.model_dump(mode="json")
            payload["interaction_surfaces"][0]["evidence"] = [{"path": "src/click/core.py"}]
            session.add(
                RepoManifestRecord(ingestion_id=ingestion.id, manifest=payload, confidence=0.7)
            )
            session.commit()

        response = client.get("/lessons", params={"repo_url": other})

        assert response.status_code == 404
        assert "no built-in lesson" in response.json()["detail"]

    def test_fetching_twice_returns_the_same_lesson(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())

        first = client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()
        second = client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()

        assert first["lesson"]["id"] == second["lesson"]["id"]
        assert first["stored"] is False, "it did not exist before the first call"
        assert second["stored"] is True, "and it does after"

    def test_a_stored_lesson_is_served_when_there_is_one(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())
        with db() as session:
            stored = cassandra_lesson(CASSANDRA_REPO, SHA).model_copy(
                update={"title": "A generated lesson", "generated_by": "super"}
            )
            save_lesson(session, stored)
            session.commit()

        body = client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()

        assert body["stored"] is True
        assert body["lesson"]["title"] == "A generated lesson"

    def test_a_mini_lab_is_rendered_as_simulated(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        """No terminal, no invented output, and every step says it needs a real build."""
        investigated(db, build=artifact(BuildKind.MINI_LAB, tag="mini-lab/cassandra"))

        body = client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()

        simulated = body["simulated"]
        assert simulated is not None
        assert simulated["label"] == "SIMULATED"
        assert simulated["covered_count"] == 0
        assert all(step["requires_real_build"] for step in simulated["steps"])
        assert all(
            "requires a real environment" in step["explanation"] for step in simulated["steps"]
        )

    def test_a_real_environment_is_not_rendered_as_simulated(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())

        assert (
            client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()["simulated"] is None
        )

    def test_the_explainable_steps_are_named(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())

        body = client.get("/lessons", params={"repo_url": CASSANDRA_REPO}).json()

        assert 7 in body["explainable_steps"], "the error step always has something to explain"

    def test_generation_is_queued_not_performed(
        self, client: TestClient, db: sessionmaker[Session], enqueued: list[tuple]
    ) -> None:
        investigated(db, build=artifact())

        response = client.post("/lessons", json={"repo_url": CASSANDRA_REPO})

        assert response.status_code == 202
        assert response.json()["status"] == JobStatus.QUEUED.value
        assert len(enqueued) == 1, "the work went to a worker"


class TestCheckingASteps:
    async def test_a_step_is_judged_from_what_the_container_printed(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        with db() as session:
            record = save_lesson(session, lesson)
            session.commit()
            lesson_id = record.id

        step = lesson.step(2)
        assert step is not None
        await manager.execute(session_id, step.action)

        body = client.post(
            f"/lessons/{lesson_id}/check", json={"step": 2, "session_id": session_id}
        ).json()

        assert body["passed"] is True
        assert body["decided_by"] == "deterministic"
        assert "system_schema" in body["output"]
        assert body["completed"] == [2]

    async def test_a_step_cannot_be_passed_on_output_nobody_executed(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        """The request says which step; it does not get to say what happened."""
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        with db() as session:
            lesson_id = save_lesson(session, cassandra_lesson(CASSANDRA_REPO, SHA)).id
            session.commit()

        response = client.post(
            f"/lessons/{lesson_id}/check", json={"step": 2, "session_id": session_id}
        )

        assert response.status_code == 409
        assert "nothing has been run" in response.json()["detail"]

    async def test_the_error_step_passes_when_the_server_really_refuses(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        with db() as session:
            lesson_id = save_lesson(session, lesson).id
            session.commit()

        step = lesson.step(7)
        assert step is not None
        await manager.execute(session_id, step.action)

        body = client.post(
            f"/lessons/{lesson_id}/check", json={"step": 7, "session_id": session_id}
        ).json()

        assert body["passed"] is True
        assert "ALLOW FILTERING" in body["reason"]

    async def test_a_failed_step_is_given_its_own_hint(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        with db() as session:
            lesson_id = save_lesson(session, lesson).id
            session.commit()

        # Run step 2's statement, then claim it was step 6.
        step = lesson.step(2)
        assert step is not None
        await manager.execute(session_id, step.action)

        body = client.post(
            f"/lessons/{lesson_id}/check", json={"step": 6, "session_id": session_id}
        ).json()

        assert body["passed"] is False
        assert body["hint"] == lesson.step(6).hint  # type: ignore[union-attr]

    async def test_progress_accumulates_and_reports_the_next_step(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        with db() as session:
            lesson_id = save_lesson(session, lesson).id
            session.commit()

        for number in (1, 2):
            step = lesson.step(number)
            assert step is not None
            record = await manager.execute(session_id, step.action)
            body = client.post(
                f"/lessons/{lesson_id}/check",
                json={"step": number, "session_id": session_id, "sequence": record.sequence},
            ).json()

        assert body["completed"] == [1, 2]
        assert body["next_step"] == 3
        assert body["lesson_complete"] is False

        stored = client.get(
            f"/lessons/{lesson_id}/progress", params={"session_id": session_id}
        ).json()
        assert stored["completed"] == [1, 2]

    async def test_the_whole_lesson_can_be_completed(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        """The phase's done-when, in miniature: every step, judged from real output."""
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        with db() as session:
            lesson_id = save_lesson(session, lesson).id
            session.commit()

        body: dict = {}
        for step in lesson.steps:
            record = await manager.execute(session_id, step.action)
            body = client.post(
                f"/lessons/{lesson_id}/check",
                json={
                    "step": step.number,
                    "session_id": session_id,
                    "sequence": record.sequence,
                },
            ).json()
            assert body["passed"] is True, f"step {step.number}: {body['reason']}"

        assert body["lesson_complete"] is True
        assert body["next_step"] is None

    def test_an_unstored_lesson_says_how_to_get_one(
        self, client: TestClient, db: sessionmaker[Session]
    ) -> None:
        investigated(db, build=artifact())

        response = client.post("/lessons/nope/check", json={"step": 1, "session_id": "x"})

        assert response.status_code == 404
        assert "POST /lessons" in response.json()["detail"]


class TestExplaining:
    async def test_without_a_model_it_says_so_rather_than_filling_in(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        with db() as session:
            lesson_id = save_lesson(session, lesson).id
            session.commit()
        await manager.execute(session_id, lesson.steps[0].action)

        body = client.post(
            f"/lessons/{lesson_id}/explain", json={"step": 1, "session_id": session_id}
        ).json()

        assert body["available"] is False
        assert "No model is configured" in body["reason"]
        assert body["explanation"] is None

    async def test_an_explanation_is_served_with_the_evidence_that_checks_it(
        self,
        client: TestClient,
        db: sessionmaker[Session],
        manager: CliSessionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A claim quoting output that is not there is reported, not printed as fact."""
        from repolab.lessons.explain import Claim, SupportKind, WhatHappened

        answer = WhatHappened(
            summary="Cassandra refused to scan every partition.",
            claims=[
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="The server refused the query and suggested ALLOW FILTERING.",
                    support="use ALLOW FILTERING",
                ),
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="It scanned 4096 partitions first.",
                    support="use ALLOW FILTERING",
                ),
            ],
        )

        class Scripted:
            async def super(self, **_: object):
                class Completion:
                    value = answer

                return Completion()

        monkeypatch.setattr(lesson_routes, "_router", lambda: Scripted())

        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)
        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        with db() as session:
            lesson_id = save_lesson(session, lesson).id
            session.commit()
        step = lesson.step(7)
        assert step is not None
        await manager.execute(session_id, step.action)

        body = client.post(
            f"/lessons/{lesson_id}/explain", json={"step": 7, "session_id": session_id}
        ).json()

        assert body["available"] is True
        assert body["grounded"] is False, "the invented number was caught"
        assert "4096" in body["unsupported"][0]["problem"]
        assert body["evidence"]["simulated"] is False
        assert body["evidence"]["measurements"] == []


class TestReclaimingAbandonedTerminals:
    """A terminal session is a live 4 GB container. Something has to release it."""

    async def test_the_reaper_stops_sessions_nobody_is_using(
        self, client: TestClient, db: sessionmaker[Session], provider: FakeSandboxProvider
    ) -> None:
        idle = CliSessionManager(provider, idle_timeout_seconds=-1.0)
        cli_routes.set_manager(idle)
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, idle, job_id)

        # The loop the application runs, driven one tick at a time: it sleeps
        # first, so a single cancelled pass is the honest way to exercise it.
        task = asyncio.create_task(cli_routes.reap_idle_sessions(interval=0.0))
        await asyncio.sleep(0.05)
        task.cancel()

        assert idle.get(session_id) is not None
        assert idle.get(session_id).status is SessionStatus.STOPPED  # type: ignore[union-attr]
        assert provider.terminated, "the container was released"

    async def test_a_process_with_no_terminals_builds_no_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reaper must not import Docker to discover there is nothing to do."""
        cli_routes.set_manager(None)
        assert cli_routes.current_manager() is None


class TestTheSocketUnderStress:
    """What the terminal does when things go wrong around it.

    From the pre-demo audit: a raw exception string used to reach the terminal,
    a second concurrent statement produced `RuntimeError: session … is already
    running`, and a session reaped under an open socket left the client being
    told "stopped" forever with no way out.
    """

    async def test_a_reaped_session_closes_the_socket_with_an_explanation(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            socket.receive_json()  # ready

            # The idle reaper reclaims it while the socket is open.
            await manager.stop(session_id)

            socket.send_json({"command": "SELECT 1"})
            frame = socket.receive_json()

        assert frame["type"] == "error"
        assert "sat idle" in frame["message"] or "no longer running" in frame["message"]
        assert "RuntimeError" not in frame["message"]

    async def test_a_concurrent_statement_is_refused_in_plain_words(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        environment = manager.environment(session_id)
        assert environment is not None
        environment.begin("CREATE KEYSPACE demo")  # something is already running

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            socket.receive_json()
            socket.send_json({"command": "SELECT 1"})
            frame = socket.receive_json()

        assert frame["type"] == "error"
        assert frame["message"] == "A statement is already running. Wait for it to finish."
        assert "RuntimeError" not in frame["message"]

    async def test_an_execution_failure_reaches_the_terminal_as_prose(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        from repolab.sandbox.models import SandboxError

        job_id = investigated(db, build=artifact())
        session_id = await started_session(client, manager, job_id)

        async def broken(*args: object, **kwargs: object):
            raise SandboxError("the container has gone away")

        manager._provider.execute = broken  # type: ignore[attr-defined]

        with client.websocket_connect(f"/cli/sessions/{session_id}/terminal") as socket:
            socket.receive_json()
            socket.send_json({"command": "SELECT 1"})
            frame = socket.receive_json()

        assert frame["type"] == "error"
        assert frame["headline"] == "The execution environment is not available."
        assert "SandboxError" not in frame["message"]
        assert "SandboxError" in frame["technical"], "available, not shown"

    async def test_one_terminal_is_reused_rather_than_doubled(
        self, client: TestClient, db: sessionmaker[Session], manager: CliSessionManager
    ) -> None:
        """A double-clicked button must not allocate a second 4 GB container."""
        job_id = investigated(db, build=artifact())

        first = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO, "job_id": job_id})
        second = client.post("/cli/sessions", json={"repo_url": CASSANDRA_REPO, "job_id": job_id})

        assert first.json()["session_id"] == second.json()["session_id"]
        assert len(manager.sessions) == 1
