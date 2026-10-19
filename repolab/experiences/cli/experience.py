"""The CLI experience adapter.

What a frontend needs in order to render a terminal for a repository, assembled
from the two things that actually know: the manifest, which found the surface and
cited the files proving it, and the ready build artifact, which knows what the
environment can and cannot do.

Nothing here is Cassandra-specific. The manifest decides that a repository has a
CLI surface and the artifact decides which binary drives it, which is the rule
the experience layer already follows -- the frontend renders from the manifest
rather than from repository-specific code (CLAUDE.md).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

from repolab.builds.models import BuildArtifact
from repolab.evidence.models import CodeReference
from repolab.manifest.models import InteractionSurface, RepoManifest, SurfaceType


class NoCliSurfaceError(RuntimeError):
    """This repository does not expose a CLI surface."""

    def __init__(self, repo_url: str, found: list[str]) -> None:
        self.found = found
        super().__init__(
            f"{repo_url} exposes no CLI surface"
            + (f" (found: {', '.join(found)})" if found else "")
        )


class CLIExperience(BaseModel):
    """A terminal surface a learner can be given.

    ``available`` is the honest answer to "can someone type into this": a CLI
    surface with no ready artifact, or a ready artifact with no CLI binary, is
    described rather than quietly rendered as if it worked.
    """

    repo_url: str
    commit_sha: str = ""

    name: str
    description: str = ""
    surface_confidence: float = 0.0
    reason: str = ""
    """Why this surface was detected, from the manifest."""

    cli_command: str | None = None
    """The binary a statement is run through, from the artifact."""

    prompt: str = "cqlsh>"
    """What the terminal shows before a statement. Cosmetic; never faked output."""

    fidelity: str = "unknown"
    simulated: bool = False
    version: str = ""
    build_source: str = ""

    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    """Disclosed differences from the requested commit, carried through verbatim."""

    evidence: list[CodeReference] = Field(default_factory=list)
    """The repository files that justify this surface."""

    @computed_field
    @property
    def available(self) -> bool:
        """Whether a real statement can actually be executed here."""
        return bool(self.cli_command) and not self.simulated

    @computed_field
    @property
    def unavailable_reason(self) -> str:
        if self.available:
            return ""
        if self.simulated:
            return (
                "This is a conceptual simulation. No server is running, "
                "so commands are not executed."
            )
        return "This environment exposes no command-line binary, so nothing can be run."

    @computed_field
    @property
    def real_experiments(self) -> bool:
        """Whether what happens here counts as evidence."""
        return not self.simulated


def cli_surface(manifest: RepoManifest) -> InteractionSurface:
    """The manifest's CLI surface, preferring the primary one.

    Raises:
        NoCliSurfaceError: if the manifest found no CLI surface at all.
    """
    surfaces = [s for s in manifest.interaction_surfaces if s.type is SurfaceType.CLI]
    if not surfaces:
        raise NoCliSurfaceError(
            manifest.repo_url,
            [s.type.value for s in manifest.interaction_surfaces],
        )
    # A repository can declare several CLIs; the primary one is the one the
    # classifier recommended, and confidence breaks the tie otherwise.
    surfaces.sort(key=lambda s: (s.primary, float(s.confidence)), reverse=True)
    return surfaces[0]


def build_cli_experience(manifest: RepoManifest, artifact: BuildArtifact) -> CLIExperience:
    """Assemble the CLI experience for *manifest* running on *artifact*.

    The artifact is the authority on what the environment offers -- its
    capabilities, limitations and disclosed deviations are passed through
    unedited, because softening them is how a learner ends up believing they
    measured something they did not.
    """
    surface = cli_surface(manifest)
    return CLIExperience(
        repo_url=manifest.repo_url,
        commit_sha=artifact.commit_sha or manifest.commit_sha,
        name=surface.name,
        description=surface.description or "",
        surface_confidence=float(surface.confidence),
        reason=surface.reason,
        cli_command=artifact.cli_command,
        prompt=_prompt_for(artifact.cli_command),
        fidelity=artifact.fidelity.value,
        simulated=artifact.simulated,
        version=artifact.version or "",
        build_source=artifact.source.value,
        capabilities=list(artifact.capabilities.capabilities),
        limitations=list(artifact.capabilities.limitations),
        deviations=list(artifact.capabilities.deviations),
        evidence=list(surface.evidence),
    )


def _prompt_for(cli_command: str | None) -> str:
    """The prompt string a learner expects, derived from the binary's name."""
    if not cli_command:
        return ">"
    name = cli_command.rsplit("/", 1)[-1].removesuffix(".py")
    return f"{name}>"
