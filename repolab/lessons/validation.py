"""Deciding whether a lesson step worked.

Deterministic first. Every check a lesson may declare is answerable by ordinary
Python from the command and its real output, and that is what runs: the exit
code, the text the server printed, the name the learner used. A model is asked
only when the deterministic check genuinely cannot reach a verdict -- the learner
succeeded in a way the step did not literally predict -- and when it is asked,
its answer is recorded as the model's, not as a measurement.

The same module gates *generated* lessons. A step whose command would be refused
by the terminal, or whose expectation cannot be evaluated, is rejected before a
learner ever sees it: a check that cannot fail is a check that teaches nothing.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from repolab.experiences.cli.safety import Refusal
from repolab.experiences.cli.safety import check as check_safety
from repolab.lessons.models import MAX_STEPS, CheckKind, Lesson, LessonStep, StepAttempt

#: Text cqlsh prints when the server rejected a statement. Checked in addition to
#: the exit code, because a statement can fail inside an otherwise successful
#: invocation and the printed message is the only sign.
ERROR_MARKERS: tuple[str, ...] = (
    "invalidrequest",
    "syntaxexception",
    "error from server",
    "unconfigured table",
    "undefined column",
    "keyspace does not exist",
    "table does not exist",
    "no viable alternative",
    "configurationexception",
    "traceback (most recent call last)",
)

#: How a verdict was reached.
DETERMINISTIC = "deterministic"
NANO = "nano"


class Verdict(BaseModel):
    """What a check concluded, and who concluded it."""

    passed: bool
    decided_by: str = DETERMINISTIC
    reason: str = ""
    conclusive: bool = True
    """False when deterministic logic could not settle it and a model was the
    only remaining option. Recorded so an inconclusive pass is never shown as a
    certainty."""

    def to_attempt(self, step: int, command: str, output: str) -> StepAttempt:
        return StepAttempt(
            step=step,
            command=command,
            output=output,
            passed=self.passed,
            decided_by=self.decided_by,
            reason=self.reason,
        )


class NanoVerdict(BaseModel):
    """The schema Nano answers an ambiguous step with."""

    passed: bool
    reason: str


def looks_like_error(output: str) -> bool:
    """Whether this output shows the server rejecting the statement."""
    lowered = output.lower()
    return any(marker in lowered for marker in ERROR_MARKERS)


def failed(output: str, exit_code: int | None) -> bool:
    """Whether the statement did not succeed.

    Either signal is enough. A non-zero exit is conclusive, and so is a printed
    server error, because ``cqlsh -e`` has been observed to report both.
    """
    if exit_code is not None and exit_code != 0:
        return True
    return looks_like_error(output)


def check_step(
    step: LessonStep,
    command: str,
    output: str,
    exit_code: int | None = None,
) -> Verdict:
    """Decide *step* from the real command and its real output.

    Returns an inconclusive verdict -- never a guess -- when the deterministic
    check cannot settle it. The caller may then consult Nano through
    :func:`resolve_with_model`.
    """
    check = step.validation

    # What was run has to resemble what was asked. Without this, a `succeeds`
    # check passes for *any* statement the server accepts: `DESCRIBE TABLES`
    # satisfied a step that asked for `CREATE KEYSPACE`, which was found by
    # running it. Inconclusive rather than failed, so a learner who phrased the
    # same intent differently is judged rather than rejected.
    mismatch = _verb_mismatch(step, command)
    if mismatch is not None:
        return mismatch
    did_fail = failed(output, exit_code)

    if check.kind is CheckKind.FAILS_WITH:
        # The lesson provokes one error on purpose, so here failure is the pass.
        if not did_fail:
            return Verdict(
                passed=False,
                reason="the statement was accepted; this step expects the server to reject it",
            )
        if _contains(output, check.expected):
            return Verdict(
                passed=True,
                reason=f"the server rejected it, mentioning {check.expected!r}",
            )
        return Verdict(
            passed=False,
            conclusive=False,
            reason=(
                f"the statement was rejected, but the message does not mention {check.expected!r}"
            ),
        )

    if did_fail:
        # Nothing fuzzy about this: every other check needs the statement to have
        # worked, and it did not.
        return Verdict(passed=False, reason=_first_error_line(output) or "the statement failed")

    if check.kind is CheckKind.SUCCEEDS:
        return Verdict(passed=True, reason="the server accepted the statement")

    if check.kind is CheckKind.OUTPUT_CONTAINS:
        if _contains(output, check.expected):
            return Verdict(passed=True, reason=f"the output contains {check.expected!r}")
        return Verdict(
            passed=False,
            conclusive=False,
            reason=(f"the statement succeeded but the output does not contain {check.expected!r}"),
        )

    if check.kind is CheckKind.OUTPUT_MATCHES:
        try:
            matched = re.search(check.expected, output, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            # A broken pattern is the lesson's fault, not the learner's, and it
            # must not silently pass them.
            return Verdict(
                passed=False,
                reason=f"this step's expectation is not a valid pattern: {exc}",
            )
        if matched:
            return Verdict(passed=True, reason="the output matches what the step expected")
        return Verdict(
            passed=False,
            conclusive=False,
            reason="the statement succeeded but the output does not match what the step expected",
        )

    return Verdict(passed=False, reason=f"unsupported check: {check.kind}")


async def resolve_with_model(
    step: LessonStep,
    command: str,
    output: str,
    verdict: Verdict,
    router: object | None,
) -> Verdict:
    """Ask Nano about an inconclusive verdict. Anything else is returned unchanged.

    The guard rails are deliberate. Nano is never consulted for a verdict
    deterministic code already reached, it is never shown the step's own hint,
    and its answer is marked ``decided_by="nano"`` so a learner can see that the
    pass came from a judgement rather than a check. With no router configured the
    deterministic verdict stands, which is why the offline suite needs no model.
    """
    if verdict.conclusive or router is None:
        return verdict

    prompt = _nano_prompt(step, command, output)
    try:
        completion = await router.nano(  # type: ignore[attr-defined]
            purpose="lesson_step_check",
            schema=NanoVerdict,
            prompt=prompt,
            system=(
                "You judge whether a learner achieved a lesson step's goal. "
                "Answer only from the command and the output you are shown. "
                "If the output does not show the goal achieved, answer false."
            ),
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001 - a model failure must not block a lesson
        return verdict.model_copy(
            update={"reason": f"{verdict.reason} (could not consult a model: {exc})"}
        )

    answer = completion.value
    return Verdict(
        passed=answer.passed,
        decided_by=NANO,
        conclusive=False,
        reason=answer.reason.strip() or verdict.reason,
    )


def _nano_prompt(step: LessonStep, command: str, output: str) -> str:
    return "\n".join(
        [
            "A learner is working through a guided Cassandra lesson.",
            "",
            f"Step goal: {step.goal}",
            f"What the step expected them to see: {step.expected_observation}",
            f"The step suggested this statement: {step.action}",
            "",
            "What the learner actually ran:",
            command,
            "",
            "What the server actually printed:",
            output[:4000] or "(no output)",
            "",
            "Did the learner achieve the step's goal? Judge only from the output above.",
        ]
    )


def _verb_mismatch(step: LessonStep, command: str) -> Verdict | None:
    """``None`` when the command's leading verb is the one the step asked for.

    Only the verb. Comparing whole statements would reject every legitimate
    variation -- a different keyspace name, extra whitespace, an abbreviation --
    and the content checks already constrain the rest. What this catches is a
    statement that is not the *kind* of thing the step was about.
    """
    wanted = _leading_verb(step.action)
    actual = _leading_verb(command)
    if not wanted or not actual or wanted == actual:
        return None

    # DESC is CQL's own abbreviation for DESCRIBE; treating them as different
    # verbs would fail a learner for using the shorter form the lesson suggests.
    if {wanted, actual} <= {"desc", "describe"}:
        return None

    return Verdict(
        passed=False,
        conclusive=False,
        reason=(f"this step is about {wanted.upper()}, and {actual.upper()} was run instead"),
    )


def _leading_verb(statement: str) -> str:
    first = statement.strip().split(maxsplit=1)
    return first[0].lower().rstrip(";") if first else ""


def _contains(output: str, expected: str) -> bool:
    return expected.strip().lower() in output.lower()


def _first_error_line(output: str) -> str:
    """The line the server failed on, for a reason a learner can act on."""
    for line in output.splitlines():
        if any(marker in line.lower() for marker in ERROR_MARKERS):
            return line.strip()[:300]
    return ""


# -- gating generated lessons ---------------------------------------------


class LessonRejected(ValueError):
    """A generated lesson is not fit to put in front of a learner."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def lesson_problems(lesson: Lesson) -> list[str]:
    """Everything wrong with *lesson*, as sentences naming the step.

    Applied to any lesson a model wrote, for the same reason the experiment plan
    is validated before it runs: the model proposes and deterministic code
    decides.
    """
    problems: list[str] = []

    if not lesson.steps:
        problems.append("the lesson has no steps")
    if len(lesson.steps) > MAX_STEPS:
        problems.append(f"the lesson has {len(lesson.steps)} steps; the limit is {MAX_STEPS}")

    expected_numbers = list(range(1, len(lesson.steps) + 1))
    if [step.number for step in lesson.steps] != expected_numbers:
        problems.append("steps are not numbered 1..n in order")

    for step in lesson.steps:
        label = f"step {step.number}"
        if not step.action.strip():
            problems.append(f"{label} asks the learner to run nothing")
            continue
        if not step.validation.is_checkable:
            problems.append(f"{label} declares an expectation that cannot be checked")
        verdict = check_safety(step.action)
        if isinstance(verdict, Refusal):
            problems.append(f"{label} would run a refused command: {verdict.reason}")
        elif step.validation.kind is CheckKind.OUTPUT_MATCHES:
            try:
                re.compile(step.validation.expected)
            except re.error as exc:
                problems.append(f"{label} declares an invalid pattern: {exc}")
        if not step.goal.strip() or not step.concept.strip():
            problems.append(f"{label} is missing its concept or goal")
        if not step.expected_observation.strip():
            problems.append(f"{label} does not say what the learner should see")

    return problems


def ensure_usable(lesson: Lesson) -> Lesson:
    """Return *lesson* if it is fit for a learner, else raise.

    Raises:
        LessonRejected: listing every problem, so one round trip fixes them all.
    """
    problems = lesson_problems(lesson)
    if problems:
        raise LessonRejected(problems)
    return lesson
