"""The last word on what the architecture may claim.

Run after everything else, including the model's pass. A component without a
reference to a real file is dropped; a reference to a file that is not in the
repository is removed; a relationship RepoLab inferred is dropped unless it
cites evidence. Every refusal is counted in ``graph.dropped``.
"""

from __future__ import annotations

from repolab.architecture.models import ArchitectureGraph, Provenance
from repolab.architecture.source import SourceIndex


def validate(graph: ArchitectureGraph, index: SourceIndex) -> ArchitectureGraph:
    def drop(reason: str, count: int = 1) -> None:
        if count:
            graph.dropped[reason] = graph.dropped.get(reason, 0) + count

    for node in list(graph.nodes):
        kept = [
            ref for ref in node.source_references if index.line_exists(ref.path, ref.start_line)
        ]
        drop("reference to a file not in the repository", len(node.source_references) - len(kept))
        node.source_references = kept
        if not kept:
            drop("component without evidence")
            graph.remove_node(node.id)

    for edge in list(graph.edges):
        edge.evidence = [
            ref for ref in edge.evidence if index.line_exists(ref.path, ref.start_line)
        ]
        if edge.provenance is Provenance.INFERRED and not edge.evidence:
            drop("inferred relationship without evidence")
            graph.edges.remove(edge)

    graph.important_files = [f for f in graph.important_files if f.path in index.all_paths]
    known = {node.id for node in graph.nodes}
    for item in graph.important_files:
        item.node_ids = [i for i in item.node_ids if i in known]
    graph.entry_points = [e for e in graph.entry_points if e in known]
    for subsystem in graph.subsystems:
        subsystem.component_ids = [i for i in subsystem.component_ids if i in known]
    for concept in graph.system_model.concepts:
        concept.member_ids = [i for i in concept.member_ids if i in known]
    for walk in graph.walkthroughs:
        if walk.level.value == "component" and any(s.target_id not in known for s in walk.steps):
            drop("walkthrough through a removed component")
    graph.walkthroughs = [
        w
        for w in graph.walkthroughs
        if w.level.value != "component" or all(s.target_id in known for s in w.steps)
    ]
    return ArchitectureGraph.model_validate(graph.model_dump())
