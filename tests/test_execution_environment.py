"""The execution abstraction.

The point of `ExecutionEnvironment` is that callers above it need no knowledge of
where work runs. These tests hold it to that: nothing here mentions a sandbox
handle, and the same code would drive a VM environment unchanged.
"""

from __future__ import annotations

import pytest

from repolab.sandbox import (
    EnvironmentNotStartedError,
    ExecutionEnvironment,
    FakeSandboxProvider,
    SandboxEnvironment,
    ScriptedCommand,
)


@pytest.fixture
def environment() -> tuple[SandboxEnvironment, FakeSandboxProvider]:
    provider = FakeSandboxProvider(
        scripted={
            "echo ready": ScriptedCommand(stdout="ready\n"),
            "make thing": ScriptedCommand(creates=("/work/thing",)),
        }
    )
    return SandboxEnvironment(provider), provider


class TestConformance:
    def test_it_satisfies_the_protocol(self, environment) -> None:
        env, _ = environment
        assert isinstance(env, ExecutionEnvironment)
        assert env.kind == "sandbox"

    def test_it_names_its_provider(self, environment) -> None:
        env, _ = environment
        assert env.provider_name == "fake"

    def test_it_exposes_the_provider_capabilities(self, environment) -> None:
        """A caller asks the environment, not the provider underneath."""
        env, _ = environment
        assert env.capabilities.supports_checkpoints is True


class TestLifecycle:
    async def test_a_command_before_start_is_refused(self, environment) -> None:
        env, _ = environment
        assert env.started is False
        with pytest.raises(EnvironmentNotStartedError):
            await env.run("echo ready")

    async def test_start_then_run(self, environment) -> None:
        env, _ = environment
        await env.start("python:3.11-slim")

        assert env.started is True
        result = await env.run("echo ready")
        assert result.stdout == "ready\n"

    async def test_state_carries_between_runs(self, environment) -> None:
        env, provider = environment
        await env.start("busybox")
        await env.run("make thing")
        assert "/work/thing" in provider.files_in(env.handle)

    async def test_files_can_be_placed(self, environment) -> None:
        env, provider = environment
        await env.start("busybox")
        await env.put_files({"/work/repo.tar.gz": b"archive"})
        assert provider.files_in(env.handle)["/work/repo.tar.gz"] == b"archive"

    async def test_save_returns_a_checkpoint(self, environment) -> None:
        env, _ = environment
        await env.start("busybox")
        await env.run("make thing")
        checkpoint = await env.save(tag="repolab/demo:v1")

        assert checkpoint.id
        assert checkpoint.tag == "repolab/demo:v1"

    async def test_stop_releases_the_environment(self, environment) -> None:
        env, provider = environment
        await env.start("busybox")
        handle_id = env.handle.id
        await env.stop()

        assert env.started is False
        assert handle_id in provider.terminated

    async def test_stopping_an_unstarted_environment_is_harmless(self, environment) -> None:
        env, _ = environment
        await env.stop()

    async def test_it_works_as_a_context_manager(self, environment) -> None:
        env, provider = environment
        async with env:
            await env.start("busybox")
            handle_id = env.handle.id
        assert handle_id in provider.terminated

    async def test_it_can_start_from_a_checkpoint(self, environment) -> None:
        env, provider = environment
        await env.start("busybox")
        await env.run("make thing")
        checkpoint = await env.save()
        await env.stop()

        resumed = SandboxEnvironment(provider)
        await resumed.start("busybox", from_checkpoint=checkpoint.id)
        assert "/work/thing" in provider.files_in(resumed.handle)
