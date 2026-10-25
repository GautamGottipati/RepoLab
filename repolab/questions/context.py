"""What RepoLab already knows about the part of the repository a question is about.

The investigation that produced the Architecture page found components, the
relationships between them and the lines that show each one. A question about
"how the APIs use the database" deserves that map, not a fresh guess: a bounded
slice of it goes to the answer, its excerpts join the evidence, and its
relationship ids become something a diagram edge can cite.

Measured values come the same way -- from the runtime overlay, which holds only
values recorded by real runs. Nothing here is computed, estimated or asked of a
model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from repolab.architecture.actions import suggested_questions
from repolab.architecture.models import ArchitectureEdge, ArchitectureGraph, ArchitectureNode
from repolab.architecture.runtime import RuntimeOverlay
from repolab.evidence.models import CodeReference, Measurement, MeasurementSource
from repolab.questions.models import MAX_FOLLOW_UPS, ArchitectureFocus

#: Bounds on what one question carries from the architecture.
MAX_SLICE_NODES = 12
MAX_SLICE_EDGES = 16
MAX_SLICE_REFERENCES = 8
MAX_MEASUREMENTS = 12

_WORD = re.compile(r"[a-z][a-z0-9]+")
_EDGE_ID = re.compile(r"edge:[^\s,;()]+")

#: Words too common in questions and component names to tie one to the other.
_COMMON = frozenset(
    {
        "the",
        "and",
        "how",
        "does",
        "what",
        "why",
        "where",
        "which",
        "this",
        "that",
        "use",
        "uses",
        "used",
        "with",
        "from",
        "into",
        "when",
        "for",
        "are",
        "is",
        "api",
        "apis",
        "app",
        "application",
        "component",
        "components",
        "service",
        "services",
        "repository",
        "repo",
        "code",
        "data",
        "file",
        "files",
        "work",
        "works",
        "happens",
        "between",
        "about",
        "their",
        "they",
        "them",
        "its",
    }
)


def edge_id(edge: ArchitectureEdge) -> str:
    """A stable name for a relationship, which an answer may cite."""
    return f"edge:{edge.source}->{edge.target}"


@dataclass
class ArchitectureSlice:
    """The components and relationships relevant to one question."""

    nodes: list[ArchitectureNode] = field(default_factory=list)
    edges: list[ArchitectureEdge] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.nodes

    def edge(self, cited: str) -> ArchitectureEdge | None:
        """The relationship a citation names -- also when the model copied the
        whole map line (``edge:a->b (calls, static)``) rather than the id."""
        match = _EDGE_ID.search(cited)
        if match is None:
            return None
        return next((edge for edge in self.edges if edge_id(edge) == match.group(0)), None)

    def node_ids(self) -> set[str]:
        return {node.id for node in self.nodes}

    def references(self) -> list[CodeReference]:
        """The lines the architecture analysis cited, with their excerpts."""
        refs: list[CodeReference] = []
        for node in self.nodes:
            refs.extend(node.source_references[:2])
        for edge in self.edges:
            refs.extend(edge.evidence[:1])
        seen: set[tuple[str, int | None]] = set()
        kept: list[CodeReference] = []
        for ref in refs:
            key = (ref.path, ref.start_line)
            if key not in seen:
                seen.add(key)
                kept.append(ref)
        return kept[:MAX_SLICE_REFERENCES]

    def describe(self) -> str:
        """The slice as lines a model can read and cite."""
        if self.empty:
            return "- (no architecture map for this question)"
        lines: list[str] = []
        for node in self.nodes:
            what = node.category.value + (f", {node.technology}" if node.technology else "")
            files = ", ".join(_cite(ref) for ref in node.source_references[:3])
            line = f"- component {node.id}: {node.label} ({what})"
            if node.description:
                line += f" -- {node.description[:160]}"
            if files:
                line += f" [files: {files}]"
            lines.append(line)
        for edge in self.edges:
            files = ", ".join(_cite(ref) for ref in edge.evidence[:2])
            line = f"- {edge_id(edge)} ({edge.type.value}, {edge.provenance.value})"
            if edge.description:
                line += f": {edge.description[:160]}"
            if files:
                line += f" [shown by: {files}]"
            lines.append(line)
        return "\n".join(lines)


def architecture_slice(
    graph: ArchitectureGraph | None,
    question: str,
    *,
    focus: ArchitectureFocus | None = None,
    max_nodes: int = MAX_SLICE_NODES,
    max_edges: int = MAX_SLICE_EDGES,
) -> ArchitectureSlice:
    """The components the question names or was asked from, and their neighbours."""
    if graph is None or not graph.nodes:
        return ArchitectureSlice()

    seeds: list[str] = []
    if focus is not None:
        seeds.extend(_focus_members(graph, focus.node_id))
    words = {word for word in _WORD.findall(question.lower()) if word not in _COMMON}
    scored = sorted(
        ((_overlap(node, words), node.id) for node in graph.nodes),
        key=lambda pair: (-pair[0], pair[1]),
    )
    seeds.extend(node_id for score, node_id in scored if score > 0)
    seeds = list(dict.fromkeys(seeds))[:max_nodes]

    chosen = list(seeds)
    if len(chosen) < max_nodes:
        # One hop out, most-connected first: what the named parts talk to.
        degree: dict[str, int] = {}
        for edge in graph.edges:
            for this, other in ((edge.source, edge.target), (edge.target, edge.source)):
                if this in seeds and other not in chosen:
                    degree[other] = degree.get(other, 0) + 1
        for node_id in sorted(degree, key=lambda n: (-degree[n], n)):
            if len(chosen) >= max_nodes:
                break
            chosen.append(node_id)

    ids = set(chosen)
    nodes = [node for node in graph.nodes if node.id in ids]
    nodes.sort(key=lambda node: chosen.index(node.id))
    edges = [edge for edge in graph.edges if edge.source in ids and edge.target in ids]
    edges.sort(key=lambda edge: (edge.provenance.value != "static", edge_id(edge)))
    return ArchitectureSlice(nodes=nodes, edges=edges[:max_edges])


def runtime_measurements(
    overlay: RuntimeOverlay | None,
    graph: ArchitectureGraph | None,
    node_ids: set[str],
    *,
    focus: ArchitectureFocus | None = None,
) -> list[Measurement]:
    """Values recorded by real runs of the components in question, as evidence."""
    if overlay is None:
        return []
    labels = {node.id: node.label for node in graph.nodes} if graph is not None else {}
    found: list[Measurement] = []
    for node_id in sorted(node_ids):
        for value in overlay.measurements.get(node_id, []):
            found.append(_measurement(f"{labels.get(node_id, node_id)} · {value.name}", value))
    if focus is not None:
        for value in overlay.by_group.get(focus.node_id, []):
            found.append(_measurement(value.name, value))
    unique = {(m.name, m.value, m.unit): m for m in found}
    return list(unique.values())[:MAX_MEASUREMENTS]


def follow_up_questions(
    graph: ArchitectureGraph | None,
    selected: ArchitectureSlice,
    *,
    asked: str,
    limit: int = MAX_FOLLOW_UPS,
) -> list[str]:
    """Next questions from what the slice's components actually contain."""
    if graph is None:
        return []
    questions: list[str] = []
    for node in selected.nodes[:4]:
        for question in suggested_questions(graph, node):
            if question.strip().lower() != asked.strip().lower() and question not in questions:
                questions.append(question)
                break
    return questions[:limit]


def _focus_members(graph: ArchitectureGraph, node_id: str) -> list[str]:
    if graph.node(node_id) is not None:
        return [node_id]
    concept = graph.system_model.concept(node_id)
    if concept is not None:
        return list(concept.member_ids)
    subsystem = graph.subsystem(node_id)
    if subsystem is not None:
        return list(subsystem.component_ids)
    return []


def _overlap(node: ArchitectureNode, words: set[str]) -> int:
    named = set(_WORD.findall(f"{node.label} {node.technology or ''}".lower())) - _COMMON
    hits = 0
    for word in words:
        if word in named or any(len(word) > 3 and part.startswith(word) for part in named):
            hits += 1
    return hits


def _measurement(name: str, value: object) -> Measurement:
    source = str(getattr(value, "source", ""))
    return Measurement(
        name=name[:200],
        value=float(getattr(value, "value", 0.0)),
        unit=str(getattr(value, "unit", "")),
        source=(
            MeasurementSource.SPARK_EVENT_LOG
            if source.lower().startswith("spark")
            else MeasurementSource.CLI_OUTPUT
        ),
    )


def _cite(ref: CodeReference) -> str:
    return ref.path + (f":{ref.start_line}" if ref.start_line else "")
