"""Lessons, steps, and what a learner actually did.

The step structure is fixed by the specification, and every field is here for a
reason a learner would recognise:

    concept                 what this teaches
    goal                    what they will have done
    context                 why it matters, before they type anything
    action                  what to type
    expected observation    what they should see
    validation              how RepoLab decides it worked
    hint                    what to try when it does not
    code references         the repository lines behind it
    documentation references  the official pages behind it

The last two are what separate a lesson from a tutorial someone wrote once: every
step points at the real code and the real documentation it came from, so a
learner can check the claim rather than take it.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from repolab.evidence.models import CodeReference, DocumentationReference

#: Bounds, so a generated lesson cannot become a wall of text.
MAX_STEPS = 12
MAX_TEXT = 600


class CheckKind(StrEnum):
    """How a step decides whether it worked.

    Every one of these is answerable from the command and its output by ordinary
    code. That is the point: a model is consulted only when a deterministic check
    cannot reach a verdict, never as the first resort.
    """

    OUTPUT_CONTAINS = "output_contains"
    """The output contains a given string, case-insensitively."""

    OUTPUT_MATCHES = "output_matches"
    """The output matches a regular expression."""

    SUCCEEDS = "succeeds"
    """The statement ran and Cassandra accepted it."""

    FAILS_WITH = "fails_with"
    """The statement was *rejected*, with a message containing a given string.

    The lesson deliberately provokes one error; a step that expects a failure has
    to be able to say so, or the only way to teach an error would be to call it a
    success.
    """


class StepCheck(BaseModel):
    """How RepoLab decides a step worked."""

    kind: CheckKind
    expected: str = ""
    """What to look for. Unused by ``SUCCEEDS``."""

    describe: str = ""
    """How the check reads to a learner, e.g. "the output lists your keyspace"."""

    @property
    def is_checkable(self) -> bool:
        """Whether this check can actually be evaluated.

        A check that needs an ``expected`` and has none is not a check, and a
        step carrying one is rejected at generation rather than passing
        everything by accident.
        """
        if self.kind is CheckKind.SUCCEEDS:
            return True
        return bool(self.expected.strip())


#: Statements cqlsh runs in silence when they succeed. Verified against the real
#: shell: a successful ``CREATE KEYSPACE`` prints nothing at all.
SILENT_VERBS: frozenset[str] = frozenset(
    {"create", "alter", "drop", "truncate", "insert", "update", "delete", "use", "begin", "apply"}
)

#: What a silent success looks like, said plainly.
SILENT_OBSERVATION = (
    "No output. cqlsh prints nothing when this statement succeeds; the absence of "
    "an error is the result."
)

#: Wording that says the step prints nothing. "No error" alone is not enough: a
#: generated step paired it with "the prompt changes", which RepoLab's terminal,
#: whose prompt is fixed, does not do.
_ACKNOWLEDGES_SILENCE = re.compile(r"no output|prints nothing|nothing is printed|silen", re.I)


def runs_silently(action: str) -> bool:
    """Whether cqlsh prints nothing for *action* when every statement in it succeeds."""
    from repolab.experiences.cli.safety import split_statements

    statements = split_statements(action)
    return bool(statements) and all(
        statement.split(maxsplit=1)[0].lower() in SILENT_VERBS for statement in statements
    )


class LessonStep(BaseModel):
    """One thing a learner does."""

    number: int = Field(ge=1)

    concept: str
    goal: str
    context: str
    action: str
    """What to type. The exact statement, so the terminal can offer it."""

    expected_observation: str
    validation: StepCheck
    hint: str = ""

    code_references: list[CodeReference] = Field(default_factory=list)
    documentation_references: list[DocumentationReference] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        for field in ("concept", "goal", "context", "expected_observation", "hint"):
            value = getattr(self, field)
            if isinstance(value, str) and len(value) > MAX_TEXT:
                object.__setattr__(self, field, value[:MAX_TEXT])

        # A generated lesson once told learners to expect "Keyspace created
        # successfully." -- output cqlsh never prints. An expectation that
        # contradicts the real shell is model-invented output, so it is replaced
        # rather than shown. Applied on load as well, which corrects lessons
        # already stored.
        if (
            self.validation.kind is CheckKind.SUCCEEDS
            and runs_silently(self.action)
            and not _ACKNOWLEDGES_SILENCE.search(self.expected_observation)
        ):
            object.__setattr__(self, "expected_observation", SILENT_OBSERVATION)

    @computed_field
    @property
    def grounded(self) -> bool:
        """Whether this step cites the repository or its documentation."""
        return bool(self.code_references or self.documentation_references)

    @property
    def expects_failure(self) -> bool:
        return self.validation.kind is CheckKind.FAILS_WITH


class Lesson(BaseModel):
    """One guided exploration of a repository."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    repo_url: str
    commit_sha: str = ""

    title: str
    summary: str
    steps: list[LessonStep] = Field(default_factory=list)

    generated_by: str = "deterministic"
    """``super`` when a model wrote it, ``deterministic`` when RepoLab did."""

    fidelity: str = "unknown"
    """The fidelity of the environment this lesson was written for."""

    simulated: bool = False
    """True when this lesson runs against a Mini Lab rather than a real server."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def step_count(self) -> int:
        return len(self.steps)

    def step(self, number: int) -> LessonStep | None:
        return next((step for step in self.steps if step.number == number), None)

    @property
    def ungrounded_steps(self) -> list[int]:
        """Steps citing neither code nor documentation."""
        return [step.number for step in self.steps if not step.grounded]


class StepAttempt(BaseModel):
    """One try at one step, and how it was judged.

    ``decided_by`` matters: a learner being told they succeeded should be able to
    find out whether that was a deterministic check or a model's opinion.
    """

    step: int
    command: str
    output: str = ""
    passed: bool = False
    decided_by: str = "deterministic"
    reason: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LessonProgress(BaseModel):
    """How far a learner has got, and what they tried on the way."""

    lesson_id: str
    session_id: str | None = None
    attempts: list[StepAttempt] = Field(default_factory=list)
    completed: list[int] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def record(self, attempt: StepAttempt) -> None:
        """Record a try. A passed step is completed once, however often retried."""
        self.attempts.append(attempt)
        if attempt.passed and attempt.step not in self.completed:
            self.completed.append(attempt.step)

    @computed_field
    @property
    def completed_count(self) -> int:
        return len(self.completed)

    def attempts_for(self, step: int) -> list[StepAttempt]:
        return [attempt for attempt in self.attempts if attempt.step == step]

    def is_complete(self, lesson: Lesson) -> bool:
        return all(step.number in self.completed for step in lesson.steps)

    def next_step(self, lesson: Lesson) -> LessonStep | None:
        """The first step not yet completed."""
        return next((step for step in lesson.steps if step.number not in self.completed), None)

    def finish(self) -> None:
        self.finished_at = datetime.now(UTC)
