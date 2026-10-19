"""The Cassandra lesson, executed against a real Cassandra.

Marked ``docker``: this starts a real node, restores it as a *server*, and runs
every statement of the lesson through the container's own cqlsh. Run with
``make test-docker``.

This is the file that proves the phase's central claim. A terminal that produced
plausible output would pass every offline test in the suite, so what is checked
here is that the output is real:

* the version in step 1 is the version the running server reports;
* the keyspace created in step 3 is visible to a later statement, which is only
  true if both ran in the same live node;
* step 7's error is Cassandra's own ``ALLOW FILTERING`` refusal, with its real
  message, from the real restriction check;
* and RepoLab's deterministic validation passes all eight steps on that output
  without a model being involved anywhere.
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
    cassandra_official_plan,
)
from repolab.builds.cassandra import OFFICIAL_IMAGE
from repolab.experiences.cli.experience import build_cli_experience
from repolab.experiences.cli.session import CliSessionManager, SessionStatus
from repolab.lessons.cassandra import KEYSPACE, cassandra_lesson
from repolab.lessons.validation import check_step
from repolab.manifest.models import BuildKind
from repolab.sandbox import DockerSandboxProvider
from repolab.sandbox.docker import docker_available, ensure_image
from tests.support.builds import CASSANDRA_REPO, SHA, cassandra_manifest, repository

pytestmark = pytest.mark.docker

#: A real node takes 30-60s to accept connections, and the lesson then runs
#: eight statements, each paying cqlsh's own start-up cost.
LESSON_TIMEOUT = 900.0


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
def artifact(manifest, tmp_path_factory):
    """One real official-release build, resolved once for the whole module."""
    root = repository(tmp_path_factory.mktemp("cassandra"))
    ladder = BuildLadder(
        DockerSandboxProvider(),
        cache=InMemoryBuildCache(),
        limits=BuildLimits(max_attempts=1, candidate_timeout_seconds=LESSON_TIMEOUT),
    )
    result = asyncio.run(
        ladder.resolve(
            BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA),
            [
                BuildCandidate(
                    kind=BuildKind.OFFICIAL_RELEASE,
                    plan=cassandra_official_plan(manifest),
                    why="official",
                )
            ],
            root,
            manifest=manifest,
        )
    )
    return result.served


@pytest.fixture(scope="module")
def walkthrough(artifact):
    """Run the whole lesson once, in one live session, and keep what happened.

    One session for the whole lesson on purpose: the steps depend on each other,
    and a fresh container per step would hide the one thing worth proving -- that
    state created by step 3 is really there in step 6.
    """
    lesson = cassandra_lesson(CASSANDRA_REPO, artifact.commit_sha, artifact=artifact)
    provider = DockerSandboxProvider()
    manager = CliSessionManager(provider)

    async def run():
        session = await manager.start(artifact, repo_url=CASSANDRA_REPO)
        assert session.status is SessionStatus.READY, session.error

        # A clean slate: an earlier run of this module may have left the keyspace
        # behind in the image's checkpoint.
        await manager.execute(session.id, f"DROP KEYSPACE IF EXISTS {KEYSPACE}")

        outcomes = []
        try:
            for step in lesson.steps:
                record = await manager.execute(session.id, step.action)
                verdict = check_step(step, record.command, record.output, record.exit_code)
                outcomes.append((step, record, verdict))
        finally:
            await manager.close()
        return outcomes

    try:
        return lesson, asyncio.run(asyncio.wait_for(run(), LESSON_TIMEOUT))
    except TimeoutError:  # pragma: no cover - a stalled node, not a failure of logic
        pytest.fail(f"the lesson did not finish within {LESSON_TIMEOUT:.0f}s")


class TestTheTerminalIsReal:
    def test_the_session_runs_the_builds_own_cqlsh(self, artifact, walkthrough) -> None:
        _, outcomes = walkthrough
        assert artifact.cli_command is not None
        assert artifact.cli_command.endswith("cqlsh")
        assert all(record.exit_code is not None for _, record, _ in outcomes), (
            "every statement reached a real process"
        )

    def test_the_version_is_the_running_servers_own(self, walkthrough) -> None:
        """Not the plan's, not the repository's: the one the node reports."""
        _, outcomes = walkthrough
        _, record, _ = outcomes[0]

        assert "release_version" in record.output
        assert "5.0" in record.output

    def test_state_created_early_is_there_later(self, walkthrough) -> None:
        """Only true of one live node. A faked terminal would not notice."""
        _, outcomes = walkthrough
        _, insert, _ = outcomes[4]
        _, query, _ = outcomes[5]

        assert insert.ok is True, insert.output
        assert "kitchen" in query.output

    def test_nothing_was_refused(self, walkthrough) -> None:
        """Every statement the lesson asks for must pass the allowlist."""
        _, outcomes = walkthrough
        refused = [(step.number, record.refused) for step, record, _ in outcomes if record.refused]
        assert refused == []


class TestTheProvokedError:
    def test_cassandra_really_refuses_the_query(self, walkthrough) -> None:
        _, outcomes = walkthrough
        step, record, _ = outcomes[6]

        assert step.expects_failure is True
        assert record.ok is False
        assert "ALLOW FILTERING" in record.output
        assert "InvalidRequest" in record.output or "Invalid query" in record.output

    def test_the_message_is_the_servers_own(self, walkthrough) -> None:
        """The wording comes from Cassandra's restriction check, not from RepoLab."""
        _, outcomes = walkthrough
        _, record, _ = outcomes[6]

        assert "unpredictable performance" in record.output

    def test_permitting_filtering_then_answers_it(self, walkthrough) -> None:
        _, outcomes = walkthrough
        _, record, _ = outcomes[7]

        assert record.ok is True
        assert "kitchen" in record.output


class TestTheLessonCanBeCompleted:
    def test_every_step_passes_its_own_validation(self, walkthrough) -> None:
        """The phase's done-when, checked against real output and no model."""
        _, outcomes = walkthrough
        failures = [
            (step.number, verdict.reason, record.output[:200])
            for step, record, verdict in outcomes
            if not verdict.passed
        ]
        assert failures == []

    def test_every_verdict_was_deterministic(self, walkthrough) -> None:
        _, outcomes = walkthrough
        assert all(verdict.decided_by == "deterministic" for _, _, verdict in outcomes)
        assert all(verdict.conclusive for _, _, verdict in outcomes)

    def test_the_lesson_is_the_eight_steps_the_phase_describes(self, walkthrough) -> None:
        lesson, outcomes = walkthrough
        assert lesson.step_count == 8
        assert len(outcomes) == 8


class TestTheExperienceTheBrowserRenders:
    def test_it_is_built_from_the_manifest_and_the_real_artifact(self, manifest, artifact) -> None:
        experience = build_cli_experience(manifest, artifact)

        assert experience.available is True
        assert experience.simulated is False
        assert experience.prompt == "cqlsh>"
        assert experience.cli_command == artifact.cli_command
        assert experience.capabilities, "the artifact declares what it can do"
        assert experience.deviations, "and discloses that it is not the requested commit"
