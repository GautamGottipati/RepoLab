"""Which experiences a repository offers, and which of them this build can run.

A repository is never reduced to one application type (CLAUDE.md), so the
manifest can name several interaction surfaces. This module turns that list into
something a person can act on, and it answers the one question the manifest
cannot: **does RepoLab actually have an experience for this surface?**

The answer is not a list of surface types maintained by hand. It is asked of the
code that gates the real paths:

    data playground -> is there a build ladder for this repository?
                       (repolab.builds.candidates.candidates_for)
    cli             -> is there a ladder, and does the built-in lesson apply?
                       (plus repolab.lessons.cassandra.teaches_cassandra)
    everything else -> there is no adapter in this build

So a surface cannot be called runnable here and then refuse to run when opened.
Anything RepoLab cannot run says exactly one thing -- :data:`NOT_RUNNABLE` -- and
says it with the specific reason beside it. Nothing is ever presented as working
because it was detected.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from repolab.classify.signals import HYBRID_CONFIDENCE, STRONG_CONFIDENCE
from repolab.evidence.models import CodeReference
from repolab.manifest.models import InteractionSurface, RepoManifest, SurfaceType

#: The sentence an undeliverable experience shows. Fixed by the specification,
#: defined once, and asserted verbatim in the tests so it cannot drift into
#: something vaguer.
NOT_RUNNABLE = "Detected, but not yet runnable in this RepoLab build."

#: CLAUDE.md's own vocabulary for the surfaces, so the screen and the
#: specification read the same way.
SURFACE_ICONS: dict[SurfaceType, str] = {
    SurfaceType.WEB: "🌐",
    SurfaceType.CLI: ">_",
    SurfaceType.API: "🔌",
    SurfaceType.NOTEBOOK: "📓",
    SurfaceType.DATA_PLAYGROUND: "⚡",
    SurfaceType.TERMINAL: "⌨",
}

#: Short labels, matching the specification's list.
SURFACE_LABELS: dict[SurfaceType, str] = {
    SurfaceType.WEB: "Web",
    SurfaceType.CLI: "CLI",
    SurfaceType.API: "API",
    SurfaceType.NOTEBOOK: "Code / Notebook",
    SurfaceType.DATA_PLAYGROUND: "Data Pipeline",
    SurfaceType.TERMINAL: "Terminal",
}

#: What each surface means to someone new to the repository.
SURFACE_MEANING: dict[SurfaceType, str] = {
    SurfaceType.WEB: "A user interface that runs in the browser.",
    SurfaceType.API: "An HTTP service that other programs, or a frontend, send requests to.",
    SurfaceType.CLI: "Commands you run in a terminal.",
    SurfaceType.NOTEBOOK: "A library you use by writing code against it.",
    SurfaceType.DATA_PLAYGROUND: "A data job you run and measure.",
    SurfaceType.TERMINAL: "A shell inside the repository's environment.",
}

#: Detector signal kinds, as a reader would say them. Unknown kinds fall back to
#: the kind itself, so a new detector never makes a reason disappear.
SIGNAL_PHRASES: dict[str, str] = {
    "express_app": "creates an Express app",
    "fastapi_app": "creates a FastAPI app",
    "flask_app": "creates a Flask app",
    "django_urls": "declares Django URL routes",
    "route_decorator": "declares HTTP route handlers",
    "uvicorn_run": "starts an ASGI server",
    "frontend_app": "bootstraps a browser application",
    "port": "configures a network port",
    "exposed_ports": "exposes ports in a container",
    "spark_session": "builds a SparkSession",
    "spark_context": "creates a SparkContext",
    "spark_import": "imports PySpark",
    "spark_scala_import": "imports Spark (Scala)",
    "spark_submit": "submits Spark jobs",
    "spark_config": "sets Spark configuration",
    "click_command": "defines Click commands",
    "typer_app": "defines a Typer CLI",
    "argparse": "parses command-line arguments",
    "bin_executable": "ships executables in bin/",
    "shell_script": "includes shell scripts",
    "sys_argv": "reads command-line arguments",
    "interactive_shell": "has an interactive shell",
    "server_daemon": "runs a long-lived server process",
    "notebook_file": "includes notebooks",
    "library_package": "is packaged as an importable library",
    "web_server": "a script starts a server",
    "cli_script": "declares command-line entry points",
    "services": "defines services to run alongside it",
    "server+shell": "a shell talks to a running server",
    "library-without-executable": "has nothing to run on its own",
}

#: The experience adapter for a surface, where one exists.
#:
#: Two entries, because two adapters exist. A surface absent from this table has
#: no implementation at all, which is a different and more honest reason than
#: "the repository is not supported".
ADAPTERS: dict[SurfaceType, str] = {
    SurfaceType.DATA_PLAYGROUND: "SparkDataExperience",
    SurfaceType.CLI: "CLIExperience",
    SurfaceType.WEB: "LiveAppExperience",
    SurfaceType.API: "LiveAppExperience",
}

#: Adapters that run the repository's own services from a :class:`RuntimePlan`,
#: rather than through the build ladder.
LIVE_ADAPTERS = frozenset({"LiveAppExperience"})

#: Where a runnable experience opens, relative to the workspace.
ROUTES: dict[SurfaceType, str] = {
    SurfaceType.DATA_PLAYGROUND: "/playground",
    SurfaceType.CLI: "/learn",
}

#: Why a surface with an adapter still cannot run here.
NO_LADDER = "RepoLab has no build strategy for this repository yet"
NO_LESSON = "RepoLab has no lesson for this repository's command line yet"
NO_ADAPTER = "RepoLab has no experience for this kind of surface yet"
NOT_PRIMARY = "This is not the surface RepoLab would build for this repository"
NO_RUNTIME_PLAN = (
    "this investigation predates runtime planning; investigate the repository again so "
    "RepoLab can work out how to run it"
)


class ExperienceStatus(StrEnum):
    """Whether an experience can be opened."""

    RUNNABLE = "runnable"
    """There is an adapter, and this repository satisfies what it needs."""

    DETECTED_ONLY = "detected_only"
    """Found, with evidence, and not runnable in this build."""


class AvailableExperience(BaseModel):
    """One experience a repository offers, and what RepoLab can do about it."""

    surface: SurfaceType
    icon: str
    label: str

    name: str
    """The surface's own name, from the classifier."""

    description: str = ""
    confidence: float
    primary: bool = False

    reason: str
    """Why this surface was detected, naming the signals responsible."""

    evidence: list[CodeReference] = Field(default_factory=list)
    """The repository files that justify it, passed through unedited."""

    status: ExperienceStatus
    route: str | None = None
    """Where it opens. ``None`` whenever it is not runnable."""

    adapter: str | None = None
    unavailable_reason: str = ""
    """The specification's sentence, plus the specific reason. Empty when runnable."""

    meaning: str = ""
    """What this kind of surface is, in plain words."""

    signals: list[str] = Field(default_factory=list)
    """Why it was detected, as readable phrases ("creates an Express app")."""

    @computed_field
    @property
    def runnable(self) -> bool:
        return self.status is ExperienceStatus.RUNNABLE

    @computed_field
    @property
    def weak(self) -> bool:
        """Whether the evidence is too thin to call this a substantial surface.

        Surfaced rather than filtered: a learner looking at Airflow should see
        that its notebook signal is a handful of `.ipynb` files and some
        `__init__.py` packaging, not that RepoLab decided for them.
        """
        return self.confidence < HYBRID_CONFIDENCE

    @computed_field
    @property
    def strength(self) -> str:
        """The confidence in words: ``strong``, ``moderate`` or ``weak`` evidence."""
        if self.confidence >= STRONG_CONFIDENCE:
            return "strong"
        return "weak" if self.weak else "moderate"

    @computed_field
    @property
    def supported(self) -> bool:
        """Whether an adapter exists at all, regardless of this repository."""
        return self.adapter is not None


class ExperienceCatalog(BaseModel):
    """Every experience a repository offers, best supported first."""

    repo_url: str
    commit_sha: str = ""
    experiences: list[AvailableExperience] = Field(default_factory=list)

    hybrid: bool = False
    """True when more than one surface is substantially supported."""

    primary_type: SurfaceType | None = None
    rationale: str = ""
    """Why that primary was recommended over the alternatives."""

    @computed_field
    @property
    def surface_count(self) -> int:
        return len(self.experiences)

    @computed_field
    @property
    def runnable_count(self) -> int:
        return sum(1 for item in self.experiences if item.runnable)

    @computed_field
    @property
    def runnable_surfaces(self) -> list[SurfaceType]:
        """The surfaces that can actually be opened. What the tabs are gated on."""
        return [item.surface for item in self.experiences if item.runnable]

    def of(self, surface: SurfaceType) -> AvailableExperience | None:
        return next((item for item in self.experiences if item.surface is surface), None)


def experiences_for(manifest: RepoManifest) -> ExperienceCatalog:
    """The experiences *manifest* offers, with runnability decided per surface."""
    experiences = [_describe(surface, manifest) for surface in manifest.interaction_surfaces]

    # Runnable first, then by confidence. A learner should reach the thing they
    # can do without reading past three things they cannot -- and on Airflow the
    # most confident surface is one RepoLab cannot run.
    experiences.sort(key=lambda item: (not item.runnable, -item.confidence, item.surface.value))

    classification = manifest.classification
    return ExperienceCatalog(
        repo_url=manifest.repo_url,
        commit_sha=manifest.commit_sha,
        experiences=experiences,
        hybrid=classification.hybrid if classification is not None else False,
        primary_type=(
            manifest.primary_surface.type if manifest.primary_surface is not None else None
        ),
        rationale=classification.rationale if classification is not None else "",
    )


def _describe(surface: InteractionSurface, manifest: RepoManifest) -> AvailableExperience:
    adapter = ADAPTERS.get(surface.type)
    blocked = _why_not(surface, manifest, adapter)

    return AvailableExperience(
        surface=surface.type,
        icon=SURFACE_ICONS.get(surface.type, "•"),
        label=SURFACE_LABELS.get(surface.type, surface.type.value),
        name=surface.name,
        description=surface.description or "",
        confidence=float(surface.confidence),
        primary=surface.primary,
        reason=surface.reason,
        evidence=list(surface.evidence),
        status=ExperienceStatus.DETECTED_ONLY if blocked else ExperienceStatus.RUNNABLE,
        route=None if blocked else ROUTES.get(surface.type),
        adapter=adapter,
        unavailable_reason=f"{NOT_RUNNABLE} {blocked}." if blocked else "",
        meaning=SURFACE_MEANING.get(surface.type, ""),
        signals=readable_signals(surface.reason),
    )


def readable_signals(reason: str) -> list[str]:
    """The classifier's reason (``detected from a, b; corroborated by c``) as phrases."""
    detected, _, corroborated = reason.partition("; corroborated by ")
    detected = detected.removeprefix("detected from ")
    phrases: list[str] = []
    for kind in [*detected.split(","), *corroborated.split(",")]:
        kind = kind.strip()
        if not kind or kind == "no direct signal":
            continue
        if kind.startswith("framework:"):
            phrase = f"depends on {kind.split(':', 1)[1]}"
        else:
            phrase = SIGNAL_PHRASES.get(kind, kind.replace("_", " "))
        if phrase not in phrases:
            phrases.append(phrase)
    return phrases


def _why_not(
    surface: InteractionSurface, manifest: RepoManifest, adapter: str | None
) -> str | None:
    """Why this surface cannot be opened, or ``None`` if it can.

    Each branch defers to the code that would actually refuse. Asking
    ``candidates_for`` rather than keeping a list of supported repositories is
    what keeps this honest when a new rung is added to the ladder.
    """
    if adapter is None:
        return NO_ADAPTER

    if adapter in LIVE_ADAPTERS:
        # Runnable means a plan exists for this surface -- built from the
        # repository's own scripts and ports -- not that anything has run.
        plan = manifest.runtime
        if plan is None:
            return NO_RUNTIME_PLAN
        if not plan.for_surface(surface.type):
            return plan.unplanned.get(surface.type.value, "RepoLab found no way to start it")
        return None

    if not _has_ladder(manifest):
        return NO_LADDER

    # The ladder is chosen from the primary surface, so a secondary surface with
    # an adapter would be built as something else.
    primary = manifest.primary_surface
    if primary is None or primary.type is not surface.type:
        return NOT_PRIMARY

    if surface.type is SurfaceType.CLI:
        from repolab.lessons.cassandra import teaches_cassandra

        if not teaches_cassandra(manifest):
            return NO_LESSON

    return None


def _has_ladder(manifest: RepoManifest) -> bool:
    """Whether any rung of the build ladder covers this repository."""
    from repolab.builds.candidates import NoCandidatesError, candidates_for

    try:
        return bool(candidates_for(manifest))
    except NoCandidatesError:
        return False


__all__ = [
    "ADAPTERS",
    "NOT_PRIMARY",
    "NOT_RUNNABLE",
    "NO_ADAPTER",
    "NO_LADDER",
    "NO_LESSON",
    "SURFACE_ICONS",
    "SURFACE_LABELS",
    "AvailableExperience",
    "ExperienceCatalog",
    "ExperienceStatus",
    "experiences_for",
]
