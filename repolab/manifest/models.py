"""``RepoManifest`` and the models it composes.

``RepoManifest`` is the canonical description of a repository: the Repository
Investigator produces it and every downstream component consumes it. Frontend
rendering depends on the manifest rather than on repository-specific code.

A repository may expose several interaction surfaces at once, so nothing here
forces a repository into a single application type.

Deliberately thin. Fields are added when a milestone needs them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from repolab.evidence.models import CodeReference

#: A confidence score. An annotated alias rather than a shared ``Field``
#: instance, which pydantic models must not share.
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class SurfaceType(StrEnum):
    """Kinds of interaction surface, one per experience adapter."""

    CLI = "cli"
    WEB = "web"
    API = "api"
    DATA_PLAYGROUND = "data_playground"
    TERMINAL = "terminal"
    NOTEBOOK = "notebook"


class EntryPointKind(StrEnum):
    """How an entry point is invoked."""

    CLI_SCRIPT = "cli_script"
    MODULE = "module"
    WEB_SERVER = "web_server"
    SPARK_JOB = "spark_job"
    TEST_SUITE = "test_suite"


class BuildKind(StrEnum):
    """How a runnable environment was produced.

    Cached builds are cached instances of a real version, not a separate kind
    (CLAUDE.md), so there is no ``cached`` member.
    """

    SOURCE_BUILD = "source_build"
    OFFICIAL_RELEASE = "official_release"
    PARTIAL_BUILD = "partial_build"
    MINI_LAB = "mini_lab"


class Fidelity(StrEnum):
    """How faithfully an environment represents the real software."""

    EXACT = "exact"
    HIGH = "high"
    PARTIAL = "partial"
    CONCEPTUAL = "conceptual"
    """Mini Lab only. Real experiments are never possible at this fidelity."""


class EntryPoint(BaseModel):
    """A way the software is intended to be started."""

    name: str
    kind: EntryPointKind
    command: str
    path: str | None = None
    """The file declaring or implementing the entry point, when known."""


class Service(BaseModel):
    """External infrastructure the software needs in order to run."""

    name: str
    image: str | None = None
    ports: list[int] = Field(default_factory=list)
    required: bool = True


class Dependency(BaseModel):
    """A declared dependency."""

    name: str
    ecosystem: str
    """Where it comes from, e.g. ``pypi``, ``maven``, ``npm``."""

    version_spec: str | None = None


class Concept(BaseModel):
    """Something worth learning about this repository.

    Concepts carry code references so a lesson or explanation built from one is
    grounded in the repository rather than in general knowledge.
    """

    name: str
    summary: str
    code_references: list[CodeReference] = Field(default_factory=list)


class SmokeTest(BaseModel):
    """The check that decides whether a build is usable.

    A build is ``ready`` only after its smoke test passes (CLAUDE.md).
    """

    name: str
    command: str
    expect_exit_code: int = 0
    expect_stdout_contains: str | None = None
    timeout_seconds: int = Field(default=120, gt=0)


class BuildStrategy(BaseModel):
    """How to produce a runnable environment, and what that environment can do."""

    kind: BuildKind
    fidelity: Fidelity
    base_image: str | None = None
    steps: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @property
    def supports_real_experiments(self) -> bool:
        """Whether measured experiments are possible here.

        Conceptual fidelity is Mini Lab, which never produces measurements.
        """
        return self.fidelity is not Fidelity.CONCEPTUAL


class ClassificationSource(StrEnum):
    """How a classification was resolved."""

    DETERMINISTIC = "deterministic"
    SUPER = "super"
    """A model broke a tie the deterministic rules could not."""


class InteractionSurface(BaseModel):
    """One way a learner can interact with the software.

    Carries its own evidence and reason: a surface RepoLab cannot justify is a
    surface it should not claim.
    """

    type: SurfaceType
    name: str
    description: str | None = None
    entrypoint: str | None = None
    """The name of the :class:`EntryPoint` backing this surface, when there is one."""

    primary: bool = False
    confidence: Confidence

    reason: str = ""
    """Why this surface was proposed, naming the signals responsible."""

    evidence: list[CodeReference] = Field(default_factory=list)
    """The repository files that justify this surface."""

    @property
    def is_supported(self) -> bool:
        """Whether this surface cites any evidence."""
        return bool(self.evidence)


class Classification(BaseModel):
    """The detected interaction surfaces and the recommended primary experience.

    A repository is never reduced to a single type: ``surfaces`` may hold several,
    and ``hybrid`` records when more than one is strongly supported.
    """

    surfaces: list[InteractionSurface] = Field(default_factory=list)
    primary_type: SurfaceType | None = None
    rationale: str = ""
    """Why this primary was recommended over the alternatives."""

    hybrid: bool = False
    """Two or more surfaces are strongly supported."""

    ambiguous: bool = False
    """Deterministic signals were too weak or too close to decide alone."""

    resolved_by: ClassificationSource = ClassificationSource.DETERMINISTIC
    notes: list[str] = Field(default_factory=list)

    @property
    def primary_surface(self) -> InteractionSurface | None:
        return next((s for s in self.surfaces if s.primary), None)

    @property
    def surface_types(self) -> list[SurfaceType]:
        return [s.type for s in self.surfaces]


class RepoManifest(BaseModel):
    """The canonical description of a repository."""

    repo_url: str
    commit_sha: str

    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)

    entrypoints: list[EntryPoint] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)

    interaction_surfaces: list[InteractionSurface] = Field(default_factory=list)

    classification: Classification | None = None
    """How the surfaces above were decided, and which experience is recommended."""

    build_strategy: BuildStrategy
    smoke_tests: list[SmokeTest] = Field(default_factory=list)

    concepts: list[Concept] = Field(default_factory=list)

    confidence: Confidence

    @model_validator(mode="after")
    def _at_most_one_primary_surface(self) -> RepoManifest:
        """A manifest recommends a single primary experience, or none."""
        primaries = [s.name for s in self.interaction_surfaces if s.primary]
        if len(primaries) > 1:
            raise ValueError(f"at most one interaction surface may be primary, got {primaries}")
        return self

    @property
    def primary_surface(self) -> InteractionSurface | None:
        """The surface to render first.

        The explicitly flagged one, else the most confident, else ``None``.
        """
        if not self.interaction_surfaces:
            return None
        for surface in self.interaction_surfaces:
            if surface.primary:
                return surface
        return max(self.interaction_surfaces, key=lambda s: s.confidence)

    @property
    def surface_types(self) -> list[SurfaceType]:
        """Every surface type present, in declaration order, without duplicates."""
        seen: dict[SurfaceType, None] = {}
        for surface in self.interaction_surfaces:
            seen.setdefault(surface.type, None)
        return list(seen)
