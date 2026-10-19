"""Build artifacts, capabilities and attempts.

Two invariants are encoded in the types rather than left to callers:

* **A build is ready only after its smoke test passes.** ``BuildArtifact.ready``
  is computed from the smoke result, so nothing can mark a build usable by
  mistake.
* **Fidelity is derived, not asserted.** A build claims ``EXACT`` only when it
  needed no substitutions. If a pinned dependency had to be replaced, the artifact
  says so and its fidelity drops, because a later experiment measuring that
  environment would not be measuring what the repository pins.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from repolab.manifest.models import BuildKind, Fidelity

#: Longest log tail kept with an attempt. Full logs are preserved separately.
MAX_LOG_TAIL = 4000


class BuildStatus(StrEnum):
    """Where a build stands."""

    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {BuildStatus.READY, BuildStatus.FAILED}


class BuildSource(StrEnum):
    """How this artifact was obtained.

    Kept separate from fidelity: a cached build is a cached instance of a real
    build, not a lesser kind of one (CLAUDE.md). Every other member mirrors a
    rung of the fallback ladder, so an artifact can always say where it came
    from -- which is one of the things the specification requires it declare.
    """

    CACHE = "cache"
    SOURCE_BUILD = "source_build"
    OFFICIAL_RELEASE = "official_release"
    PARTIAL_BUILD = "partial_build"
    MINI_LAB = "mini_lab"

    @classmethod
    def for_kind(cls, kind: BuildKind) -> BuildSource:
        return cls(kind.value)


#: Fidelity, worst to best. Used to pick the highest-fidelity candidate that
#: passed, and to decide whether a newly ready build is worth offering as an
#: upgrade.
FIDELITY_ORDER: tuple[Fidelity, ...] = (
    Fidelity.CONCEPTUAL,
    Fidelity.PARTIAL,
    Fidelity.HIGH,
    Fidelity.EXACT,
)


def fidelity_rank(fidelity: Fidelity) -> int:
    """Where *fidelity* sits in the ladder. Higher is better."""
    return FIDELITY_ORDER.index(fidelity)


def fidelity_for(kind: BuildKind, deviations: list[str] | tuple[str, ...] = ()) -> Fidelity:
    """The fidelity a build of this kind, with these deviations, actually has.

    Derived, never asserted. CLAUDE.md fixes the table:

    ===================  ============================  ============
    kind                 deviations                    fidelity
    ===================  ============================  ============
    source build         none                          ``EXACT``
    source build         any                           ``HIGH``
    official release     (always discloses a version)  ``HIGH``
    partial build        --                            ``PARTIAL``
    Mini Lab             --                            ``CONCEPTUAL``
    ===================  ============================  ============

    A source build that had to substitute a pinned dependency is **not** exact:
    a later experiment would be measuring something other than what the
    repository pins, and saying so is the whole point of recording deviations.
    """
    if kind is BuildKind.MINI_LAB:
        return Fidelity.CONCEPTUAL
    if kind is BuildKind.PARTIAL_BUILD:
        return Fidelity.PARTIAL
    if kind is BuildKind.OFFICIAL_RELEASE:
        return Fidelity.HIGH
    return Fidelity.EXACT if not deviations else Fidelity.HIGH


class SmokeTestResult(BaseModel):
    """What the smoke test actually did.

    A build's readiness rests entirely on this, so it records the command, the
    exit code and the output rather than a bare boolean.
    """

    name: str
    command: str
    passed: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float | None = None
    reason: str = ""
    """Why it failed, when it did."""

    @classmethod
    def failure(cls, name: str, command: str, reason: str, **kwargs: Any) -> SmokeTestResult:
        return cls(name=name, command=command, passed=False, reason=reason, **kwargs)


class BuildCapabilities(BaseModel):
    """What the built environment can and cannot do."""

    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    deviations: list[str] = Field(default_factory=list)
    """Where the build departs from what the repository declares.

    Each entry is a disclosure, e.g. a substituted dependency version. A non-empty
    list means the environment is not an exact reproduction.
    """

    versions: dict[str, str] = Field(default_factory=dict)
    """What was actually installed, so an experiment can cite real versions."""

    @property
    def is_exact(self) -> bool:
        """Whether the build needed no substitutions.

        Necessary for ``EXACT`` fidelity but not sufficient: an official release
        has no deviations *of its own* and is still not an exact build of the
        requested commit. Fidelity is decided by :func:`fidelity_for`, which
        knows the kind.
        """
        return not self.deviations


class StepOutcome(BaseModel):
    """One command run during a build."""

    name: str
    command: str
    exit_code: int | None = None
    duration_seconds: float | None = None
    ok: bool = True
    output_tail: str = ""

    def model_post_init(self, _context: object) -> None:
        if len(self.output_tail) > MAX_LOG_TAIL:
            object.__setattr__(self, "output_tail", self.output_tail[-MAX_LOG_TAIL:])


class BuildAttempt(BaseModel):
    """One attempt at producing a build.

    Every attempt is recorded, successful or not: CLAUDE.md requires that build
    failures are never hidden, and a later diagnosing agent needs the history.
    """

    attempt: int = 1
    provider: str = "unknown"
    base_image: str = ""
    steps: list[StepOutcome] = Field(default_factory=list)
    smoke: SmokeTestResult | None = None
    error: str | None = None

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and all(step.ok for step in self.steps)

    @property
    def failed_step(self) -> StepOutcome | None:
        return next((step for step in self.steps if not step.ok), None)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    def finish(self, error: str | None = None) -> None:
        self.error = error
        self.finished_at = datetime.now(UTC)


class BuildArtifact(BaseModel):
    """A built, checkpointed environment for one repository commit."""

    repo_url: str
    commit_sha: str = ""
    """The commit this was built from. Empty for a Mini Lab, which builds nothing.

    "Commit if applicable" is how the specification puts it, and a conceptual
    simulation is the case where it does not apply.
    """

    kind: BuildKind = BuildKind.SOURCE_BUILD

    cached: bool = False
    """Whether this came out of the cache rather than being built just now.

    The only thing ``source`` carries beyond ``kind``, which is why it is the
    only part stored: a field holding "official release" beside a kind of
    "source build" would be a contradiction nothing prevents.
    """

    provider: str = "unknown"
    base_image: str = ""

    version: str | None = None
    """The application version this environment actually provides.

    For a source build, the commit it was built from; for an official release,
    the released version. An experiment cites this rather than what the
    repository pins, because they are not always the same thing.
    """

    checkpoint_id: str | None = None
    checkpoint_tag: str | None = None

    service_command: str | None = None
    """What makes this checkpoint a running server, when it is one.

    Recorded on the artifact rather than looked up from a plan, because the
    artifact is what gets cached: a session started in a different process has
    the checkpoint and nothing else, and a checkpoint nobody knows how to start
    is not a usable environment.
    """

    cli_command: str | None = None
    """The binary that drives this environment's CLI surface, when it has one."""

    memory: str | None = None
    """Container memory this environment needs, when it needs more than default."""

    capabilities: BuildCapabilities = Field(default_factory=BuildCapabilities)
    smoke: SmokeTestResult | None = None
    attempts: list[BuildAttempt] = Field(default_factory=list)

    simulated: bool = False
    """True only for a Mini Lab. Surfaced everywhere the artifact is shown."""

    demo_delay_seconds: float = 0.0
    """A deliberate delay applied for demonstration, in seconds.

    Recorded so a build that was slowed on purpose can never be mistaken for one
    that was genuinely slow.
    """

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        """A build is ready only once its smoke test has passed.

        Computed rather than stored, so readiness cannot be claimed without the
        evidence that justifies it.
        """
        return bool(self.smoke and self.smoke.passed and self.checkpoint_id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source(self) -> BuildSource:
        """How this artifact was obtained. Derived, so it cannot contradict the kind.

        A cached build is a cached instance of a real build, not a lesser kind of
        one (CLAUDE.md), so the cache is the one thing that overrides the rung.
        """
        return BuildSource.CACHE if self.cached else BuildSource.for_kind(self.kind)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fidelity(self) -> Fidelity:
        """Derived from the kind and the deviations, never asserted."""
        return fidelity_for(self.kind, self.capabilities.deviations)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fidelity_rank(self) -> int:
        """Higher is better. How the ladder decides which candidate to serve."""
        return fidelity_rank(self.fidelity)

    @property
    def status(self) -> BuildStatus:
        if self.ready:
            return BuildStatus.READY
        return BuildStatus.FAILED if self.attempts else BuildStatus.PENDING

    @property
    def cache_key(self) -> tuple[str, str]:
        return (self.repo_url, self.commit_sha)

    @property
    def from_cache(self) -> bool:
        return self.cached

    @property
    def is_service(self) -> bool:
        """Whether this environment runs a server rather than waiting for commands."""
        return bool(self.service_command)

    @property
    def supports_real_experiments(self) -> bool:
        """Whether measured experiments may run here.

        Conceptual fidelity never supports them: a Mini Lab must never produce
        performance measurements, and simulated behaviour must never be
        presented as experimental evidence (CLAUDE.md).
        """
        return self.ready and self.fidelity is not Fidelity.CONCEPTUAL

    def better_than(self, other: BuildArtifact | None) -> bool:
        """Whether this is a strict fidelity improvement on *other*.

        The test an upgrade offer has to pass. A ready artifact always beats
        nothing; an equal or lower fidelity is never offered, because switching
        environments has a cost and no benefit.
        """
        if not self.ready:
            return False
        if other is None:
            return True
        return self.fidelity_rank > other.fidelity_rank

    def declaration(self) -> dict[str, Any]:
        """Everything this artifact is required to declare about itself.

        The specification names these exactly: source, version, commit if
        applicable, fidelity, capabilities, limitations and the smoke-test
        result. Gathered in one place so no caller has to assemble it and
        forget one.
        """
        return {
            "source": self.source.value,
            "kind": self.kind.value,
            "version": self.version,
            "commit_sha": self.commit_sha or None,
            "fidelity": self.fidelity.value,
            "capabilities": list(self.capabilities.capabilities),
            "limitations": list(self.capabilities.limitations),
            "deviations": list(self.capabilities.deviations),
            "smoke_test": self.smoke.model_dump(mode="json") if self.smoke else None,
            "ready": self.ready,
            "simulated": self.simulated,
            "supports_real_experiments": self.supports_real_experiments,
        }

    def summary(self) -> str:
        """One line describing what this artifact is."""
        state = "READY" if self.ready else self.status.value.upper()
        where = f"@{self.commit_sha[:12]}" if self.commit_sha else ""
        marker = " [SIMULATED]" if self.simulated else ""
        return (
            f"{state} {self.repo_url}{where} "
            f"({self.source.value}, {self.fidelity.value}, {self.provider}){marker}"
        )


class BuildRequest(BaseModel):
    """A request to make a repository runnable."""

    repo_url: str
    commit_sha: str
    use_cache: bool = True
    tag_prefix: str = "repolab"

    def checkpoint_tag_for(self, kind: BuildKind | None = None) -> str:
        """A durable, collision-resistant name for one candidate's checkpoint.

        **The kind is part of the name.** Without it every rung of a ladder
        commits to the same tag and the last one to finish silently replaces the
        others -- observed with Cassandra, where the official release and the
        partial build both landed on ``repolab/apache-cassandra:<commit>``.
        """
        slug = self.repo_url.rstrip("/").rsplit("/", 2)[-2:]
        name = "-".join(part.replace(".", "-") for part in slug) or "repo"
        suffix = f"-{kind.value.replace('_', '-')}" if kind is not None else ""
        return f"{self.tag_prefix}/{name}:{self.commit_sha[:12]}{suffix}"

    @property
    def checkpoint_tag(self) -> str:
        """The tag for a single-strategy source build."""
        return self.checkpoint_tag_for(BuildKind.SOURCE_BUILD)


class BuildError(RuntimeError):
    """A build could not be produced."""


class BuildUnavailableError(BuildError):
    """Every rung of the ladder failed.

    The reason names each candidate and why it did not work. "The build failed"
    without saying which four things were tried is not a useful thing to tell
    anyone, and the whole point of a ladder is that there were four.
    """

    def __init__(self, repo_url: str, reason: str) -> None:
        self.repo_url = repo_url
        self.reason = reason
        super().__init__(f"no build available for {repo_url}: {reason}")
