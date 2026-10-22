"""What a question is, and what answering one without an experiment produces.

Ask RepoLab receives free text. Before anything is designed it is triaged into
one of three kinds, because each deserves a different response:

    conceptual   "What is Cassandra?"            explain, tied to this repository
    repository   "Where is the CQL parser?"       answer from the code itself
    testable     "What happens if I filter on a   run an experiment on this
                 non-key column?"                 repository's real environment

Only a testable question becomes an experiment, and only when this repository
has an environment an experiment can run against. Everything else is answered
from repository evidence and official documentation, with every claim labelled
by what supports it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from repolab.evidence.models import EvidenceBundle

#: Bound on the model's free-text reason, so a triage cannot arrive as an essay.
MAX_REASON_LENGTH = 300


class QuestionKind(StrEnum):
    """What sort of answer a question calls for."""

    CONCEPTUAL = "conceptual"
    """About an idea or the software in general. Explained, then tied to the code."""

    REPOSITORY = "repository"
    """About this repository's code: where, which, how it is implemented."""

    TESTABLE = "testable"
    """About behaviour that running the software could show."""


class ExperimentBackend(StrEnum):
    """What an experiment on this repository would actually run."""

    SPARK = "spark"
    """The repository's Spark job, measured from Spark's event log."""

    CLI = "cli"
    """CQL statements through the repository's built cqlsh, measured from its output."""


class Route(StrEnum):
    """Where a triaged question goes next."""

    ANSWER = "answer"
    EXPERIMENT = "experiment"


class TriageVerdict(BaseModel):
    """What the model is asked to return. Untrusted: deterministic code decides."""

    kind: QuestionKind = Field(description="conceptual, repository, or testable")
    reason: str = Field(description="One sentence: why this kind")


class Triage(BaseModel):
    """How a question was classified, and where it was sent.

    ``kind`` is what the question *is*; ``route`` is what RepoLab will *do*. They
    differ when a question is testable but nothing here can test it -- that is
    reported in ``downgraded`` rather than silently answered as if it had never
    been testable.
    """

    kind: QuestionKind
    reason: str
    decided_by: str
    """``rules``, ``model``, or ``fallback``: which step settled the kind."""

    route: Route
    backend: ExperimentBackend | None = None
    downgraded: str | None = None
    """Why a testable question is being answered instead of tested."""


class AnswerClaimKind(StrEnum):
    """What supports a claim in an answer."""

    REPOSITORY = "repository"
    """Something the repository's own files show. Must cite one of them."""

    DOCUMENTED = "documented"
    """Something official documentation states. Must cite a retrieved page."""

    INFERRED = "inferred"
    """A conclusion drawn from the two above. Not itself evidence."""


class AnswerClaim(BaseModel):
    kind: AnswerClaimKind = Field(description="repository, documented, or inferred")
    statement: str = Field(description="One sentence")
    support: str = Field(
        description=(
            "For 'repository': one file path from the evidence, copied exactly. "
            "For 'documented': one URL from the evidence. "
            "For 'inferred': which claims it follows from."
        )
    )


class GroundedAnswer(BaseModel):
    """What the model produced."""

    answer: str = Field(description="A direct answer, two to six sentences")
    claims: list[AnswerClaim] = Field(description="The claims the answer rests on, each labelled")
    caveats: list[str] = Field(description="What the evidence does not establish")


class UnsupportedAnswerClaim(BaseModel):
    statement: str
    kind: AnswerClaimKind
    problem: str


class VerifiedAnswer(BaseModel):
    """An answer together with what could and could not be corroborated."""

    answer: GroundedAnswer
    unsupported: list[UnsupportedAnswerClaim] = Field(default_factory=list)
    checked_claims: int = 0

    @computed_field
    @property
    def grounded(self) -> bool:
        return not self.unsupported


class AnswerResult(BaseModel):
    """Everything an answered question carries to the screen."""

    question: str
    kind: QuestionKind
    answer: VerifiedAnswer | None = None
    """``None`` when no model could be asked; the evidence still stands."""

    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)
    findings: list[str] = Field(default_factory=list)
    """What the read-only investigation did, as user-facing status lines."""

    downgraded: str | None = None
    error: str | None = None
    """Why there is no answer text, when there is none."""
