"""The execution abstraction.

Everything above this layer asks for an :class:`ExecutionEnvironment` and does not
care where work actually happens. Today only :class:`SandboxEnvironment` exists,
backed by a :class:`~repolab.sandbox.provider.SandboxProvider`.

A ``VMEnvironment`` will implement the same protocol when something needs a
lifecycle sandboxes cannot provide -- a long-running Cassandra or Airflow with
exposed ports, since inbound networking is undocumented for sandboxes. It is
deliberately not stubbed here: an empty class would be dead code, and the protocol
already records where it fits.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from repolab.interfaces import CommandResult
from repolab.sandbox.models import Checkpoint, SandboxCapabilities, SandboxHandle
from repolab.sandbox.provider import SandboxProvider


@runtime_checkable
class ExecutionEnvironment(Protocol):
    """Somewhere RepoLab can run commands and save state.

    Deliberately narrow, and deliberately silent about *where* it runs.
    """

    kind: str

    @property
    def capabilities(self) -> SandboxCapabilities: ...

    async def start(
        self,
        image: str,
        *,
        from_checkpoint: str | None = None,
        command: str | None = None,
        memory: str | None = None,
    ) -> None:
        """Bring the environment up.

        *command*, where a backend supports it, becomes the main process -- for
        software that has to run as a server. Backends that cannot honour it
        ignore it rather than failing, and the caller finds out from the smoke
        test, which is the thing that decides readiness anyway.
        """
        ...

    async def put_files(self, files: Mapping[str, bytes]) -> None: ...

    async def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...

    async def save(self, *, tag: str | None = None) -> Checkpoint: ...

    async def stop(self) -> None: ...


class EnvironmentNotStartedError(RuntimeError):
    """A command was attempted before the environment was started."""


class SandboxEnvironment:
    """An :class:`ExecutionEnvironment` backed by a sandbox provider.

    Holds the current handle so callers work with a single object rather than
    threading provider state through every call.
    """

    kind = "sandbox"

    def __init__(self, provider: SandboxProvider) -> None:
        self._provider = provider
        self._handle: SandboxHandle | None = None

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self._provider.capabilities

    @property
    def handle(self) -> SandboxHandle:
        if self._handle is None:
            raise EnvironmentNotStartedError("the environment has not been started")
        return self._handle

    @property
    def started(self) -> bool:
        return self._handle is not None

    async def start(
        self,
        image: str,
        *,
        from_checkpoint: str | None = None,
        command: str | None = None,
        memory: str | None = None,
    ) -> None:
        # Passed only to providers that accept them: `SandboxProvider` does not
        # require either, and a provider that cannot run a chosen main process
        # should not fail to start because one was offered.
        extra: dict[str, object] = {}
        if command is not None:
            extra["command"] = command
        if memory is not None:
            extra["memory"] = memory

        try:
            self._handle = await self._provider.create(
                image, from_checkpoint=from_checkpoint, **extra
            )
        except TypeError:
            if not extra:
                raise
            self._handle = await self._provider.create(image, from_checkpoint=from_checkpoint)

    async def put_files(self, files: Mapping[str, bytes]) -> None:
        self._handle = await self._provider.upload(self.handle, files)

    async def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        handle, result = await self._provider.execute(
            self.handle, command, timeout=timeout, cwd=cwd, env=env
        )
        self._handle = handle
        return result

    async def save(self, *, tag: str | None = None) -> Checkpoint:
        return await self._provider.checkpoint(self.handle, tag=tag)

    async def stop(self) -> None:
        if self._handle is not None:
            await self._provider.terminate(self._handle)
            self._handle = None

    async def __aenter__(self) -> SandboxEnvironment:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()
