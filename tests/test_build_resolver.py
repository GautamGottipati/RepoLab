"""Build resolution.

Covers the two strategies that exist -- an exact cached build and an exact source
build -- and holds the resolver to the rules that make a build trustworthy: a
failing smoke test never produces a ready artifact, a failed build is never
cached, and every attempt is recorded.
"""

from __future__ import annotations

import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from repolab.builds import (
    BuildRequest,
    BuildResolver,
    BuildSource,
    BuildUnavailableError,
    InMemoryBuildCache,
    NullBuildCache,
    archive_repository,
    spark_plan,
)
from repolab.builds.resolver import ArchiveTooLargeError
from repolab.events import CollectingEmitter, EventType
from repolab.manifest.models import RepoManifest
from repolab.sandbox import FakeSandboxProvider, ScriptedCommand
from tests.test_detectors import SPARK_REPO

REPO = "https://github.com/AlexIoannides/pyspark-example-project"
SHA = "e" * 40


def manifest_for(root: Path, sha: str = SHA) -> RepoManifest:
    """A manifest for the Spark fixture, via the real detection path.

    Uses the synchronous classifier: no model is involved, so spinning up a second
    event loop inside a test that already has one would be both unnecessary and
    broken.
    """
    from repolab.classify import classify_deterministic
    from repolab.ingest.content import ReadBudget
    from repolab.ingest.detectors import DetectionContext, detect_all
    from repolab.ingest.languages import detect_languages
    from repolab.ingest.tree import build_file_tree
    from repolab.manifest.builder import build_manifest

    tree = build_file_tree(root)
    context = DetectionContext(root=root, tree=tree, commit_sha=sha)
    outcome = detect_all(context, ReadBudget())
    classification = classify_deterministic(
        outcome.run, entrypoints=outcome.entrypoints, services=outcome.services
    )
    return build_manifest(
        repo_url=REPO,
        commit_sha=sha,
        outcome=outcome,
        languages=detect_languages(tree),
        classification=classification,
    )


def provider_that_builds(**overrides: ScriptedCommand) -> FakeSandboxProvider:
    """A fake whose smoke job succeeds, unless overridden."""
    from repolab.builds.spark import SMOKE_MARKER

    scripted = {
        "python3 /work/repolab_smoke.py": ScriptedCommand(stdout=f"{SMOKE_MARKER} 3.5.1\n"),
    }
    scripted.update(overrides)
    return FakeSandboxProvider(scripted=scripted)


@pytest.fixture
def spark_fixture(make_repo):
    root, _ = make_repo(SPARK_REPO)
    return root, spark_plan(manifest_for(root))


class TestSourceBuild:
    async def test_it_produces_a_ready_artifact(self, spark_fixture) -> None:
        root, plan = spark_fixture
        resolver = BuildResolver(provider_that_builds())

        artifact = await resolver.resolve(BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root)

        assert artifact.ready is True
        assert artifact.source is BuildSource.SOURCE_BUILD
        assert artifact.checkpoint_id
        assert artifact.smoke is not None and artifact.smoke.passed

    async def test_the_repository_is_uploaded(self, spark_fixture) -> None:
        """The archive goes in; nothing is cloned inside the sandbox."""
        root, plan = spark_fixture
        provider = provider_that_builds()
        resolver = BuildResolver(provider)

        await resolver.resolve(BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root)
        uploaded = [paths for _, paths in provider.uploads]
        assert any(plan.archive_path in paths for paths in uploaded)

    async def test_the_plan_steps_all_run(self, spark_fixture) -> None:
        root, plan = spark_fixture
        provider = provider_that_builds()
        resolver = BuildResolver(provider)

        artifact = await resolver.resolve(BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root)
        attempt = artifact.attempts[0]
        assert len(attempt.steps) == len(plan.all_steps)
        assert all(step.ok for step in attempt.steps)

    async def test_the_checkpoint_is_tagged_with_the_commit(self, spark_fixture) -> None:
        root, plan = spark_fixture
        resolver = BuildResolver(provider_that_builds())
        request = BuildRequest(repo_url=REPO, commit_sha=SHA)

        artifact = await resolver.resolve(request, plan, root)
        assert artifact.checkpoint_tag == request.checkpoint_tag
        assert SHA[:12] in artifact.checkpoint_tag

    async def test_the_environment_is_released_afterwards(self, spark_fixture) -> None:
        root, plan = spark_fixture
        provider = provider_that_builds()

        await BuildResolver(provider).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )
        assert provider.terminated

    async def test_the_checkpoint_survives_the_environment(self, spark_fixture) -> None:
        """The artifact is useless if its checkpoint dies with the builder."""
        root, plan = spark_fixture
        provider = provider_that_builds()

        artifact = await BuildResolver(provider).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )
        restored = await provider.restore(artifact.checkpoint_id)
        assert restored.is_restored is True

    async def test_no_deviation_means_an_exact_build(self, spark_fixture) -> None:
        """The fixture pins a usable pyspark, so nothing is substituted."""
        root, plan = spark_fixture
        artifact = await BuildResolver(provider_that_builds()).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )
        assert artifact.capabilities.deviations == []
        assert artifact.fidelity.value == "exact"

    async def test_an_unusable_pin_is_disclosed_and_lowers_fidelity(self, make_repo) -> None:
        """The real demo repository pins pyspark 2.4.0, which cannot be honoured."""
        files = {**SPARK_REPO, "requirements.txt": "pyspark==2.4.0\npandas==2.1.0\n"}
        root, _ = make_repo(files)
        plan = spark_plan(manifest_for(root))

        artifact = await BuildResolver(provider_that_builds()).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )
        assert any("pyspark 2.4.0" in item for item in artifact.capabilities.deviations)
        assert artifact.fidelity.value == "high"
        assert artifact.capabilities.is_exact is False


class TestSmokeTestGatesReadiness:
    async def test_a_failing_smoke_test_prevents_a_build(self, spark_fixture) -> None:
        root, plan = spark_fixture
        provider = provider_that_builds(
            **{
                "python3 /work/repolab_smoke.py": ScriptedCommand(
                    stderr="ModuleNotFoundError: pyspark", exit_code=1
                )
            }
        )
        resolver = BuildResolver(provider)

        with pytest.raises(BuildUnavailableError, match=r"smoke test failed|exit 1"):
            await resolver.resolve(BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root)

    async def test_a_zero_exit_without_the_marker_still_fails(self, spark_fixture) -> None:
        """A silent success is not proof the Spark job ran."""
        root, plan = spark_fixture
        provider = provider_that_builds(
            **{"python3 /work/repolab_smoke.py": ScriptedCommand(stdout="", exit_code=0)}
        )

        with pytest.raises(BuildUnavailableError, match="did not contain"):
            await BuildResolver(provider).resolve(
                BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
            )

    async def test_a_failed_build_is_not_cached(self, spark_fixture) -> None:
        root, plan = spark_fixture
        cache = InMemoryBuildCache()
        provider = provider_that_builds(
            **{"python3 /work/repolab_smoke.py": ScriptedCommand(exit_code=1)}
        )

        with pytest.raises(BuildUnavailableError):
            await BuildResolver(provider, cache=cache).resolve(
                BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
            )
        assert len(cache) == 0

    async def test_no_checkpoint_is_taken_when_the_smoke_test_fails(self, spark_fixture) -> None:
        """Checkpointing a broken environment would cache a trap."""
        root, plan = spark_fixture
        provider = provider_that_builds(
            **{"python3 /work/repolab_smoke.py": ScriptedCommand(exit_code=1)}
        )

        with pytest.raises(BuildUnavailableError):
            await BuildResolver(provider).resolve(
                BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
            )
        assert provider.checkpoint_count == 0


class TestFailingSteps:
    async def test_a_failing_step_stops_the_build(self, spark_fixture) -> None:
        root, plan = spark_fixture
        provider = provider_that_builds(
            **{plan.steps[1].command: ScriptedCommand(stderr="Could not reach pypi", exit_code=1)}
        )

        with pytest.raises(BuildUnavailableError, match="install pyspark"):
            await BuildResolver(provider).resolve(
                BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
            )

    async def test_the_failure_and_its_logs_are_recorded(self, spark_fixture) -> None:
        """Build failures are never hidden."""
        root, plan = spark_fixture
        provider = provider_that_builds(
            **{plan.steps[1].command: ScriptedCommand(stderr="no network", exit_code=1)}
        )
        emitter = CollectingEmitter("job-1")

        with pytest.raises(BuildUnavailableError):
            await BuildResolver(provider, emitter=emitter).resolve(
                BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
            )

        logs = " ".join(e.message for e in emitter.of_type(EventType.LOG_LINE))
        assert "no network" in logs
        assert emitter.of_type(EventType.FAILED)

    async def test_the_error_names_what_actually_failed(self, spark_fixture) -> None:
        """Single-strategy resolution has nowhere to fall back to, and says why.

        The ladder is what provides fallbacks; `resolve` is the path that does
        not want them, so a failure here is final and names the step.
        """
        root, plan = spark_fixture
        provider = provider_that_builds(**{plan.steps[1].command: ScriptedCommand(exit_code=1)})

        with pytest.raises(BuildUnavailableError) as excinfo:
            await BuildResolver(provider).resolve(
                BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
            )
        assert "install pyspark" in str(excinfo.value)


class TestCaching:
    async def test_a_ready_build_is_cached(self, spark_fixture) -> None:
        root, plan = spark_fixture
        cache = InMemoryBuildCache()

        await BuildResolver(provider_that_builds(), cache=cache).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )
        assert len(cache) == 1

    async def test_a_second_resolve_uses_the_cache(self, spark_fixture) -> None:
        root, plan = spark_fixture
        cache = InMemoryBuildCache()
        request = BuildRequest(repo_url=REPO, commit_sha=SHA)

        first_provider = provider_that_builds()
        await BuildResolver(first_provider, cache=cache).resolve(request, plan, root)

        second_provider = provider_that_builds()
        cached = await BuildResolver(second_provider, cache=cache).resolve(request, plan, root)

        assert cached.source is BuildSource.CACHE
        assert cached.ready is True
        # Nothing was executed the second time.
        assert second_provider.executed == []

    async def test_a_cache_hit_is_reported(self, spark_fixture) -> None:
        root, plan = spark_fixture
        cache = InMemoryBuildCache()
        request = BuildRequest(repo_url=REPO, commit_sha=SHA)
        await BuildResolver(provider_that_builds(), cache=cache).resolve(request, plan, root)

        emitter = CollectingEmitter("job-2")
        await BuildResolver(provider_that_builds(), cache=cache, emitter=emitter).resolve(
            request, plan, root
        )
        done = emitter.of_type(EventType.STEP_DONE)
        assert any("cached build" in event.message for event in done)

    async def test_use_cache_false_forces_a_rebuild(self, spark_fixture) -> None:
        root, plan = spark_fixture
        cache = InMemoryBuildCache()
        await BuildResolver(provider_that_builds(), cache=cache).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )

        provider = provider_that_builds()
        artifact = await BuildResolver(provider, cache=cache).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA, use_cache=False), plan, root
        )
        assert artifact.source is BuildSource.SOURCE_BUILD
        assert provider.executed

    async def test_a_different_commit_is_a_cache_miss(self, spark_fixture) -> None:
        root, plan = spark_fixture
        cache = InMemoryBuildCache()
        await BuildResolver(provider_that_builds(), cache=cache).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )

        provider = provider_that_builds()
        artifact = await BuildResolver(provider, cache=cache).resolve(
            BuildRequest(repo_url=REPO, commit_sha="f" * 40), plan, root
        )
        assert artifact.source is BuildSource.SOURCE_BUILD

    async def test_the_null_cache_never_hits(self, spark_fixture) -> None:
        root, plan = spark_fixture
        request = BuildRequest(repo_url=REPO, commit_sha=SHA)
        resolver = BuildResolver(provider_that_builds(), cache=NullBuildCache())

        first = await resolver.resolve(request, plan, root)
        second = await BuildResolver(provider_that_builds(), cache=NullBuildCache()).resolve(
            request, plan, root
        )
        assert first.source is second.source is BuildSource.SOURCE_BUILD


class TestProgressEvents:
    async def test_the_build_reports_progress_in_order(self, spark_fixture) -> None:
        root, plan = spark_fixture
        emitter = CollectingEmitter("job-1")

        await BuildResolver(provider_that_builds(), emitter=emitter).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )
        types = emitter.types()
        assert types[0] is EventType.STEP_STARTED
        assert EventType.PROGRESS in types
        assert types[-1] is EventType.STEP_DONE

    async def test_the_final_event_carries_the_checkpoint(self, spark_fixture) -> None:
        root, plan = spark_fixture
        emitter = CollectingEmitter("job-1")

        artifact = await BuildResolver(provider_that_builds(), emitter=emitter).resolve(
            BuildRequest(repo_url=REPO, commit_sha=SHA), plan, root
        )
        final = emitter.of_type(EventType.STEP_DONE)[-1]
        assert final.data["checkpoint_id"] == artifact.checkpoint_id
        assert final.data["fidelity"] == artifact.fidelity.value


class TestArchiving:
    def test_it_produces_a_readable_archive(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        data = archive_repository(root)

        with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
            names = archive.getnames()
        assert any(name.endswith("etl_job.py") for name in names)

    def test_git_metadata_is_excluded(self, make_repo) -> None:
        """A build needs the working tree, not the history."""
        root, _ = make_repo(SPARK_REPO)
        with tarfile.open(fileobj=BytesIO(archive_repository(root)), mode="r:gz") as archive:
            names = archive.getnames()
        assert not any(name.startswith(".git/") or "/.git/" in name for name in names)

    def test_symlinks_are_excluded(self, make_repo, tmp_path: Path) -> None:
        """An extracted symlink could point outside the build workspace."""
        root, _ = make_repo(SPARK_REPO)
        outside = tmp_path / "outside.txt"
        outside.write_text("sensitive\n")
        (root / "link.txt").symlink_to(outside)

        with tarfile.open(fileobj=BytesIO(archive_repository(root)), mode="r:gz") as archive:
            names = archive.getnames()
        assert "link.txt" not in names

    def test_an_oversized_archive_is_refused(self, make_repo) -> None:
        # Random-looking content, since repeated bytes would gzip to almost nothing.
        import random

        random.seed(0)
        payload = "".join(
            random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(60_000)
        )
        root, _ = make_repo({"big.bin": payload})

        with pytest.raises(ArchiveTooLargeError, match="upload limit"):
            archive_repository(root, limit=1000)


class TestSymlinksInTheArchive:
    """Links that stay inside the repository are part of it; links that escape
    would read or clobber host files once extracted somewhere else.

    Dropping every link was safe and wrong: Cassandra's `ant jar` fails on a
    missing `.build/git/git-hooks/post-switch`, which the repository ships as a
    symlink to a sibling directory. Handing a build a quietly different tree is
    its own kind of unsafe.
    """

    def test_an_internal_symlink_is_kept(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / "hooks" / "real").mkdir(parents=True)
        (root / "hooks" / "real" / "script.sh").write_text("#!/bin/sh\n")
        (root / "hooks" / "alias").symlink_to("real")

        names = set(_names(archive_repository(root)))
        assert "hooks/alias" in names

    def test_a_relative_escape_is_dropped(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / "inner").mkdir(parents=True)
        (root / "inner" / "escape").symlink_to("../../outside")

        assert "inner/escape" not in set(_names(archive_repository(root)))

    def test_an_absolute_symlink_is_dropped(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        (root / "passwd").symlink_to("/etc/passwd")

        assert "passwd" not in set(_names(archive_repository(root)))

    @pytest.mark.parametrize(
        ("name", "target", "inside"),
        [
            (".build/git/git-hooks/post-switch", "post-checkout", True),
            ("a/b/link", "../c", True),
            ("deep/a/b/link", "../../../x", True),
            ("a/link", "../../etc/passwd", False),
            ("a/link", "../..", False),
            ("link", "/etc/passwd", False),
        ],
    )
    def test_containment_is_decided_textually(self, name, target, inside) -> None:
        """Where the link points once extracted *elsewhere*, not where it points now."""
        from repolab.builds.resolver import points_inside

        assert points_inside(name, target) is inside


def _names(data: bytes) -> list[str]:
    with tarfile.open(fileobj=BytesIO(data)) as archive:
        return archive.getnames()
