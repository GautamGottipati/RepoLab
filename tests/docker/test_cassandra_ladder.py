"""The Cassandra ladder, against real containers.

Marked ``docker``: these start real Cassandra nodes and run real CQL. Run with
``make test-docker``.

The last class is the phase's done-when criterion, mechanically: the official
release becomes ready, a learner begins work on it, the source build completes,
and an upgrade is **offered** rather than forced.

The source build is stood in for by a delayed candidate here. Compiling
Cassandra from source takes ten to twenty minutes and is exercised separately in
``test_cassandra_source_build.py``; what this file tests is the *ordering and the
gate*, which is what the specification asks to see demonstrated.
"""

from __future__ import annotations

import asyncio

import pytest

from repolab.builds import (
    BuildCandidate,
    BuildLadder,
    BuildLimits,
    BuildRequest,
    EnvironmentSession,
    InMemoryBuildCache,
    UpgradeRefused,
    cassandra_official_plan,
    cassandra_partial_plan,
)
from repolab.builds.cassandra import OFFICIAL_IMAGE, OFFICIAL_VERSION
from repolab.builds.models import BuildArtifact
from repolab.events import CollectingEmitter, EventType
from repolab.manifest.models import BuildKind, Fidelity
from repolab.sandbox import DockerSandboxProvider
from repolab.sandbox.docker import docker_available, ensure_image
from tests.support.builds import CASSANDRA_REPO, SHA, cassandra_manifest, repository

pytestmark = pytest.mark.docker

#: A real node takes 30-60s to accept connections, and two run concurrently.
LADDER_TIMEOUT = 900.0


@pytest.fixture(scope="module", autouse=True)
def requires_docker() -> None:
    if not docker_available():
        pytest.skip("docker is not available")
    status = ensure_image(OFFICIAL_IMAGE)
    if not status.ready:
        pytest.skip(f"{OFFICIAL_IMAGE} is unavailable: {status.error}")


@pytest.fixture(scope="module")
def manifest():
    return cassandra_manifest()


@pytest.fixture(scope="module")
def resolved(manifest, tmp_path_factory):
    """Resolve the two container-based rungs once; every test reads the result."""
    root = repository(tmp_path_factory.mktemp("cassandra"))
    emitter = CollectingEmitter("docker-ladder")
    candidates = [
        BuildCandidate(
            kind=BuildKind.OFFICIAL_RELEASE,
            plan=cassandra_official_plan(manifest),
            why="official",
        ),
        BuildCandidate(
            kind=BuildKind.PARTIAL_BUILD,
            plan=cassandra_partial_plan(manifest),
            why="partial",
        ),
    ]

    ladder = BuildLadder(
        DockerSandboxProvider(),
        cache=InMemoryBuildCache(),
        emitter=emitter,
        limits=BuildLimits(max_attempts=1, candidate_timeout_seconds=LADDER_TIMEOUT),
    )
    result = asyncio.run(
        ladder.resolve(
            BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA),
            candidates,
            root,
            manifest=manifest,
        )
    )
    return result, emitter


class TestTheOfficialRelease:
    def test_a_real_cassandra_starts_and_answers_cql(self, resolved) -> None:
        """CLAUDE.md's smoke test for Cassandra, actually executed."""
        result, _ = resolved
        official = next(
            artifact for artifact in result.ready if artifact.kind is BuildKind.OFFICIAL_RELEASE
        )

        assert official.ready is True
        assert official.smoke is not None
        assert official.smoke.passed is True
        assert "SELECT" in official.smoke.command

    def test_the_version_comes_from_the_server_not_the_plan(self, resolved) -> None:
        result, _ = resolved
        official = next(
            artifact for artifact in result.ready if artifact.kind is BuildKind.OFFICIAL_RELEASE
        )
        assert official.version is not None
        assert official.version.startswith(OFFICIAL_VERSION)

    def test_it_is_high_fidelity_and_discloses_why_it_is_not_exact(self, resolved) -> None:
        result, _ = resolved
        official = next(
            artifact for artifact in result.ready if artifact.kind is BuildKind.OFFICIAL_RELEASE
        )

        assert official.fidelity is Fidelity.HIGH
        assert official.capabilities.deviations
        assert "not a build of that commit" in " ".join(official.capabilities.deviations)

    def test_it_supports_real_experiments(self, resolved) -> None:
        result, _ = resolved
        assert result.served is not None
        assert result.served.supports_real_experiments is True


class TestThePartialBuild:
    def test_a_reduced_node_also_answers_cql(self, resolved) -> None:
        result, _ = resolved
        partial = next(
            artifact for artifact in result.ready if artifact.kind is BuildKind.PARTIAL_BUILD
        )
        assert partial.ready is True
        assert partial.smoke is not None and partial.smoke.passed is True

    def test_it_is_partial_fidelity_and_says_what_it_cannot_do(self, resolved) -> None:
        result, _ = resolved
        partial = next(
            artifact for artifact in result.ready if artifact.kind is BuildKind.PARTIAL_BUILD
        )

        assert partial.fidelity is Fidelity.PARTIAL
        limitations = " ".join(partial.capabilities.limitations).lower()
        assert "heap" in limitations
        assert "performance measurements" in limitations


class TestServingTheBest:
    def test_the_official_release_is_served_over_the_partial_build(self, resolved) -> None:
        result, _ = resolved
        assert result.served is not None
        assert result.served.kind is BuildKind.OFFICIAL_RELEASE

    def test_both_rungs_are_kept(self, resolved) -> None:
        result, _ = resolved
        assert len(result.ready) == 2

    def test_each_rung_checkpoints_under_its_own_name(self, resolved) -> None:
        """Without the kind in the tag, the last commit silently replaces the rest."""
        result, _ = resolved
        tags = {artifact.checkpoint_tag for artifact in result.ready}
        assert len(tags) == 2

    def test_each_artifact_declares_everything_required(self, resolved) -> None:
        result, _ = resolved
        for artifact in result.ready:
            declaration = artifact.declaration()
            assert declaration["source"]
            assert declaration["version"]
            assert declaration["commit_sha"] == SHA
            assert declaration["fidelity"]
            assert declaration["capabilities"]
            assert declaration["limitations"]
            assert declaration["smoke_test"]["passed"] is True


class TestTheUpgradeSequence:
    """The done-when criterion.

    official ready -> the learner begins -> the better build completes ->
    an upgrade is OFFERED, and not applied mid-command.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def sequence(manifest, tmp_path_factory):
        root = repository(tmp_path_factory.mktemp("cassandra-upgrade"))
        emitter = CollectingEmitter("docker-upgrade")
        session = EnvironmentSession(repo_url=CASSANDRA_REPO)
        order: list[str] = []

        # A stand-in for the source build: the official plan with its version
        # deviation removed, so it derives EXACT fidelity and ranks exactly as a
        # real `ant jar` would. Without that it would rank HIGH, tie with the
        # official release, and never be treated as the better candidate.
        #
        # Deliberately delayed, so the official release is ready first every
        # time this runs. The delay is disclosed on the artifact; see
        # `test_the_delay_is_disclosed`.
        stand_in = cassandra_official_plan(manifest).model_copy(update={"deviations": []})

        candidates = [
            BuildCandidate(
                kind=BuildKind.SOURCE_BUILD,
                plan=stand_in,
                why="stands in for the real ant build, which takes 10-20 minutes",
                demo_delay_seconds=45.0,
            ),
            BuildCandidate(
                kind=BuildKind.OFFICIAL_RELEASE,
                plan=cassandra_official_plan(manifest),
                why="official",
            ),
        ]

        async def on_ready(artifact: BuildArtifact) -> None:
            order.append(f"{artifact.kind.value} ready")

            if session.artifact is None:
                session.use(artifact, emitter=emitter)
                session.begin("CREATE KEYSPACE demo")
                order.append("learner began work")
                return

            offer = session.propose(artifact, emitter=emitter)
            if offer is None:
                return
            order.append("upgrade offered")
            try:
                session.accept(emitter=emitter)
                order.append("upgrade applied mid-command")  # must not happen
            except UpgradeRefused:
                order.append("upgrade refused while busy")

        ladder = BuildLadder(
            DockerSandboxProvider(),
            cache=InMemoryBuildCache(),
            emitter=emitter,
            limits=BuildLimits(max_attempts=1, candidate_timeout_seconds=LADDER_TIMEOUT),
        )
        asyncio.run(
            ladder.resolve(
                BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA),
                candidates,
                root,
                manifest=manifest,
                on_ready=on_ready,
            )
        )
        return session, emitter, order

    def test_the_cheaper_build_was_ready_first(self, sequence) -> None:
        _session, _emitter, order = sequence
        assert order[0] == "official_release ready"
        assert order[1] == "learner began work"

    def test_the_better_build_completed_afterwards(self, sequence) -> None:
        _session, _emitter, order = sequence
        assert "source_build ready" in order
        assert order.index("source_build ready") > order.index("learner began work")

    def test_an_upgrade_was_offered(self, sequence) -> None:
        session, emitter, order = sequence
        assert "upgrade offered" in order
        assert emitter.of_type(EventType.UPGRADE_AVAILABLE)
        assert session.upgrade_pending is True

    def test_it_was_not_applied_mid_command(self, sequence) -> None:
        """The rule the specification states explicitly."""
        _session, emitter, order = sequence

        assert "upgrade refused while busy" in order
        assert "upgrade applied mid-command" not in order
        assert emitter.of_type(EventType.UPGRADE_APPLIED) == []

    def test_the_learner_stayed_on_the_environment_they_started_on(self, sequence) -> None:
        session, _emitter, _order = sequence
        assert session.artifact is not None
        assert session.artifact.kind is BuildKind.OFFICIAL_RELEASE

    def test_it_applies_once_the_command_finishes(self, sequence) -> None:
        session, emitter, _order = sequence
        session.end()

        assert session.can_upgrade_now is True
        applied = session.accept(emitter=emitter)

        assert applied.kind is BuildKind.SOURCE_BUILD
        assert emitter.of_type(EventType.UPGRADE_APPLIED)

    def test_the_delay_is_disclosed(self, sequence) -> None:
        """A build slowed for demonstration must never read as a slow build."""
        _session, emitter, _order = sequence
        assert any(event.data.get("demo_delay") for event in emitter.of_type(EventType.LOG_LINE))
