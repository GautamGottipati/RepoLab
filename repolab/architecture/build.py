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
from repolab.architecture.code_model import Corpus, build_code_model, reading_path
from repolab.architecture.models import ArchitectureGraph
from repolab.architecture.modules import module_dependencies
from repolab.architecture.runtime_synthesis import name_runtime
from repolab.architecture.source import SourceIndex
from repolab.architecture.subsystems import discover_subsystems, rank
from repolab.architecture.synthesis import SynthesisRouter, synthesise
from repolab.architecture.understanding import orient, readme_path
from repolab.architecture.validate import validate
from repolab.architecture.verify import verify_graph
from repolab.evidence.models import DocumentationReference
from repolab.ingest.detectors import DetectionContext, DetectionOutcome
from repolab.ingest.priority import prioritized
from repolab.manifest.models import RepoManifest
from repolab.sources import CloneSnapshot

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
    graph = validate(graph, index)
    return await runtime_architecture(graph, context, index, router)


async def runtime_architecture(
    graph: ArchitectureGraph,
    context: DetectionContext,
    index: SourceIndex,
    router: SynthesisRouter | None,
) -> ArchitectureGraph:
    """The machinery under the surfaces: key types, how they use each other,
    runtime components, flows, module dependencies -- and every reference in
    the graph checked against the snapshot. See :mod:`repolab.architecture.code_model`."""
    paths = [entry.path for entry in prioritized(context.tree.files)]
    snapshot = CloneSnapshot(root=context.root, commit_sha=index.commit_sha, paths=set(paths))
    corpus = Corpus.read(context.root, index.commit_sha, paths)
    readme = (index.text(found) or "") if (found := readme_path(index)) else ""
    graph = build_code_model(graph, corpus, readme=readme)
    module_dependencies(graph, snapshot, paths)
    graph = await name_runtime(graph, router)
    graph = verify_graph(graph, snapshot)
    # Ordered after verification, which is what makes a reference eligible.
    graph.reading_path = reading_path(graph)
    return graph
