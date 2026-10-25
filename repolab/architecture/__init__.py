"""How a repository works: a generic, evidence-backed architecture.

    manifest + detector findings      what the investigation already established
            |
    subsystems                        the repository's major parts, from its
            |                         declared modules or source directories
    generic analyzers                 entry points, HTTP, CLI, data, workflow,
            |                         storage, services, library, configuration
    framework adapters                refine subtype and technology, add nothing
            |
    assembly                          evidenced edges, grouping, flows, files, view
            |
    Nemotron Super (optional)         names, purpose, groups, flows, summary
            |
    orientation                       what this is, 3-8 concepts, a walkthrough,
            |                         a learning path -- deterministic, then Super
    validation                        drops anything without real evidence

Usage::

    graph = await build_architecture(context, outcome, manifest, router=router)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from repolab.architecture.models import ArchitectureGraph, load_graph

if TYPE_CHECKING:
    from repolab.ingest.detectors import DetectionContext, DetectionOutcome
    from repolab.manifest.models import RepoManifest


# The builder is imported on first use. It reads files through
# ``repolab.ingest``, which itself imports this package for the model, so an
# eager import here would be circular.


async def build_architecture(
    context: DetectionContext,
    outcome: DetectionOutcome,
    manifest: RepoManifest | None,
    *,
    router: Any | None = None,
    summaries: dict[str, str] | None = None,
    docs: list[Any] | None = None,
) -> ArchitectureGraph:
    from repolab.architecture.build import build_architecture as build

    return await build(context, outcome, manifest, router=router, summaries=summaries, docs=docs)


def build_static_architecture(
    context: DetectionContext, outcome: DetectionOutcome, manifest: RepoManifest | None
) -> tuple[ArchitectureGraph, Any]:
    from repolab.architecture.build import build_static_architecture as build

    return build(context, outcome, manifest)


__all__ = ["ArchitectureGraph", "build_architecture", "build_static_architecture", "load_graph"]
