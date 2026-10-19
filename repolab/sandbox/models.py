"""Types describing an isolated execution environment.

Capabilities are modelled explicitly because the underlying service does not
support everything one might expect. Rather than let callers assume, a provider
states what it can actually do -- notably that Nebius Sandboxes offer no way to
delete an image (see :class:`SandboxCapabilities`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class SandboxCapabilities(BaseModel):
    """What a provider actually supports.

    Discoverable in code so that a caller never has to guess, and so a limitation
    of the service shows up as a fact rather than as a surprising failure.
    """

    supports_checkpoints: bool = True
    supports_restore: bool = True
    supports_file_upload: bool = True

    supports_deletion: bool = False
    """Whether state can be explicitly deleted.

    False for Nebius Sandboxes: the API exposes no delete operation, so
    ``terminate`` can only release a reference and let retention reclaim it.
    """

    supports_network: bool | None = None
    """Whether the environment has outbound network access.

    ``None`` means undocumented and untested, which is the honest answer for
    Nebius Sandboxes today.
    """

    max_concurrent_operations: int | None = None
    retention_days: int | None = None
    notes: list[str] = Field(default_factory=list)


class SandboxHandle(BaseModel):
    """An opaque reference to live environment state.

    Callers pass handles around and never construct provider-specific identifiers
    themselves.
    """

    id: str
    image: str
    from_checkpoint: str | None = None
    """The checkpoint this handle was restored from, if any."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_restored(self) -> bool:
        return self.from_checkpoint is not None


class Checkpoint(BaseModel):
    """A saved state that later work can start from.

    Every experiment variant will begin from one of these, which is why a
    checkpoint must be independently restorable rather than a position in a
    mutable session.
    """

    id: str
    tag: str | None = None
    image: str | None = None
    parent_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def reference(self) -> str:
        """The durable name if it has one, otherwise the id."""
        return self.tag or self.id


class CheckpointMissingError(RuntimeError):
    """A checkpoint RepoLab recorded is no longer on this machine.

    Distinct from :class:`SandboxError` because the remedy is different and the
    generic advice is actively wrong: Docker is running, the environment was
    really built, and what is gone is the image a cached build points at --
    `docker image prune` is enough to cause it. Telling someone to check Docker
    sends them looking in the wrong place, and every retry fails identically
    until the stale row is forgotten.
    """


class SandboxError(RuntimeError):
    """An operation against the execution environment failed."""


class UnsupportedOperationError(SandboxError):
    """The provider does not support this operation.

    Raised instead of silently doing nothing, so an unsupported capability cannot
    be mistaken for a successful one.
    """

    def __init__(self, operation: str, provider: str) -> None:
        self.operation = operation
        self.provider = provider
        super().__init__(f"{provider} does not support {operation}")
