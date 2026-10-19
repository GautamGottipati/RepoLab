"""The environment a learner is using, and how it gets upgraded.

One sentence of the specification is what this file exists for:

> Offer an upgrade. **Do not switch mid-command or mid-lesson-step.**

Like the experiment approval gate, the rule lives in deterministic code rather
than in whatever client calls it -- so these tests are mostly about what the
session *refuses*.
"""

from __future__ import annotations

import pytest

from repolab.builds import (
    BuildArtifact,
    EnvironmentSession,
    SmokeTestResult,
    UpgradeRefused,
)
from repolab.events import CollectingEmitter, EventType
from repolab.manifest.models import BuildKind

REPO = "https://github.com/apache/cassandra"


def ready(kind: BuildKind) -> BuildArtifact:
    return BuildArtifact(
        repo_url=REPO,
        commit_sha="c" * 40,
        kind=kind,
        checkpoint_id="cp",
        smoke=SmokeTestResult(name="s", command="c", passed=True),
        simulated=kind is BuildKind.MINI_LAB,
    )


def session(kind: BuildKind | None = BuildKind.OFFICIAL_RELEASE) -> EnvironmentSession:
    return EnvironmentSession(repo_url=REPO, artifact=ready(kind) if kind else None)


class TestOfferingAnUpgrade:
    def test_an_offer_never_switches_the_environment(self) -> None:
        """The whole point. Offering is not applying."""
        subject = session()
        offer = subject.propose(ready(BuildKind.SOURCE_BUILD))

        assert offer is not None
        assert subject.upgrade_pending is True
        assert subject.artifact is not None
        assert subject.artifact.kind is BuildKind.OFFICIAL_RELEASE, "still the old one"

    def test_an_equal_fidelity_build_is_not_offered(self) -> None:
        """Switching costs the learner their state, so it needs to buy something."""
        assert session().propose(ready(BuildKind.OFFICIAL_RELEASE)) is None

    def test_a_lower_fidelity_build_is_not_offered(self) -> None:
        assert session().propose(ready(BuildKind.MINI_LAB)) is None

    def test_an_unready_build_is_not_offered(self) -> None:
        unready = ready(BuildKind.SOURCE_BUILD)
        unready.smoke = None
        assert session().propose(unready) is None

    def test_a_session_with_nothing_takes_anything_ready(self) -> None:
        subject = session(kind=None)
        assert subject.propose(ready(BuildKind.MINI_LAB)) is not None

    def test_the_offer_records_what_it_is_from_and_to(self) -> None:
        offer = session().propose(ready(BuildKind.SOURCE_BUILD))
        assert offer is not None
        assert offer.from_fidelity == "high"
        assert offer.to_fidelity == "exact"


class TestNotMidCommand:
    def test_accepting_while_busy_is_refused(self) -> None:
        """The rule the phase names explicitly."""
        subject = session()
        subject.propose(ready(BuildKind.SOURCE_BUILD))
        subject.begin("CREATE KEYSPACE demo")

        with pytest.raises(UpgradeRefused, match=r"must not change|will be applied once"):
            subject.accept()

        assert subject.artifact is not None
        assert subject.artifact.kind is BuildKind.OFFICIAL_RELEASE

    def test_the_offer_survives_the_refusal(self) -> None:
        """Waiting is correct behaviour, not an error to work around."""
        subject = session()
        subject.propose(ready(BuildKind.SOURCE_BUILD))
        subject.begin("a lesson step")

        with pytest.raises(UpgradeRefused):
            subject.accept()

        assert subject.upgrade_pending is True
        assert subject.can_upgrade_now is False

    def test_it_applies_the_moment_the_step_finishes(self) -> None:
        subject = session()
        subject.propose(ready(BuildKind.SOURCE_BUILD))
        subject.begin("a lesson step")
        subject.end()

        assert subject.can_upgrade_now is True
        applied = subject.accept()

        assert applied.kind is BuildKind.SOURCE_BUILD
        assert subject.artifact is not None
        assert subject.artifact.kind is BuildKind.SOURCE_BUILD

    def test_switching_environments_directly_is_also_refused_while_busy(self) -> None:
        subject = session()
        subject.begin("a command")

        with pytest.raises(UpgradeRefused):
            subject.use(ready(BuildKind.SOURCE_BUILD))

    def test_overlapping_activities_are_refused(self) -> None:
        """Otherwise "busy" would be ambiguous, and the gate with it."""
        subject = session()
        subject.begin("first")

        with pytest.raises(RuntimeError, match="already running"):
            subject.begin("second")


class TestAcceptingAndDeclining:
    def test_accepting_with_nothing_pending_is_refused(self) -> None:
        with pytest.raises(UpgradeRefused, match="no pending upgrade"):
            session().accept()

    def test_accepting_twice_is_refused(self) -> None:
        subject = session()
        subject.propose(ready(BuildKind.SOURCE_BUILD))
        subject.accept()

        with pytest.raises(UpgradeRefused):
            subject.accept()

    def test_declining_keeps_the_current_environment(self) -> None:
        subject = session()
        subject.propose(ready(BuildKind.SOURCE_BUILD))
        subject.decline()

        assert subject.upgrade_pending is False
        assert subject.artifact is not None
        assert subject.artifact.kind is BuildKind.OFFICIAL_RELEASE

    def test_a_declined_offer_cannot_then_be_accepted(self) -> None:
        subject = session()
        subject.propose(ready(BuildKind.SOURCE_BUILD))
        subject.decline()

        with pytest.raises(UpgradeRefused):
            subject.accept()


class TestEvents:
    def test_offering_and_applying_are_distinct_events(self) -> None:
        emitter = CollectingEmitter("job-1")
        subject = session()

        subject.propose(ready(BuildKind.SOURCE_BUILD), emitter=emitter)
        assert emitter.of_type(EventType.UPGRADE_AVAILABLE)
        assert emitter.of_type(EventType.UPGRADE_APPLIED) == []

        subject.accept(emitter=emitter)
        assert emitter.of_type(EventType.UPGRADE_APPLIED)

    def test_the_offer_says_whether_the_learner_is_busy(self) -> None:
        emitter = CollectingEmitter("job-1")
        subject = session()
        subject.begin("a lesson step")
        subject.propose(ready(BuildKind.SOURCE_BUILD), emitter=emitter)

        event = emitter.of_type(EventType.UPGRADE_AVAILABLE)[0]
        assert event.data["busy"] is True
        assert event.data["to_fidelity"] == "exact"

    def test_the_offered_artifact_declares_itself(self) -> None:
        emitter = CollectingEmitter("job-1")
        session().propose(ready(BuildKind.SOURCE_BUILD), emitter=emitter)

        data = emitter.of_type(EventType.UPGRADE_AVAILABLE)[0].data
        assert data["fidelity"] == "exact"
        assert data["source"] == "source_build"
        assert "limitations" in data


class TestHistory:
    def test_the_session_records_what_happened(self) -> None:
        """So the demonstration can show the sequence rather than assert it."""
        subject = session()
        subject.begin("CREATE KEYSPACE demo")
        subject.propose(ready(BuildKind.SOURCE_BUILD))
        subject.end()
        subject.accept()

        assert subject.history == [
            "began CREATE KEYSPACE demo",
            "offered an upgrade to exact",
            "finished CREATE KEYSPACE demo",
            "upgraded to exact",
        ]


class TestABetterOfferIsNotReplaced:
    """Observed on the real Cassandra ladder: the partial build finishes moments
    after the official release, and without this the learner is offered `high`,
    then `partial`, and accepting gives them the worse one."""

    def test_a_worse_offer_does_not_replace_a_better_pending_one(self) -> None:
        subject = session(kind=BuildKind.MINI_LAB)

        subject.propose(ready(BuildKind.OFFICIAL_RELEASE))
        assert subject.propose(ready(BuildKind.PARTIAL_BUILD)) is None

        assert subject.offer is not None
        assert subject.offer.artifact.kind is BuildKind.OFFICIAL_RELEASE

    def test_accepting_gives_the_best_that_was_offered(self) -> None:
        subject = session(kind=BuildKind.MINI_LAB)
        subject.propose(ready(BuildKind.OFFICIAL_RELEASE))
        subject.propose(ready(BuildKind.PARTIAL_BUILD))

        assert subject.accept().kind is BuildKind.OFFICIAL_RELEASE

    def test_a_better_offer_does_replace_a_worse_pending_one(self) -> None:
        subject = session(kind=BuildKind.MINI_LAB)
        subject.propose(ready(BuildKind.PARTIAL_BUILD))
        subject.propose(ready(BuildKind.SOURCE_BUILD))

        assert subject.offer is not None
        assert subject.offer.artifact.kind is BuildKind.SOURCE_BUILD

    def test_best_known_prefers_a_pending_offer_over_what_is_in_use(self) -> None:
        subject = session(kind=BuildKind.MINI_LAB)
        subject.propose(ready(BuildKind.SOURCE_BUILD))

        assert subject.best_known is not None
        assert subject.best_known.kind is BuildKind.SOURCE_BUILD
        assert subject.artifact is not None
        assert subject.artifact.kind is BuildKind.MINI_LAB, "still not switched"

    def test_a_declined_offer_stops_counting(self) -> None:
        subject = session(kind=BuildKind.MINI_LAB)
        subject.propose(ready(BuildKind.OFFICIAL_RELEASE))
        subject.decline()

        assert subject.propose(ready(BuildKind.PARTIAL_BUILD)) is not None
