"""An in-memory sandbox provider.

Not a stub. It models the semantics that matter: state accumulates as commands
run, a checkpoint captures a *snapshot* of that state, and restoring a checkpoint
twice produces two environments that cannot affect each other. That last property
is what makes later experiment tests meaningful -- if the fake let branches share
state, a test proving "every variant starts from identical state" would prove
nothing.

Commands are scripted rather than interpreted: the fake does not pretend to be a
shell. Anything unscripted succeeds with empty output, and a small number of
built-in behaviours (``echo``, file creation via ``tar``) keep build plans
realistic.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from repolab.interfaces import CommandResult
from repolab.sandbox.models import (
    Checkpoint,
    SandboxCapabilities,
    SandboxError,
    SandboxHandle,
)

_ECHO = re.compile(r"^\s*echo\s+(?P<text>.*)$")


@dataclass
class ScriptedCommand:
    """What the fake should do when a command matches."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    creates: tuple[str, ...] = ()
    """Paths this command adds to the environment's filesystem."""

    delay_seconds: float = 0.0
    raises: Exception | None = None


@dataclass
class _State:
    """One environment's contents."""

    image: str
    files: dict[str, bytes] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    from_checkpoint: str | None = None

    def snapshot(self) -> _State:
        """A deep-enough copy that later mutation cannot leak across branches."""
        return _State(
            image=self.image,
            files=dict(self.files),
            commands=list(self.commands),
            from_checkpoint=self.from_checkpoint,
        )


class FakeSandboxProvider:
    """A sandbox provider backed by in-memory state."""

    name = "fake"

    def __init__(
        self,
        *,
        scripted: Mapping[str, ScriptedCommand] | None = None,
        default_exit_code: int = 0,
        fail_on_create: Exception | None = None,
        supports_network: bool | None = True,
    ) -> None:
        self._scripted = dict(scripted or {})
        self._default_exit_code = default_exit_code
        self._fail_on_create = fail_on_create
        self._supports_network = supports_network

        self._states: dict[str, _State] = {}
        self._checkpoints: dict[str, _State] = {}
        self._checkpoint_meta: dict[str, Checkpoint] = {}
        self._counter = 0

        # Observability for tests.
        self.executed: list[tuple[str, str]] = []
        self.terminated: list[str] = []
        self.restored: list[tuple[str, str | None, str | None]] = []
        self.uploads: list[tuple[str, tuple[str, ...]]] = []
        self.closed = False

    # -- helpers ----------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def script(self, command: str, result: ScriptedCommand) -> None:
        """Define what *command* does."""
        self._scripted[command] = result

    def files_in(self, handle: SandboxHandle) -> dict[str, bytes]:
        """The environment's contents, for assertions."""
        return dict(self._state(handle).files)

    def commands_in(self, handle: SandboxHandle) -> list[str]:
        return list(self._state(handle).commands)

    def _state(self, handle: SandboxHandle) -> _State:
        state = self._states.get(handle.id)
        if state is None:
            raise SandboxError(f"unknown sandbox handle: {handle.id!r}")
        return state

    # -- provider surface -------------------------------------------------

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supports_checkpoints=True,
            supports_restore=True,
            supports_file_upload=True,
            # The fake *can* forget state, unlike the real provider.
            supports_deletion=True,
            supports_network=self._supports_network,
            notes=["in-memory provider; nothing is really executed"],
        )

    async def create(self, image: str, *, from_checkpoint: str | None = None) -> SandboxHandle:
        if self._fail_on_create is not None:
            raise self._fail_on_create

        if from_checkpoint is not None:
            return await self.restore(from_checkpoint)

        handle_id = self._next_id("sandbox")
        self._states[handle_id] = _State(image=image)
        return SandboxHandle(id=handle_id, image=image)

    async def upload(self, handle: SandboxHandle, files: Mapping[str, bytes]) -> SandboxHandle:
        state = self._state(handle)
        state.files.update(files)
        self.uploads.append((handle.id, tuple(sorted(files))))
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
        state = self._state(handle)
        self.executed.append((handle.id, command))
        state.commands.append(command)

        scripted = self._scripted.get(command)
        if scripted is not None:
            if scripted.raises is not None:
                raise scripted.raises
            if scripted.delay_seconds:
                if timeout is not None and scripted.delay_seconds > timeout:
                    return handle, CommandResult(
                        stderr=f"timed out after {timeout:.1f}s", exit_code=124
                    )
                await asyncio.sleep(scripted.delay_seconds)
            for path in scripted.creates:
                state.files[path] = b""
            return handle, CommandResult(
                stdout=scripted.stdout,
                stderr=scripted.stderr,
                exit_code=scripted.exit_code,
                state_id=handle.id,
                elapsed_seconds=scripted.delay_seconds,
            )

        return handle, CommandResult(
            stdout=self._builtin_stdout(command),
            exit_code=self._default_exit_code,
            state_id=handle.id,
        )

    @staticmethod
    def _builtin_stdout(command: str) -> str:
        """A couple of behaviours that keep build plans realistic."""
        match = _ECHO.match(command)
        if match:
            return match.group("text").strip("'\"") + "\n"
        return ""

    async def checkpoint(self, handle: SandboxHandle, *, tag: str | None = None) -> Checkpoint:
        state = self._state(handle)
        checkpoint_id = self._next_id("ckpt")
        # A snapshot, not a reference: later mutation of the live state must not
        # change what this checkpoint restores to.
        self._checkpoints[checkpoint_id] = state.snapshot()

        record = Checkpoint(
            id=checkpoint_id,
            tag=tag,
            image=state.image,
            parent_id=handle.from_checkpoint,
            metadata={"files": len(state.files), "commands": len(state.commands)},
        )
        self._checkpoint_meta[checkpoint_id] = record
        if tag:
            self._checkpoint_meta[tag] = record
            self._checkpoints[tag] = self._checkpoints[checkpoint_id]
        return record

    async def restore(
        self,
        checkpoint: str | Checkpoint,
        *,
        command: str | None = None,
        memory: str | None = None,
    ) -> SandboxHandle:
        reference = checkpoint.reference if isinstance(checkpoint, Checkpoint) else checkpoint
        saved = self._checkpoints.get(reference)
        if saved is None:
            raise SandboxError(f"unknown checkpoint: {reference!r}")

        handle_id = self._next_id("sandbox")
        # Each restore gets its own copy, so two branches cannot interfere.
        restored = saved.snapshot()
        restored.from_checkpoint = reference
        self._states[handle_id] = restored
        # Recorded so a test can assert a terminal session restored the
        # checkpoint *as a server*: a restore with no command is a checkpoint
        # with nothing listening, which is the bug this exists to catch.
        self.restored.append((reference, command, memory))
        return SandboxHandle(id=handle_id, image=restored.image, from_checkpoint=reference)

    async def terminate(self, handle: SandboxHandle) -> None:
        self._states.pop(handle.id, None)
        self.terminated.append(handle.id)

    async def close(self) -> None:
        self.closed = True

    # -- test conveniences ------------------------------------------------

    @property
    def checkpoint_count(self) -> int:
        """Distinct checkpoints taken, ignoring tag aliases."""
        return sum(1 for key in self._checkpoint_meta if key.startswith("ckpt-"))

    def checkpoint_metadata(self, reference: str) -> Checkpoint | None:
        return self._checkpoint_meta.get(reference)

    def state_summary(self) -> dict[str, Any]:
        return {
            "live": len(self._states),
            "checkpoints": self.checkpoint_count,
            "executed": len(self.executed),
        }
