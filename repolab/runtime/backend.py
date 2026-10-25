"""Where services actually run: isolated containers, reachable only through RepoLab.

The worker never runs repository code. It issues ``docker`` commands; the code
runs inside containers, and those containers are held to this shape:

    build     a disposable container on Docker's default network (the package
              registry has to be reachable), as the image's unprivileged user,
              committed to an image so the next start skips the install
    run       on a per-runtime ``--internal`` network: no internet, no host, no
              RepoLab Postgres or Redis -- measured, not assumed
              --cpus, --memory, --pids-limit, --cap-drop ALL, no-new-privileges,
              no mounts, secrets passed in an env file rather than on argv
    expose    a RepoLab sidecar on the internal network *and* a bridge network
              published on 127.0.0.1 only, forwarding to exactly one service
              port. Repository code never holds a host port.

The proxy then reaches a running service only through that sidecar's port,
which only RepoLab knows.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from repolab.interfaces import CommandResult

log = structlog.get_logger(__name__)

#: The forwarding sidecar. Pinned, small, and RepoLab's -- not the repository's.
SIDECAR_IMAGE = "alpine/socat:1.8.0.0"
SIDECAR_PORT = 8080

#: Images for dependencies RepoLab can provision, with a readiness command and
#: the connection string a service is given.
DEPENDENCY_IMAGES: dict[str, tuple[str, str, str, dict[str, str]]] = {
    "mongodb": (
        "mongo:7",
        "mongosh --quiet --eval 'db.runCommand({ping:1}).ok'",
        "mongodb://{host}:27017/app",
        {},
    ),
    "postgres": (
        "postgres:16-alpine",
        "pg_isready -U app -d app",
        "postgresql://app:{password}@{host}:5432/app",
        {"POSTGRES_USER": "app", "POSTGRES_DB": "app", "POSTGRES_PASSWORD": "{password}"},
    ),
    "redis": ("redis:7-alpine", "redis-cli ping", "redis://{host}:6379", {}),
}


@dataclass
class Limits:
    cpus: str = "2"
    memory: str = "3g"
    build_memory: str = "4g"
    pids: int = 512


@dataclass
class Listener:
    address: str
    port: int

    @property
    def everywhere(self) -> bool:
        return self.address in {"0.0.0.0", "::"}

    @property
    def loopback(self) -> bool:
        return self.address.startswith("127.") or self.address == "::1"


@dataclass
class RunSpec:
    image: str
    command: str
    cwd: str
    env: dict[str, str]
    network: str
    alias: str
    user: str | None = "node"
    limits: Limits = field(default_factory=Limits)


class RuntimeBackend(Protocol):
    """What the launcher needs from wherever services run."""

    name: str
    long_running: bool
    """Whether it can keep a server process alive with a reachable port. Nebius
    Sandboxes cannot: they run one command per immutable image."""

    async def image_exists(self, image: str) -> bool: ...
    async def build_container(self, image: str, limits: Limits) -> str: ...
    async def upload(self, container: str, path: str, data: bytes) -> None: ...
    async def exec(
        self,
        container: str,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        timeout: float = 900.0,
    ) -> CommandResult: ...
    async def commit(self, container: str, tag: str) -> None: ...
    async def create_network(self, name: str) -> None: ...
    async def run_service(self, name: str, spec: RunSpec) -> str: ...
    async def run_dependency(
        self, name: str, image: str, network: str, alias: str, env: Mapping[str, str]
    ) -> str: ...
    async def run_sidecar(self, name: str, network: str, target: str, port: int) -> int: ...
    async def alive(self, container: str) -> bool: ...
    async def listeners(self, container: str) -> list[Listener]: ...
    async def logs(self, container: str, *, tail: int = 80) -> str: ...
    async def remove(self, container: str) -> None: ...
    async def remove_network(self, name: str) -> None: ...


def parse_proc_net(text: str, *, ipv6: bool = False) -> list[Listener]:
    """Listening TCP sockets from ``/proc/net/tcp`` or ``tcp6``.

    Read from the kernel's table rather than with ``ss`` or ``netstat``, which
    slim images do not ship.
    """
    found: list[Listener] = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "0A":  # 0A = LISTEN
            continue
        address, _, port_hex = parts[1].partition(":")
        port = int(port_hex, 16)
        if ipv6:
            if set(address) == {"0"}:
                ip = "::"
            elif address == "00000000000000000000000001000000":
                ip = "::1"
            else:
                ip = address
        else:
            octets = [str(int(address[i : i + 2], 16)) for i in range(6, -1, -2)]
            ip = ".".join(octets)
        # Docker's embedded DNS resolver listens inside every container.
        if ip == "127.0.0.11":
            continue
        found.append(Listener(ip, port))
    return found


class DockerRuntimeBackend:
    """Containers on the local Docker daemon."""

    name = "docker"
    long_running = True

    async def image_exists(self, image: str) -> bool:
        result = await _docker("image", "inspect", image, timeout=60)
        return result.returncode == 0

    async def build_container(self, image: str, limits: Limits) -> str:
        await _ensure(image)
        name = f"repolab-build-{os.urandom(6).hex()}"
        result = await _docker(
            "run", "--detach", "--name", name, "--memory", limits.build_memory,
            "--cpus", limits.cpus, "--pids-limit", str(limits.pids * 4),
            "--security-opt", "no-new-privileges", "--entrypoint", "/bin/sh",
            image, "-c", "sleep infinity",
            timeout=300,
        )  # fmt: skip
        if result.returncode != 0:
            raise RuntimeError(f"could not start a build container from {image}: {_tail(result)}")
        await _docker("exec", "--user", "root", name, "mkdir", "-p", "/work", timeout=60)
        return name

    async def upload(self, container: str, path: str, data: bytes) -> None:
        with tempfile.NamedTemporaryFile() as staged:
            staged.write(data)
            staged.flush()
            result = await _docker("cp", staged.name, f"{container}:{path}", timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"could not upload {path}: {_tail(result)}")

    async def exec(
        self,
        container: str,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        timeout: float = 900.0,
    ) -> CommandResult:
        argv = ["exec"]
        if cwd:
            argv += ["--workdir", cwd]
        if user:
            argv += ["--user", user]
        for key, value in (env or {}).items():
            argv += ["--env", f"{key}={value}"]
        argv += [container, "/bin/sh", "-c", command]
        try:
            result = await _docker(*argv, timeout=timeout)
        except subprocess.TimeoutExpired:
            return CommandResult(exit_code=124, stdout="", stderr=f"timed out after {timeout:.0f}s")
        return CommandResult(
            exit_code=result.returncode, stdout=result.stdout or "", stderr=result.stderr or ""
        )

    async def commit(self, container: str, tag: str) -> None:
        result = await _docker("commit", container, tag, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"could not save the installed environment: {_tail(result)}")

    async def create_network(self, name: str) -> None:
        result = await _docker(
            "network", "create", "--internal", "--label", "repolab.runtime=1", name, timeout=60
        )
        if result.returncode != 0 and "already exists" not in (result.stderr or ""):
            raise RuntimeError(f"could not create the runtime network: {_tail(result)}")

    async def run_service(self, name: str, spec: RunSpec) -> str:
        await _ensure(spec.image)
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as env_file:
            for key, value in spec.env.items():
                env_file.write(f"{key}={value}\n")
        try:
            argv = [
                "run", "--detach", "--name", name, "--network", spec.network,
                "--network-alias", spec.alias, "--label", "repolab.runtime=1",
                "--memory", spec.limits.memory, "--cpus", spec.limits.cpus,
                "--pids-limit", str(spec.limits.pids), "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--env-file", env_file.name,
                "--workdir", spec.cwd,
            ]  # fmt: skip
            if spec.user:
                argv += ["--user", spec.user]
            argv += ["--entrypoint", "/bin/sh", spec.image, "-c", spec.command]
            result = await _docker(*argv, timeout=300)
        finally:
            os.unlink(env_file.name)
        if result.returncode != 0:
            raise RuntimeError(f"could not start {spec.alias}: {_tail(result)}")
        return name

    async def run_dependency(
        self, name: str, image: str, network: str, alias: str, env: Mapping[str, str]
    ) -> str:
        """A provisioned database: not repository code, so without the user override."""
        await _ensure(image)
        argv = [
            "run", "--detach", "--name", name, "--network", network, "--network-alias", alias,
            "--label", "repolab.runtime=1", "--memory", "1g", "--pids-limit", "512",
        ]  # fmt: skip
        for key, value in env.items():
            argv += ["--env", f"{key}={value}"]
        argv.append(image)
        result = await _docker(*argv, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"could not start {alias}: {_tail(result)}")
        return name

    async def run_sidecar(self, name: str, network: str, target: str, port: int) -> int:
        await _ensure(SIDECAR_IMAGE)
        result = await _docker(
            "run", "--detach", "--name", name, "--label", "repolab.runtime=1",
            "--publish", f"127.0.0.1::{SIDECAR_PORT}", "--memory", "64m", "--pids-limit", "64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            SIDECAR_IMAGE, f"TCP-LISTEN:{SIDECAR_PORT},fork,reuseaddr", f"TCP:{target}:{port}",
            timeout=300,
        )  # fmt: skip
        if result.returncode != 0:
            raise RuntimeError(f"could not start the RepoLab sidecar: {_tail(result)}")
        connected = await _docker("network", "connect", network, name, timeout=60)
        if connected.returncode != 0:
            raise RuntimeError(f"could not attach the sidecar: {_tail(connected)}")
        published = await _docker("port", name, str(SIDECAR_PORT), timeout=60)
        match = re.search(r"127\.0\.0\.1:(\d+)", published.stdout or "")
        if not match:
            raise RuntimeError("the sidecar did not publish a port")
        return int(match.group(1))

    async def alive(self, container: str) -> bool:
        result = await _docker("inspect", "-f", "{{.State.Running}}", container, timeout=60)
        return (result.stdout or "").strip() == "true"

    async def listeners(self, container: str) -> list[Listener]:
        v4 = await self.exec(container, "cat /proc/net/tcp", timeout=30)
        v6 = await self.exec(container, "cat /proc/net/tcp6 2>/dev/null || true", timeout=30)
        return [*parse_proc_net(v4.stdout), *parse_proc_net(v6.stdout, ipv6=True)]

    async def logs(self, container: str, *, tail: int = 80) -> str:
        result = await _docker("logs", "--tail", str(tail), container, timeout=60)
        return ((result.stdout or "") + (result.stderr or ""))[-6000:]

    async def remove(self, container: str) -> None:
        await _docker("rm", "--force", container, timeout=120)

    async def remove_network(self, name: str) -> None:
        await _docker("network", "rm", name, timeout=60)


async def _docker(*argv: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        subprocess.run,
        ["docker", *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


async def _ensure(image: str) -> None:
    from repolab.sandbox.docker import ensure_image

    status = await asyncio.to_thread(ensure_image, image)
    if not status.ready:
        raise RuntimeError(status.describe())


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    lines = (result.stderr or result.stdout or "").strip().splitlines()
    return lines[-1] if lines else "no output"
