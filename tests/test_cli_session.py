"""A learner's terminal session, against the fake provider.

What matters here is not that the session works but *what it refuses to do*: run
on a build that is not ready, run a statement the allowlist rejects, or change
the environment while a statement is in flight.
"""

from __future__ import annotations

import pytest

from repolab.builds.models import BuildArtifact, SmokeTestResult
from repolab.builds.session import UpgradeRefused
from repolab.experiences.cli.session import (
    CliSessionManager,
    SessionNotReady,
    SessionStatus,
    cql_command,
    keyspace_in,
)
from repolab.manifest.models import BuildKind
from repolab.sandbox.fake import FakeSandboxProvider, ScriptedCommand
from repolab.sandbox.models import SandboxError

REPO = "https://github.com/apache/cassandra"
CQLSH = "/opt/cassandra/bin/cqlsh"
SERVICE = "/usr/local/bin/docker-entrypoint.sh cassandra -f"
TAG = "repolab/apache-cassandra:abc-official-release"


def artifact(
    kind: BuildKind = BuildKind.OFFICIAL_RELEASE,
    *,
    ready: bool = True,
    cli: str | None = CQLSH,
    service: str | None = SERVICE,
) -> BuildArtifact:
    return BuildArtifact(
        repo_url=REPO,
        commit_sha="c" * 40,
        kind=kind,
        version="5.0.9",
        checkpoint_id="ckpt-1" if ready else None,
        checkpoint_tag=TAG if ready else None,
        service_command=service,
        cli_command=cli,
        memory="4g",
        smoke=SmokeTestResult(name="cqlsh connects", command="x", passed=ready),
        simulated=kind is BuildKind.MINI_LAB,
    )


async def provider_with_checkpoint(**scripted: ScriptedCommand) -> FakeSandboxProvider:
    """A provider holding the artifact's checkpoint, so it can be restored."""
    fake = FakeSandboxProvider(scripted=dict(scripted))
    handle = await fake.create("cassandra:5.0")
    await fake.checkpoint(handle, tag=TAG)
    return fake


class TestStarting:
    async def test_a_session_restores_the_checkpoint_as_a_server(self) -> None:
        """A checkpoint restored without its service command has nothing listening.

        Measured on the real thing in Phase 9: a server started from a build step
        dies with the shell that started it, so the artifact carries the command
        and the session must pass it on.
        """
        fake = await provider_with_checkpoint()
        session = await CliSessionManager(fake).start(artifact(), repo_url=REPO)

        assert session.status is SessionStatus.READY
        assert fake.restored == [(TAG, SERVICE, "4g")]

    async def test_readiness_is_proved_by_the_cli_answering(self) -> None:
        fake = await provider_with_checkpoint()
        await CliSessionManager(fake).start(artifact(), repo_url=REPO)

        probes = [command for _, command in fake.executed if "system.local" in command]
        assert probes, "the session must prove the CLI answers before claiming ready"

    async def test_a_build_that_is_not_ready_is_refused(self) -> None:
        fake = await provider_with_checkpoint()
        with pytest.raises(SandboxError, match="not ready"):
            CliSessionManager(fake).create(artifact(ready=False), repo_url=REPO)

    async def test_a_build_with_no_cli_is_refused(self) -> None:
        fake = await provider_with_checkpoint()
        with pytest.raises(SandboxError, match="no CLI"):
            CliSessionManager(fake).create(artifact(cli=None), repo_url=REPO)

    async def test_a_failed_bring_up_is_recorded_not_raised(self) -> None:
        """The bring-up runs in a background task, where nothing would catch it."""
        fake = FakeSandboxProvider()  # no checkpoint, so the restore fails
        session = await CliSessionManager(fake).start(artifact(), repo_url=REPO)

        assert session.status is SessionStatus.FAILED
        assert session.error is not None and "checkpoint" in session.error

    async def test_an_unanswering_environment_gives_up_saying_so(self) -> None:
        fake = await provider_with_checkpoint()
        fake.script(
            cql_command(CQLSH, "SELECT now() FROM system.local"),
            ScriptedCommand(exit_code=1, stderr="Connection refused"),
        )
        manager = CliSessionManager(fake, ready_timeout_seconds=0.01)
        session = await manager.start(artifact(), repo_url=REPO)

        assert session.status is SessionStatus.FAILED
        assert session.error is not None and "Connection refused" in session.error


class TestExecuting:
    async def test_real_output_is_returned_unmodified(self) -> None:
        """What the terminal shows is what cqlsh printed. Nothing is reformatted."""
        raw = " release_version\n-----------------\n           5.0.9\n\n(1 rows)\n"
        fake = await provider_with_checkpoint()
        fake.script(
            cql_command(CQLSH, "SELECT release_version FROM system.local"),
            ScriptedCommand(stdout=raw),
        )
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        record = await manager.execute(session.id, "SELECT release_version FROM system.local")

        assert record.stdout == raw
        assert record.ok is True
        assert record.sequence == 1

    async def test_an_error_is_returned_as_an_error(self) -> None:
        """The lesson provokes one error on purpose; it must survive the trip."""
        message = "<stdin>:1:InvalidRequest: Error from server: use ALLOW FILTERING"
        fake = await provider_with_checkpoint()
        fake.script(
            cql_command(CQLSH, "SELECT * FROM repolab.t WHERE c = 21.5"),
            ScriptedCommand(stderr=message, exit_code=2),
        )
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        record = await manager.execute(session.id, "SELECT * FROM repolab.t WHERE c = 21.5")

        assert record.ok is False
        assert "ALLOW FILTERING" in record.output

    async def test_a_refused_statement_never_reaches_the_container(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)
        before = len(fake.executed)

        record = await manager.execute(session.id, "rm -rf /")

        assert record.refused is not None
        assert record.exit_code is None
        assert len(fake.executed) == before, "nothing was sent"

    async def test_statements_are_quoted_so_nothing_escapes_the_argument(self) -> None:
        assert cql_command(CQLSH, "SELECT 'a b'") == f"{CQLSH} -e 'SELECT '\"'\"'a b'\"'\"''"

    async def test_executing_in_a_session_that_is_gone_is_refused(self) -> None:
        fake = await provider_with_checkpoint()
        with pytest.raises(SessionNotReady):
            await CliSessionManager(fake).execute("nope", "SELECT 1")

    async def test_history_records_what_was_run(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        await manager.execute(session.id, "SELECT 1")
        await manager.execute(session.id, "DESCRIBE KEYSPACES")

        assert [item.command for item in session.history] == ["SELECT 1", "DESCRIBE KEYSPACES"]


class TestTheSelectedKeyspace:
    """`USE` has to survive into the next statement.

    Statements run one per `cqlsh -e`, so each is its own connection. Without
    carrying the selection forward, a learner types `USE demo`, sees it accepted,
    and the next statement fails with "No keyspace has been specified" — which is
    exactly how a real generated lesson broke at its fourth step.
    """

    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            ("USE demo", "demo"),
            ("use demo;", "demo"),
            ('USE "Demo"', "Demo"),
            ("SELECT 1", None),
            ("USE demo; DROP TABLE t", None),
        ],
    )
    def test_the_selected_keyspace_is_parsed_conservatively(
        self, statement: str, expected: str | None
    ) -> None:
        assert keyspace_in(statement) == expected

    def test_it_becomes_the_k_option(self) -> None:
        """Verified against the real binary's own help: `-k KEYSPACE`."""
        assert cql_command(CQLSH, "SELECT 1", "demo") == f"{CQLSH} -k demo -e 'SELECT 1'"

    def test_no_selection_means_no_option(self) -> None:
        assert cql_command(CQLSH, "SELECT 1") == f"{CQLSH} -e 'SELECT 1'"

    async def test_a_successful_use_scopes_later_statements(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        await manager.execute(session.id, "USE demo")
        await manager.execute(session.id, "SELECT * FROM users")

        assert session.keyspace == "demo"
        assert any("-k demo -e" in command for _, command in fake.executed)

    async def test_a_rejected_use_does_not(self) -> None:
        """Otherwise the learner is scoped to a keyspace that does not exist."""
        fake = await provider_with_checkpoint()
        fake.script(
            cql_command(CQLSH, "USE nope"),
            ScriptedCommand(stderr="InvalidRequest: Keyspace 'nope' does not exist", exit_code=2),
        )
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        await manager.execute(session.id, "USE nope")

        assert session.keyspace is None


class TestUpgrades:
    async def test_a_better_environment_is_offered_not_applied(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        offer = manager.propose(session.id, artifact(BuildKind.SOURCE_BUILD))

        assert offer is not None
        assert session.artifact.kind is BuildKind.OFFICIAL_RELEASE, "still the old one"

    async def test_an_equal_environment_is_not_offered(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        assert manager.propose(session.id, artifact(BuildKind.OFFICIAL_RELEASE)) is None

    async def test_accepting_replaces_the_container_under_the_same_session(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)
        manager.propose(session.id, artifact(BuildKind.SOURCE_BUILD))

        upgraded = await manager.accept(session.id)

        assert upgraded.id == session.id, "the browser keeps its socket"
        assert upgraded.artifact.kind is BuildKind.SOURCE_BUILD
        assert upgraded.status is SessionStatus.READY
        assert fake.terminated, "the old container was released"

    async def test_an_upgrade_is_refused_mid_statement(self) -> None:
        """The rule the specification states: do not switch mid-command."""
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)
        manager.propose(session.id, artifact(BuildKind.SOURCE_BUILD))

        environment = manager.environment(session.id)
        assert environment is not None
        environment.begin("CREATE KEYSPACE repolab")

        with pytest.raises(UpgradeRefused, match=r"must not change|once it finishes"):
            await manager.accept(session.id)

    async def test_the_offer_survives_the_refusal(self) -> None:
        """Waiting is correct behaviour, so the offer stays pending."""
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)
        manager.propose(session.id, artifact(BuildKind.SOURCE_BUILD))
        environment = manager.environment(session.id)
        assert environment is not None

        environment.begin("CREATE KEYSPACE repolab")
        with pytest.raises(UpgradeRefused):
            await manager.accept(session.id)
        environment.end()

        upgraded = await manager.accept(session.id)
        assert upgraded.artifact.kind is BuildKind.SOURCE_BUILD

    async def test_a_statement_marks_the_session_busy_only_while_it_runs(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        await manager.execute(session.id, "SELECT 1")

        environment = manager.environment(session.id)
        assert environment is not None and environment.busy is False


class TestReclaimingContainers:
    async def test_an_idle_session_is_reaped(self) -> None:
        """A forgotten tab would otherwise hold a 4 GB Cassandra for the process's life."""
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake, idle_timeout_seconds=-1.0)
        session = await manager.start(artifact(), repo_url=REPO)

        reaped = await manager.reap_idle()

        assert reaped == [session.id]
        assert session.status is SessionStatus.STOPPED

    async def test_a_busy_session_is_not_reaped_for_being_new(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake, idle_timeout_seconds=3600.0)
        await manager.start(artifact(), repo_url=REPO)

        assert await manager.reap_idle() == []

    async def test_stopping_releases_the_container(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        session = await manager.start(artifact(), repo_url=REPO)

        await manager.stop(session.id)

        assert session.status is SessionStatus.STOPPED
        assert fake.terminated

    async def test_closing_stops_everything(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        first = await manager.start(artifact(), repo_url=REPO)
        second = await manager.start(artifact(), repo_url=REPO)

        await manager.close()

        assert first.status is SessionStatus.STOPPED
        assert second.status is SessionStatus.STOPPED


class TestOneTerminalPerRepository:
    """A double-clicked button must not start a second 4 GB container.

    Found in the pre-demo audit. Each Cassandra session is sized at 4 GB, so on a
    machine allocated 6 GB the second container dies the way Phase 9 measured:
    silently, with an empty log.
    """

    async def test_a_second_request_reuses_the_live_session(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        first = await manager.start(artifact(), repo_url=REPO)

        second = manager.create(artifact(), repo_url=REPO)

        assert second.id == first.id
        assert len(manager.sessions) == 1
        assert fake.restored == [(TAG, SERVICE, "4g")], "one container, not two"

    async def test_a_session_that_is_still_starting_counts(self) -> None:
        """The race itself: two clicks before the first one is ready."""
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)

        first = manager.create(artifact(), repo_url=REPO)
        assert first.status is SessionStatus.STARTING

        assert manager.create(artifact(), repo_url=REPO).id == first.id
        assert len(manager.sessions) == 1

    async def test_a_stopped_session_does_not_block_a_new_one(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        first = await manager.start(artifact(), repo_url=REPO)
        await manager.stop(first.id)

        second = manager.create(artifact(), repo_url=REPO)

        assert second.id != first.id

    async def test_too_many_live_sessions_is_refused_with_a_reason(self) -> None:
        fake = await provider_with_checkpoint()
        manager = CliSessionManager(fake)
        for index in range(2):
            manager.create(artifact(), repo_url=f"{REPO}-{index}")

        with pytest.raises(SandboxError, match="as many as this machine is sized for"):
            manager.create(artifact(), repo_url=f"{REPO}-extra")
