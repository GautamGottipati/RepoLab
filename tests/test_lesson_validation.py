"""Deciding whether a step worked, and whether a lesson is fit to show.

The specification says: deterministic validation first, Nano only for fuzzy
cases. So these tests check both halves of that -- that the clear cases never
reach a model, and that the one genuinely ambiguous case does and is labelled as
having done so.
"""

from __future__ import annotations

import pytest

from repolab.lessons.cassandra import cassandra_lesson
from repolab.lessons.models import CheckKind, Lesson, LessonStep, StepCheck
from repolab.lessons.validation import (
    DETERMINISTIC,
    NANO,
    LessonRejected,
    NanoVerdict,
    check_step,
    ensure_usable,
    failed,
    lesson_problems,
    looks_like_error,
    resolve_with_model,
)

FILTERING_ERROR = (
    "<stdin>:1:InvalidRequest: Error from server: code=2200 [Invalid query] "
    'message="Cannot execute this query as it might involve data filtering and thus may '
    "have unpredictable performance. If you want to execute this query despite the "
    'performance unpredictability, use ALLOW FILTERING"'
)

LESSON = cassandra_lesson("https://github.com/apache/cassandra", "c" * 40)


def step(number: int) -> LessonStep:
    found = LESSON.step(number)
    assert found is not None
    return found


class FakeRouter:
    """Records whether it was consulted, and with what."""

    def __init__(self, *, passed: bool = True, reason: str = "equivalent") -> None:
        self.calls: list[str] = []
        self._answer = NanoVerdict(passed=passed, reason=reason)

    async def nano(self, *, purpose, schema, prompt, system=None, **_: object):
        self.calls.append(prompt)

        class Completion:
            value = self._answer

        return Completion()


class TestRecognisingFailure:
    @pytest.mark.parametrize(
        "output",
        [
            FILTERING_ERROR,
            "SyntaxException: line 1:0 no viable alternative at input 'SELCT'",
            "InvalidRequest: unconfigured table sensor_readings",
        ],
    )
    def test_a_printed_server_error_is_a_failure(self, output: str) -> None:
        """cqlsh prints the error; the exit code is not the only signal."""
        assert looks_like_error(output) is True
        assert failed(output, 0) is True

    def test_a_non_zero_exit_is_a_failure(self) -> None:
        assert failed("", 2) is True

    def test_ordinary_output_is_not(self) -> None:
        assert failed(" id | c\n----+---\n  a | 1\n", 0) is False


class TestTheDeterministicChecks:
    def test_a_version_number_satisfies_a_pattern_check(self) -> None:
        verdict = check_step(step(1), step(1).action, "release_version\n 5.0.9\n", 0)
        assert verdict.passed is True
        assert verdict.decided_by == DETERMINISTIC
        assert verdict.conclusive is True

    def test_a_listed_keyspace_satisfies_a_contains_check(self) -> None:
        verdict = check_step(step(2), step(2).action, "system_schema\nsystem\n", 0)
        assert verdict.passed is True

    def test_silence_satisfies_a_success_check(self) -> None:
        """Cassandra accepts a schema change by saying nothing."""
        assert check_step(step(3), step(3).action, "", 0).passed is True

    def test_a_failure_fails_a_success_check_with_the_server_reason(self) -> None:
        verdict = check_step(step(3), step(3).action, "ConfigurationException: bad class", 2)
        assert verdict.passed is False
        assert "ConfigurationException" in verdict.reason
        assert verdict.conclusive is True, "a rejected statement is not an ambiguous case"

    def test_a_broken_pattern_in_a_lesson_fails_the_step_rather_than_passing_it(self) -> None:
        broken = step(1).model_copy(
            update={"validation": StepCheck(kind=CheckKind.OUTPUT_MATCHES, expected="(unclosed")}
        )
        verdict = check_step(broken, broken.action, "5.0.9", 0)
        assert verdict.passed is False
        assert "not a valid pattern" in verdict.reason


class TestWhatWasRunMustResembleWhatWasAsked:
    """Found by running it: `DESCRIBE TABLES` passed a step asking for `CREATE KEYSPACE`.

    A `succeeds` check only looks at the exit code, so before this any statement
    the server accepted satisfied it -- a learner could be told they had created
    a keyspace for running `SELECT 1`.
    """

    def test_an_unrelated_statement_does_not_satisfy_a_succeeds_check(self) -> None:
        create = step(3)
        assert create.validation.kind is CheckKind.SUCCEEDS

        verdict = check_step(create, "DESCRIBE TABLES", "", 0)

        assert verdict.passed is False
        assert "CREATE" in verdict.reason and "DESCRIBE" in verdict.reason

    def test_the_mismatch_is_inconclusive_so_a_model_can_adjudicate(self) -> None:
        """A learner who phrased the same intent differently is judged, not rejected."""
        verdict = check_step(step(3), "DESCRIBE TABLES", "", 0)

        assert verdict.conclusive is False

    def test_the_asked_for_statement_still_passes(self) -> None:
        create = step(3)
        assert check_step(create, create.action, "", 0).passed is True

    def test_a_different_keyspace_name_is_still_the_same_kind_of_statement(self) -> None:
        """Only the verb is compared; the content checks constrain the rest."""
        create = step(3)
        altered = create.action.replace("repolab", "mine")

        assert check_step(create, altered, "", 0).passed is True

    def test_cqls_own_abbreviation_is_not_a_mismatch(self) -> None:
        """The lesson's own hint suggests DESC, so it must not fail for it."""
        describe = step(2)

        verdict = check_step(describe, "DESC KEYSPACES", "system_schema\n", 0)

        assert verdict.passed is True


class TestTheStepThatMustFail:
    """Step 7 provokes an error on purpose. Succeeding is how it fails."""

    def test_the_expected_error_passes_the_step(self) -> None:
        verdict = check_step(step(7), step(7).action, FILTERING_ERROR, 2)
        assert verdict.passed is True
        assert "ALLOW FILTERING" in verdict.reason

    def test_an_accepted_statement_fails_the_step(self) -> None:
        verdict = check_step(step(7), step(7).action, " id | c\n  a | 1\n", 0)
        assert verdict.passed is False
        assert "expects the server to reject it" in verdict.reason

    def test_a_different_error_is_inconclusive_rather_than_wrong(self) -> None:
        """They did provoke an error -- just not this one. That is a judgement call."""
        verdict = check_step(step(7), step(7).action, "InvalidRequest: unconfigured table t", 2)
        assert verdict.passed is False
        assert verdict.conclusive is False


class TestWhenNanoIsConsulted:
    async def test_a_conclusive_verdict_never_reaches_a_model(self) -> None:
        router = FakeRouter()
        verdict = check_step(step(2), step(2).action, "system_schema\n", 0)

        resolved = await resolve_with_model(
            step(2), step(2).action, "system_schema\n", verdict, router
        )

        assert router.calls == [], "deterministic first means deterministic only, when it can"
        assert resolved is verdict

    async def test_an_inconclusive_verdict_is_resolved_by_nano_and_labelled(self) -> None:
        """The real fuzzy case: the learner did the right thing with a different name."""
        output = " sensor_id | celsius\n   bedroom |    19.0\n"
        verdict = check_step(step(6), step(6).action, output, 0)
        assert verdict.conclusive is False

        router = FakeRouter(passed=True, reason="they queried their own row")
        resolved = await resolve_with_model(step(6), step(6).action, output, verdict, router)

        assert len(router.calls) == 1
        assert resolved.passed is True
        assert resolved.decided_by == NANO
        assert resolved.conclusive is False, "a model's answer is never a certainty"

    async def test_nano_is_shown_the_output_and_not_the_hint(self) -> None:
        output = " sensor_id | celsius\n   bedroom |    19.0\n"
        verdict = check_step(step(6), step(6).action, output, 0)
        router = FakeRouter()

        await resolve_with_model(step(6), step(6).action, output, verdict, router)

        prompt = router.calls[0]
        assert "bedroom" in prompt
        assert step(6).hint not in prompt

    async def test_no_router_leaves_the_deterministic_verdict_standing(self) -> None:
        """Which is why the offline suite needs no model."""
        output = " sensor_id | celsius\n   bedroom |    19.0\n"
        verdict = check_step(step(6), step(6).action, output, 0)

        resolved = await resolve_with_model(step(6), step(6).action, output, verdict, None)

        assert resolved is verdict

    async def test_a_model_failure_does_not_block_the_lesson(self) -> None:
        class Broken:
            async def nano(self, **_: object):
                raise RuntimeError("no credentials")

        output = " sensor_id | celsius\n   bedroom |    19.0\n"
        verdict = check_step(step(6), step(6).action, output, 0)

        resolved = await resolve_with_model(step(6), step(6).action, output, verdict, Broken())

        assert resolved.passed is False
        assert "could not consult a model" in resolved.reason


class TestGatingAGeneratedLesson:
    def test_the_deterministic_lesson_passes_its_own_gate(self) -> None:
        assert lesson_problems(LESSON) == []
        assert ensure_usable(LESSON) is LESSON

    def test_a_step_running_a_refused_command_is_rejected(self) -> None:
        bad = LESSON.model_copy(
            update={"steps": [step(1).model_copy(update={"action": "DROP KEYSPACE system_auth"})]}
        )
        problems = lesson_problems(bad)
        assert any("refused command" in problem for problem in problems)

    def test_a_step_whose_expectation_cannot_be_checked_is_rejected(self) -> None:
        bad = LESSON.model_copy(
            update={
                "steps": [
                    step(1).model_copy(
                        update={"validation": StepCheck(kind=CheckKind.OUTPUT_CONTAINS)}
                    )
                ]
            }
        )
        assert any("cannot be checked" in problem for problem in lesson_problems(bad))

    def test_a_lesson_with_no_steps_is_rejected(self) -> None:
        empty = Lesson(repo_url="r", title="t", summary="s")
        assert "the lesson has no steps" in lesson_problems(empty)

    def test_steps_out_of_order_are_rejected(self) -> None:
        shuffled = LESSON.model_copy(update={"steps": [step(2), step(1)]})
        assert any("numbered" in problem for problem in lesson_problems(shuffled))

    def test_rejection_lists_every_problem_at_once(self) -> None:
        """One round trip should be enough to fix a generated lesson."""
        bad = LESSON.model_copy(
            update={
                "steps": [
                    step(1).model_copy(
                        update={
                            "action": "rm -rf /",
                            "validation": StepCheck(kind=CheckKind.OUTPUT_CONTAINS),
                            "concept": "",
                        }
                    )
                ]
            }
        )
        with pytest.raises(LessonRejected) as raised:
            ensure_usable(bad)
        assert len(raised.value.problems) >= 2
