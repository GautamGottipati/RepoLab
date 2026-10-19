"""The CLI experience: a real terminal, over a WebSocket.

    POST   /cli/environment              build one, with the learner's say-so (202)
    POST   /cli/sessions                 start one (202; it comes up in the background)
    GET    /cli/sessions/{id}            where it stands
    POST   /cli/sessions/{id}/upgrade    accept a better environment
    DELETE /cli/sessions/{id}            stop it, releasing the container
    WS     /cli/sessions/{id}/terminal   statements in, real output out
    GET    /cli/environment              what this repository's CLI surface offers

Two rules shape all of it.

**Repository code never runs on the API host.** Every statement is executed by
``docker exec`` inside the learner's own container, through the sandbox provider.
This process orchestrates and relays; it does not interpret CQL and it does not
run cqlsh.

**Slow work never happens in a handler.** Restoring a checkpoint and waiting for
Cassandra to accept a connection takes tens of seconds, so ``POST`` registers the
session, returns its id, and brings it up in a background task. The browser polls
or opens the socket, which waits for readiness itself.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import api.deps as deps
from api.deps import get_session, session_factory
from repolab.builds.models import BuildArtifact
from repolab.builds.session import SessionBusy, UpgradeRefused
from repolab.db.builds import load_builds
from repolab.db.jobs import create_job
from repolab.db.store import load_latest_ingestion, load_manifest
from repolab.events import DbEventEmitter, JobStatus
from repolab.experiences.cli.experience import (
    CLIExperience,
    NoCliSurfaceError,
    build_cli_experience,
)
from repolab.experiences.cli.session import (
    CliSession,
    CliSessionManager,
    CommandRecord,
    SessionNotReady,
    SessionStatus,
)
from repolab.failures import explain
from repolab.ingest.url import InvalidRepoUrlError, validate_repo_url
from repolab.sandbox.models import CheckpointMissingError, SandboxError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/cli", tags=["cli"])

#: What an upgrade costs a learner, stated plainly wherever one is offered.
#:
#: A better environment is a different container restored from a different
#: checkpoint. Nothing created in the old one exists in the new one, and a
#: learner who was not told that would think the terminal had lost their data.
UPGRADE_DISCLOSURE = (
    "A higher-fidelity environment starts from its own checkpoint, so anything "
    "you created here will not be there. Steps you have completed need running "
    "again."
)

#: How long the socket waits for a session that is still coming up.
SOCKET_READY_TIMEOUT_SECONDS = 420.0
SOCKET_POLL_SECONDS = 2.0

_manager: CliSessionManager | None = None

#: Bring-up tasks, held until they finish. Without a reference, Python is free to
#: garbage-collect a running task, and the session would sit in `starting`
#: forever with nothing working on it.
_tasks: set[asyncio.Task[None]] = set()


def manager() -> CliSessionManager:
    """The process-wide session manager.

    One per process, holding live containers. Built lazily so importing the app
    does not require Docker -- the offline test suite imports this module.
    """
    global _manager
    if _manager is None:
        from repolab.analytics import Analytics, DbAnalyticsWriter
        from repolab.sandbox.docker_provider import DockerSandboxProvider

        _manager = CliSessionManager(
            DockerSandboxProvider(),
            analytics=Analytics(DbAnalyticsWriter(session_factory())),
        )
    return _manager


def current_manager() -> CliSessionManager | None:
    """The manager, if one has been built. Never builds one.

    The reaper uses this: a process where nobody has opened a terminal has no
    containers to reclaim, and should not import a Docker provider to find that
    out.
    """
    return _manager


def set_manager(replacement: CliSessionManager | None) -> None:
    """Install a manager. For tests, which drive a fake provider."""
    global _manager
    _manager = replacement


#: How often idle sessions are reclaimed. Well under the 20-minute idle
#: deadline, so a reclaimed container is released promptly rather than at some
#: point in the following interval.
REAP_INTERVAL_SECONDS = 120.0


async def reap_idle_sessions(interval: float = REAP_INTERVAL_SECONDS) -> None:
    """Reclaim abandoned terminals, for as long as the application runs.

    Without this the idle deadline is a field nobody reads: a forgotten browser
    tab holds a 4 GB Cassandra until the process exits.
    """
    while True:
        await asyncio.sleep(interval)
        manager_instance = current_manager()
        if manager_instance is None:
            continue
        try:
            reaped = await manager_instance.reap_idle()
        except Exception as exc:  # noqa: BLE001 - the reaper must outlive a failure
            log.warning("cli.reaper_failed", error=str(exc))
            continue
        if reaped:
            log.info("cli.sessions_reaped", sessions=reaped)


class ResolveEnvironment(BaseModel):
    repo_url: str = Field(description="Public https repository URL")
    slow_source_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=600.0,
        description=(
            "Delay the source build on purpose, to demonstrate the upgrade sequence. "
            "Recorded on the artifact, so a build slowed deliberately is never "
            "mistaken for one that was slow."
        ),
    )


class EnvironmentResolving(BaseModel):
    job_id: str
    status: JobStatus
    repo_url: str
    events_url: str
    environment_url: str


class StartSession(BaseModel):
    repo_url: str = Field(description="Public https repository URL")
    job_id: str | None = Field(default=None, description="Job whose events this belongs to")


class SessionView(BaseModel):
    """A terminal session as the browser renders it."""

    session_id: str
    status: SessionStatus
    repo_url: str
    job_id: str | None = None
    error: str | None = None

    fidelity: str
    simulated: bool
    cli_command: str | None = None
    version: str = ""
    build_kind: str = ""

    busy: bool = False
    """Whether a statement is in flight. An upgrade waits for this to clear."""

    upgrade: dict[str, Any] | None = None
    """A pending offer of a better environment, if one has been made."""

    upgrade_disclosure: str = UPGRADE_DISCLOSURE
    history: list[CommandRecord] = Field(default_factory=list)
    terminal_url: str = ""


class EnvironmentView(BaseModel):
    """What this repository's CLI surface offers, and what is running it."""

    repo_url: str
    commit_sha: str = ""
    experience: CLIExperience | None = None
    ready: bool = False
    reason: str = ""
    """Why there is no experience yet, when there is none."""

    better_available: bool = False
    """Whether a higher-fidelity build than the best one is already cached."""


def _view(session: CliSession) -> SessionView:
    environment = manager().environment(session.id)
    offer = environment.offer if environment is not None else None
    return SessionView(
        session_id=session.id,
        status=session.status,
        repo_url=session.repo_url,
        job_id=session.job_id,
        error=session.error,
        fidelity=session.fidelity,
        simulated=session.simulated,
        cli_command=session.artifact.cli_command,
        version=session.artifact.version or "",
        build_kind=session.artifact.kind.value,
        busy=bool(environment and environment.busy),
        upgrade=(
            {
                "from_fidelity": offer.from_fidelity,
                "to_fidelity": offer.to_fidelity,
                "describe": offer.describe(),
                "pending": offer.pending,
                **offer.artifact.declaration(),
            }
            if offer is not None and offer.pending
            else None
        ),
        history=list(session.history),
        terminal_url=f"/cli/sessions/{session.id}/terminal",
    )


def _best_artifact(session: Session, repo_url: str) -> tuple[BuildArtifact | None, str]:
    """The best ready build cached for this repository's investigated commit.

    The commit matters: a build of a different commit is a build of different
    software. The second element is why there is nothing, so a browser can say
    "still building" rather than "unavailable".
    """
    ingestion = load_latest_ingestion(session, repo_url)
    if ingestion is None:
        return None, "this repository has not been investigated yet"

    artifacts = load_builds(session, repo_url, ingestion.commit_sha)
    if not artifacts:
        return None, "no environment has finished building yet"
    return artifacts[0], ""


@router.post("/sessions", response_model=SessionView, status_code=status.HTTP_202_ACCEPTED)
async def start_session(
    payload: StartSession,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> SessionView:
    """Start a terminal on the best environment already built for this repository."""
    try:
        repo_url = validate_repo_url(payload.repo_url)
    except InvalidRepoUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    artifact, reason = _best_artifact(session, repo_url.normalized)
    if artifact is None:
        raise HTTPException(status.HTTP_409_CONFLICT, reason)
    if artifact.simulated:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "only a Mini Lab is available for this repository; there is no server to connect to",
        )

    try:
        cli_session = manager().create(
            artifact, repo_url=repo_url.normalized, job_id=payload.job_id
        )
    except SandboxError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    # Outside the handler: restoring the checkpoint and waiting for Cassandra to
    # answer takes tens of seconds.
    task = asyncio.create_task(_bring_up(cli_session.id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    response.headers["Location"] = f"/cli/sessions/{cli_session.id}"
    return _view(cli_session)


async def _bring_up(session_id: str) -> None:
    """Background bring-up. Failures land on the session, never on a request.

    A checkpoint that has gone is forgotten rather than retried: the cache row
    says `ready`, nothing can start from it, and leaving it means every attempt
    a learner makes fails identically. Dropping it means the next *Build an
    environment* actually rebuilds.
    """
    current = manager().get(session_id)
    if current is None:
        return

    session = await manager().bring_up(current)
    if session.status is SessionStatus.FAILED and _checkpoint_is_gone(session):
        _forget(session.artifact)


def _checkpoint_is_gone(session: CliSession) -> bool:
    return CheckpointMissingError.__name__ in (session.error or "")


def _forget(artifact: BuildArtifact) -> None:
    from repolab.builds.cache import DbBuildCache
    from repolab.db import session_scope

    dropped = DbBuildCache(lambda: session_scope(deps.engine())).forget(
        artifact.repo_url, artifact.commit_sha, artifact.kind.value
    )
    log.warning(
        "cli.stale_checkpoint_forgotten",
        repo=artifact.repo_url,
        kind=artifact.kind.value,
        dropped=dropped,
    )


@router.get("/sessions/{session_id}", response_model=SessionView)
async def read_session(session_id: str) -> SessionView:
    """Where a session stands, including anything it has already run."""
    current = manager().get(session_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id}")
    return _view(current)


@router.post("/sessions/{session_id}/upgrade", response_model=SessionView)
async def upgrade_session(
    session_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> SessionView:
    """Accept a better environment, if one is ready and nothing is in flight.

    Offered rather than forced, and refused mid-statement: the rule is the same
    one the ladder demonstration establishes, now reachable from a browser.
    """
    current = manager().get(session_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id}")

    artifact, reason = _best_artifact(session, current.repo_url)
    if artifact is None:
        raise HTTPException(status.HTTP_409_CONFLICT, reason)

    emitter = _emitter(current.job_id, session)
    manager().propose(session_id, artifact, emitter=emitter)
    try:
        upgraded = await manager().accept(session_id, emitter=emitter)
    except UpgradeRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except SessionNotReady as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _view(upgraded)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def stop_session(session_id: str) -> Response:
    """Stop a session and release its container."""
    if manager().get(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id}")
    await manager().stop(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/environment", response_model=EnvironmentResolving, status_code=status.HTTP_202_ACCEPTED
)
def resolve_environment(
    payload: ResolveEnvironment,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> EnvironmentResolving:
    """Build an environment for this repository, through the whole fidelity ladder.

    Asked for explicitly, and that is deliberate. Resolving Cassandra compiles
    source, pulls a release and starts a 4 GB node -- CLAUDE.md requires explicit
    approval before starting expensive services, so RepoLab does not do it
    behind a learner's back when they open a screen.

    Returns 202: the ladder takes minutes and runs in a worker. Progress arrives
    on the job's event stream, and each rung that passes its smoke test is
    cached, which is how the terminal finds one to connect to.
    """
    try:
        url = validate_repo_url(payload.repo_url)
    except InvalidRepoUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    if load_latest_ingestion(session, url.normalized) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this repository has not been investigated yet, so RepoLab does not know "
            "how to build it",
        )

    job = create_job(session, url.normalized, kind="environment")
    session.commit()

    try:
        from worker.jobs import enqueue_environment_resolution

        enqueue_environment_resolution(
            job.id, url.normalized, slow_source_seconds=payload.slow_source_seconds
        )
    except Exception as exc:  # noqa: BLE001 - reported; the job row survives
        log.warning("cli.enqueue_failed", job_id=job.id, error=str(exc))

    response.headers["Location"] = f"/cli/environment?repo_url={url.normalized}"
    return EnvironmentResolving(
        job_id=job.id,
        status=JobStatus.QUEUED,
        repo_url=url.normalized,
        events_url=f"/jobs/{job.id}/events",
        environment_url=f"/cli/environment?repo_url={url.normalized}",
    )


@router.get("/environment", response_model=EnvironmentView)
def read_environment(
    session: Annotated[Session, Depends(get_session)],
    repo_url: Annotated[str, Query(description="Repository to describe")],
) -> EnvironmentView:
    """The CLI experience for a repository: its surface, and what is running it."""
    try:
        url = validate_repo_url(repo_url)
    except InvalidRepoUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    manifest = load_manifest(session, url.normalized)
    if manifest is None:
        return EnvironmentView(
            repo_url=url.normalized,
            reason="this repository has not been investigated yet",
        )

    artifact, reason = _best_artifact(session, url.normalized)
    if artifact is None:
        return EnvironmentView(
            repo_url=url.normalized, commit_sha=manifest.commit_sha, reason=reason
        )

    try:
        experience = build_cli_experience(manifest, artifact)
    except NoCliSurfaceError as exc:
        return EnvironmentView(
            repo_url=url.normalized, commit_sha=manifest.commit_sha, reason=str(exc)
        )

    return EnvironmentView(
        repo_url=url.normalized,
        commit_sha=manifest.commit_sha,
        experience=experience,
        ready=experience.available,
        reason=experience.unavailable_reason,
        better_available=_better_than_running(session, url.normalized, artifact),
    )


def _better_than_running(session: Session, repo_url: str, best: BuildArtifact) -> bool:
    """Whether any live session is on something worse than the best cached build.

    The cross-process half of the upgrade sequence: the worker caches a better
    rung, and this is where the browser finds out, without either process
    knowing the other is running.
    """
    return any(
        current.repo_url == repo_url and best.better_than(current.artifact)
        for current in manager().sessions
        if current.status.is_usable
    )


@router.websocket("/sessions/{session_id}/terminal")
async def terminal(socket: WebSocket, session_id: str) -> None:
    """Statements in, real output out.

    The protocol is deliberately small. The client sends ``{"command": "..."}``
    and receives one frame per statement: the real stdout, the real stderr, the
    real exit code, or a refusal explaining why nothing was run. Output is never
    reformatted -- what the terminal prints is what cqlsh printed.
    """
    await socket.accept()

    current = manager().get(session_id)
    if current is None:
        await socket.send_json({"type": "error", "message": f"no session {session_id}"})
        await socket.close(code=4404)
        return

    ready = await _await_ready(session_id, socket)
    if not ready:
        return

    await socket.send_json({"type": "ready", **_view(current).model_dump(mode="json")})

    try:
        while True:
            message = await socket.receive_json()
            raw = str(message.get("command", "")).strip()
            if not raw:
                await socket.send_json({"type": "error", "message": "Nothing to run."})
                continue

            # The idle reaper can reclaim a session under an open socket, and a
            # client that keeps typing into a stopped one would otherwise be told
            # "session is stopped" forever.
            live = manager().get(session_id)
            if live is None or not live.status.is_usable:
                await socket.send_json(
                    {
                        "type": "error",
                        "message": (
                            "This terminal was closed because it sat idle. "
                            "Start a new one to carry on."
                        ),
                    }
                )
                await socket.close(code=4410)
                return

            try:
                record = await manager().execute(session_id, raw)
            except SessionNotReady:
                await socket.send_json(
                    {"type": "error", "message": "This terminal is no longer running."}
                )
                await socket.close(code=4409)
                return
            except SessionBusy as exc:
                # `EnvironmentSession.begin` refuses to overlap statements, which
                # is deliberate: one at a time, so an upgrade can never land
                # mid-command. Caught by its own type: `except RuntimeError`
                # also caught SandboxError and misreported a dead container.
                log.info("cli.execute_busy", session=session_id, error=str(exc))
                await socket.send_json(
                    {
                        "type": "error",
                        "message": "A statement is already running. Wait for it to finish.",
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001 - reported, socket stays open
                failure = explain(exc)
                log.warning("cli.execute_failed", session=session_id, error=failure.technical)
                await socket.send_json(
                    {"type": "error", "message": failure.summary, **failure.model_dump_public()}
                )
                continue

            await socket.send_json({"type": "result", **record.model_dump(mode="json")})
    except WebSocketDisconnect:
        # The container outlives the socket on purpose: a reload reconnects to
        # the same session and the learner's keyspace is still there. The idle
        # reaper is what eventually reclaims it.
        log.info("cli.socket_closed", session=session_id)
    except Exception as exc:  # noqa: BLE001 - a dead socket must not leave a task raising
        log.warning("cli.socket_failed", session=session_id, error=f"{type(exc).__name__}: {exc}")


async def _await_ready(session_id: str, socket: WebSocket) -> bool:
    """Hold the socket while the environment comes up, reporting as it goes."""
    deadline = asyncio.get_running_loop().time() + SOCKET_READY_TIMEOUT_SECONDS

    while True:
        current = manager().get(session_id)
        if current is None:
            await socket.send_json({"type": "error", "message": f"no session {session_id}"})
            await socket.close(code=4404)
            return False
        if current.status is SessionStatus.READY:
            return True
        if current.status in {SessionStatus.FAILED, SessionStatus.STOPPED}:
            await socket.send_json(
                {
                    "type": "error",
                    "message": current.error or f"session is {current.status.value}",
                }
            )
            await socket.close(code=4409)
            return False
        if asyncio.get_running_loop().time() > deadline:
            await socket.send_json(
                {"type": "error", "message": "the environment did not become ready in time"}
            )
            await socket.close(code=4408)
            return False

        await socket.send_json({"type": "starting", "status": current.status.value})
        await asyncio.sleep(SOCKET_POLL_SECONDS)


def _emitter(job_id: str | None, session: Session) -> DbEventEmitter | None:
    """An emitter writing to the job's event stream, when there is a job.

    Upgrade offers belong on the same stream as the build events that produced
    them, so a browser already watching the job sees them without a second
    channel.

    The sequence number is read through the request's own session, and the
    emitter opens its scopes against :func:`api.deps.engine` at the moment it
    writes -- reached through the module rather than bound at import, so a test
    that swaps the engine is actually swapping the one this uses.
    """
    if job_id is None:
        return None

    from repolab.db import session_scope
    from repolab.db.jobs import get_job

    job = get_job(session, job_id)
    start_seq = job.last_seq if job is not None else 0

    return DbEventEmitter(job_id, lambda: session_scope(deps.engine()), start_seq=start_seq)
