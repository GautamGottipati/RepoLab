"""Cloning against real local git repositories. No network involved."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repolab.ingest import CloneError, IngestLimits, RepoUrl, shallow_clone
from repolab.ingest.clone import TemporaryClone, build_clone_command


def file_url(path: Path) -> RepoUrl:
    """A RepoUrl pointing at a local repository over the ``file://`` transport.

    ``validate_repo_url`` would refuse this, which is the point: it is built
    directly so the clone mechanics can be exercised without a network. The
    ``file://`` form rather than a bare path is deliberate -- git ignores
    ``--depth`` for local-path clones but honours it over a real transport.
    """
    return RepoUrl(
        normalized=f"file://{path.resolve()}",
        host="github.com",
        owner="local",
        name=path.name,
    )


class TestSuccessfulClone:
    def test_records_the_commit_sha(self, make_repo, tmp_path: Path) -> None:
        root, sha = make_repo({"README.md": "# demo\n"})
        result = shallow_clone(file_url(root), tmp_path / "dest", allow_local_source=True)
        assert result.commit_sha == sha

    def test_records_the_branch(self, make_repo, tmp_path: Path) -> None:
        root, _ = make_repo({"README.md": "# demo\n"})
        assert (
            shallow_clone(file_url(root), tmp_path / "dest", allow_local_source=True).branch
            == "main"
        )

    def test_a_non_main_default_branch_is_followed(self, make_repo, tmp_path: Path) -> None:
        """apache/cassandra uses `trunk`, so no branch name may be assumed."""
        root, sha = make_repo({"README.md": "# c\n"}, branch="trunk")
        result = shallow_clone(file_url(root), tmp_path / "dest", allow_local_source=True)
        assert result.branch == "trunk"
        assert result.commit_sha == sha

    def test_the_working_tree_is_present(self, make_repo, tmp_path: Path) -> None:
        root, _ = make_repo({"src/app.py": "print('hi')\n"})
        result = shallow_clone(file_url(root), tmp_path / "dest", allow_local_source=True)
        assert (result.path / "src" / "app.py").read_text() == "print('hi')\n"

    def test_the_clone_is_shallow(self, make_repo, tmp_path: Path) -> None:
        """Only the tip commit is fetched, so large histories stay cheap."""
        root, _ = make_repo({"README.md": "# demo\n"})
        # A second commit gives the clone something it could truncate.
        (root / "README.md").write_text("# demo v2\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t.invalid",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "second",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )

        result = shallow_clone(file_url(root), tmp_path / "dest", allow_local_source=True)
        count = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=result.path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert count == "1"


class TestFailures:
    def test_a_missing_repository_raises(self, tmp_path: Path) -> None:
        with pytest.raises(CloneError, match="git clone failed"):
            shallow_clone(file_url(tmp_path / "nope"), tmp_path / "dest", allow_local_source=True)

    def test_a_timeout_raises(self, make_repo, tmp_path: Path) -> None:
        root, _ = make_repo({"README.md": "# demo\n"})
        limits = IngestLimits(clone_timeout_seconds=0.001)
        with pytest.raises(CloneError):
            shallow_clone(file_url(root), tmp_path / "dest", limits=limits, allow_local_source=True)


class TestTemporaryClone:
    def test_the_clone_is_removed_on_exit(self, make_repo) -> None:
        root, _ = make_repo({"README.md": "# demo\n"})
        with TemporaryClone(file_url(root), allow_local_source=True) as clone:
            path = clone.path
            assert path.exists()
        assert not path.exists()

    def test_cleanup_happens_even_on_failure(self, tmp_path: Path) -> None:
        with (
            pytest.raises(CloneError),
            TemporaryClone(file_url(tmp_path / "nope"), allow_local_source=True),
        ):
            pass  # pragma: no cover - the clone fails first

    def test_repository_contents_do_not_persist(self, make_repo) -> None:
        """Nothing a repository contains should outlive its ingestion."""
        root, _ = make_repo({"secret.txt": "sensitive\n"})
        with TemporaryClone(file_url(root), allow_local_source=True) as clone:
            leaked = clone.path / "secret.txt"
            assert leaked.exists()
        assert not leaked.exists()


class TestGitEnvironment:
    def test_ambient_git_config_is_not_used(self, make_repo, tmp_path: Path) -> None:
        """A developer's aliases or insteadOf rules must not affect ingestion."""
        root, _ = make_repo({"README.md": "# demo\n"})
        result = shallow_clone(file_url(root), tmp_path / "dest", allow_local_source=True)
        config = subprocess.run(
            ["git", "config", "--local", "--list"],
            cwd=result.path,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        assert "hooksPath" not in config


class TestLocalSourcesAreRefusedByDefault:
    """The `file` transport is denied unless a caller explicitly opts in.

    Production never opts in, so a repository URL can never cause git to read the
    local filesystem -- a second barrier behind `validate_repo_url`.
    """

    def test_default_refuses_a_local_path(self, make_repo, tmp_path: Path) -> None:
        root, _ = make_repo({"README.md": "# demo\n"})
        with pytest.raises(CloneError, match="transport 'file' not allowed"):
            shallow_clone(file_url(root), tmp_path / "dest")

    def test_the_override_is_absent_from_the_default_command(self) -> None:
        command = build_clone_command("https://example.com/a/b", Path("/tmp/x"))
        assert "protocol.file.allow=always" not in command

    def test_the_override_is_present_only_when_asked(self) -> None:
        command = build_clone_command(
            "https://example.com/a/b", Path("/tmp/x"), allow_local_source=True
        )
        assert "protocol.file.allow=always" in command
