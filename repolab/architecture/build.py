"""Building the architecture. See :mod:`repolab.architecture` for the layers."""

from __future__ import annotations

from repolab.architecture.analyzers.adapters import (
    SparkAdapter,
    WebFrameworkAdapter,
    WorkflowAdapter,
)
from repolab.architecture.analyzers.base import AnalysisContext, ArchitectureAnalyzer
from repolab.architecture.analyzers.cli import CliAnalyzer
from repolab.architecture.analyzers.data import DataPipelineAnalyzer
from repolab.architecture.analyzers.entrypoints import EntryPointAnalyzer
from repolab.architecture.analyzers.http import HttpAnalyzer
from repolab.architecture.analyzers.library import ConfigurationAnalyzer, LibraryAnalyzer
from repolab.architecture.analyzers.services import ServiceAnalyzer
from repolab.architecture.analyzers.storage import StorageAnalyzer
from repolab.architecture.analyzers.workflow import WorkflowAnalyzer
from repolab.architecture.assemble import assemble, fit
from repolab.architecture.models import ArchitectureGraph
from repolab.architecture.source import SourceIndex
from repolab.architecture.subsystems import discover_subsystems, rank
from repolab.architecture.synthesis import SynthesisRouter, synthesise
from repolab.architecture.understanding import orient
from repolab.architecture.validate import validate
from repolab.evidence.models import DocumentationReference
from repolab.ingest.detectors import DetectionContext, DetectionOutcome
from repolab.manifest.models import RepoManifest

#: Generic analyzers, in order. Library runs last: it only describes a
#: repository nothing more specific matched.
GENERIC_ANALYZERS: tuple[ArchitectureAnalyzer, ...] = (
    EntryPointAnalyzer(),
    HttpAnalyzer(),
    CliAnalyzer(),
    DataPipelineAnalyzer(),
    WorkflowAnalyzer(),
    ServiceAnalyzer(),
    StorageAnalyzer(),
    ConfigurationAnalyzer(),
    LibraryAnalyzer(),
)

#: Framework adapters. Enrichment only.
ADAPTERS: tuple[ArchitectureAnalyzer, ...] = (
    SparkAdapter(),
    WebFrameworkAdapter(),
    WorkflowAdapter(),
)


def build_static_architecture(
    context: DetectionContext,
    outcome: DetectionOutcome,
    manifest: RepoManifest | None,
    *,
    index: SourceIndex | None = None,
    adapters: tuple[ArchitectureAnalyzer, ...] = ADAPTERS,
) -> tuple[ArchitectureGraph, SourceIndex]:
    """The deterministic architecture, before any model is consulted."""
    index = index if index is not None else SourceIndex.build(context)
    graph = ArchitectureGraph(subsystems=discover_subsystems(index))
    analysis = AnalysisContext(index=index, outcome=outcome, manifest=manifest, graph=graph)
    for analyzer in (*GENERIC_ANALYZERS, *adapters):
        analyzer.analyze(analysis)
    graph = validate(assemble(analysis), index)
    rank(graph, index, manifest)
    fit(graph)  # folding small subsystems together can overfill "Other modules"
    return graph, index


async def build_architecture(
    context: DetectionContext,
    outcome: DetectionOutcome,
    manifest: RepoManifest | None,
    *,
    router: SynthesisRouter | None = None,
    summaries: dict[str, str] | None = None,
    docs: list[DocumentationReference] | None = None,
) -> ArchitectureGraph:
    """The full architecture, from components up to "what is this repository?".

    Static analysis and subsystems first; Super refines the components, then
    orients a newcomer (levels 0 and 1); validation runs after each model pass.
    *summaries* are optional one-line file purposes (Nano's) and *docs* official
    documentation the investigation retrieved -- context for Super, and *docs*
    may be cited, but neither is treated as repository evidence on its own.
    """
    graph, index = build_static_architecture(context, outcome, manifest)
    graph = validate(await synthesise(graph, index, router, summaries=summaries), index)
    rank(graph, index, manifest)
    graph = await orient(graph, index, manifest, router, docs=docs)
    return validate(graph, index)
