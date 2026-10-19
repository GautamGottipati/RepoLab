"""The candidates a repository can be built as, best first.

A candidate is a *rung on the ladder*: a kind, the plan that realises it, and why
RepoLab thinks it is worth trying. Producing them is deliberately separate from
racing them -- the list can be shown to a user, or asserted in a test, without
anything being built.

Spark keeps exactly one candidate, so nothing P0 depends on changes. Cassandra
gets all four, which is what makes it the repository this phase demonstrates.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from repolab.builds.models import fidelity_rank
from repolab.builds.plan import BuildPlan
from repolab.manifest.models import BuildKind, Fidelity, RepoManifest, SurfaceType


class BuildCandidate(BaseModel):
    """One way of producing a runnable environment."""

    kind: BuildKind
    plan: BuildPlan | None = None
    """How to build it. ``None`` for a Mini Lab, which builds nothing."""

    why: str = ""
    """Why this rung exists, in one line. Shown before anything runs."""

    demo_delay_seconds: float = Field(default=0.0, ge=0)
    """A deliberate delay before this candidate starts, for demonstration.

    The ladder's interesting behaviour -- a cheap candidate serving while an
    expensive one finishes, then an upgrade -- is only observable when the
    candidates finish in that order. Real builds do that naturally and slowly;
    this makes it happen on demand.

    It is recorded on the attempt and on the artifact, so a build that was
    slowed on purpose can never be read as one that was genuinely slow.
    """

    @property
    def expected_fidelity(self) -> Fidelity:
        """What this candidate would be worth *if it passes its smoke test*.

        Expected, not promised: a source build that has to substitute a pinned
        dependency comes out at ``HIGH``, and the artifact decides that from the
        deviations it actually recorded.
        """
        from repolab.builds.models import fidelity_for

        return fidelity_for(self.kind, self.plan.deviations if self.plan else [])

    @property
    def rank(self) -> int:
        """Higher is better. Orders the ladder."""
        return fidelity_rank(self.expected_fidelity)

    @property
    def builds_nothing(self) -> bool:
        """True for the Mini Lab, which has no environment to build."""
        return self.plan is None


class NoCandidatesError(RuntimeError):
    """No rung of the ladder covers this repository.

    Distinct from every candidate *failing*: this is RepoLab not knowing how to
    try, which is worth saying differently from having tried and not managed it.
    """

    def __init__(self, repo_url: str, surface: str) -> None:
        self.repo_url = repo_url
        self.surface = surface
        super().__init__(
            f"no build candidates for {repo_url} (primary surface: {surface}). "
            "Spark data playgrounds and Cassandra are supported."
        )


def candidates_for(
    manifest: RepoManifest, *, source_delay_seconds: float = 0.0
) -> list[BuildCandidate]:
    """The ladder for this repository, highest fidelity first.

    *source_delay_seconds* slows the exact source build for demonstration. It
    defaults to zero, so it cannot affect a real build unless a caller asks for
    it.

    Raises:
        NoCandidatesError: no rung covers this repository's shape.
    """
    if is_cassandra(manifest):
        return _cassandra_ladder(manifest, source_delay_seconds)

    primary = manifest.primary_surface
    surface = primary.type if primary is not None else None

    if surface is SurfaceType.DATA_PLAYGROUND:
        from repolab.builds.spark import spark_plan

        # One rung. The Spark path is a source build or nothing, which is what
        # P0 shipped and what its tests assert.
        return [
            BuildCandidate(
                kind=BuildKind.SOURCE_BUILD,
                plan=spark_plan(manifest),
                why="build the repository's own Spark job from this commit",
                demo_delay_seconds=source_delay_seconds,
            )
        ]

    raise NoCandidatesError(manifest.repo_url, surface.value if surface else "none")


def is_cassandra(manifest: RepoManifest) -> bool:
    """Whether this manifest describes Apache Cassandra.

    Matched on the repository rather than on detected frameworks: Cassandra's
    build is Ant with few recognisable packages, so framework detection finds
    nothing for it -- a known gap recorded since Phase 3.
    """
    url = manifest.repo_url.lower().rstrip("/").removesuffix(".git")
    return url.endswith("/apache/cassandra") or url.endswith("/cassandra")


def _cassandra_ladder(manifest: RepoManifest, source_delay: float) -> list[BuildCandidate]:
    from repolab.builds.cassandra import (
        OFFICIAL_VERSION,
        cassandra_official_plan,
        cassandra_partial_plan,
        cassandra_source_plan,
    )

    return [
        BuildCandidate(
            kind=BuildKind.SOURCE_BUILD,
            plan=cassandra_source_plan(manifest),
            why="build the requested commit with the repository's own ant target",
            demo_delay_seconds=source_delay,
        ),
        BuildCandidate(
            kind=BuildKind.OFFICIAL_RELEASE,
            plan=cassandra_official_plan(manifest),
            why=f"run the official Cassandra {OFFICIAL_VERSION} release, version disclosed",
        ),
        BuildCandidate(
            kind=BuildKind.PARTIAL_BUILD,
            plan=cassandra_partial_plan(manifest),
            why="a reduced single node, for machines the full server will not fit on",
        ),
        BuildCandidate(
            kind=BuildKind.MINI_LAB,
            plan=None,
            why="a conceptual simulation, when nothing can be run at all",
        ),
    ]


def needs_submodules(candidates: list[BuildCandidate]) -> bool:
    """Whether any candidate needs the repository's submodules cloned.

    Asked before cloning, so the caller fetches them once rather than
    discovering halfway through a build that the top rung cannot compile.
    """
    return any(
        candidate.plan is not None and candidate.plan.needs_submodules for candidate in candidates
    )
