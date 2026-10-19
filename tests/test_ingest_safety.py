"""Ingestion must never execute repository code.

This is the phase's central safety property, so it is demonstrated rather than
asserted: the fixture repository contains a ``setup.py``, a ``Makefile``, a
``package.json`` ``postinstall`` hook, a git hook and an executable shell script
that would each write a sentinel file if they ever ran. After a full ingestion,
no sentinel exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.ingest import ingest_clone
from repolab.ingest.clone import CLONE_FLAGS, SAFETY_CONFIG, build_clone_command
from repolab.ingest.content import ReadBudget, read_text
from repolab.ingest.limits import IngestLimits

SENTINEL = "EXECUTED"

#: Every one of these writes ./EXECUTED-<n> if the corresponding tool runs it.
BOOBY_TRAPPED_REPO: dict[str, str] = {
    "setup.py": f"""
        from setuptools import setup
        from pathlib import Path

        # If ingestion ever imports or runs this file, the sentinel appears.
        Path("{SENTINEL}-setup").write_text("setup.py executed")

        setup(name="trap", install_requires=["requests>=2.0", "pyspark"])
    """,
    "Makefile": f"""
        all:
        \t touch {SENTINEL}-make

        install:
        \t touch {SENTINEL}-make-install
    """,
    "package.json": f"""
        {{
          "name": "trap",
          "scripts": {{
            "postinstall": "touch {SENTINEL}-npm",
            "start": "node server.js"
          }},
          "dependencies": {{ "express": "^4.18.0" }}
        }}
    """,
    "install.sh": f"""
        #!/bin/sh
        touch {SENTINEL}-shell
    """,
    "conftest.py": f"""
        from pathlib import Path

        # pytest collection would trigger this; ingestion must not collect.
        Path("{SENTINEL}-conftest").write_text("conftest imported")
    """,
    "jobs/etl.py": """
        from pyspark.sql import SparkSession

        if __name__ == "__main__":
            SparkSession.builder.appName("trap").getOrCreate()
    """,
    "README.md": "# Trap\n\nRun with `spark-submit jobs/etl.py`.\n",
}


def sentinels(*roots: Path) -> list[Path]:
    """Every sentinel file found anywhere under *roots*."""
    found: list[Path] = []
    for root in roots:
        found.extend(root.rglob(f"{SENTINEL}*"))
    return found


class TestNoRepositoryCodeIsExecuted:
    async def test_full_ingestion_leaves_no_sentinel(
        self, make_repo, local_clone, fake_repo_url, tmp_path: Path
    ) -> None:
        root, sha = make_repo(BOOBY_TRAPPED_REPO)
        # Make the script executable, as a real repository would.
        (root / "install.sh").chmod(0o755)

        result = await ingest_clone(fake_repo_url, local_clone(root, sha))

        assert sentinels(root, tmp_path, Path.cwd()) == []
        # And it still learned something from the repository.
        assert result.manifest.dependencies
        assert result.manifest.entrypoints

    async def test_setup_py_is_parsed_as_text_not_executed(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        """Dependencies come out of setup.py without importing it."""
        root, sha = make_repo(BOOBY_TRAPPED_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))

        names = {dep.name.lower() for dep in result.manifest.dependencies}
        assert "requests" in names
        assert "pyspark" in names
        assert not (root / f"{SENTINEL}-setup").exists()

    async def test_package_json_scripts_are_recorded_not_run(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(BOOBY_TRAPPED_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))

        commands = {entry.command for entry in result.manifest.entrypoints}
        assert any("node server.js" in command for command in commands)
        assert not (root / f"{SENTINEL}-npm").exists()

    async def test_makefile_is_a_build_signal_only(
        self, make_repo, local_clone, fake_repo_url
    ) -> None:
        root, sha = make_repo(BOOBY_TRAPPED_REPO)
        result = await ingest_clone(fake_repo_url, local_clone(root, sha))

        # A Makefile means "this is how it builds", never "run it now".
        assert result.manifest.build_strategy.steps
        assert not (root / f"{SENTINEL}-make").exists()
        assert not (root / f"{SENTINEL}-make-install").exists()


class TestCloneHardening:
    """The one subprocess ingestion runs is git, configured so a remote cannot
    cause local execution."""

    def test_hooks_are_disabled(self) -> None:
        command = build_clone_command("https://example.com/a/b", Path("/tmp/x"))
        assert "core.hooksPath=/dev/null" in command

    @pytest.mark.parametrize("option", ["protocol.ext.allow=never", "protocol.file.allow=never"])
    def test_transport_helpers_are_denied(self, option: str) -> None:
        """protocol.ext would let a URL name a command to execute."""
        assert option in build_clone_command("https://example.com/a/b", Path("/tmp/x"))

    def test_submodules_are_not_initialised(self) -> None:
        assert "--recurse-submodules=no" in build_clone_command(
            "https://example.com/a/b", Path("/tmp/x")
        )

    def test_clone_is_shallow_and_single_branch(self) -> None:
        command = build_clone_command("https://example.com/a/b", Path("/tmp/x"))
        assert "--depth" in command and "1" in command
        assert "--single-branch" in command
        assert "--no-tags" in command

    def test_url_is_passed_after_a_separator(self) -> None:
        """`--` stops a hostile URL being read as an option."""
        command = build_clone_command("--upload-pack=evil", Path("/tmp/x"))
        assert command.index("--") < command.index("--upload-pack=evil")

    def test_no_shell_is_involved(self) -> None:
        """argv is a list, so no shell metacharacter can be interpreted."""
        command = build_clone_command("https://example.com/a/b;touch X", Path("/tmp/x"))
        assert isinstance(command, list)
        assert "https://example.com/a/b;touch X" in command

    def test_every_documented_safety_option_is_applied(self) -> None:
        command = build_clone_command("https://example.com/a/b", Path("/tmp/x"))
        for option in SAFETY_CONFIG:
            assert option in command
        for flag in CLONE_FLAGS:
            assert flag in command


class TestNoBuildToolsAreInvoked:
    async def test_ingestion_spawns_only_git(
        self, make_repo, local_clone, fake_repo_url, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Any subprocess other than git during inspection is a bug."""
        import subprocess as subprocess_module

        spawned: list[list[str]] = []
        real_run = subprocess_module.run
        real_popen = subprocess_module.Popen

        def record_run(command, *args, **kwargs):
            spawned.append(list(command) if isinstance(command, (list, tuple)) else [command])
            return real_run(command, *args, **kwargs)

        def record_popen(command, *args, **kwargs):
            spawned.append(list(command) if isinstance(command, (list, tuple)) else [command])
            return real_popen(command, *args, **kwargs)

        root, sha = make_repo(BOOBY_TRAPPED_REPO)
        monkeypatch.setattr(subprocess_module, "run", record_run)
        monkeypatch.setattr(subprocess_module, "Popen", record_popen)

        await ingest_clone(fake_repo_url, local_clone(root, sha))

        forbidden = {"pip", "npm", "yarn", "mvn", "gradle", "make", "python", "sh", "bash"}
        for command in spawned:
            executable = Path(str(command[0])).name
            assert executable not in forbidden, f"ingestion spawned {executable}"


class TestTheManifestReservation:
    """Dependency manifests must not lose the budget to a large source tree.

    Observed on a fresh ingestion of `apache/airflow`: 362 dependency manifests
    found, none read, `frameworks` empty, and a repository that ships a web UI,
    an HTTP API and a CLI classified with two surfaces instead of four. A
    manifest is the only evidence of a framework when a bounded scan never
    reaches the application module.
    """

    def test_an_ordinary_read_leaves_the_reservation_intact(self) -> None:
        limits = IngestLimits(max_total_read_bytes=1_000, manifest_reserve_bytes=400)
        budget = ReadBudget(limits)

        assert budget.can_afford(600) is True, "the unreserved part"
        assert budget.can_afford(601) is False, "would eat into the reservation"

    def test_a_reserved_read_may_use_it(self) -> None:
        limits = IngestLimits(max_total_read_bytes=1_000, manifest_reserve_bytes=400)
        budget = ReadBudget(limits)
        budget.charge(600)

        assert budget.can_afford(300) is False, "nothing left outside the reservation"
        assert budget.can_afford(300, reserved=True) is True

    def test_the_total_is_still_a_true_upper_bound(self) -> None:
        """The reservation is carved out of the total, never added to it."""
        limits = IngestLimits(max_total_read_bytes=1_000, manifest_reserve_bytes=400)
        budget = ReadBudget(limits)
        budget.charge(1_000)

        assert budget.can_afford(1, reserved=True) is False
        assert budget.remaining == 0

    def test_a_manifest_is_read_when_the_general_scan_has_spent_everything_else(
        self, tmp_path: Path
    ) -> None:
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text('[project]\ndependencies = ["fastapi"]\n')

        limits = IngestLimits(max_total_read_bytes=1_000, manifest_reserve_bytes=400)
        budget = ReadBudget(limits)
        budget.charge(600)

        assert read_text(manifest, budget=budget) is None, "an ordinary read is refused"
        assert read_text(manifest, budget=budget, reserved=True) is not None
