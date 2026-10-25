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

from repolab.evidence.models import CodeReference, EvidenceBundle

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


class ExplanationShape(StrEnum):
    """How an answer is best laid out. Presentation only: ``QuestionKind`` routes.

    Decided from the question's phrasing where the phrasing settles it, and
    otherwise chosen by the same model call that writes the answer -- never by a
    call of its own.
    """

    FLOW = "flow"
    """How X uses or reaches Y: a small diagram of what talks to what."""

    EXECUTION = "execution"
    """What happens when X runs: an ordered sequence of steps."""

    DEFINITION = "definition"
    """What X is: what it is, its role here, how it connects, where it lives."""

    WHY = "why"
    """Why X: problem, design choice, why it helps, trade-offs."""

    COMPARISON = "comparison"
    """X versus Y: attributes side by side."""

    LOCATION = "location"
    """Where X is: a map of the files involved, in order."""

    ARCHITECTURE = "architecture"
    """How the system is organised: its parts and how they connect."""

    DEBUGGING = "debugging"
    """Why X fails or is slow: symptom, cause, evidence, how to check."""

    GENERAL = "general"


class Route(StrEnum):
    """Where a triaged question goes next."""

    ANSWER = "answer"
    EXPERIMENT = "experiment"


class TriageVerdict(BaseModel):
    """What the model is asked to return. Untrusted: deterministic code decides."""

    kind: QuestionKind = Field(description="conceptual, repository, or testable")
    reason: str = Field(description="One sentence: why this kind")


class ArchitectureFocus(BaseModel):
    """The component a question was asked about, from the Architecture page.

    Passed with the question so the answer starts from the right code: its files
    are read first and its relationships are part of the evidence. Bounded, so a
    client cannot ship a repository's worth of references with one question.
    """

    node_id: str = Field(max_length=200)
    """A component id -- or a concept or subsystem id, at those levels."""

    label: str = Field(max_length=200)
    category: str = Field(max_length=40)
    source_references: list[CodeReference] = Field(default_factory=list, max_length=8)
    relationships: list[str] = Field(default_factory=list, max_length=10)
    level: str | None = Field(default=None, max_length=20)
    """``system``, ``subsystem`` or ``component``: how far the reader had drilled in."""

    subsystem: str | None = Field(default=None, max_length=200)
    """The label of the part of the repository the reader was exploring."""

    directories: list[str] = Field(default_factory=list, max_length=6)
    """Directories that hold it. Named to the answer; files are what get read."""

    def describe(self) -> str:
        files = ", ".join(
            f"{ref.path}" + (f":{ref.start_line}" if ref.start_line else "")
            for ref in self.source_references
        )
        what = {"system": "concept", "subsystem": "part of the repository"}.get(
            self.level or "", "component"
        )
        lines = [f"The question is about the {what} '{self.label}' ({self.category})."]
        if self.subsystem and self.subsystem != self.label:
            lines.append(f"It belongs to the part of the repository called '{self.subsystem}'.")
        if self.directories:
            lines.append("It lives under: " + ", ".join(d[:120] for d in self.directories) + ".")
        if files:
            lines.append(f"It is implemented in: {files}.")
        if self.relationships:
            lines.append(
                "Its relationships: " + "; ".join(r[:160] for r in self.relationships) + "."
            )
        return " ".join(lines)


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

    context: ArchitectureFocus | None = None
    """The architecture component the question was asked from, if any."""

    shape: ExplanationShape | None = None
    """How the answer should be laid out, when the phrasing settled it."""

    experience_session: str | None = None
    """The experience session an experiment targets: the learner's own code, run
    on that session's runtime, instead of the repository's job."""


class AnswerClaimKind(StrEnum):
    """What supports a claim in an answer."""

    REPOSITORY = "repository"
    """Something the repository's own files show. Must cite one of them."""

    DOCUMENTED = "documented"
    """Something official documentation states. Must cite a retrieved page."""

    INFERRED = "inferred"
    """A conclusion drawn from the two above. Not itself evidence."""

    MEASURED = "measured"
    """A value observed in a real, recorded run. Must name a measurement in the evidence."""


class AnswerClaim(BaseModel):
    kind: AnswerClaimKind = Field(description="repository, documented, inferred, or measured")
    statement: str = Field(description="One sentence")
    support: str = Field(
        description=(
            "For 'repository': one file path from the evidence, copied exactly. "
            "For 'documented': one URL from the evidence. "
            "For 'inferred': which claims it follows from. "
            "For 'measured': the name of one measurement from the evidence."
        )
    )


class SectionBasis(StrEnum):
    """How firmly a section of an answer is known."""

    EVIDENCED = "evidenced"
    """Shown directly by the cited repository files or documentation."""

    INFERRED = "inferred"
    """A reasonable reading of the evidence, not stated by it."""

    UNKNOWN = "unknown"
    """The evidence does not say. The section says so rather than guessing."""


class VisualNode(BaseModel):
    id: str = Field(description="Short id, unique within the diagram")
    label: str = Field(description="What a beginner would call it, a few words")
    kind: str = Field(default="", description="e.g. server, route, model, database, client")
    component_id: str | None = Field(
        default=None, description="The architecture component id, when it is one of them"
    )
    source: str | None = Field(
        default=None, description="A file path from the evidence that implements it"
    )


class VisualEdge(BaseModel):
    source: str = Field(description="A node id in this diagram")
    target: str = Field(description="A node id in this diagram")
    label: str = Field(default="", description="What passes or happens, a few words")
    support: str = Field(
        description=(
            "What shows this relationship: a file path from the evidence, an "
            "architecture relationship id (edge:...), or the word 'inferred'"
        )
    )
    provenance: str = "static"
    """Set by verification: ``static`` when cited, ``inferred`` otherwise."""


class AnswerVisual(BaseModel):
    """A small diagram of how the parts in the answer relate. Three to eight nodes."""

    nodes: list[VisualNode] = Field(default_factory=list)
    edges: list[VisualEdge] = Field(default_factory=list)


class AnswerStep(BaseModel):
    title: str = Field(description="A few words")
    description: str = Field(description="One or two plain sentences")
    sources: list[str] = Field(
        default_factory=list, description="File paths (path or path:line) from the evidence"
    )


class AnswerSection(BaseModel):
    key: str = Field(
        description=(
            "definition: what, role, connects, where. why: problem, choice, helps, "
            "tradeoffs. debugging: symptom, cause, check. Otherwise a short key."
        )
    )
    title: str
    body: str = Field(description="Two to four plain sentences")
    basis: SectionBasis = Field(
        default=SectionBasis.EVIDENCED, description="evidenced, inferred, or unknown"
    )
    sources: list[str] = Field(default_factory=list)


class ComparisonRow(BaseModel):
    attribute: str
    values: list[str] = Field(description="One value per subject, in order")
    support: str = Field(description="A file path or URL from the evidence, or 'inferred'")
    provenance: str = "static"
    """Set by verification: ``static``, ``inferred``, or ``unknown`` when not established."""


class AnswerComparison(BaseModel):
    subjects: list[str] = Field(default_factory=list)
    rows: list[ComparisonRow] = Field(default_factory=list)


class AnswerLocation(BaseModel):
    role: str = Field(description="e.g. Entry, Validation, Business logic, Persistence")
    path: str = Field(description="A repository file or directory, copied exactly")
    line: int | None = None
    why: str = Field(default="", description="One short sentence")


#: Bounds on the structured parts of an answer, so a diagram stays a glance.
MAX_VISUAL_NODES = 8
MAX_STEPS = 8
MAX_FOLLOW_UPS = 4


class GroundedAnswer(BaseModel):
    """What the model produced.

    ``answer`` and ``claims`` are the original contract and stay the record of
    what the answer rests on. The other fields lay the same answer out for a
    reader; every one defaults, so answers stored before them still load.
    """

    answer: str = Field(description="A fuller explanation, two to six sentences")
    claims: list[AnswerClaim] = Field(description="The claims the answer rests on, each labelled")
    caveats: list[str] = Field(description="What the evidence does not establish")
    summary: str = Field(
        default="",
        description="The direct answer for a beginner, one to three short sentences",
    )
    shape: ExplanationShape = Field(
        default=ExplanationShape.GENERAL,
        description="flow, execution, definition, why, comparison, location, "
        "architecture, debugging, or general",
    )
    visual: AnswerVisual | None = None
    steps: list[AnswerStep] = Field(default_factory=list)
    sections: list[AnswerSection] = Field(default_factory=list)
    comparison: AnswerComparison | None = None
    locations: list[AnswerLocation] = Field(default_factory=list)
    follow_ups: list[str] = Field(
        default_factory=list, description="Two to four next questions a learner would ask"
    )


class UnsupportedAnswerClaim(BaseModel):
    statement: str
    kind: AnswerClaimKind
    problem: str


class VerifiedAnswer(BaseModel):
    """An answer together with what could and could not be corroborated."""

    answer: GroundedAnswer
    unsupported: list[UnsupportedAnswerClaim] = Field(default_factory=list)
    checked_claims: int = 0
    not_established: list[str] = Field(default_factory=list)
    """Relationships the model drew that the evidence did not show. Not drawn."""

    dropped: dict[str, int] = Field(default_factory=dict)
    """What verification removed from the layout, and why. Counted, never hidden."""

    @computed_field
    @property
    def grounded(self) -> bool:
        return not self.unsupported


class AnswerResult(BaseModel):
    """Everything an answered question carries to the screen."""

    question: str
    kind: QuestionKind
    shape: ExplanationShape = ExplanationShape.GENERAL
    """How the answer is laid out: the rules' decision, or the model's when they left it open."""

    answer: VerifiedAnswer | None = None
    """``None`` when no model could be asked; the evidence still stands."""

    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)
    findings: list[str] = Field(default_factory=list)
    """What the read-only investigation did, as user-facing status lines."""

    downgraded: str | None = None
    error: str | None = None
    """Why there is no answer text, when there is none."""

    follow_ups: list[str] = Field(default_factory=list)
    """Next questions: the model's, then ones drawn from the architecture. Never canned."""
