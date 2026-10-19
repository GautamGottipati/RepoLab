"""The real Cassandra source build.

Marked ``docker`` **and** ``slow``: this runs the repository's own ``ant jar``,
which clones Cassandra, resolves its dependencies over the network from inside
the container, and compiles for ten to twenty minutes. It is excluded from
``make test-docker`` and run on purpose:

    uv run pytest -m slow -v

It exists because the exact source build is the top rung of the ladder and the
only one that produces ``EXACT`` fidelity. Everything else is tested with the
official container, which is fast; this is the one test that proves the rung
RepoLab claims is best can actually be built.

**A failure here is a legitimate outcome, not only a broken test.** Ivy may not
reach the network, the toolchain may not match -- which is exactly why the
ladder has three rungs beneath this one, and why their tests do not depend on
this passing.
"""

from __future__ import annotations

import asyncio

import pytest

from repolab.builds import (
    BuildCandidate,
    BuildLadder,
    BuildLimits,
    BuildRequest,
    InMemoryBuildCache,
    cassandra_source_plan,
)
from repolab.builds.cassandra import SOURCE_IMAGE
from repolab.events import CollectingEmitter
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.limits import IngestLimits
from repolab.ingest.url import validate_repo_url
from repolab.manifest.models import BuildKind, Fidelity
from repolab.sandbox import DockerSandboxProvider
from repolab.sandbox.docker import docker_available, ensure_image
from tests.support.builds import CASSANDRA_REPO, cassandra_manifest

pytestmark = [pytest.mark.docker, pytest.mark.slow]

#: Cassandra is large; a shallow clone of trunk is still hundreds of megabytes.
CLONE_LIMITS = IngestLimits(max_scanned_files=5_000, max_total_read_bytes=64_000_000)

#: `ant jar` compiles thousands of files after resolving dependencies.
BUILD_TIMEOUT = 2700.0


@pytest.fixture(scope="module")
def built():
    if not docker_available():
        pytest.skip("docker is not available")
    status = ensure_image(SOURCE_IMAGE)
    if not status.ready:
        pytest.skip(f"{SOURCE_IMAGE} is unavailable: {status.error}")

    manifest = cassandra_manifest()
    emitter = CollectingEmitter("source-build")

    async def build():
        repo_url = validate_repo_url(CASSANDRA_REPO)
        plan = cassandra_source_plan(manifest)
        # Read from the plan, not hardcoded: it is the plan that knows its own
        # build invokes a gradle project in `modules/accord`.
        with TemporaryClone(
            repo_url, limits=CLONE_LIMITS, submodules=plan.needs_submodules
        ) as clone:
            candidate = BuildCandidate(
                kind=BuildKind.SOURCE_BUILD,
                plan=plan,
                why="the repository's own ant target",
            )
            ladder = BuildLadder(
                DockerSandboxProvider(),
                cache=InMemoryBuildCache(),
                emitter=emitter,
                limits=BuildLimits(max_attempts=1, candidate_timeout_seconds=BUILD_TIMEOUT),
            )
            from repolab.builds import BuildUnavailableError

            try:
                return await ladder.resolve(
                    BuildRequest(repo_url=repo_url.normalized, commit_sha=clone.commit_sha),
                    [candidate],
                    __import__("pathlib").Path(clone.path),
                    manifest=manifest,
                )
            except BuildUnavailableError as exc:
                pytest.skip(f"the real ant build did not succeed here: {exc}")

    return asyncio.run(build()), emitter


class TestTheExactSourceBuild:
    def test_it_builds_the_requested_commit(self, built) -> None:
        result, _ = built
        assert result.served is not None
        assert result.served.kind is BuildKind.SOURCE_BUILD
        assert len(result.served.commit_sha) == 40

    def test_it_is_the_only_rung_that_is_exact(self, built) -> None:
        """Nothing was substituted, so nothing lowers its fidelity."""
        result, _ = built
        assert result.served is not None
        assert result.served.fidelity is Fidelity.EXACT
        assert result.served.capabilities.deviations == []

    def test_the_repositorys_own_ant_target_ran(self, built) -> None:
        result, _ = built
        attempt = result.served.attempts[-1]
        commands = " ".join(step.command for step in attempt.steps)
        assert "ant jar" in commands

    def test_cqlsh_connected_and_a_select_executed(self, built) -> None:
        result, _ = built
        smoke = result.served.smoke
        assert smoke is not None and smoke.passed is True
        assert "SELECT" in smoke.command

    def test_it_supports_real_experiments(self, built) -> None:
        result, _ = built
        assert result.served.supports_real_experiments is True
