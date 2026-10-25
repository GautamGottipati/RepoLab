"""An in-memory runtime backend, for tests that must not need Docker.

Scripted rather than simulated: a test says what each command returns and how
each service behaves once started -- which ports it listens on, whether it
exits, what it logs -- and the launcher is exercised against exactly that. It
never pretends to run anything.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from repolab.interfaces import CommandResult
from repolab.runtime.backend import Limits, Listener, RunSpec


@dataclass
class ServiceScript:
    """How a started service behaves, by its alias."""

    listeners: list[Listener] = field(default_factory=list)
    listen_after: int = 0
    """Polls before it starts listening, to exercise the wait."""

    exits: bool = False
    logs: str = ""


@dataclass
class FakeRuntimeBackend:
    name: str = "fake"
    long_running: bool = True
    commands: dict[str, CommandResult] = field(default_factory=dict)
    """Regex -> result for commands run while building. Unmatched ones succeed."""

    services: dict[str, ServiceScript] = field(default_factory=dict)
    images: set[str] = field(default_factory=set)

    executed: list[tuple[str, str, str | None, dict[str, str]]] = field(default_factory=list)
    started: list[RunSpec] = field(default_factory=list)
    dependencies: list[tuple[str, str]] = field(default_factory=list)
    sidecars: dict[str, tuple[str, int]] = field(default_factory=dict)
    networks: set[str] = field(default_factory=set)
    removed: list[str] = field(default_factory=list)
    _polls: dict[str, int] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)
    _next_port: int = 41000

    async def image_exists(self, image: str) -> bool:
        return image in self.images

    async def build_container(self, image: str, limits: Limits) -> str:
        return f"build-{len(self.executed)}-{image}"

    async def upload(self, container: str, path: str, data: bytes) -> None:
        self.executed.append((container, f"upload {path}", None, {}))

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
        self.executed.append((container, command, cwd, dict(env or {})))
        for pattern, result in self.commands.items():
            if re.search(pattern, command):
                return result
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def commit(self, container: str, tag: str) -> None:
        self.images.add(tag)

    async def create_network(self, name: str) -> None:
        self.networks.add(name)

    async def run_service(self, name: str, spec: RunSpec) -> str:
        self.started.append(spec)
        self._aliases[name] = spec.alias
        return name

    async def run_dependency(
        self, name: str, image: str, network: str, alias: str, env: Mapping[str, str]
    ) -> str:
        self.dependencies.append((alias, image))
        self._aliases[name] = alias
        return name

    async def run_sidecar(self, name: str, network: str, target: str, port: int) -> int:
        self._next_port += 1
        self.sidecars[name] = (target, port)
        return self._next_port

    def _script(self, container: str) -> ServiceScript:
        return self.services.get(self._aliases.get(container, ""), ServiceScript())

    async def alive(self, container: str) -> bool:
        return not self._script(container).exits

    async def listeners(self, container: str) -> list[Listener]:
        script = self._script(container)
        polls = self._polls.get(container, 0) + 1
        self._polls[container] = polls
        return script.listeners if polls > script.listen_after else []

    async def logs(self, container: str, *, tail: int = 80) -> str:
        return self._script(container).logs

    async def remove(self, container: str) -> None:
        self.removed.append(container)

    async def remove_network(self, name: str) -> None:
        self.networks.discard(name)
