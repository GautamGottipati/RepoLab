"""The build command.

The done-when criterion: one command takes a repository through resolve/build,
smoke test and checkpoint, and prints READY. It must also be impossible to mistake
a simulated build for a real one.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_repo.py"


def run_cli(
    *args: str, timeout: int = 300, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the command as a user would, in its own process."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def credential_free_environment(tmp_path: Path) -> dict[str, str]:
    """An environment with no Nebius credentials reachable.

    Clearing the variables is not enough: settings also read ``.env`` from the
    working directory, so the command is run from an empty directory with the
    repository on ``PYTHONPATH`` instead. Without this the test passes only on a
    machine that happens to have no credentials configured, which is exactly how
    it stopped testing anything once they existed.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("NEBIUS_", "MODEL_", "TAVILY_"))
    }
    environment["PYTHONPATH"] = str(REPO_ROOT)
    return environment


class TestArgumentHandling:
    def test_an_invalid_url_exits_two(self) -> None:
        result = run_cli("not-a-url", "--provider", "fake")
        assert result.returncode == 2
        assert "invalid repository URL" in result.stderr

    def test_an_internal_host_is_refused(self) -> None:
        result = run_cli("https://localhost/a/b", "--provider", "fake")
        assert result.returncode == 2

    def test_requesting_nebius_without_credentials_fails_clearly(self, tmp_path: Path) -> None:
        result = run_cli(
            "https://github.com/a/b",
            "--provider",
            "nebius",
            cwd=tmp_path,
            env=credential_free_environment(tmp_path),
        )
        assert result.returncode != 0
        assert "NEBIUS_API_KEY" in (result.stderr + result.stdout)

    def test_help_documents_the_provider_choice(self) -> None:
        result = run_cli("--help")
        assert result.returncode == 0
        assert "--provider" in result.stdout
        assert "fake" in result.stdout


@pytest.mark.network
class TestTheReadyPath:
    """Clones the real demo repository, so it needs network but no credentials."""

    URL = "https://github.com/AlexIoannides/pyspark-example-project"

    def test_it_reaches_ready_and_exits_zero(self) -> None:
        result = run_cli(self.URL, "--provider", "fake", "--cache", "memory")

        assert result.returncode == 0, result.stderr
        assert "READY" in result.stdout

    def test_the_output_names_the_provider(self) -> None:
        """A READY from the fake must never look like a real build."""
        result = run_cli(self.URL, "--provider", "fake", "--cache", "memory")

        assert "provider       fake" in result.stdout
        assert "SIMULATED" in result.stdout

    def test_it_reports_the_whole_chain(self) -> None:
        result = run_cli(self.URL, "--provider", "fake", "--cache", "memory")
        for expected in ("commit", "primary", "base image", "smoke", "checkpoint"):
            assert expected in result.stdout

    def test_it_shows_the_plan_before_building(self) -> None:
        result = run_cli(self.URL, "--provider", "fake", "--cache", "memory")
        assert "smoke test" in result.stdout
        assert "extract repository" in result.stdout

    def test_it_discloses_the_substituted_pin(self) -> None:
        result = run_cli(self.URL, "--provider", "fake", "--cache", "memory")

        assert "deviations:" in result.stdout
        assert "pyspark 2.4.0" in result.stdout
        assert "fidelity       high" in result.stdout

    def test_the_artifact_can_be_written_out(self, tmp_path: Path) -> None:
        target = tmp_path / "artifact.json"
        result = run_cli(self.URL, "--provider", "fake", "--cache", "memory", "--json", str(target))

        assert result.returncode == 0
        import json

        artifact = json.loads(target.read_text())
        assert artifact["ready"] is True
        assert artifact["checkpoint_id"]
        assert artifact["smoke"]["passed"] is True

    def test_an_unsupported_repository_is_reported_cleanly(self) -> None:
        """A shape RepoLab cannot build yet is a message, not a traceback."""
        result = run_cli(
            "https://github.com/psf/requests",
            "--provider",
            "fake",
            "--cache",
            "memory",
        )

        assert result.returncode == 3
        assert "NOT SUPPORTED" in result.stderr
        assert "Traceback" not in result.stderr
        assert "Only Spark" in result.stderr
