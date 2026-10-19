"""A learner's live terminal session.

    ready artifact --> restore its checkpoint, running its server
                   --> wait for the CLI to answer
                   --> execute real statements, return real output

Every statement runs as a real ``cqlsh -e`` inside the container. **Nothing is
simulated and nothing is reformatted** -- what the terminal shows is what cqlsh
printed, including its errors, which the lesson deliberately provokes at one
point.

A session holds a container for as long as it lives, so it holds an idle
deadline too. A forgotten browser tab should not keep a 4 GB Cassandra alive
indefinitely.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum

import structlog
from pydantic import BaseModel, Field, computed_field

from repolab.analytics import Analytics, AnalyticsEventType, NullAnalyticsWriter
from repolab.builds.models import BuildArtifact
from repolab.builds.session import EnvironmentSession, UpgradeOffer
from repolab.events import EventEmitter
from repolab.experiences.cli.safety import Refusal, check
from repolab.failures import describe
from repolab.sandbox.models import SandboxError, SandboxHandle
from repolab.sandbox.provider import SandboxProvider

log = structlog.get_logger(__name__)

#: How long a session may sit unused before it is reclaimed.
IDLE_TIMEOUT_SECONDS = 20 * 60

#: How long to wait for the server to accept its first connection. A restored
#: Cassandra replays its commit log, so it is not instant even from a checkpoint.
READY_TIMEOUT_SECONDS = 300.0

#: Between readiness probes.
PROBE_INTERVAL_SECONDS = 5.0

#: Per statement. Generous -- cqlsh starts a Python process each time -- but
#: bounded, so a pathological query cannot hold the session forever.
COMMAND_TIMEOUT_SECONDS = 120.0

#: Kept per session, so a reconnecting browser can replay what it missed.
MAX_HISTORY = 200

#: Live sessions one process will hold at once.
#:
#: Each is a container sized for the software it runs -- 4 GB for Cassandra --
#: so this is a memory budget, not a politeness limit. Two is enough for a
#: learner who left a tab open and opened another; a third would be the one that
#: gets OOM-killed, and a silently dead JVM is the least debuggable failure
#: there is.
MAX_LIVE_SESSIONS = 2


class SessionStatus(StrEnum):
    """Where a session stands."""

    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPED = "stopped"

    @property
    def is_usable(self) -> bool:
        """Whether a statement can be run right now."""
        return self is SessionStatus.READY

    @property
    def holds_container(self) -> bool:
        """Whether a container is allocated to this session.

        True from the moment a bring-up begins, not from the moment it succeeds:
        a session that is still starting already has the container, and counting
        only the ready ones is what let a double-clicked button start a second
        one for the same learner.
        """
        return self in {SessionStatus.STARTING, SessionStatus.READY}


class CommandRecord(BaseModel):
    """One statement and exactly what came back.

    ``stdout`` and ``stderr`` are cqlsh's own bytes, decoded and otherwise
    untouched. A refused statement never reaches the container and records why.
    """

    sequence: int
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    refused: str | None = None
    duration_seconds: float = 0.0
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def ok(self) -> bool:
        """Whether the statement ran and Cassandra accepted it."""
        return self.refused is None and self.exit_code == 0

    @property
    def output(self) -> str:
        """Everything the terminal shows for this statement."""
        if self.refused is not None:
            return self.refused
        return "\n".join(part for part in (self.stdout, self.stderr) if part.strip())


class CliSession(BaseModel):
    """One learner, one container, one terminal."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    repo_url: str
    job_id: str | None = None

    artifact: BuildArtifact
    status: SessionStatus = SessionStatus.STARTING
    error: str | None = None

    keyspace: str | None = None
    """The keyspace the learner has selected with ``USE``, when they have.

    Statements run one per ``cqlsh -e``, so each one is its own connection and
    ``USE`` would otherwise be forgotten the moment it succeeded -- a learner
    would type ``USE demo``, see it accepted, and have the next statement fail
    with "No keyspace has been specified". Observed on a real generated lesson,
    which broke at its fourth step for exactly that reason.

    Carrying it forward as ``cqlsh -k`` is not a simulation: it is the session
    state a persistent shell would have had.
    """

    history: list[CommandRecord] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"arbitrary_types_allowed": True}

    @computed_field
    @property
    def fidelity(self) -> str:
        return self.artifact.fidelity.value

    @computed_field
    @property
    def simulated(self) -> bool:
        """True when this session is backed by a Mini Lab rather than a server."""
        return self.artifact.simulated

    @property
    def idle_seconds(self) -> float:
        return (datetime.now(UTC) - self.last_used_at).total_seconds()

    def touch(self) -> None:
        self.last_used_at = datetime.now(UTC)


class SessionNotReady(RuntimeError):
    """A statement was submitted before the environment could answer."""


class CliSessionManager:
    """Starts, drives and reclaims terminal sessions.

    One manager per process. Sessions are held in memory: a terminal is a live
    container and a live socket, neither of which survives a restart, so
    persisting the session would promise something the container cannot keep.
    """

    def __init__(
        self,
        provider: SandboxProvider,
        *,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        ready_timeout_seconds: float = READY_TIMEOUT_SECONDS,
        analytics: Analytics | None = None,
    ) -> None:
        self._provider = provider
        self._idle_timeout = idle_timeout_seconds
        self._ready_timeout = ready_timeout_seconds

        # What a learner ran, never the statement itself: the verb and whether it
        # was refused are the metric; the text they typed is their business.
        self._analytics = analytics if analytics is not None else Analytics(NullAnalyticsWriter())

        self._sessions: dict[str, CliSession] = {}
        self._handles: dict[str, SandboxHandle] = {}

        # The upgrade state belongs to Phase 9's `EnvironmentSession`, which
        # already knows not to switch mid-command. One per terminal, rather than
        # a second `busy` flag here that would drift from it.
        self._environments: dict[str, EnvironmentSession] = {}

    @property
    def sessions(self) -> list[CliSession]:
        return list(self._sessions.values())

    def get(self, session_id: str) -> CliSession | None:
        return self._sessions.get(session_id)

    def live_for(self, repo_url: str) -> CliSession | None:
        """A session already running for *repo_url*, if there is one."""
        return next(
            (
                session
                for session in self._sessions.values()
                if session.repo_url == repo_url and session.status.holds_container
            ),
            None,
        )

    def create(
        self, artifact: BuildArtifact, *, repo_url: str, job_id: str | None = None
    ) -> CliSession:
        """Register a session in ``starting`` without touching the environment.

        Separate from :meth:`bring_up` because restoring a checkpoint and waiting
        for Cassandra to replay its commit log takes tens of seconds. An HTTP
        handler creates the session, returns its id immediately, and the bring-up
        runs outside the request -- which is the rule about slow work, and also
        the only way a browser can show "starting" at all.

        Raises:
            SandboxError: the artifact is not something a terminal can drive.
        """
        if not artifact.ready:
            raise SandboxError(
                "this build is not ready; a terminal needs an environment whose smoke test passed"
            )
        if artifact.checkpoint_id is None or artifact.checkpoint_tag is None:
            raise SandboxError("this build has no checkpoint to restore")
        if not artifact.cli_command:
            raise SandboxError(f"a {artifact.kind.value} build declares no CLI to drive")

        # One terminal per repository. A double-clicked button or a reloaded tab
        # would otherwise start a second 4 GB container for the same learner,
        # and on a machine sized for one the second one dies silently.
        existing = self.live_for(repo_url)
        if existing is not None:
            log.info("cli.session_reused", session=existing.id, repo=repo_url)
            return existing

        live = [session for session in self._sessions.values() if session.status.holds_container]
        if len(live) >= MAX_LIVE_SESSIONS:
            raise SandboxError(
                f"{len(live)} terminal sessions are already running, which is as many as "
                "this machine is sized for. Close one and try again."
            )

        session = CliSession(repo_url=repo_url, job_id=job_id, artifact=artifact)
        self._sessions[session.id] = session

        environment = EnvironmentSession(id=session.id, repo_url=repo_url)
        environment.use(artifact)
        self._environments[session.id] = environment
        return session

    async def start(
        self, artifact: BuildArtifact, *, repo_url: str, job_id: str | None = None
    ) -> CliSession:
        """Create a session and bring it up. The whole operation, for one caller."""
        session = self.create(artifact, repo_url=repo_url, job_id=job_id)
        return await self.bring_up(session)

    async def bring_up(self, session: CliSession) -> CliSession:
        """Restore the checkpoint and wait until the CLI answers.

        Never raises: a failure is recorded on the session, because the caller is
        usually a background task with nobody to catch it and the browser needs
        to be told what went wrong.
        """
        artifact = session.artifact
        try:
            handle = await self._provider.restore(
                artifact.checkpoint_tag,
                command=artifact.service_command,
                memory=artifact.memory,
            )
            self._handles[session.id] = handle
            await self._wait_until_answering(session, handle)
        except Exception as exc:  # noqa: BLE001 - reported on the session
            session.status = SessionStatus.FAILED
            # A learner reads this on the Learn screen, so it says what to do.
            session.error = describe(exc)
            log.warning("cli.session_failed", session=session.id, error=session.error)
            await self.stop(session.id)
            return session

        session.status = SessionStatus.READY
        session.touch()
        log.info("cli.session_ready", session=session.id, fidelity=session.fidelity)
        return session

    async def execute(self, session_id: str, raw: str) -> CommandRecord:
        """Run one statement and return exactly what came back.

        Raises:
            SessionNotReady: the session is not in a state that can run anything.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotReady(f"no session {session_id}")
        if not session.status.is_usable:
            raise SessionNotReady(
                f"session {session_id} is {session.status.value}"
                + (f": {session.error}" if session.error else "")
            )

        session.touch()
        sequence = len(session.history) + 1

        # Checked before anything is sent. A refused statement never reaches the
        # container, so the refusal is the whole record.
        outcome = check(raw)
        if isinstance(outcome, Refusal):
            record = CommandRecord(sequence=sequence, command=raw, refused=str(outcome))
            session.history.append(record)
            self._trim(session)
            self._analytics.record(
                AnalyticsEventType.COMMAND_EXECUTED,
                refused=True,
                reason=outcome.reason,
            )
            return record

        handle = self._handles[session_id]
        started = time.perf_counter()

        # Marked in flight for exactly as long as the statement is running. An
        # upgrade arriving in this window waits rather than pulling the
        # environment out from under the learner.
        environment = self._environments.get(session_id)
        if environment is not None:
            environment.begin(outcome.text)
        try:
            _, result = await self._provider.execute(
                handle,
                cql_command(
                    session.artifact.cli_command or "cqlsh",
                    outcome.text,
                    session.keyspace,
                ),
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        finally:
            if environment is not None:
                environment.end()

        record = CommandRecord(
            sequence=sequence,
            command=outcome.text,
            # cqlsh's own output, decoded and otherwise untouched.
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.exit_code,
            duration_seconds=round(time.perf_counter() - started, 3),
        )

        # Only a USE the server accepted changes the session's keyspace. A
        # rejected one must not, or the learner would be scoped to a keyspace
        # that does not exist.
        if record.ok and outcome.verb == "use":
            selected = keyspace_in(outcome.text)
            if selected is not None:
                session.keyspace = selected

        session.history.append(record)
        self._trim(session)
        self._analytics.record(
            AnalyticsEventType.COMMAND_EXECUTED,
            verb=outcome.verb,
            ok=record.ok,
            refused=False,
            fidelity=session.fidelity,
        )
        return record

    async def stop(self, session_id: str) -> None:
        """Release the session's container."""
        session = self._sessions.get(session_id)
        handle = self._handles.pop(session_id, None)

        if handle is not None:
            try:
                await self._provider.terminate(handle)
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                log.warning("cli.terminate_failed", session=session_id, error=str(exc))

        if session is not None and session.status is not SessionStatus.FAILED:
            session.status = SessionStatus.STOPPED

    async def reap_idle(self) -> list[str]:
        """Stop sessions nobody has used recently.

        A forgotten browser tab would otherwise hold a 4 GB Cassandra for as long
        as the process lives.
        """
        stale = [
            session.id
            for session in self._sessions.values()
            if session.status.is_usable and session.idle_seconds > self._idle_timeout
        ]
        for session_id in stale:
            log.info("cli.session_reaped", session=session_id)
            await self.stop(session_id)
        return stale

    async def close(self) -> None:
        for session_id in list(self._sessions):
            await self.stop(session_id)

    # -- upgrades ---------------------------------------------------------

    def environment(self, session_id: str) -> EnvironmentSession | None:
        """The upgrade state for a session: what it is on, and what is pending."""
        return self._environments.get(session_id)

    def propose(
        self,
        session_id: str,
        artifact: BuildArtifact,
        *,
        emitter: EventEmitter | None = None,
    ) -> UpgradeOffer | None:
        """Offer a better environment. Never switches; returns ``None`` if it is not better."""
        environment = self._environments.get(session_id)
        if environment is None:
            return None
        return environment.propose(artifact, emitter=emitter)

    async def accept(self, session_id: str, *, emitter: EventEmitter | None = None) -> CliSession:
        """Apply the pending upgrade to this terminal.

        The old container is terminated and the better checkpoint is restored in
        its place, under the same session id -- so a browser keeps its socket and
        its history. What does **not** carry over is anything the learner created
        in the old container: a new environment starts from its own checkpoint.
        That is disclosed rather than hidden, because a learner whose keyspace
        silently vanished would reasonably conclude the terminal was lying.

        Raises:
            SessionNotReady: no such session.
            UpgradeRefused: nothing pending, or a statement is in flight. The
                offer stays pending; it can be accepted when the statement ends.
        """
        session = self._sessions.get(session_id)
        environment = self._environments.get(session_id)
        if session is None or environment is None:
            raise SessionNotReady(f"no session {session_id}")

        artifact = environment.accept(emitter=emitter)

        handle = self._handles.pop(session_id, None)
        if handle is not None:
            try:
                await self._provider.terminate(handle)
            except Exception as exc:  # noqa: BLE001 - the upgrade matters more
                log.warning("cli.upgrade_terminate_failed", session=session_id, error=str(exc))

        session.artifact = artifact
        session.status = SessionStatus.STARTING
        session.error = None
        return await self.bring_up(session)

    # -- internals --------------------------------------------------------

    async def _wait_until_answering(self, session: CliSession, handle: SandboxHandle) -> None:
        """Poll until the CLI connects, or give up saying so.

        A restored Cassandra replays its commit log before accepting
        connections, so a checkpoint is not instantly usable.
        """
        deadline = time.monotonic() + self._ready_timeout
        probe = cql_command(
            session.artifact.cli_command or "cqlsh", "SELECT now() FROM system.local"
        )

        last = ""
        while time.monotonic() < deadline:
            _, result = await self._provider.execute(handle, probe, timeout=60.0)
            if result.exit_code == 0:
                return
            last = (result.stderr or result.stdout or "").strip()[-300:]
            await asyncio.sleep(PROBE_INTERVAL_SECONDS)

        raise SandboxError(
            f"the environment did not accept a connection within "
            f"{self._ready_timeout:.0f}s: {last or 'no output'}"
        )

    @staticmethod
    def _trim(session: CliSession) -> None:
        if len(session.history) > MAX_HISTORY:
            del session.history[: len(session.history) - MAX_HISTORY]


def keyspace_in(statement: str) -> str | None:
    """The keyspace a ``USE`` statement selects, if it is one."""
    match = re.match(r"\s*use\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*;?\s*$", statement, re.I)
    return match.group(1) if match else None


def cql_command(cli: str, statement: str, keyspace: str | None = None) -> str:
    """The shell command that runs *statement* through the CLI.

    *keyspace* becomes ``-k``, verified against the real binary's own ``--help``:
    ``-k KEYSPACE, --keyspace KEYSPACE``. It is how a ``USE`` the learner already
    ran survives into the next invocation.

    The statement is quoted with :func:`shlex.quote`, so nothing a learner types
    can break out of the argument and become part of the command. That is belt
    and braces -- :mod:`repolab.experiences.cli.safety` has already refused
    anything containing shell metacharacters -- and belt and braces is the right
    amount for the one place a person's text reaches a command line.
    """
    scope = f" -k {shlex.quote(keyspace)}" if keyspace else ""
    return f"{cli}{scope} -e {shlex.quote(statement)}"
