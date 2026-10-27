"""Every reference in an architecture, checked against the investigated snapshot.

Runs last. A reference RepoLab's own detectors produced is a line they read
from the file, so it is confirmed by re-reading that line; a citation with an
excerpt must still contain it; one that cannot be confirmed is marked
unverified (and the UI says so) rather than shown as a fact. Each one is also
stamped with the commit it belongs to and how directly it shows the claim.

``cited_paths`` lists the files the architecture points at, so they can be
copied into the source store before the clone is deleted.
"""

from __future__ import annotations

from collections.abc import Iterator

from repolab.architecture.models import ArchitectureGraph, Evidence
from repolab.evidence.models import CodeReference
from repolab.sources import Snapshot, strength_of, verify_reference


def _anchor(excerpt: str | None) -> str | None:
    for line in (excerpt or "").splitlines():
        stripped = line.strip()
        if len(stripped) >= 3:
            return stripped[:120]
    return None


def _check(ref: CodeReference, snapshot: Snapshot) -> CodeReference:
    if ref.commit_sha and ref.commit_sha != snapshot.commit_sha:
        # Made against another version: never re-resolved against this one.
        return verify_reference(snapshot, ref.path, symbol=ref.symbol, commit_sha=ref.commit_sha)
    if ref.verified and snapshot.exists(ref.path):
        return ref
    if ref.symbol:
        checked = verify_reference(snapshot, ref.path, symbol=ref.symbol, line=ref.start_line)
    elif ref.start_line is not None:
        checked = verify_reference(
            snapshot,
            ref.path,
            line=ref.start_line,
            end_line=ref.end_line,
            expect=_anchor(ref.excerpt)
            if ref.excerpt and "\n" not in ref.excerpt.strip()
            else None,
        )
        if checked.verified and checked.verification == "line exists; no anchor to check":
            # A detector read this exact line; re-reading it is the check.
            checked.verification = "found by RepoLab's analysis at this line"
    else:
        checked = (
            CodeReference(
                path=ref.path,
                commit_sha=snapshot.commit_sha,
                strength=strength_of(ref.path),
                verified=True,
                verification="file exists",
            )
            if snapshot.exists(ref.path)
            else verify_reference(snapshot, ref.path)
        )
    if checked.verified and ref.excerpt and not checked.excerpt:
        checked.excerpt = ref.excerpt
    return checked


def _check_evidence(item: Evidence, snapshot: Snapshot) -> Evidence:
    if item.path is None or item.verified:
        return item
    if not snapshot.exists(item.path) and item.line is None:
        # A directory: it is in the tree, which is all it claims.
        item.verified = True
        return item
    expect = _anchor(item.excerpt) if item.line is not None else None
    ref = verify_reference(snapshot, item.path, line=item.line, expect=expect)
    item.verified = ref.verified
    if ref.verified and item.line is not None:
        item.line, item.end_line = ref.start_line, ref.end_line
    return item


def verify_graph(graph: ArchitectureGraph, snapshot: Snapshot) -> ArchitectureGraph:
    for node in graph.nodes:
        node.source_references = [_check(r, snapshot) for r in node.source_references]
    for edge in graph.edges:
        edge.evidence = [_check(r, snapshot) for r in edge.evidence]
    for subsystem in graph.subsystems:
        subsystem.evidence = [_check_evidence(e, snapshot) for e in subsystem.evidence]
        for dependency in subsystem.depends_on:
            dependency.evidence = _check(dependency.evidence, snapshot)
    for concept in graph.system_model.concepts:
        concept.evidence = [_check_evidence(e, snapshot) for e in concept.evidence]
    if graph.understanding is not None:
        graph.understanding.evidence = [
            _check_evidence(e, snapshot) for e in graph.understanding.evidence
        ]

    # The runtime model was built from verified references only; anything that
    # lost its evidence (a commit mismatch, a removed file) leaves with it.
    runtime = graph.runtime
    for component in runtime.components:
        component.source_references = [
            r for r in (_check(r, snapshot) for r in component.source_references) if r.verified
        ]
        component.responsibilities = [
            item for item in component.responsibilities if _check(item.reference, snapshot).verified
        ]
    removed = {c.id for c in runtime.components if not c.source_references}
    if removed:
        graph.dropped["runtime component whose source could not be verified"] = len(removed)
    runtime.components = [c for c in runtime.components if c.id not in removed]
    kept = []
    for link in runtime.links:
        if link.source in removed or link.target in removed:
            continue
        link.evidence = [r for r in (_check(r, snapshot) for r in link.evidence) if r.verified]
        if link.evidence:
            kept.append(link)
    runtime.links = kept
    graph.flows = [f for f in graph.flows if not ({s.component for s in f.steps} & removed)]
    graph.reading_path = [item for item in graph.reading_path if item.reference.verified]
    return graph


def _references(graph: ArchitectureGraph) -> Iterator[CodeReference]:
    for node in graph.nodes:
        yield from node.source_references
    for edge in graph.edges:
        yield from edge.evidence
    for abstraction in graph.abstractions:
        yield abstraction.definition
        yield from abstraction.usages
    for relation in graph.relations:
        yield relation.evidence
    for component in graph.runtime.components:
        yield from component.source_references
        yield from (r.reference for r in component.responsibilities)
    for link in graph.runtime.links:
        yield from link.evidence
    for flow in graph.flows:
        for step in flow.steps:
            yield from step.references
    for item in graph.reading_path:
        yield item.reference
    for subsystem in graph.subsystems:
        for dependency in subsystem.depends_on:
            yield dependency.evidence


def _evidence_paths(graph: ArchitectureGraph) -> Iterator[str]:
    items: list[Evidence] = []
    for subsystem in graph.subsystems:
        items += subsystem.evidence
    for concept in graph.system_model.concepts:
        items += concept.evidence
    if graph.understanding is not None:
        items += graph.understanding.evidence
    for item in items:
        if item.path and item.line is not None:
            yield item.path


def cited_paths(graph: ArchitectureGraph) -> list[str]:
    """Every file the architecture cites at a line -- what the source store must keep."""
    paths = [ref.path for ref in _references(graph) if ref.verified or ref.start_line is not None]
    paths += list(_evidence_paths(graph))
    return list(dict.fromkeys(paths))
