"""The fidelity fallback ladder.

Two things here are easy to get subtly wrong, and both are what the phase is
about:

* **served is not "first to finish"** — the cheap candidates finish first by
  design, and the thing served is always the best that *passed*;
* **passing is not finishing** — a candidate that built and then failed its
  smoke test is not a candidate at all.

Everything runs against the fake sandbox, so none of it needs Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.analytics import Analytics, AnalyticsEventType, CollectingAnalyticsWriter
from repolab.builds import (
    BuildCandidate,
    BuildLadder,
    BuildLimits,
    BuildRequest,
    BuildUnavailableError,
    InMemoryBuildCache,
    cassandra_official_plan,
    cassandra_partial_plan,
    cassandra_source_plan,
)
from repolab.builds.cassandra import SMOKE_MARKER
from repolab.events import CollectingEmitter, EventType
from repolab.manifest.models import BuildKind, Fidelity
from tests.support.builds import (
    CASSANDRA_REPO,
    SHA,
    cassandra_manifest,
    failing_smoke,
    failing_step,
    passing,
    provider,
    repository,
)


@pytest.fixture
def manifest():
    return cassandra_manifest()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return repository(tmp_path)


def candidates(manifest, *, delay: float = 0.0) -> list[BuildCandidate]:
    """The three buildable rungs. The Mini Lab is exercised separately."""
    return [
        BuildCandidate(
            kind=BuildKind.SOURCE_BUILD,
            plan=cassandra_source_plan(manifest),
            why="source",
            demo_delay_seconds=delay,
        ),
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


def ladder(sandbox, **overrides) -> BuildLadder:
    fields = {
        "cache": InMemoryBuildCache(),
        "emitter": CollectingEmitter("job-1"),
        "limits": BuildLimits(max_attempts=1),
    }
    fields.update(overrides)
    return BuildLadder(sandbox, **fields)


def request_for() -> BuildRequest:
    return BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA)


class TestChoosingWhatToServe:
    async def test_the_highest_fidelity_passing_candidate_is_served(self, manifest, root) -> None:
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert result.served is not None
        assert result.served.kind is BuildKind.SOURCE_BUILD
        assert result.served.fidelity is Fidelity.EXACT

    async def test_a_cheaper_candidate_serves_when_the_source_build_fails(
        self, manifest, root
    ) -> None:
        """The fallback, doing its job: Ant fails, and a learner still gets Cassandra."""
        plans = candidates(manifest)
        sandbox = provider(
            failing_step(plans[0].plan, "build the jars", stderr="ivy could not resolve"),
            passing(plans[1].plan, SMOKE_MARKER),
            passing(plans[2].plan, SMOKE_MARKER),
        )

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert result.served is not None
        assert result.served.kind is BuildKind.OFFICIAL_RELEASE
        assert result.served.fidelity is Fidelity.HIGH

    async def test_a_candidate_that_failed_its_smoke_test_is_never_served(
        self, manifest, root
    ) -> None:
        """Finishing is not passing. This is the invariant the phase insists on.

        The official release is failed at its own `record versions` step rather
        than at its smoke test, because it and the partial build share an
        identical smoke command -- deliberately, since they check the same
        thing -- and the fake matches commands exactly.
        """
        plans = candidates(manifest)
        sandbox = provider(
            failing_smoke(plans[0].plan),
            failing_step(plans[1].plan, "record versions", stderr="not found"),
            passing(plans[2].plan, SMOKE_MARKER),
        )

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert result.served is not None
        assert result.served.kind is BuildKind.PARTIAL_BUILD
        assert all(not outcome.ready for outcome in result.outcomes[:2])

    async def test_official_and_partial_check_the_same_thing(self, manifest) -> None:
        """Both must prove cqlsh connects and a SELECT runs.

        The fidelity difference between them is in what they can *do*, recorded
        as limitations -- not in how thoroughly they were verified.
        """
        plans = candidates(manifest)
        assert plans[1].plan.smoke.command == plans[2].plan.smoke.command
        assert "SELECT" in plans[1].plan.smoke.command

    async def test_every_ready_candidate_is_kept_not_just_the_served_one(
        self, manifest, root
    ) -> None:
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert [artifact.kind for artifact in result.ready] == [
            BuildKind.SOURCE_BUILD,
            BuildKind.OFFICIAL_RELEASE,
            BuildKind.PARTIAL_BUILD,
        ]

    async def test_outcomes_are_reported_in_ladder_order(self, manifest, root) -> None:
        """Whatever order they finished in, they are read best-first."""
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert [outcome.kind for outcome in result.outcomes] == [
            BuildKind.SOURCE_BUILD,
            BuildKind.OFFICIAL_RELEASE,
            BuildKind.PARTIAL_BUILD,
        ]


class TestWhenEverythingFails:
    async def test_it_raises_naming_every_candidate(self, manifest, root) -> None:
        """ "The build failed" without saying which three things were tried is useless."""
        plans = candidates(manifest)
        sandbox = provider(*(failing_smoke(c.plan) for c in plans))

        with pytest.raises(BuildUnavailableError) as excinfo:
            await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        message = str(excinfo.value)
        for kind in ("source_build", "official_release", "partial_build"):
            assert kind in message

    async def test_the_failure_is_announced(self, manifest, root) -> None:
        plans = candidates(manifest)
        emitter = CollectingEmitter("job-1")
        sandbox = provider(*(failing_smoke(c.plan) for c in plans))

        with pytest.raises(BuildUnavailableError):
            await ladder(sandbox, emitter=emitter).resolve(
                request_for(), plans, root, manifest=manifest
            )

        assert emitter.of_type(EventType.FAILED)


class TestCaching:
    async def test_a_ready_candidate_is_cached(self, manifest, root) -> None:
        plans = candidates(manifest)
        cache = InMemoryBuildCache()
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        await ladder(sandbox, cache=cache).resolve(request_for(), plans, root, manifest=manifest)

        assert len(cache) == 3, "each rung is cached separately"

    async def test_a_failed_candidate_is_never_cached(self, manifest, root) -> None:
        plans = candidates(manifest)
        cache = InMemoryBuildCache()
        sandbox = provider(
            failing_smoke(plans[0].plan),
            passing(plans[1].plan, SMOKE_MARKER),
            passing(plans[2].plan, SMOKE_MARKER),
        )

        await ladder(sandbox, cache=cache).resolve(request_for(), plans, root, manifest=manifest)

        stored = {artifact.kind for artifact in cache.get_all(CASSANDRA_REPO, SHA)}
        assert BuildKind.SOURCE_BUILD not in stored

    async def test_the_cache_is_consulted_before_anything_is_built(self, manifest, root) -> None:
        plans = candidates(manifest)
        cache = InMemoryBuildCache()
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        await ladder(sandbox, cache=cache).resolve(request_for(), plans, root, manifest=manifest)
        built = len(sandbox.executed)

        second = await ladder(sandbox, cache=cache).resolve(
            request_for(), plans, root, manifest=manifest
        )

        assert second.from_cache is True
        assert len(sandbox.executed) == built, "nothing was rebuilt"

    async def test_the_best_cached_rung_is_the_one_returned(self, manifest, root) -> None:
        """Several rungs can be cached for one commit; the best one wins."""
        plans = candidates(manifest)
        cache = InMemoryBuildCache()
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        await ladder(sandbox, cache=cache).resolve(request_for(), plans, root, manifest=manifest)
        second = await ladder(sandbox, cache=cache).resolve(
            request_for(), plans, root, manifest=manifest
        )

        assert second.served is not None
        assert second.served.kind is BuildKind.SOURCE_BUILD
        assert second.served.from_cache is True

    async def test_use_cache_false_rebuilds(self, manifest, root) -> None:
        plans = candidates(manifest)
        cache = InMemoryBuildCache()
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))
        request = BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA, use_cache=False)

        await ladder(sandbox, cache=cache).resolve(request, plans, root, manifest=manifest)
        built = len(sandbox.executed)
        await ladder(sandbox, cache=cache).resolve(request, plans, root, manifest=manifest)

        assert len(sandbox.executed) > built


class TestTheDemonstrationDelay:
    async def test_it_is_disclosed_not_silent(self, manifest, root) -> None:
        """A build slowed on purpose must never read as one that was slow."""
        plans = candidates(manifest, delay=0.05)
        emitter = CollectingEmitter("job-1")
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        result = await ladder(sandbox, emitter=emitter).resolve(
            request_for(), plans, root, manifest=manifest
        )

        assert result.served is not None
        assert result.served.demo_delay_seconds == 0.05
        assert any(event.data.get("demo_delay") for event in emitter.of_type(EventType.LOG_LINE))

    async def test_it_defaults_to_nothing(self, manifest) -> None:
        """It cannot affect a real build unless a caller asks for it."""
        assert all(candidate.demo_delay_seconds == 0 for candidate in candidates(manifest))


class TestEvents:
    async def test_each_candidate_is_announced_and_reported(self, manifest, root) -> None:
        plans = candidates(manifest)
        emitter = CollectingEmitter("job-1")
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        await ladder(sandbox, emitter=emitter).resolve(
            request_for(), plans, root, manifest=manifest
        )

        statuses = emitter.of_type(EventType.AGENT_STATUS)
        assert any("3 candidate" in event.message for event in statuses)
        assert len(emitter.of_type(EventType.PARTIAL_RESULT)) == 3

    async def test_the_served_artifact_declares_itself_on_the_stream(self, manifest, root) -> None:
        plans = candidates(manifest)
        emitter = CollectingEmitter("job-1")
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        await ladder(sandbox, emitter=emitter).resolve(
            request_for(), plans, root, manifest=manifest
        )

        final = emitter.of_type(EventType.STEP_DONE)[-1]
        assert final.data["fidelity"] == "exact"
        assert final.data["source"] == "source_build"
        assert "limitations" in final.data


class TestReadyCallback:
    async def test_each_ready_candidate_is_handed_over_as_it_lands(self, manifest, root) -> None:
        """How a caller learns an upgrade is possible without polling."""
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))
        seen: list[BuildKind] = []

        async def observe(artifact) -> None:
            seen.append(artifact.kind)

        await ladder(sandbox).resolve(
            request_for(), plans, root, manifest=manifest, on_ready=observe
        )

        assert sorted(kind.value for kind in seen) == [
            "official_release",
            "partial_build",
            "source_build",
        ]

    async def test_a_failed_candidate_is_not_handed_over(self, manifest, root) -> None:
        plans = candidates(manifest)
        sandbox = provider(
            failing_smoke(plans[0].plan),
            passing(plans[1].plan, SMOKE_MARKER),
        )
        seen: list[BuildKind] = []

        async def observe(artifact) -> None:
            seen.append(artifact.kind)

        await ladder(sandbox).resolve(
            request_for(), plans, root, manifest=manifest, on_ready=observe
        )

        assert BuildKind.SOURCE_BUILD not in seen


class TestRetryingWithinLimits:
    async def test_it_does_not_retry_without_a_diagnosis_saying_to(self, manifest, root) -> None:
        """RepoLab does not spend minutes on a guess; without a model, once is it."""
        plans = candidates(manifest)[:1]
        sandbox = provider(failing_step(plans[0].plan, "build the jars"))

        with pytest.raises(BuildUnavailableError):
            await BuildLadder(sandbox, limits=BuildLimits(max_attempts=3)).resolve(
                request_for(), plans, root, manifest=manifest
            )

        built = [command for _handle, command in sandbox.executed if "ant jar" in command]
        assert len(built) == 1, "no model to diagnose it, so no retry despite the budget"

    async def test_a_diagnosis_recommending_a_retry_produces_one(self, manifest, root) -> None:
        import json

        from repolab.llm import ModelRouter, ModelTier
        from repolab.testing.fakes import FakeLLMProvider, FakeMessage

        plans = candidates(manifest)[:1]
        sandbox = provider(failing_step(plans[0].plan, "build the jars"))
        model = ModelRouter(
            FakeLLMProvider(
                message=FakeMessage(
                    content=json.dumps(
                        {
                            "cause": "transient",
                            "retry_worthwhile": True,
                            "suggestion": "try again",
                            "produced_by": "super",
                        }
                    )
                )
            ),
            {ModelTier.NANO: "fake/nano", ModelTier.SUPER: "fake/super"},
        )

        with pytest.raises(BuildUnavailableError):
            await BuildLadder(sandbox, router=model, limits=BuildLimits(max_attempts=2)).resolve(
                request_for(), plans, root, manifest=manifest
            )

        built = [command for _handle, command in sandbox.executed if "ant jar" in command]
        assert len(built) == 2, "one retry, and only one"

    async def test_the_failure_summary_is_recorded_on_the_outcome(self, manifest, root) -> None:
        """Logs preserved, summary added — never the other way round."""
        plans = candidates(manifest)
        sandbox = provider(
            failing_step(plans[0].plan, "build the jars", stderr="ivy could not resolve"),
            passing(plans[1].plan, SMOKE_MARKER),
        )

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        failed = result.outcomes[0]
        assert failed.ready is False
        assert "build the jars" in failed.failure
        assert failed.summary is not None
        assert "ivy could not resolve" in failed.summary.log_excerpt


class TestTheMiniLabRung:
    async def test_it_serves_when_nothing_can_be_built(self, manifest, root) -> None:
        """The final fallback, doing its job."""
        from repolab.builds import BuildCandidate as Candidate

        plans = [
            *(Candidate(kind=c.kind, plan=c.plan, why=c.why) for c in candidates(manifest)),
            Candidate(kind=BuildKind.MINI_LAB, plan=None, why="conceptual"),
        ]
        sandbox = provider(*(failing_smoke(c.plan) for c in plans if c.plan))

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert result.served is not None
        assert result.served.kind is BuildKind.MINI_LAB
        assert result.served.fidelity is Fidelity.CONCEPTUAL
        assert result.served.simulated is True

    async def test_it_is_never_preferred_over_something_real(self, manifest, root) -> None:
        from repolab.builds import BuildCandidate as Candidate

        plans = [
            *(Candidate(kind=c.kind, plan=c.plan, why=c.why) for c in candidates(manifest)),
            Candidate(kind=BuildKind.MINI_LAB, plan=None, why="conceptual"),
        ]
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans if c.plan))

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert result.served is not None
        assert result.served.kind is BuildKind.SOURCE_BUILD


class TestServiceBasedBuilds:
    """A server has to be the container's main process, not a background job."""

    def test_the_cassandra_plans_declare_a_service(self, manifest) -> None:
        for candidate in candidates(manifest):
            assert candidate.plan.service_command, f"{candidate.kind} has no server to start"
            assert "cassandra" in candidate.plan.service_command

    def test_the_spark_plan_declares_none(self, make_repo) -> None:
        """Nothing P0 depends on acquired a service it does not need."""
        from repolab.builds import spark_plan
        from tests.test_build_resolver import manifest_for
        from tests.test_detectors import SPARK_REPO

        root, _ = make_repo(SPARK_REPO)
        assert spark_plan(manifest_for(root)).service_command is None

    def test_the_plans_declare_the_memory_they_need(self, manifest) -> None:
        """2 GB kills Cassandra's JVM before it logs anything; 4 GB starts."""
        ladder = candidates(manifest)
        assert ladder[1].plan.memory == "4g", "the official release needs real headroom"
        assert ladder[2].plan.memory == "2g", "the partial build exists to need less"

    async def test_a_service_build_is_smoke_tested_in_a_fresh_container(
        self, manifest, root
    ) -> None:
        """Started from the checkpoint, so the build's own steps are in place."""
        plans = candidates(manifest)[1:2]
        sandbox = provider(passing(plans[0].plan, SMOKE_MARKER))

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert result.served is not None
        assert result.served.ready is True
        # Two environments were created: the build, then the service.
        assert len(sandbox.terminated) >= 2


class TestBuildAnalytics:
    """The events a build success rate can be computed from.

    The cache keeps successful builds only, so the `builds` table can never
    report a failure: a rate computed from it reads 100% however many candidates
    failed. These events are the only honest source, which is why the ladder
    records one per candidate.
    """

    async def test_every_candidate_records_that_it_started(self, manifest, root) -> None:
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))
        writer = CollectingAnalyticsWriter()

        await ladder(sandbox, analytics=Analytics(writer)).resolve(
            request_for(), plans, root, manifest=manifest
        )

        started = writer.of_type(AnalyticsEventType.BUILD_STARTED)
        assert len(started) == len(plans)
        assert {event.properties["kind"] for event in started} == {
            candidate.kind.value for candidate in plans
        }

    async def test_a_candidate_that_fails_its_smoke_test_records_a_failure(
        self, manifest, root
    ) -> None:
        """Not ready is not a build: the smoke test is what decides."""
        plans = candidates(manifest)
        sandbox = provider(*(failing_smoke(c.plan) for c in plans))
        writer = CollectingAnalyticsWriter()

        with pytest.raises(BuildUnavailableError):
            await ladder(sandbox, analytics=Analytics(writer)).resolve(
                request_for(), plans, root, manifest=manifest
            )

        failed = writer.of_type(AnalyticsEventType.BUILD_FAILED)
        assert len(failed) == len(plans)
        assert all(event.properties["attempts"] >= 1 for event in failed)

    async def test_a_candidate_that_passes_records_no_failure(self, manifest, root) -> None:
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))
        writer = CollectingAnalyticsWriter()

        await ladder(sandbox, analytics=Analytics(writer)).resolve(
            request_for(), plans, root, manifest=manifest
        )

        assert writer.of_type(AnalyticsEventType.BUILD_FAILED) == []

    async def test_recovery_is_recorded_only_when_a_retry_rescued_it(self, manifest, root) -> None:
        """A first-attempt success is not a recovery, however many attempts were allowed."""
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))
        writer = CollectingAnalyticsWriter()

        await ladder(
            sandbox,
            analytics=Analytics(writer),
            limits=BuildLimits(max_attempts=2),
        ).resolve(request_for(), plans, root, manifest=manifest)

        assert writer.of_type(AnalyticsEventType.BUILD_RECOVERED) == []

    async def test_a_ladder_without_a_writer_is_unchanged(self, manifest, root) -> None:
        """Analytics is optional, so no existing caller needs to know about it."""
        plans = candidates(manifest)
        sandbox = provider(*(passing(c.plan, SMOKE_MARKER) for c in plans))

        result = await ladder(sandbox).resolve(request_for(), plans, root, manifest=manifest)

        assert result.served.ready is True
