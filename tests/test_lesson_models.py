"""The lesson structure, and the record of what a learner did.

The specification names nine things a step must carry -- concept, goal, context,
action, expected observation, validation, hint, code references, documentation
references -- so the first thing these tests check is that all nine are there and
that the ones a lesson cannot work without are required.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repolab.evidence.models import CodeReference, DocumentationReference
from repolab.lessons.models import (
    MAX_TEXT,
    CheckKind,
    Lesson,
    LessonProgress,
    LessonStep,
    StepAttempt,
    StepCheck,
)


def step(number: int = 1, **overrides: object) -> LessonStep:
    defaults: dict[str, object] = {
        "number": number,
        "concept": "partition keys decide where a row lives",
        "goal": "create a table whose key you chose",
        "context": "the key determines which queries are possible",
        "action": "CREATE TABLE repolab.t (id text PRIMARY KEY)",
        "expected_observation": "no output; the table was created",
        "validation": StepCheck(kind=CheckKind.SUCCEEDS),
    }
    return LessonStep(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestTheStepStructure:
    @pytest.mark.parametrize(
        "field",
        [
            "concept",
            "goal",
            "context",
            "action",
            "expected_observation",
            "validation",
            "hint",
            "code_references",
            "documentation_references",
        ],
    )
    def test_every_named_field_exists(self, field: str) -> None:
        assert field in LessonStep.model_fields

    @pytest.mark.parametrize(
        "missing", ["concept", "goal", "context", "action", "expected_observation", "validation"]
    )
    def test_the_fields_a_lesson_cannot_work_without_are_required(self, missing: str) -> None:
        payload = {
            "number": 1,
            "concept": "c",
            "goal": "g",
            "context": "x",
            "action": "SELECT 1",
            "expected_observation": "o",
            "validation": StepCheck(kind=CheckKind.SUCCEEDS),
        }
        del payload[missing]
        with pytest.raises(ValidationError):
            LessonStep(**payload)  # type: ignore[arg-type]

    def test_a_hint_is_optional(self) -> None:
        assert step().hint == ""

    def test_step_numbers_start_at_one(self) -> None:
        with pytest.raises(ValidationError):
            step(0)

    def test_long_prose_is_truncated_rather_than_rejected(self) -> None:
        """A generated step that rambles is still usable; a wall of text is not."""
        assert len(step(context="x" * (MAX_TEXT * 2)).context) == MAX_TEXT

    def test_a_step_citing_the_repository_is_grounded(self) -> None:
        assert step(code_references=[CodeReference(path="bin/cqlsh")]).grounded is True

    def test_a_step_citing_documentation_is_grounded(self) -> None:
        reference = DocumentationReference(url="https://cassandra.apache.org/doc/")
        assert step(documentation_references=[reference]).grounded is True

    def test_a_step_citing_nothing_is_not(self) -> None:
        assert step().grounded is False


class TestChecks:
    def test_a_success_check_needs_no_expectation(self) -> None:
        assert StepCheck(kind=CheckKind.SUCCEEDS).is_checkable is True

    @pytest.mark.parametrize(
        "kind", [CheckKind.OUTPUT_CONTAINS, CheckKind.OUTPUT_MATCHES, CheckKind.FAILS_WITH]
    )
    def test_an_expectation_free_content_check_is_not_checkable(self, kind: CheckKind) -> None:
        """A check that cannot fail is a check that teaches nothing."""
        assert StepCheck(kind=kind, expected="   ").is_checkable is False

    def test_a_step_expecting_failure_says_so(self) -> None:
        failing = step(validation=StepCheck(kind=CheckKind.FAILS_WITH, expected="ALLOW FILTERING"))
        assert failing.expects_failure is True
        assert step().expects_failure is False


class TestTheLesson:
    def test_steps_are_reachable_by_number(self) -> None:
        lesson = Lesson(repo_url="r", title="t", summary="s", steps=[step(1), step(2)])
        assert lesson.step(2) is not None
        assert lesson.step(3) is None

    def test_the_step_count_comes_from_the_steps(self) -> None:
        assert Lesson(repo_url="r", title="t", summary="s", steps=[step()]).step_count == 1

    def test_ungrounded_steps_are_identified(self) -> None:
        """So a thin lesson is visible rather than merely thin."""
        lesson = Lesson(
            repo_url="r",
            title="t",
            summary="s",
            steps=[step(1, code_references=[CodeReference(path="bin/cqlsh")]), step(2)],
        )
        assert lesson.ungrounded_steps == [2]


class TestProgress:
    def test_an_attempt_is_recorded_whether_it_passed_or_not(self) -> None:
        progress = LessonProgress(lesson_id="l")
        progress.record(StepAttempt(step=1, command="SELECT 1", passed=False))
        progress.record(StepAttempt(step=1, command="SELECT 1", passed=True))

        assert len(progress.attempts_for(1)) == 2
        assert progress.completed == [1]

    def test_a_step_is_completed_once_however_often_it_is_rerun(self) -> None:
        progress = LessonProgress(lesson_id="l")
        for _ in range(3):
            progress.record(StepAttempt(step=1, command="SELECT 1", passed=True))

        assert progress.completed == [1]
        assert progress.completed_count == 1

    def test_who_decided_is_recorded(self) -> None:
        """A learner told they succeeded should be able to see what decided it."""
        progress = LessonProgress(lesson_id="l")
        progress.record(
            StepAttempt(step=1, command="SELECT 1", passed=True, decided_by="nano", reason="close")
        )
        assert progress.attempts[0].decided_by == "nano"

    def test_the_next_step_is_the_first_not_completed(self) -> None:
        lesson = Lesson(repo_url="r", title="t", summary="s", steps=[step(1), step(2), step(3)])
        progress = LessonProgress(lesson_id=lesson.id)
        progress.record(StepAttempt(step=1, command="x", passed=True))

        following = progress.next_step(lesson)
        assert following is not None and following.number == 2

    def test_a_lesson_is_complete_when_every_step_is(self) -> None:
        lesson = Lesson(repo_url="r", title="t", summary="s", steps=[step(1), step(2)])
        progress = LessonProgress(lesson_id=lesson.id)

        progress.record(StepAttempt(step=1, command="x", passed=True))
        assert progress.is_complete(lesson) is False

        progress.record(StepAttempt(step=2, command="y", passed=True))
        assert progress.is_complete(lesson) is True
        assert progress.next_step(lesson) is None

    def test_finishing_is_recorded(self) -> None:
        progress = LessonProgress(lesson_id="l")
        assert progress.finished_at is None
        progress.finish()
        assert progress.finished_at is not None


class TestSilentStatements:
    """cqlsh prints nothing when a schema change or write succeeds.

    A generated lesson told a learner to expect "Keyspace created successfully."
    The terminal truthfully showed "(no output)", and the lesson looked wrong.
    """

    @staticmethod
    def step(action: str, observation: str, kind: str = "succeeds"):
        from repolab.lessons.models import CheckKind, LessonStep, StepCheck

        return LessonStep(
            number=1,
            concept="keyspaces",
            goal="make one",
            context="x",
            action=action,
            expected_observation=observation,
            validation=StepCheck(kind=CheckKind(kind), expected="" if kind == "succeeds" else "x"),
        )

    def test_an_invented_success_message_is_replaced(self) -> None:
        from repolab.lessons.models import SILENT_OBSERVATION

        step = self.step(
            "CREATE KEYSPACE IF NOT EXISTS demo WITH REPLICATION = "
            "{'class': 'SimpleStrategy', 'replication_factor': 1}",
            "Keyspace created successfully.",
        )
        assert step.expected_observation == SILENT_OBSERVATION

    def test_an_honest_expectation_is_kept(self) -> None:
        step = self.step("INSERT INTO t (k) VALUES (1)", "No output: the row is there.")
        assert step.expected_observation == "No output: the row is there."

    def test_a_statement_that_prints_is_left_alone(self) -> None:
        step = self.step("SELECT * FROM t", "One row: kitchen, 21.5.")
        assert step.expected_observation == "One row: kitchen, 21.5."

    def test_an_expected_failure_is_left_alone(self) -> None:
        step = self.step("CREATE TABLE t (k int)", "An error naming the missing key", "fails_with")
        assert step.expected_observation == "An error naming the missing key"
