"""The lesson, when only a Mini Lab could be produced.

The same eight steps, in the same order, teaching the same concepts -- and not
one line of invented output. CLAUDE.md's Mini Lab rules are enforced here rather
than requested:

* every response carries the ``SIMULATED`` label and says it was not executed;
* a step the lab cannot speak to returns the "requires a real environment"
  sentence and nothing else, because inventing plausible output is the exact
  failure these rules exist to prevent;
* anything that reads as a measurement is withheld, using the same check the
  Mini Lab build already applies;
* every step keeps the repository references the real lesson has, so a simulated
  concept is still tied to the code that implements it.

The steps keep their real validation checks even though nothing here can satisfy
them. That is deliberate: when a real environment becomes available, this is the
lesson that replays against it (CLAUDE.md), and a lesson whose checks had been
hollowed out would have nothing left to replay.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

from repolab.builds.minilab import (
    NEEDS_REAL_ENVIRONMENT,
    SIMULATED_LABEL,
    MiniLab,
    measurement_language,
)
from repolab.evidence.models import CodeReference, EvidenceBundle
from repolab.lessons.cassandra import RepositoryEvidence, cassandra_lesson
from repolab.lessons.models import Lesson, LessonStep

#: Shown on every step. Not the same sentence as the lab's own refusal: this one
#: is about the lesson as a whole, and appears whether or not the lab happens to
#: cover the step.
SIMULATED_NOTICE = (
    "Nothing is executed in this lesson. No environment could be built for this "
    "repository, so each step explains what the statement would do and names the "
    "source that implements it. Completing the steps for real requires a real build."
)

#: Replaces each step's expected observation. The real one describes output, and
#: there is no output here.
NO_OBSERVATION = "Nothing to observe: this statement was not run. It requires a real build."

#: Withheld in place of anything that reads like a performance figure.
WITHHELD = (
    "This explanation was withheld because it contained a figure that would look "
    "like a measurement. A Mini Lab measures nothing."
)


class SimulatedStep(BaseModel):
    """One step of a simulated lesson."""

    number: int
    concept: str
    goal: str
    context: str
    action: str
    """What the learner would type. Shown, never executed."""

    explanation: str
    """What it would conceptually do, or why nothing can be said about it."""

    covered: bool
    """Whether the Mini Lab actually has something to say about this statement."""

    code_references: list[CodeReference] = Field(default_factory=list)

    @computed_field
    @property
    def label(self) -> str:
        return SIMULATED_LABEL

    @computed_field
    @property
    def requires_real_build(self) -> bool:
        """Always true. Every statement in this lesson needs a real server to run."""
        return True


class SimulatedLesson(BaseModel):
    """A Mini Lab rendering of the lesson. Simulated throughout."""

    repo_url: str
    commit_sha: str = ""
    title: str
    summary: str
    notice: str = SIMULATED_NOTICE
    steps: list[SimulatedStep] = Field(default_factory=list)
    lesson: Lesson
    """The real lesson this simulates, kept whole so it can be replayed later."""

    @computed_field
    @property
    def simulated(self) -> bool:
        return True

    @computed_field
    @property
    def label(self) -> str:
        return SIMULATED_LABEL

    @computed_field
    @property
    def covered_count(self) -> int:
        """How many steps the lab can speak to. Often few, and said plainly."""
        return sum(1 for step in self.steps if step.covered)

    def evidence(self) -> EvidenceBundle:
        """Code references only, marked simulated.

        No measurements by construction: ``EvidenceBundle`` refuses to hold a
        measurement alongside ``simulated=True``, so this cannot regress into
        presenting simulated behaviour as experimental evidence.
        """
        references: list[CodeReference] = []
        for step in self.steps:
            references.extend(step.code_references)
        return EvidenceBundle(code_references=references[:20], simulated=True)


def simulated_lesson(
    repo_url: str,
    commit_sha: str = "",
    *,
    lab: MiniLab | None = None,
    evidence: RepositoryEvidence | None = None,
) -> SimulatedLesson:
    """The Cassandra lesson as a conceptual simulation.

    *lab* supplies explanations where it has them. With no lab -- or for a
    statement it does not cover -- the step says it requires a real environment,
    which is the only truthful thing available.
    """
    lesson = cassandra_lesson(repo_url, commit_sha, evidence=evidence)
    steps = [_simulate(step, lab) for step in lesson.steps]

    return SimulatedLesson(
        repo_url=repo_url,
        commit_sha=commit_sha,
        title=lesson.title,
        summary=lesson.summary,
        steps=steps,
        lesson=lesson.model_copy(update={"simulated": True, "fidelity": "conceptual"}),
    )


def _simulate(step: LessonStep, lab: MiniLab | None) -> SimulatedStep:
    explanation, covered = _explanation_for(step, lab)
    return SimulatedStep(
        number=step.number,
        concept=step.concept,
        goal=step.goal,
        # The step's own context is RepoLab's prose about the concept, not
        # invented output, so it is safe to keep and is the most useful thing
        # here.
        context=step.context,
        action=step.action,
        explanation=explanation,
        covered=covered,
        code_references=list(step.code_references),
    )


def _explanation_for(step: LessonStep, lab: MiniLab | None) -> tuple[str, bool]:
    """What the lab can say about this step, and whether it could say anything."""
    if lab is None:
        return NEEDS_REAL_ENVIRONMENT, False

    response = lab.respond(step.action)
    if not response.supported:
        return NEEDS_REAL_ENVIRONMENT, False

    figure = measurement_language(response.explanation)
    if figure is not None:
        # The lab build validates its own entries, but a lab can reach here from
        # a cache written by an older version. Withholding costs a learner one
        # explanation; printing it would be a fabricated measurement.
        return WITHHELD, False

    return response.explanation, True


def simulated_observation(step: LessonStep) -> str:
    """What to show where the real lesson shows an expected observation."""
    return NO_OBSERVATION


__all__ = [
    "NEEDS_REAL_ENVIRONMENT",
    "NO_OBSERVATION",
    "SIMULATED_LABEL",
    "SIMULATED_NOTICE",
    "SimulatedLesson",
    "SimulatedStep",
    "simulated_lesson",
    "simulated_observation",
]
