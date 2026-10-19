"""Nebius Sandboxes (the ConTree runtime).

Written against the current official documentation and cross-checked against the
installed ``contree-sdk`` 0.3.6 source. The mapping from RepoLab's lifecycle to
the real API:

===========  ====================================================================
create       ``sdk.images.use(ref)`` resolves lazily; ``use(ref, strict=True)``
             verifies via the API; ``sdk.images.oci("docker://…")`` imports from a
             registry when the image is not held locally.
upload       ``image.apply_files(files={dest: bytes})`` derives a new image with
             those files present.
execute      ``await image.run(shell=…, disposable=…, timeout=…, env=…, cwd=…)``.
             ``run()`` only *prepares*; awaiting it executes and yields a new image
             exposing ``stdout``, ``stderr``, ``exit_code``, ``elapsed``, ``uuid``
             and ``result`` (which also carries ``cost``).
checkpoint   ``run(…, disposable=False)`` persists the resulting state;
             ``tag_as(tag)`` gives it a durable name. The id is ``.uuid``.
restore      ``sdk.images.use(uuid_or_tag)`` -- resolving a saved image *is*
             starting from a checkpoint, and each resolve is independent.
terminate    **No deletion API exists.** Confirmed in the documentation and by
             searching the installed SDK. ``terminate`` therefore untags, leaving
             the image unreferenced for the provider's 180-day retention, and
             :attr:`capabilities` reports ``supports_deletion=False``.
===========  ====================================================================

Authentication needs two values: ``IAMAuth`` sends ``Authorization: Bearer`` *and*
a ``Project`` header, so ``NEBIUS_PROJECT_ID`` is required alongside the API key.

State is handled immutably rather than through ``ContreeSession``: a session's
``_copy_self`` returns ``self``, so it mutates in place and cannot be forked.
Experiment variants must each start from the *same* checkpoint, which the
image-chaining style supports and a session does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

from repolab.interfaces import CommandResult
from repolab.sandbox.models import (
    Checkpoint,
    SandboxCapabilities,
    SandboxError,
    SandboxHandle,
)

if TYPE_CHECKING:
    from contree_sdk import Contree

log = structlog.get_logger(__name__)

#: Documented service limits (Nebius Sandboxes).
MAX_CONCURRENT_OPERATIONS = 50
CHECKPOINT_RETENTION_DAYS = 180


def _as_text(value: Any) -> str:
    """Normalise a stream value, which may be ``str``, ``bytes`` or ``None``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


class NebiusSandboxProvider:
    """Isolated execution on Nebius Sandboxes."""

    name = "nebius"

    def __init__(
        self,
        api_key: str,
        project_id: str,
        *,
        operation_timeout: float = 900.0,
        operation_run_timeout: float = 600.0,
        operation_import_timeout: float = 1200.0,
        default_command_timeout: float = 300.0,
    ) -> None:
        # Imported lazily so importing repolab never requires the SDK.
        from contree_sdk import Contree
        from contree_sdk.auth import IAMAuth
        from contree_sdk.config import ContreeConfig

        self._config = ContreeConfig(
            auth=IAMAuth(token=api_key, project_id=project_id),
            operation_timeout=operation_timeout,
            operation_run_timeout=operation_run_timeout,
            operation_import_timeout=operation_import_timeout,
        )
        self._sdk: Contree = Contree(self._config)
        self._default_command_timeout = default_command_timeout

        # Handle id -> the live image object it refers to.
        self._images: dict[str, Any] = {}
        self._counter = 0

    # -- capabilities -----------------------------------------------------

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supports_checkpoints=True,
            supports_restore=True,
            supports_file_upload=True,
            # The API exposes no delete operation; terminate can only untag.
            supports_deletion=False,
            # Egress is not documented, and has not been tested.
            supports_network=None,
            max_concurrent_operations=MAX_CONCURRENT_OPERATIONS,
            retention_days=CHECKPOINT_RETENTION_DAYS,
            notes=[
                "no deletion API: terminate untags and relies on retention",
                "inbound networking and port exposure are not documented",
                f"at most {MAX_CONCURRENT_OPERATIONS} simultaneous operations",
            ],
        )

    # -- lifecycle --------------------------------------------------------

    def _register(self, image: Any, *, from_checkpoint: str | None = None) -> SandboxHandle:
        self._counter += 1
        handle_id = f"nebius-{self._counter}"
        self._images[handle_id] = image
        uuid = getattr(image, "uuid", None)
        return SandboxHandle(
            id=handle_id,
            image=str(getattr(image, "tag", None) or uuid or "unknown"),
            from_checkpoint=from_checkpoint,
        )

    def _image(self, handle: SandboxHandle) -> Any:
        image = self._images.get(handle.id)
        if image is None:
            raise SandboxError(f"unknown sandbox handle: {handle.id!r}")
        return image

    async def create(self, image: str, *, from_checkpoint: str | None = None) -> SandboxHandle:
        """Resolve *image*, importing it from a registry if necessary."""
        if from_checkpoint is not None:
            return await self.restore(from_checkpoint)

        try:
            if image.startswith("docker://"):
                # `oci` behaves like use(strict=True) but imports when not found.
                resolved = await self._sdk.images.oci(image)
            else:
                # `strict=True` matters: without it `use` resolves lazily and
                # returns without calling the API at all, so a nonexistent image
                # -- or an account with no sandbox entitlement -- would look like
                # a successful creation until the first command failed.
                resolved = await self._sdk.images.use(image, strict=True)
        except Exception as exc:
            raise SandboxError(f"could not prepare image {image!r}: {exc}") from exc

        log.info("sandbox.created", image=image)
        return self._register(resolved)

    async def upload(self, handle: SandboxHandle, files: Mapping[str, bytes]) -> SandboxHandle:
        """Place *files* into the environment, deriving new state."""
        image = self._image(handle)
        try:
            derived = await image.apply_files(files=dict(files))
        except Exception as exc:
            raise SandboxError(f"upload failed: {exc}") from exc

        self._images[handle.id] = derived
        log.info("sandbox.uploaded", files=len(files))
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
        """Run *command* and advance the environment.

        State is preserved (``disposable=False``) so the next step continues from
        here and the result can be checkpointed.
        """
        image = self._image(handle)
        try:
            executed = await image.run(
                shell=command,
                disposable=False,
                timeout=timeout if timeout is not None else self._default_command_timeout,
                cwd=cwd,
                env=dict(env) if env else None,
            )
        except Exception as exc:  # noqa: BLE001 - reported as a failed command
            return handle, CommandResult(stderr=f"{type(exc).__name__}: {exc}", exit_code=None)

        self._images[handle.id] = executed
        result = getattr(executed, "result", None)
        elapsed = getattr(executed, "elapsed", None)
        uuid = getattr(executed, "uuid", None)

        return handle, CommandResult(
            stdout=_as_text(getattr(executed, "stdout", None)),
            stderr=_as_text(getattr(executed, "stderr", None)),
            exit_code=getattr(executed, "exit_code", None),
            state_id=str(uuid) if uuid else None,
            elapsed_seconds=elapsed.total_seconds() if elapsed is not None else None,
            cost=getattr(result, "cost", None),
        )

    async def checkpoint(self, handle: SandboxHandle, *, tag: str | None = None) -> Checkpoint:
        """Persist the current state, optionally under a durable tag."""
        image = self._image(handle)
        uuid = getattr(image, "uuid", None)
        if uuid is None:
            raise SandboxError(
                "cannot checkpoint: the current state has no id yet "
                "(run a command with preserved state first)"
            )

        if tag:
            try:
                tagged = await image.tag_as(tag)
            except Exception as exc:
                raise SandboxError(f"could not tag checkpoint: {exc}") from exc
            self._images[handle.id] = tagged
            image = tagged

        log.info("sandbox.checkpointed", checkpoint=str(uuid), tag=tag)
        return Checkpoint(
            id=str(uuid),
            tag=tag,
            image=handle.image,
            parent_id=handle.from_checkpoint,
        )

    async def restore(self, checkpoint: str | Checkpoint) -> SandboxHandle:
        """Start a fresh environment from a saved checkpoint.

        Each call resolves the saved image independently, so two restores of one
        checkpoint do not share state.
        """
        reference = checkpoint.reference if isinstance(checkpoint, Checkpoint) else checkpoint
        try:
            resolved = await self._sdk.images.use(reference, strict=True)
        except Exception as exc:
            raise SandboxError(f"could not restore {reference!r}: {exc}") from exc

        log.info("sandbox.restored", checkpoint=reference)
        return self._register(resolved, from_checkpoint=reference)

    async def terminate(self, handle: SandboxHandle) -> None:
        """Release the environment.

        The API has no delete operation, so this untags the image -- making it
        unreferenced, and therefore eligible for the provider's 180-day retention
        to reclaim -- and drops the local reference. It does not delete anything
        immediately, which is why ``capabilities.supports_deletion`` is False.
        """
        image = self._images.pop(handle.id, None)
        if image is None:
            return
        if getattr(image, "tag", None):
            try:
                await image.untag()
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                log.warning("sandbox.untag_failed", handle=handle.id, error=str(exc))

    async def close(self) -> None:
        """Release local references. The installed client exposes no close."""
        self._images.clear()
