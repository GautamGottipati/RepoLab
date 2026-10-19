"""Build plans.

A plan is the deterministic, inspectable description of how an environment will be
produced: which base image, what to upload, which commands to run, and what smoke
test decides whether it worked. It is derived from the ``RepoManifest``, so it can
be shown to a user *before* anything executes.

Plans never contain repository code. They contain commands RepoLab chose.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from repolab.manifest.models import RepoManifest

#: Where an uploaded repository archive lands, and where it is extracted.
ARCHIVE_PATH = "/work/repo.tar.gz"
WORKDIR = "/work/repo"


class BuildStep(BaseModel):
    """One command in a build."""

    name: str
    command: str
    timeout_seconds: float = Field(default=600.0, gt=0)
    optional: bool = False
    """An optional step may fail without failing the build."""


class SmokeTest(BaseModel):
    """The check that decides whether a build is usable."""

    name: str
    command: str
    expect_exit_code: int = 0
    expect_stdout_contains: str | None = None
    timeout_seconds: float = Field(default=300.0, gt=0)


class BuildPlan(BaseModel):
    """How to build one repository, and how to know it worked."""

    base_image: str
    workdir: str = WORKDIR
    archive_path: str = ARCHIVE_PATH

    service_command: str | None = None
    """A long-running server to run as the container's **main process**.

    Not a build step. A server started from a build step dies with the shell
    that started it -- measured on Cassandra, which exits silently when launched
    from an exec and then has nothing listening for the smoke test. CLAUDE.md
    anticipates this: some applications require persistent services.

    ``None`` means the environment just needs to stay alive for commands, which
    is what a build like Spark's wants.
    """

    cli_command: str | None = None
    """The binary that drives this environment's CLI surface, when it has one.

    Differs per rung -- the official image ships ``/opt/cassandra/bin/cqlsh``,
    a source build has ``bin/cqlsh`` in its own tree -- and a terminal has to
    know which. Same shape of knowledge as ``service_command``, carried the same
    way.
    """

    needs_submodules: bool = False
    """Whether the build requires the repository's git submodules.

    Declared by the plan because the plan is what knows: Cassandra's ``ant jar``
    invokes a gradle build in ``modules/accord``, which a plain shallow clone
    leaves empty. The caller doing the cloning reads this rather than guessing,
    and ingestion -- which only reads files -- never asks for them.
    """

    memory: str | None = None
    """Container memory, when the software needs more than the default.

    Measured, not guessed: Cassandra 5.0 exits silently in a 2 GB container and
    starts in 4 GB. A limit that is too low fails with no log at all, so the
    requirement belongs with the plan that knows about it.
    """

    setup: list[BuildStep] = Field(default_factory=list)
    """Steps run before the repository is available, e.g. creating directories."""

    steps: list[BuildStep] = Field(default_factory=list)
    smoke: SmokeTest

    deviations: list[str] = Field(default_factory=list)
    """Disclosed departures from what the repository declares."""

    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @property
    def extract_step(self) -> BuildStep:
        """Unpack the uploaded archive. Uses tar only -- never the repo's own tooling."""
        return BuildStep(
            name="extract repository",
            command=(
                f"mkdir -p {self.workdir} && "
                f"tar -xzf {self.archive_path} -C {self.workdir} && "
                f"ls -a {self.workdir} | head -20"
            ),
            timeout_seconds=300.0,
        )

    @property
    def all_steps(self) -> list[BuildStep]:
        """Every step in execution order, including extraction."""
        return [*self.setup, self.extract_step, *self.steps]

    def describe(self) -> str:
        """A human-readable rendering, for showing a plan before running it."""
        lines = [f"base image     {self.base_image}", f"workdir        {self.workdir}"]
        for index, step in enumerate(self.all_steps, start=1):
            lines.append(f"  {index}. {step.name}: {step.command[:80]}")
        lines.append(f"smoke test     {self.smoke.name}: {self.smoke.command[:80]}")
        if self.deviations:
            lines.append("deviations:")
            lines.extend(f"  - {item}" for item in self.deviations)
        return "\n".join(lines)


class UnsupportedRepositoryError(RuntimeError):
    """No build plan exists for this repository yet."""

    def __init__(self, repo_url: str, surface: str) -> None:
        super().__init__(
            f"no build plan for {repo_url} (primary surface: {surface}). "
            "Spark data playgrounds and Cassandra are supported."
        )


def plan_for(manifest: RepoManifest) -> BuildPlan:
    """The single highest-fidelity plan for a repository.

    The one-plan view of the ladder, kept for callers that want an exact build or
    nothing. Anything that wants the fallbacks uses
    :func:`~repolab.builds.candidates.candidates_for` instead.

    Raises:
        UnsupportedRepositoryError: if no plan covers this repository's shape.
    """
    from repolab.builds.candidates import NoCandidatesError, candidates_for

    try:
        candidates = candidates_for(manifest)
    except NoCandidatesError as exc:
        raise UnsupportedRepositoryError(manifest.repo_url, exc.surface) from exc

    for candidate in candidates:
        if candidate.plan is not None:
            return candidate.plan

    raise UnsupportedRepositoryError(manifest.repo_url, "none")
