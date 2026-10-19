"""The lesson when only a Mini Lab could be built.

CLAUDE.md's Mini Lab rules are the strictest in the document, and all of them are
about *not* doing something: never invent output, never produce a measurement,
always label it simulated, always say when a command needs a real environment.
So this file is a list of things the simulated lesson refuses to do.
"""

from __future__ import annotations

from repolab.builds.minilab import SIMULATED_LABEL, MiniLab, MiniLabEntry
from repolab.evidence.models import CodeReference
from repolab.lessons.cassandra import KEYSPACE, cassandra_lesson
from repolab.lessons.minilab_lesson import (
    NEEDS_REAL_ENVIRONMENT,
    NO_OBSERVATION,
    WITHHELD,
    simulated_lesson,
    simulated_observation,
)

REPO = "https://github.com/apache/cassandra"
SHA = "c" * 40

DESCRIBE = "DESCRIBE KEYSPACES"
SOURCE = "src/java/org/apache/cassandra/cql3/statements/DescribeStatement.java"


def lab(*entries: MiniLabEntry) -> MiniLab:
    return MiniLab(
        repo_url=REPO, commit_sha=SHA, summary="A conceptual tour", entries=list(entries)
    )


def covered_entry(explanation: str = "Lists the keyspaces the node knows about.") -> MiniLabEntry:
    return MiniLabEntry(
        command=DESCRIBE,
        concept="keyspaces",
        explanation=explanation,
        code_references=[CodeReference(path=SOURCE, start_line=42)],
    )


class TestAlwaysSimulated:
    def test_the_lesson_is_labelled(self) -> None:
        lesson = simulated_lesson(REPO, SHA)

        assert lesson.simulated is True
        assert lesson.label == SIMULATED_LABEL

    def test_every_step_is_labelled(self) -> None:
        lesson = simulated_lesson(REPO, SHA, lab=lab(covered_entry()))

        assert {step.label for step in lesson.steps} == {SIMULATED_LABEL}

    def test_every_step_states_that_it_needs_a_real_build(self) -> None:
        lesson = simulated_lesson(REPO, SHA, lab=lab(covered_entry()))

        assert all(step.requires_real_build for step in lesson.steps)

    def test_the_notice_says_nothing_is_executed(self) -> None:
        assert "Nothing is executed" in simulated_lesson(REPO, SHA).notice

    def test_the_underlying_lesson_is_marked_simulated_too(self) -> None:
        """It is kept whole for replay, and must not look like a real one meanwhile."""
        lesson = simulated_lesson(REPO, SHA)

        assert lesson.lesson.simulated is True
        assert lesson.lesson.fidelity == "conceptual"


class TestNoOutputIsEverInvented:
    def test_an_uncovered_statement_says_it_requires_a_real_environment(self) -> None:
        lesson = simulated_lesson(REPO, SHA, lab=lab(covered_entry()))

        uncovered = [step for step in lesson.steps if not step.covered]
        assert uncovered, "the lab covers one statement, not eight"
        assert all(step.explanation == NEEDS_REAL_ENVIRONMENT for step in uncovered)

    def test_with_no_lab_at_all_nothing_is_covered(self) -> None:
        lesson = simulated_lesson(REPO, SHA)

        assert lesson.covered_count == 0
        assert all(step.explanation == NEEDS_REAL_ENVIRONMENT for step in lesson.steps)

    def test_a_covered_statement_uses_the_labs_explanation_verbatim(self) -> None:
        lesson = simulated_lesson(REPO, SHA, lab=lab(covered_entry()))

        step = next(step for step in lesson.steps if step.covered)
        assert step.explanation == "Lists the keyspaces the node knows about."

    def test_no_step_claims_an_observation(self) -> None:
        lesson = simulated_lesson(REPO, SHA, lab=lab(covered_entry()))

        for step in lesson.steps:
            assert simulated_observation(lesson.lesson.steps[step.number - 1]) == NO_OBSERVATION

    def test_the_statements_are_still_shown(self) -> None:
        """A learner should see what they *would* type, which is not invented output."""
        lesson = simulated_lesson(REPO, SHA)

        assert any(KEYSPACE in step.action for step in lesson.steps)


class TestNoMeasurements:
    def test_an_explanation_reading_as_a_measurement_is_withheld(self) -> None:
        """A cached lab from an older version could still contain one."""
        lesson = simulated_lesson(
            REPO, SHA, lab=lab(covered_entry("Lists keyspaces in about 40ms."))
        )

        step = lesson.steps[1]
        assert step.explanation == WITHHELD
        assert step.covered is False, "a withheld explanation is not coverage"

    def test_the_evidence_bundle_is_simulated_and_carries_no_measurements(self) -> None:
        lesson = simulated_lesson(REPO, SHA, lab=lab(covered_entry()))
        evidence = lesson.evidence()

        assert evidence.simulated is True
        assert evidence.measurements == []


class TestLinkedToRealSource:
    def test_steps_keep_the_repository_references_the_real_lesson_has(self) -> None:
        from repolab.lessons.cassandra import RepositoryEvidence

        reference = CodeReference(path="bin/cqlsh.py", start_line=1)
        evidence = RepositoryEvidence(commit_sha=SHA, code={"cqlsh": reference})

        lesson = simulated_lesson(REPO, SHA, evidence=evidence)

        assert any(reference in step.code_references for step in lesson.steps)
        assert any(reference in bundle for bundle in [lesson.evidence().code_references])


class TestReplayableLater:
    def test_the_real_lesson_is_kept_whole(self) -> None:
        """When a real environment arrives, this is what replays against it."""
        simulated = simulated_lesson(REPO, SHA)
        real = cassandra_lesson(REPO, SHA)

        assert [step.action for step in simulated.lesson.steps] == [
            step.action for step in real.steps
        ]

    def test_the_checks_are_not_hollowed_out(self) -> None:
        """A lesson whose checks had been removed would have nothing left to replay."""
        simulated = simulated_lesson(REPO, SHA)

        assert all(step.validation.is_checkable for step in simulated.lesson.steps)
        assert any(step.expects_failure for step in simulated.lesson.steps)
