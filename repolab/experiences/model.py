"""What an Experience is: a meaningful thing to *do* with a repository.

Not a detected surface. "CLI playground · Notebook · Data playground" says what
technology a repository contains; an Experience says what a person can do with
it -- *Transform a Dataset*, *Use the Application*, *Try the Library* -- what
they will learn, and how real it is.

Four states are kept apart, and the screen shows which one an experience is in:

    DETECTED    the repository has the capability (evidence)
    SUPPORTED   RepoLab has an adapter for it
    RUNNABLE    a plan exists and RepoLab can start it now, from what is ready
    RUNNING     a session is up and its readiness check passed

And three kinds of reality, which the screen never blurs:

    REAL_REPOSITORY    runs code built from the investigated commit
    OFFICIAL_RUNTIME   runs an official release of the project, version disclosed
    MINI_LAB           an educational simulation; runs nothing from the repository
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from repolab.evidence.models import CodeReference


class ExperienceType(StrEnum):
    """What kind of interaction. Extensible: an adapter declares the types it serves."""

    WEB_APP = "web_app"
    HTTP_API = "http_api"
    CLI = "cli"
    CODE_PLAYGROUND = "code_playground"
    DATA_PLAYGROUND = "data_playground"
    QUERY_CONSOLE = "query_console"
    NOTEBOOK = "notebook"
    WORKFLOW_RUNNER = "workflow_runner"
    STREAMING_DEMO = "streaming_demo"
    DATABASE_CONSOLE = "database_console"
    ML_INFERENCE = "ml_inference"
    LIBRARY_PLAYGROUND = "library_playground"
    SERVICE = "service"
    MULTI_SERVICE_APP = "multi_service_app"
    MINI_LAB = "mini_lab"


class ExperienceState(StrEnum):
    DETECTED = "detected"
    SUPPORTED = "supported"
    RUNNABLE = "runnable"
    RUNNING = "running"


class Reality(StrEnum):
    REAL_REPOSITORY = "real_repository"
    OFFICIAL_RUNTIME = "official_runtime"
    MINI_LAB = "mini_lab"


class Strategy(StrEnum):
    """Which rung of the fallback ladder an experience runs on. Always recorded."""

    CACHED_BUILD = "cached_build"
    REPOSITORY_CONTAINER = "repository_container"
    SOURCE_BUILD = "source_build"
    OFFICIAL_RELEASE = "official_release"
    PARTIAL = "partial"
    MINI_LAB = "mini_lab"
    EXISTING = "existing"
    """An experience RepoLab already runs elsewhere (the Spark playground, the CLI
    lesson, a live app), opened from here."""


class RuntimeOption(BaseModel):
    """One way an experience can be run, and whether it can be right now."""

    strategy: Strategy
    reality: Reality
    label: str
    """"Build this commit from source", "Official Spark 3.5.1 runtime"."""

    ready: bool
    """True when starting it needs no build: an image or checkpoint is in place."""

    estimate: str
    """How long until a first result, honestly: "~20 seconds", "45-90 minutes"."""

    disclosure: str | None = None
    """What differs from the investigated commit, e.g. a version gap."""

    evidence: list[CodeReference] = Field(default_factory=list)


class StarterAction(BaseModel):
    """Something to try first. Its content is either evidence or labelled RepoLab's."""

    id: str
    title: str
    description: str = ""
    code: str | None = None
    """A snippet, command or request to start from."""

    source: str
    """``readme``, ``examples``, ``routes``, ``repolab`` (RepoLab-authored)."""

    evidence: list[CodeReference] = Field(default_factory=list)


class ExperienceDefinition(BaseModel):
    id: str
    title: str
    experience_type: ExperienceType
    adapter: str
    summary: str
    what_you_do: str
    what_you_learn: str
    what_to_notice: str = ""

    surfaces: list[str] = Field(default_factory=list)
    """The detected surfaces this experience comes from, as evidence."""

    state: ExperienceState
    why_not: str | None = None
    """Why it is not further along, in plain words. Empty when running."""

    options: list[RuntimeOption] = Field(default_factory=list)
    starter_actions: list[StarterAction] = Field(default_factory=list)
    evidence: list[CodeReference] = Field(default_factory=list)

    route: str | None = None
    """Where an existing experience opens, for adapters that already have a screen."""

    rank: float = 0.0
    rank_signals: dict[str, float] = Field(default_factory=dict)
    primary: bool = False
    session_id: str | None = None
    """The live session for this experience, when there is one."""

    @property
    def reality(self) -> Reality | None:
        ready = [o for o in self.options if o.ready]
        chosen = ready[0] if ready else (self.options[0] if self.options else None)
        return chosen.reality if chosen else None


class ExperienceList(BaseModel):
    repo_url: str
    name: str
    headline: str
    experiences: list[ExperienceDefinition] = Field(default_factory=list)
    also: list[ExperienceDefinition] = Field(default_factory=list)
    """Detected or supported, but not runnable here: shown collapsed, with why."""

    fallback: ExperienceDefinition | None = None
    """A Mini Lab, offered when nothing real is runnable."""

    nothing_runs_because: list[str] = Field(default_factory=list)
    supported_kinds: list[str] = Field(default_factory=list)
