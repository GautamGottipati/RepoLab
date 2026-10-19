"""The sandbox provider contract.

Shaped to the lifecycle the underlying API actually supports, verified against
the current Nebius Sandboxes documentation and the installed ``contree-sdk``:

    create      resolve a base image, or restore a checkpoint
    upload      place files into the environment
    execute     run a command and collect its result
    checkpoint  persist the current state under a durable reference
    restore     start again from a saved checkpoint
    terminate   release the environment

User code never executes on the API host: everything goes through a provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from repolab.interfaces import CommandResult
from repolab.sandbox.models import Checkpoint, SandboxCapabilities, SandboxHandle


@runtime_checkable
class SandboxProvider(Protocol):
    """Isolated command execution."""

    name: str

    @property
    def capabilities(self) -> SandboxCapabilities:
        """What this provider supports. Callers should ask rather than assume."""
        ...

    async def create(self, image: str, *, from_checkpoint: str | None = None) -> SandboxHandle:
        """Prepare an environment from *image*, or from a saved checkpoint."""
        ...

    async def upload(self, handle: SandboxHandle, files: Mapping[str, bytes]) -> SandboxHandle:
        """Place *files* (destination path -> contents) into the environment.

        Returns a handle for the resulting state, since uploading derives new state.
        """
        ...

    async def execute(
        self,
        handle: SandboxHandle,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> tuple[SandboxHandle, CommandResult]:
        """Run *command*, returning the resulting state and what it produced.

        The handle is returned alongside the result because execution advances the
        environment; a caller continues from the handle it is given.
        """
        ...

    async def checkpoint(self, handle: SandboxHandle, *, tag: str | None = None) -> Checkpoint:
        """Persist the current state so later work can start from it."""
        ...

    async def restore(
        self,
        checkpoint: str | Checkpoint,
        *,
        command: str | None = None,
        memory: str | None = None,
    ) -> SandboxHandle:
        """Start a fresh environment from a saved checkpoint.

        Restoring the same checkpoint twice must yield independent environments:
        that is what lets experiment variants begin from identical state.

        *command* runs as the environment's main process, which is how a
        checkpoint containing a server becomes a running server for a terminal
        session to connect to. *memory* is the limit the software needs -- both
        are declared by the artifact, because the artifact is what survives in
        the cache after the plan that built it is gone.
        """
        ...

    async def terminate(self, handle: SandboxHandle) -> None:
        """Release the environment.

        Whether this deletes anything depends on the provider; see
        :attr:`SandboxCapabilities.supports_deletion`.
        """
        ...

    async def close(self) -> None:
        """Release provider-held resources."""
        ...
