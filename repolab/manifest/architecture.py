"""Architecture representation: nodes, edges and their file references.

The graph is built deterministically from detection findings, so every node
arrives already carrying the files that justify it. Super may later label,
group and relate nodes, but anything it proposes that cannot be tied to a real
file is dropped: an architecture claim without a file reference is not evidence.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from repolab.evidence.models import CodeReference


class NodeKind(StrEnum):
    """What a node represents."""

    ENTRYPOINT = "entrypoint"
    MODULE = "module"
    SERVICE = "service"
    CONFIG = "config"
    DATASTORE = "datastore"
    EXTERNAL = "external"


class EdgeKind(StrEnum):
    """How two nodes relate."""

    DEPENDS_ON = "depends_on"
    INVOKES = "invokes"
    CONFIGURES = "configures"
    READS = "reads"
    WRITES = "writes"
    EXPOSES = "exposes"


class GraphSource(StrEnum):
    """How much of the graph was model-generated."""

    DETERMINISTIC = "deterministic"
    SUPER_ENRICHED = "super_enriched"


class ArchitectureNode(BaseModel):
    """One component of the system."""

    id: str
    kind: NodeKind
    name: str
    description: str | None = None
    file_references: list[CodeReference] = Field(default_factory=list)
    group: str | None = None
    """Optional grouping label, which Super may supply."""

    @property
    def is_supported(self) -> bool:
        return bool(self.file_references)


class ArchitectureEdge(BaseModel):
    """A relationship between two nodes."""

    source: str
    target: str
    kind: EdgeKind
    description: str | None = None
    file_references: list[CodeReference] = Field(default_factory=list)


class ArchitectureGraph(BaseModel):
    """The architecture of a repository, with evidence attached."""

    nodes: list[ArchitectureNode] = Field(default_factory=list)
    edges: list[ArchitectureEdge] = Field(default_factory=list)
    generated_by: GraphSource = GraphSource.DETERMINISTIC

    @model_validator(mode="after")
    def _edges_reference_known_nodes(self) -> ArchitectureGraph:
        """A dangling edge would render as a broken diagram, so reject it."""
        known = {node.id for node in self.nodes}
        for edge in self.edges:
            missing = {edge.source, edge.target} - known
            if missing:
                raise ValueError(
                    f"edge {edge.source}->{edge.target} references unknown node(s): "
                    f"{sorted(missing)}"
                )
        return self

    @property
    def unsupported_nodes(self) -> list[ArchitectureNode]:
        """Nodes carrying no file reference."""
        return [n for n in self.nodes if not n.is_supported]

    def node(self, node_id: str) -> ArchitectureNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def add_node(self, node: ArchitectureNode) -> ArchitectureNode:
        """Add *node*, merging references if the id already exists."""
        existing = self.node(node.id)
        if existing is None:
            self.nodes.append(node)
            return node
        existing.file_references.extend(
            ref for ref in node.file_references if ref not in existing.file_references
        )
        existing.description = existing.description or node.description
        return existing

    def add_edge(self, edge: ArchitectureEdge) -> bool:
        """Add *edge* if both endpoints exist and it is not already present.

        Returns whether it was added, so callers can report dropped claims.
        """
        known = {node.id for node in self.nodes}
        if edge.source not in known or edge.target not in known:
            return False
        for existing in self.edges:
            if (existing.source, existing.target, existing.kind) == (
                edge.source,
                edge.target,
                edge.kind,
            ):
                return False
        self.edges.append(edge)
        return True
