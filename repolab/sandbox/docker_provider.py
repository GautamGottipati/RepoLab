"""A :class:`SandboxProvider` backed by local Docker.

The build system is written against ``SandboxProvider``, and until now the only
real implementation was Nebius -- which this account cannot execute in
(``ForbiddenError``, recorded since Phase 7). That left the whole build path
runnable only against the fake, which is a poor position for a phase whose
subject is building things.

Docker supports the entire lifecycle the protocol describes, and more honestly
than the sandbox does: ``docker commit`` is a real checkpoint, a committed image
really can be run again independently, and images really can be deleted.

Built on the same primitives as :class:`~repolab.sandbox.docker.DockerEnvironment`,
which implements the *environment* protocol. The two are separate because they
answer different questions: an environment is one running thing, a provider
hands out many.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path

import structlog

from repolab.interfaces import CommandResult
from repolab.sandbox.docker import (
    CONTAINER_WORKDIR,
    DEFAULT_COMMAND_TIMEOUT,
    LIFECYCLE_TIMEOUT,
    DockerUnavailableError,
    docker_available,
    ensure_image,
    image_present,
)
from repolab.sandbox.models import (
    Checkpoint,
    CheckpointMissingError,
    SandboxCapabilities,
    SandboxError,
    SandboxHandle,
)

log = structlog.get_logger(__name__)

#: Memory per container, when a plan does not say.
#:
#: Measured rather than guessed: Cassandra 5.0 exits *silently* in a 2 GB
#: container -- no log, no error, just a dead JVM -- and starts in 4 GB. A
#: default that is too low produces the least debuggable failure there is.
DEFAULT_MEMORY = "4g"


def build_exec(name: str, command: str, cwd: str | None = None) -> list[str]:
    """The argv that runs *command* inside a build container.

    Uses ``sh -c``, not ``sh -lc``. A **login** shell re-reads ``/etc/profile``
    and replaces ``PATH`` with the system default -- which drops the directories
    an image puts its own tools in. Measured on ``cassandra:5.0``: under
    ``sh -lc`` the PATH loses ``/opt/cassandra/bin`` and ``/opt/java/openjdk/bin``,
    so the image's *own entrypoint script* fails with ``exec: cassandra: not
    found``.

    :func:`repolab.sandbox.docker.build_exec_command` keeps the login shell for
    the Spark path, which was built and tested against it.
    """
    argv = ["docker", "exec"]
    if cwd:
        argv += ["--workdir", cwd]
    # Root, because a build installs packages and writes wherever it likes; the
    # container is disposable and isolated from the host.
    argv += ["--user", "root", name, "/bin/sh", "-c", command]
    return argv


def build_run_command(
    image: str,
    name: str,
    *,
    memory: str = DEFAULT_MEMORY,
    command: str | None = None,
) -> list[str]:
    """The argv that starts a build container. Exposed so tests can assert it.

    **Deliberately no bind mount**, unlike
    :func:`repolab.sandbox.docker.build_run_command`. A bind-mounted directory is
    not part of the container filesystem, so ``docker commit`` does not capture
    it -- and a provider whose entire purpose is producing checkpoints cannot
    have the build's output land somewhere the checkpoint will not include.

    (Measured, not assumed: a file written into a mounted `/work` was absent from
    a container restored from the resulting commit.)

    The environment in :mod:`repolab.sandbox.docker` does mount one, for the
    opposite and equally good reason: Phase 6 needs to read Spark's event log
    from the host.

    *command* runs as the container's **main process**. A server started from a
    ``docker exec`` dies when that exec returns, so anything long-running has to
    be the thing the container is. Without it the container simply stays alive
    for commands.
    """
    return [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--workdir",
        CONTAINER_WORKDIR,
        "--memory",
        memory,
        "--entrypoint",
        "/bin/sh",
        image,
        "-c",
        command or "sleep infinity",
    ]


class DockerSandboxProvider:
    """Isolated command execution in local containers."""

    name = "docker"

    def __init__(
        self,
        *,
        memory: str = DEFAULT_MEMORY,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        keep_containers: bool = False,
    ) -> None:
        self._memory = memory
        self._command_timeout = command_timeout
        self._keep = keep_containers

        self._containers: dict[str, str] = {}

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supports_checkpoints=True,
            supports_restore=True,
            supports_file_upload=True,
            # Unlike Nebius Sandboxes, Docker really can delete an image.
            supports_deletion=True,
            supports_network=True,
            notes=[
                "local containers; isolated from the host but not from its network",
                "checkpoints are docker commits, kept in the local image store",
            ],
        )

    # -- lifecycle --------------------------------------------------------

    async def create(
        self,
        image: str,
        *,
        from_checkpoint: str | None = None,
        command: str | None = None,
        memory: str | None = None,
    ) -> SandboxHandle:
        """Start a container from *image*, or from a committed checkpoint.

        *command* becomes the container's main process, for software that has to
        run as a server rather than be invoked per command.
        """
        if from_checkpoint is not None:
            return await self.restore(from_checkpoint)

        if not docker_available():
            raise DockerUnavailableError("docker is not installed or its daemon is not running")

        # Pulled explicitly rather than implicitly by `docker run`, so a slow
        # first pull is a reported wait instead of an unexplained one.
        status = await asyncio.to_thread(ensure_image, image)
        if not status.ready:
            raise SandboxError(f"could not prepare image {image!r}: {status.error}")

        return await self._start(image, command=command, memory=memory)

    async def _start(
        self,
        source: str,
        *,
        from_checkpoint: str | None = None,
        command: str | None = None,
        memory: str | None = None,
    ) -> SandboxHandle:
        name = f"repolab-{uuid.uuid4().hex[:12]}"

        result = await self._run(
            build_run_command(source, name, memory=memory or self._memory, command=command),
            timeout=LIFECYCLE_TIMEOUT,
        )
        if result.returncode != 0:
            detail = _tail(result)
            if from_checkpoint is not None and not image_present(source):
                # The checkpoint is gone, not broken. Said separately so the
                # caller can forget the cache row rather than retrying forever.
                raise CheckpointMissingError(
                    f"the prepared environment {source!r} is no longer on this machine"
                )
            raise SandboxError(f"could not start a container from {source!r}: {detail}")

        # The working directory lives in the container filesystem, so whatever a
        # build writes there is captured by a later commit.
        await self._run(
            build_exec(name, f"mkdir -p {CONTAINER_WORKDIR}"), timeout=LIFECYCLE_TIMEOUT
        )

        handle = SandboxHandle(id=name, image=source, from_checkpoint=from_checkpoint)
        self._containers[handle.id] = name
        log.info("docker_provider.created", container=name, image=source)
        return handle

    async def upload(self, handle: SandboxHandle, files: Mapping[str, bytes]) -> SandboxHandle:
        """Place files into the container filesystem.

        Always ``docker cp``, never a mount: a file that is not in the container
        filesystem is not in the checkpoint committed from it.
        """
        container = self._container(handle)

        for destination, content in files.items():
            parent = str(Path(destination).parent)
            await self._run(
                build_exec(container, f"mkdir -p {_quote(parent)}"),
                timeout=LIFECYCLE_TIMEOUT,
            )

            with tempfile.NamedTemporaryFile() as staged:
                staged.write(content)
                staged.flush()
                result = await self._run(
                    ["docker", "cp", staged.name, f"{container}:{destination}"],
                    timeout=LIFECYCLE_TIMEOUT,
                )
            if result.returncode != 0:
                raise SandboxError(f"could not upload {destination}: {_tail(result)}")

        log.info("docker_provider.uploaded", files=len(files))
        return handle

    async def execute(
        self,
        handle: SandboxHandle,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> tuple[SandboxHandle, CommandResult]:
        """Run *command* inside the container."""
        container = self._container(handle)
        prefixed = command
        if env:
            exports = " ".join(f"{key}={_quote(value)}" for key, value in env.items())
            prefixed = f"export {exports}; {command}"

        argv = build_exec(container, prefixed, cwd)
        limit = timeout if timeout is not None else self._command_timeout

        try:
            result = await self._run(argv, timeout=limit)
        except subprocess.TimeoutExpired:
            # Reported as a result, not raised: a step timing out is an outcome
            # the build has to record, like any other non-zero exit.
            return handle, CommandResult(
                exit_code=124,
                stdout="",
                stderr=f"timed out after {limit:.0f}s",
                elapsed_seconds=limit,
            )

        return handle, CommandResult(
            exit_code=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )

    async def checkpoint(self, handle: SandboxHandle, *, tag: str | None = None) -> Checkpoint:
        """Commit the container to an image that can be run again independently."""
        container = self._container(handle)
        reference = _image_tag(tag)

        result = await self._run(
            ["docker", "commit", container, reference], timeout=LIFECYCLE_TIMEOUT
        )
        if result.returncode != 0:
            raise SandboxError(f"could not checkpoint {container}: {_tail(result)}")

        image_id = (result.stdout or "").strip() or reference
        log.info("docker_provider.checkpointed", container=container, image=reference)
        return Checkpoint(
            id=image_id,
            tag=reference,
            image=handle.image,
            parent_id=handle.from_checkpoint,
        )

    async def restore(
        self,
        checkpoint: str | Checkpoint,
        *,
        command: str | None = None,
        memory: str | None = None,
    ) -> SandboxHandle:
        """Start a fresh container from a committed checkpoint.

        Each call produces an independent container, which is what lets two
        experiment variants start from genuinely identical state.

        *command* runs as the container's main process -- how a checkpoint that
        contains a server becomes a running one, for a terminal session to
        connect to.
        """
        reference = checkpoint.reference if isinstance(checkpoint, Checkpoint) else checkpoint
        return await self._start(
            reference, from_checkpoint=reference, command=command, memory=memory
        )

    async def terminate(self, handle: SandboxHandle) -> None:
        """Remove the container. The checkpoint, if any, outlives it."""
        container = self._containers.pop(handle.id, None)
        if container is None or self._keep:
            return

        await self._run(["docker", "rm", "--force", container], timeout=LIFECYCLE_TIMEOUT)
        log.info("docker_provider.terminated", container=container)

    async def close(self) -> None:
        """Remove every container this provider still holds."""
        for handle_id in list(self._containers):
            await self.terminate(SandboxHandle(id=handle_id, image=""))

    # -- internals --------------------------------------------------------

    def _container(self, handle: SandboxHandle) -> str:
        container = self._containers.get(handle.id)
        if container is None:
            raise SandboxError(f"unknown sandbox handle: {handle.id!r}")
        return container

    @staticmethod
    async def _run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        # A fixed argv and no shell, so nothing in a path or command name is
        # reinterpreted by the host shell. Run off the event loop because the
        # ladder builds several candidates at once.
        return await asyncio.to_thread(
            subprocess.run,
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )


def _image_tag(tag: str | None) -> str:
    """A valid Docker reference for a checkpoint.

    Docker rejects uppercase in a repository name, so a checkpoint tag built
    from a repository slug is lowered rather than passed through.
    """
    if not tag:
        return f"repolab/checkpoint:{uuid.uuid4().hex[:12]}"
    return tag.lower()


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return detail[-1] if detail else "no output"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
