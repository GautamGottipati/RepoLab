"""Shared fixtures."""

from __future__ import annotations

import itertools
import subprocess
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
import structlog

from repolab.ingest.clone import CloneResult
from repolab.ingest.url import RepoUrl


@pytest.fixture
def capture_logs() -> Iterator[list[dict[str, Any]]]:
    """Capture structlog events emitted during a test.

    Used to assert what the model layer does *not* log: no prompts, no model
    output, no reasoning text, no credentials.
    """
    with structlog.testing.capture_logs() as captured:
        yield captured


# ---------------------------------------------------------------------------
# Repository fixtures
#
# Ingestion tests run against *real* git repositories created locally, so the
# clone path is genuinely exercised without any network access.
# ---------------------------------------------------------------------------


def git_init(path: Path, *, branch: str = "main") -> str:
    """Create a git repository at *path* and commit everything in it.

    Returns the commit SHA.
    """
    run = partial(subprocess.run, cwd=path, check=True, capture_output=True, text=True)
    run(["git", "init", "-q", "-b", branch])
    run(["git", "add", "-A"])
    run(
        [
            "git",
            "-c",
            "user.email=test@repolab.invalid",
            "-c",
            "user.name=RepoLab Test",
            "commit",
            "-qm",
            "initial",
        ]
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def write_files(root: Path, files: dict[str, str]) -> None:
    """Write *files* (relative path -> content) under *root*."""
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dedent(content).lstrip())


@pytest.fixture
def make_repo(tmp_path: Path):
    """Build a real local git repository from a mapping of files."""

    counter = itertools.count()

    def factory(files: dict[str, str], *, branch: str = "main") -> tuple[Path, str]:
        root = tmp_path / f"repo{next(counter)}"
        root.mkdir()
        write_files(root, files)
        return root, git_init(root, branch=branch)

    return factory


@pytest.fixture
def local_clone():
    """Turn a local repository path into a :class:`CloneResult`.

    Lets the inspection half of the pipeline be tested without cloning.
    """

    def factory(root: Path, commit_sha: str, branch: str | None = "main") -> CloneResult:
        return CloneResult(path=root, commit_sha=commit_sha, branch=branch)

    return factory


@pytest.fixture
def fake_repo_url() -> RepoUrl:
    return RepoUrl(
        normalized="https://github.com/example/demo",
        host="github.com",
        owner="example",
        name="demo",
    )
