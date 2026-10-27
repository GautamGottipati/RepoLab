"""Every kind of analysis artifact, in one place.

An artifact is one piece of what RepoLab knows about a repository at one
commit. This table says, for each kind:

    priority      P0 user-blocking, P1 important background, P2 on demand
    depends_on    what must be ready (or partial) first
    version       bumped when its algorithm changes; the only staleness check
    queue         where its work runs
    lazy          generated only when asked for

Artifact kinds are domain knowledge (``architecture.runtime``), never pieces of
a page; the frontend decides how to render them. Version checks live here and
nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Priority(StrEnum):
    P0 = "P0"
    """User-blocking: the first useful facts."""
    P1 = "P1"
    """Important background work, generated eagerly after P0."""
    P2 = "P2"
    """Deep or expensive: generated on demand."""


class Status(StrEnum):
    NOT_REQUESTED = "not_requested"
    QUEUED = "queued"
    GENERATING = "generating"
    VERIFYING = "verifying"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    """A dependency failed; retried with it."""

    @property
    def usable(self) -> bool:
        return self in {Status.READY, Status.PARTIAL}

    @property
    def active(self) -> bool:
        return self in {Status.QUEUED, Status.GENERATING, Status.VERIFYING}


class Queue(StrEnum):
    INTERACTIVE = "interactive"
    DEFAULT = "default"
    BACKGROUND = "background"


@dataclass(frozen=True)
class ArtifactSpec:
    type: str
    priority: Priority
    version: str
    depends_on: tuple[str, ...] = ()
    queue: Queue = Queue.DEFAULT
    lazy: bool = False
    model_tier: str | None = None
    """``nano``, ``super`` or ``ultra`` when a model is involved."""
    needs_clone: bool = False
    """Generated from the repository's files, so only inside an investigation."""
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


REPO_FACTS = "repo.facts"
REPO_MANIFEST = "repo.manifest"
ARCH_STRUCTURE = "architecture.structure"
ARCH_RUNTIME = "architecture.runtime"
ARCH_SOURCE = "architecture.source"
ARCH_COMPONENTS = "architecture.components"
ARCH_SUMMARY = "architecture.summary"
ARCH_RUNTIME_NAMING = "architecture.runtime_naming"

SPECS: dict[str, ArtifactSpec] = {
    spec.type: spec
    for spec in (
        ArtifactSpec(
            REPO_FACTS,
            Priority.P0,
            "1",
            queue=Queue.INTERACTIVE,
            needs_clone=True,
            description="Commit, languages, file counts, build system, README opening",
        ),
        ArtifactSpec(
            REPO_MANIFEST,
            Priority.P0,
            "1",
            (REPO_FACTS,),
            queue=Queue.INTERACTIVE,
            needs_clone=True,
            description="Surfaces, entry points and services; the investigator agent refines them",
        ),
        ArtifactSpec(
            ARCH_STRUCTURE,
            Priority.P0,
            "4",
            (REPO_MANIFEST,),
            queue=Queue.INTERACTIVE,
            needs_clone=True,
            description="Parts, detected components and evidenced relationships",
        ),
        ArtifactSpec(
            ARCH_RUNTIME,
            Priority.P0,
            "4",
            (ARCH_STRUCTURE,),
            queue=Queue.INTERACTIVE,
            needs_clone=True,
            description="Key types, relations, runtime components, flows, modules, reading path",
        ),
        ArtifactSpec(
            ARCH_SOURCE,
            Priority.P0,
            "1",
            (ARCH_RUNTIME,),
            queue=Queue.INTERACTIVE,
            needs_clone=True,
            description="Every cited file kept at this commit; every reference re-read",
        ),
        ArtifactSpec(
            ARCH_COMPONENTS,
            Priority.P1,
            "1",
            (ARCH_STRUCTURE,),
            model_tier="super",
            needs_clone=True,
            description="Component names, descriptions and cited relationships",
        ),
        ArtifactSpec(
            ARCH_SUMMARY,
            Priority.P1,
            "1",
            (ARCH_COMPONENTS,),
            model_tier="super",
            needs_clone=True,
            description="What the repository is, its concepts and a walkthrough",
        ),
        ArtifactSpec(
            ARCH_RUNTIME_NAMING,
            Priority.P2,
            "1",
            (ARCH_RUNTIME,),
            queue=Queue.INTERACTIVE,
            lazy=True,
            model_tier="super",
            description="Runtime component names, responsibilities, link labels and flows",
        ),
    )
}

#: What a completed investigation has produced: P0 and eager P1.
EAGER = tuple(t for t, s in SPECS.items() if not s.lazy)


def spec(artifact_type: str) -> ArtifactSpec:
    try:
        return SPECS[artifact_type]
    except KeyError:
        raise KeyError(f"unknown artifact type {artifact_type!r}") from None


def version(artifact_type: str) -> str:
    """The analysis version an artifact of this type must have to be reused."""
    return spec(artifact_type).version


def dependants(artifact_type: str) -> list[str]:
    return [t for t, s in SPECS.items() if artifact_type in s.depends_on]
