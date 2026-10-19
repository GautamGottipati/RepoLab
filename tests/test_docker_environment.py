"""The Docker execution environment.

Repository code must not run on the API host (CLAUDE.md), which is why this
backend exists. The command construction is asserted offline; the lifecycle tests
need a daemon and are marked `docker`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.sandbox import ExecutionEnvironment
from repolab.sandbox.docker import (
    CONTAINER_WORKDIR,
    DockerEnvironment,
    build_exec_command,
    build_run_command,
    docker_available,
)

IMAGE = "apache/spark:3.5.1-python3"


class TestConformance:
    def test_it_satisfies_the_execution_protocol(self, tmp_path: Path) -> None:
        """Callers above this layer need no knowledge of Docker."""
        environment = DockerEnvironment(host_workdir=tmp_path)
        assert isinstance(environment, ExecutionEnvironment)
        assert environment.kind == "docker"

    def test_capabilities_differ_honestly_from_the_sandbox(self, tmp_path: Path) -> None:
        """Docker really can delete an image; Nebius Sandboxes cannot."""
        capabilities = DockerEnvironment(host_workdir=tmp_path).capabilities

        assert capabilities.supports_checkpoints is True
        assert capabilities.supports_restore is True
        assert capabilities.supports_deletion is True
        assert capabilities.supports_network is True


class TestCommandConstruction:
    def test_the_work_directory_is_bind_mounted(self, tmp_path: Path) -> None:
        """The mount is how the event log reaches the host without docker cp."""
        argv = build_run_command(IMAGE, "repolab-test", tmp_path)
        assert "--volume" in argv
        assert f"{tmp_path}:{CONTAINER_WORKDIR}" in argv

    def test_the_container_stays_alive(self, tmp_path: Path) -> None:
        """State must accumulate across commands, as it does in a sandbox."""
        argv = build_run_command(IMAGE, "repolab-test", tmp_path)
        assert "sleep infinity" in " ".join(argv)
        assert "--detach" in argv

    def test_the_image_is_passed(self, tmp_path: Path) -> None:
        assert IMAGE in build_run_command(IMAGE, "repolab-test", tmp_path)

    def test_no_shell_interprets_the_argv(self, tmp_path: Path) -> None:
        """A list argv means a path with a space cannot be re-split."""
        argv = build_run_command(IMAGE, "repolab-test", tmp_path / "a b")
        assert isinstance(argv, list)
        assert f"{tmp_path / 'a b'}:{CONTAINER_WORKDIR}" in argv

    def test_exec_runs_the_command_in_a_shell_inside_the_container(self) -> None:
        argv = build_exec_command("repolab-test", "echo hello")
        assert argv[:2] == ["docker", "exec"]
        assert argv[-3:] == ["/bin/sh", "-lc", "echo hello"]

    def test_exec_can_set_a_working_directory(self) -> None:
        argv = build_exec_command("repolab-test", "ls", cwd="/work/repo")
        assert "--workdir" in argv
        assert "/work/repo" in argv


class TestWorkdirHandling:
    def test_a_supplied_directory_is_used(self, tmp_path: Path) -> None:
        environment = DockerEnvironment(host_workdir=tmp_path)
        assert environment.host_workdir == tmp_path

    def test_a_directory_is_created_when_not_supplied(self) -> None:
        """Under the user's home, since Docker Desktop shares /Users by default."""
        environment = DockerEnvironment()
        try:
            assert environment.host_workdir.exists()
            assert Path.home() in environment.host_workdir.parents
        finally:
            import shutil

            shutil.rmtree(environment.host_workdir, ignore_errors=True)

    def test_commands_before_start_are_refused(self, tmp_path: Path) -> None:
        from repolab.sandbox import EnvironmentNotStartedError

        environment = DockerEnvironment(host_workdir=tmp_path)
        assert environment.started is False
        with pytest.raises(EnvironmentNotStartedError):
            _ = environment.container


@pytest.mark.docker
class TestAgainstARealDaemon:
    """These need Docker; they are deselected by default."""

    IMAGE = "apache/spark:3.5.1-python3"

    async def test_the_lifecycle_works_end_to_end(self, tmp_path: Path) -> None:
        if not docker_available():
            pytest.skip("docker is not available")

        environment = DockerEnvironment(host_workdir=tmp_path)
        async with environment:
            await environment.start(self.IMAGE)
            assert environment.started

            result = await environment.run("echo hello")
            assert result.exit_code == 0
            assert "hello" in result.stdout

    async def test_uploaded_files_are_visible_inside(self, tmp_path: Path) -> None:
        if not docker_available():
            pytest.skip("docker is not available")

        environment = DockerEnvironment(host_workdir=tmp_path)
        async with environment:
            await environment.start(self.IMAGE)
            await environment.put_files({f"{CONTAINER_WORKDIR}/hello.txt": b"from the host"})

            result = await environment.run(f"cat {CONTAINER_WORKDIR}/hello.txt")
            assert "from the host" in result.stdout

    async def test_state_accumulates_across_commands(self, tmp_path: Path) -> None:
        if not docker_available():
            pytest.skip("docker is not available")

        environment = DockerEnvironment(host_workdir=tmp_path)
        async with environment:
            await environment.start(self.IMAGE)
            await environment.run("echo persisted > /tmp/marker")
            result = await environment.run("cat /tmp/marker")
            assert "persisted" in result.stdout

    async def test_a_failing_command_reports_its_exit_code(self, tmp_path: Path) -> None:
        if not docker_available():
            pytest.skip("docker is not available")

        environment = DockerEnvironment(host_workdir=tmp_path)
        async with environment:
            await environment.start(self.IMAGE)
            result = await environment.run("exit 7")
            assert result.exit_code == 7

    async def test_a_timeout_is_reported_not_raised(self, tmp_path: Path) -> None:
        if not docker_available():
            pytest.skip("docker is not available")

        environment = DockerEnvironment(host_workdir=tmp_path)
        async with environment:
            await environment.start(self.IMAGE)
            result = await environment.run("sleep 30", timeout=1.0)
            assert result.exit_code == 124
            assert "timed out" in result.stderr

    async def test_a_checkpoint_can_be_committed_and_restarted(self, tmp_path: Path) -> None:
        """docker commit is a real checkpoint: the state outlives the container."""
        if not docker_available():
            pytest.skip("docker is not available")

        first = DockerEnvironment(host_workdir=tmp_path / "a")
        checkpoint = None
        try:
            await first.start(self.IMAGE)
            await first.run("echo checkpointed > /tmp/marker")
            checkpoint = await first.save()
            assert checkpoint.id
        finally:
            await first.stop()

        second = DockerEnvironment(host_workdir=tmp_path / "b")
        try:
            await second.start(self.IMAGE, from_checkpoint=checkpoint.id)
            result = await second.run("cat /tmp/marker")
            assert "checkpointed" in result.stdout
        finally:
            await second.stop()
            import subprocess

            subprocess.run(
                ["docker", "rmi", "--force", checkpoint.id],
                capture_output=True,
                check=False,
            )
