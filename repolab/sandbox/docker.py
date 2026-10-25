"""Execution in a local Docker container.

A second real backend for the Phase 5 :class:`ExecutionEnvironment` protocol. It
exists because repository code must not run on the API host (CLAUDE.md), and a
container provides genuine isolation without needing Nebius credentials.

Unlike Nebius Sandboxes, Docker really does support the whole lifecycle: ``docker
commit`` is a true checkpoint, a committed image can be run again, and images can
be deleted. :attr:`DockerEnvironment.capabilities` reports that honestly rather
than inheriting the sandbox's limitations.

The container is kept alive with ``sleep infinity`` and driven through ``docker
exec``, so state accumulates across commands exactly as it does in a sandbox.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

import structlog
from pydantic import BaseModel

from repolab.interfaces import CommandResult
from repolab.sandbox.environment import EnvironmentNotStartedError
from repolab.sandbox.models import Checkpoint, SandboxCapabilities, SandboxError

log = structlog.get_logger(__name__)

#: Where the mounted working directory appears inside the container.
CONTAINER_WORKDIR = "/work"

#: How long to wait for container lifecycle operations.
LIFECYCLE_TIMEOUT = 300.0

#: Default per-command timeout.
DEFAULT_COMMAND_TIMEOUT = 900.0


class DockerUnavailableError(SandboxError):
    """Docker is not installed or its daemon is not reachable."""


def docker_available() -> bool:
    """Whether a usable Docker daemon is present."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


#: How long to wait for an image pull. Spark's image is around 600 MB, and a
#: cold pull on a slow connection genuinely takes minutes.
PULL_TIMEOUT = 1800.0


class ImagePullFailed(SandboxError):
    """An image is not present locally and could not be pulled."""


class ImageStatus(BaseModel):
    """Whether an image is ready to run, and what it took to get there."""

    image: str
    present: bool
    pulled: bool = False
    """True when this call fetched it, rather than finding it already there."""

    seconds: float = 0.0
    error: str | None = None

    @property
    def ready(self) -> bool:
        return self.present and self.error is None

    def describe(self) -> str:
        if not self.present:
            return f"{self.image} is unavailable: {self.error or 'unknown reason'}"
        if self.pulled:
            return f"pulled {self.image} in {self.seconds:.0f}s"
        return f"{self.image} is already present"


def image_present(image: str) -> bool:
    """Whether *image* is already in the local image store."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def ensure_image(image: str, *, timeout: float = PULL_TIMEOUT) -> ImageStatus:
    """Make *image* available locally, pulling it only if it is missing.

    Checking first matters: a pull that is already satisfied still contacts the
    registry, and the point of this call is to turn "the environment is being
    prepared" into either a few milliseconds or a reported download, rather than
    an unexplained wait at the start of the first run.

    Failure is returned, not raised. An unavailable image means the playground
    cannot run, which is worth saying plainly -- but it must not take down the
    investigation that was otherwise successful.
    """
    started = time.perf_counter()

    if image_present(image):
        return ImageStatus(image=image, present=True, seconds=time.perf_counter() - started)

    log.info("docker.image_pulling", image=image)
    try:
        result = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ImageStatus(
            image=image,
            present=False,
            seconds=time.perf_counter() - started,
            error=f"pull timed out after {timeout:.0f}s",
        )
    except OSError as exc:
        return ImageStatus(
            image=image, present=False, seconds=time.perf_counter() - started, error=str(exc)
        )

    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        return ImageStatus(image=image, present=False, seconds=elapsed, error=_tail(result))

    log.info("docker.image_pulled", image=image, seconds=round(elapsed, 1))
    return ImageStatus(image=image, present=True, pulled=True, seconds=elapsed)


#: Flags for a container that runs code a learner wrote or edited: no network,
#: no capabilities, bounded processes and CPU. The event-log bind mount stays,
#: and it is a fresh directory per run.
HARDENED_FLAGS = (
    "--network", "none",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "512",
    "--cpus", "4",
)  # fmt: skip


def build_run_command(
    image: str, name: str, host_workdir: Path, *, hardened: bool = False
) -> list[str]:
    """The argv that starts the container. Exposed so tests can assert it."""
    extra = list(HARDENED_FLAGS) if hardened else []
    return [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        # The work directory is a bind mount, so the event log Spark writes
        # inside the container is readable from the host without `docker cp`.
        "--volume",
        f"{host_workdir}:{CONTAINER_WORKDIR}",
        "--workdir",
        CONTAINER_WORKDIR,
        # Spark's driver JVM needs headroom; the default can be tight.
        "--memory",
        "4g",
        *extra,
        "--entrypoint",
        "/bin/sh",
        image,
        "-c",
        "sleep infinity",
    ]


def build_exec_command(name: str, command: str, cwd: str | None = None) -> list[str]:
    """The argv that runs *command* inside the container."""
    argv = ["docker", "exec"]
    if cwd:
        argv += ["--workdir", cwd]
    # Run as root so pip installs and writes to /work always succeed; the
    # container is disposable and isolated from the host.
    argv += ["--user", "root", name, "/bin/sh", "-lc", command]
    return argv


class DockerEnvironment:
    """An :class:`ExecutionEnvironment` backed by a local container."""

    kind = "docker"

    def __init__(
        self,
        *,
        host_workdir: Path | None = None,
        keep_container: bool = False,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        hardened: bool = False,
    ) -> None:
        self._hardened = hardened
        self._container: str | None = None
        self._image: str | None = None
        self._command_timeout = command_timeout
        self._keep_container = keep_container

        # A directory under the user's home, because Docker Desktop on macOS
        # shares /Users by default but not every temp location.
        self._owns_workdir = host_workdir is None
        if host_workdir is None:
            root = Path.home() / ".repolab" / "work"
            root.mkdir(parents=True, exist_ok=True)
            self._host_workdir = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        else:
            self._host_workdir = Path(host_workdir)
            self._host_workdir.mkdir(parents=True, exist_ok=True)

    # -- properties -------------------------------------------------------

    @property
    def host_workdir(self) -> Path:
        """The host side of the mount, where outputs can be read."""
        return self._host_workdir

    @property
    def container_workdir(self) -> str:
        return CONTAINER_WORKDIR

    @property
    def container(self) -> str:
        if self._container is None:
            raise EnvironmentNotStartedError("the container has not been started")
        return self._container

    @property
    def started(self) -> bool:
        return self._container is not None

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supports_checkpoints=True,
            supports_restore=True,
            supports_file_upload=True,
            # Docker, unlike Nebius Sandboxes, can really delete an image.
            supports_deletion=True,
            supports_network=True,
            notes=[
                "local container; isolated from the host but not from the host network",
                "checkpoints are docker commits and live in the local image store",
            ],
        )

    # -- lifecycle --------------------------------------------------------

    async def start(self, image: str, *, from_checkpoint: str | None = None) -> None:
        """Start a container from *image*, or from a previously committed checkpoint."""
        if not docker_available():
            raise DockerUnavailableError("docker is not installed or its daemon is not running")

        source = from_checkpoint or image
        name = f"repolab-{uuid.uuid4().hex[:12]}"
        result = self._run_argv(
            build_run_command(source, name, self._host_workdir, hardened=self._hardened),
            timeout=LIFECYCLE_TIMEOUT,
        )
        if result.returncode != 0:
            raise SandboxError(f"could not start a container from {source!r}: {_tail(result)}")

        self._container = name
        self._image = source
        log.info("docker.started", container=name, image=source)

    async def put_files(self, files: Mapping[str, bytes]) -> None:
        """Place files into the environment.

        Paths under the container's work directory are written straight into the
        bind mount, which is both faster and simpler than ``docker cp``.
        """
        if not self.started:
            raise EnvironmentNotStartedError("the container has not been started")

        for destination, content in files.items():
            if destination.startswith(f"{CONTAINER_WORKDIR}/"):
                target = self._host_workdir / destination[len(CONTAINER_WORKDIR) + 1 :]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                continue

            # Anywhere else needs a copy into the container's own filesystem.
            with tempfile.NamedTemporaryFile() as staged:
                staged.write(content)
                staged.flush()
                result = self._run_argv(
                    ["docker", "cp", staged.name, f"{self.container}:{destination}"],
                    timeout=LIFECYCLE_TIMEOUT,
                )
            if result.returncode != 0:
                raise SandboxError(f"could not place {destination!r}: {_tail(result)}")

    async def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run *command* inside the container."""
        if not self.started:
            raise EnvironmentNotStartedError("the container has not been started")

        prefixed = command
        if env:
            exports = " ".join(f"{key}={_quote(value)}" for key, value in env.items())
            prefixed = f"export {exports} && {command}"

        budget = timeout if timeout is not None else self._command_timeout
        import time

        started = time.perf_counter()
        try:
            result = self._run_argv(
                build_exec_command(self.container, prefixed, cwd), timeout=budget
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                stderr=f"timed out after {budget:.0f}s",
                exit_code=124,
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )

        return CommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            state_id=self._container,
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )

    async def save(self, *, tag: str | None = None) -> Checkpoint:
        """Commit the container to an image.

        A real checkpoint: the committed image can be started again later and is
        independent of the container it came from.
        """
        if not self.started:
            raise EnvironmentNotStartedError("the container has not been started")

        argv = ["docker", "commit", self.container]
        if tag:
            argv.append(tag)
        result = self._run_argv(argv, timeout=LIFECYCLE_TIMEOUT)
        if result.returncode != 0:
            raise SandboxError(f"could not commit the container: {_tail(result)}")

        # `docker commit` prints "sha256:<id>".
        committed = result.stdout.strip().removeprefix("sha256:")
        log.info("docker.checkpointed", checkpoint=committed[:12], tag=tag)
        return Checkpoint(id=committed, tag=tag, image=self._image)

    async def stop(self) -> None:
        """Remove the container, and the mount directory if we created it."""
        if self._container is not None and not self._keep_container:
            self._run_argv(["docker", "rm", "--force", self._container], timeout=LIFECYCLE_TIMEOUT)
            log.info("docker.stopped", container=self._container)
        self._container = None

        if self._owns_workdir and self._host_workdir.exists() and not self._keep_container:
            shutil.rmtree(self._host_workdir, ignore_errors=True)

    async def __aenter__(self) -> DockerEnvironment:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # -- internals --------------------------------------------------------

    @staticmethod
    def _run_argv(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        # A fixed argv and no shell, so nothing in a path or command name is
        # reinterpreted by the host shell.
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False, shell=False
        )


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return detail[-1] if detail else "no output"


def _quote(value: str) -> str:
    """Single-quote a value for the container's shell."""
    return "'" + value.replace("'", "'\\''") + "'"
