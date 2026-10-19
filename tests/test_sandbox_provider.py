"""The sandbox provider contract.

The property that matters most for everything built on top of this is checkpoint
isolation: restoring one checkpoint twice must give two environments that cannot
affect each other. Without it, a later claim that "every experiment variant
started from identical state" would be unfounded.
"""

from __future__ import annotations

import pytest

from repolab.sandbox import (
    Checkpoint,
    FakeSandboxProvider,
    SandboxError,
    SandboxHandle,
    SandboxProvider,
    ScriptedCommand,
)


@pytest.fixture
def provider() -> FakeSandboxProvider:
    return FakeSandboxProvider(
        scripted={
            "echo hello": ScriptedCommand(stdout="hello\n"),
            "make base": ScriptedCommand(creates=("/work/base",)),
            "make a": ScriptedCommand(creates=("/work/a",)),
            "make b": ScriptedCommand(creates=("/work/b",)),
            "boom": ScriptedCommand(stderr="it broke", exit_code=2),
            "slow": ScriptedCommand(delay_seconds=5.0),
            "explode": ScriptedCommand(raises=RuntimeError("provider exploded")),
        }
    )


class TestConformance:
    def test_the_fake_satisfies_the_protocol(self, provider: FakeSandboxProvider) -> None:
        assert isinstance(provider, SandboxProvider)

    def test_the_nebius_provider_satisfies_the_protocol(self) -> None:
        """Checked without constructing it, which would need credentials."""
        from repolab.sandbox.nebius import NebiusSandboxProvider

        for method in ("create", "upload", "execute", "checkpoint", "restore", "terminate"):
            assert callable(getattr(NebiusSandboxProvider, method))
        assert isinstance(NebiusSandboxProvider.capabilities, property)


class TestCapabilitiesAreHonest:
    def test_the_fake_reports_what_it_can_do(self, provider: FakeSandboxProvider) -> None:
        capabilities = provider.capabilities
        assert capabilities.supports_checkpoints is True
        assert capabilities.supports_restore is True
        assert capabilities.supports_file_upload is True

    def test_nebius_reports_that_it_cannot_delete(self) -> None:
        """The API exposes no delete operation, so this must not claim otherwise."""
        from repolab.sandbox.nebius import (
            CHECKPOINT_RETENTION_DAYS,
            MAX_CONCURRENT_OPERATIONS,
            NebiusSandboxProvider,
        )

        # Read off the class without constructing a client.
        capabilities = NebiusSandboxProvider.capabilities.fget(  # type: ignore[union-attr]
            object.__new__(NebiusSandboxProvider)
        )
        assert capabilities.supports_deletion is False
        assert capabilities.supports_network is None  # undocumented, untested
        assert capabilities.retention_days == CHECKPOINT_RETENTION_DAYS
        assert capabilities.max_concurrent_operations == MAX_CONCURRENT_OPERATIONS
        assert any("no deletion API" in note for note in capabilities.notes)


class TestCreate:
    async def test_it_returns_a_handle(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("python:3.11-slim")
        assert isinstance(handle, SandboxHandle)
        assert handle.image == "python:3.11-slim"
        assert handle.is_restored is False

    async def test_two_creates_are_independent(self, provider: FakeSandboxProvider) -> None:
        first = await provider.create("busybox")
        second = await provider.create("busybox")
        assert first.id != second.id

    async def test_a_creation_failure_propagates(self) -> None:
        failing = FakeSandboxProvider(fail_on_create=RuntimeError("401 unauthorized"))
        with pytest.raises(RuntimeError, match="401"):
            await failing.create("busybox")


class TestExecute:
    async def test_it_returns_the_handle_and_the_result(
        self, provider: FakeSandboxProvider
    ) -> None:
        handle = await provider.create("busybox")
        returned, result = await provider.execute(handle, "echo hello")

        assert returned.id == handle.id
        assert result.stdout == "hello\n"
        assert result.exit_code == 0

    async def test_a_nonzero_exit_is_a_result_not_an_exception(
        self, provider: FakeSandboxProvider
    ) -> None:
        handle = await provider.create("busybox")
        _, result = await provider.execute(handle, "boom")

        assert result.exit_code == 2
        assert result.stderr == "it broke"

    async def test_a_timeout_is_reported_as_a_result(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        _, result = await provider.execute(handle, "slow", timeout=0.01)

        assert result.exit_code == 124
        assert "timed out" in result.stderr

    async def test_a_provider_exception_propagates(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        with pytest.raises(RuntimeError, match="exploded"):
            await provider.execute(handle, "explode")

    async def test_an_unknown_handle_is_refused(self, provider: FakeSandboxProvider) -> None:
        stray = SandboxHandle(id="nope", image="busybox")
        with pytest.raises(SandboxError, match="unknown sandbox handle"):
            await provider.execute(stray, "echo hello")

    async def test_state_accumulates_across_commands(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        handle, _ = await provider.execute(handle, "make a")

        assert set(provider.files_in(handle)) == {"/work/base", "/work/a"}


class TestUpload:
    async def test_files_land_in_the_environment(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        handle = await provider.upload(handle, {"/work/repo.tar.gz": b"archive-bytes"})

        assert provider.files_in(handle)["/work/repo.tar.gz"] == b"archive-bytes"

    async def test_uploads_are_recorded(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        await provider.upload(handle, {"/a": b"1", "/b": b"2"})
        assert provider.uploads == [(handle.id, ("/a", "/b"))]


class TestCheckpointAndRestore:
    async def test_a_checkpoint_has_an_id(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        checkpoint = await provider.checkpoint(handle)

        assert isinstance(checkpoint, Checkpoint)
        assert checkpoint.id
        assert checkpoint.reference == checkpoint.id

    async def test_a_tag_becomes_the_durable_reference(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        checkpoint = await provider.checkpoint(handle, tag="repolab/demo:v1")

        assert checkpoint.tag == "repolab/demo:v1"
        assert checkpoint.reference == "repolab/demo:v1"

    async def test_restore_returns_the_state_as_of_the_checkpoint(
        self, provider: FakeSandboxProvider
    ) -> None:
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        checkpoint = await provider.checkpoint(handle)

        # Change the live environment *after* checkpointing.
        handle, _ = await provider.execute(handle, "make a")
        assert "/work/a" in provider.files_in(handle)

        restored = await provider.restore(checkpoint)
        assert set(provider.files_in(restored)) == {"/work/base"}
        assert "/work/a" not in provider.files_in(restored)

    async def test_restoring_by_tag_works(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        await provider.checkpoint(handle, tag="repolab/demo:v1")

        restored = await provider.restore("repolab/demo:v1")
        assert "/work/base" in provider.files_in(restored)

    async def test_two_branches_from_one_checkpoint_are_independent(
        self, provider: FakeSandboxProvider
    ) -> None:
        """The property every experiment variant will depend on."""
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        checkpoint = await provider.checkpoint(handle)

        branch_a = await provider.restore(checkpoint)
        branch_b = await provider.restore(checkpoint)
        await provider.execute(branch_a, "make a")
        await provider.execute(branch_b, "make b")

        files_a = set(provider.files_in(branch_a))
        files_b = set(provider.files_in(branch_b))

        assert branch_a.id != branch_b.id
        assert files_a == {"/work/base", "/work/a"}
        assert files_b == {"/work/base", "/work/b"}

    async def test_both_branches_start_from_identical_state(
        self, provider: FakeSandboxProvider
    ) -> None:
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        checkpoint = await provider.checkpoint(handle)

        first = await provider.restore(checkpoint)
        second = await provider.restore(checkpoint)
        assert provider.files_in(first) == provider.files_in(second)

    async def test_a_restored_handle_records_its_origin(
        self, provider: FakeSandboxProvider
    ) -> None:
        handle = await provider.create("busybox")
        checkpoint = await provider.checkpoint(handle)
        restored = await provider.restore(checkpoint)

        assert restored.is_restored is True
        assert restored.from_checkpoint == checkpoint.reference

    async def test_an_unknown_checkpoint_is_refused(self, provider: FakeSandboxProvider) -> None:
        with pytest.raises(SandboxError, match="unknown checkpoint"):
            await provider.restore("does-not-exist")

    async def test_create_can_start_from_a_checkpoint(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        checkpoint = await provider.checkpoint(handle)

        started = await provider.create("busybox", from_checkpoint=checkpoint.id)
        assert "/work/base" in provider.files_in(started)


class TestTerminate:
    async def test_it_releases_the_environment(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        await provider.terminate(handle)

        assert handle.id in provider.terminated
        with pytest.raises(SandboxError):
            await provider.execute(handle, "echo hello")

    async def test_a_checkpoint_outlives_the_environment(
        self, provider: FakeSandboxProvider
    ) -> None:
        """A build's checkpoint must survive tearing down the builder."""
        handle = await provider.create("busybox")
        handle, _ = await provider.execute(handle, "make base")
        checkpoint = await provider.checkpoint(handle)

        await provider.terminate(handle)
        restored = await provider.restore(checkpoint)
        assert "/work/base" in provider.files_in(restored)

    async def test_terminating_twice_is_harmless(self, provider: FakeSandboxProvider) -> None:
        handle = await provider.create("busybox")
        await provider.terminate(handle)
        await provider.terminate(handle)

    async def test_close_is_recorded(self, provider: FakeSandboxProvider) -> None:
        await provider.close()
        assert provider.closed is True
