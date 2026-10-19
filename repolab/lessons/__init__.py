"""The Learning Agent and guided lessons.

    repository evidence + its own documentation + official documentation
    + what the resolved environment can do
                   --> Super writes a lesson
                   --> deterministic code accepts or rejects it
                   --> a learner runs each step in a real terminal
                   --> deterministic validation decides, Nano only when it cannot
                   --> "What Just Happened?", checked against the evidence

Two rules shape the package. A model proposes and deterministic code decides:
a generated step whose statement would be refused, or whose expectation nothing
can check, never reaches a learner. And a citation is resolved before it is
shown: a path that is not in the repository at this commit is dropped rather
than displayed as evidence.
"""

from __future__ import annotations

from repolab.lessons.agent import (
    GeneratedLesson,
    GeneratedStep,
    LearningAgent,
    LessonInputs,
)
from repolab.lessons.cassandra import (
    KEYSPACE,
    RepositoryEvidence,
    cassandra_lesson,
    gather_repository_evidence,
)
from repolab.lessons.explain import (
    ExplainInput,
    SupportKind,
    VerifiedWhatHappened,
    WhatHappened,
    build_explain_input,
    explain_command,
    explainable,
    verify_what_happened,
)
from repolab.lessons.minilab_lesson import SimulatedLesson, simulated_lesson
from repolab.lessons.models import (
    CheckKind,
    Lesson,
    LessonProgress,
    LessonStep,
    StepAttempt,
    StepCheck,
)
from repolab.lessons.validation import (
    LessonRejected,
    Verdict,
    check_step,
    ensure_usable,
    lesson_problems,
    resolve_with_model,
)

__all__ = [
    "KEYSPACE",
    "CheckKind",
    "ExplainInput",
    "GeneratedLesson",
    "GeneratedStep",
    "LearningAgent",
    "Lesson",
    "LessonInputs",
    "LessonProgress",
    "LessonRejected",
    "LessonStep",
    "RepositoryEvidence",
    "SimulatedLesson",
    "StepAttempt",
    "StepCheck",
    "SupportKind",
    "Verdict",
    "VerifiedWhatHappened",
    "WhatHappened",
    "build_explain_input",
    "cassandra_lesson",
    "check_step",
    "ensure_usable",
    "explain_command",
    "explainable",
    "gather_repository_evidence",
    "lesson_problems",
    "resolve_with_model",
    "simulated_lesson",
    "verify_what_happened",
]
