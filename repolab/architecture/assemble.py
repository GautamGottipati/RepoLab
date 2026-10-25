"""Assembling the analyzers' findings into one architecture.

Deterministic throughout. What it adds:

* **Edges only where a file shows them.** A recorded usage -- a file that uses a
  database client, a config file named in source -- becomes an edge from the
  component that file implements. Entry points connect to what their own file
  or command runs. Nothing is joined to everything else.
* **Subsystems.** Each component is placed in the subsystem whose paths hold
  its code (:mod:`repolab.architecture.subsystems`).
* **A readable size.** Past :data:`MAX_NODES` within one subsystem, its most
  numerous category is folded into a group node that lists its members.
* **Flows, important files, the default view, and honesty about gaps.**
"""

from __future__ import annotations

import re
from collections import Counter

from repolab.architecture.analyzers.base import AnalysisContext, slug
from repolab.architecture.models import (
    MAX_NODES,
    ArchitectureEdge,
    ArchitectureFlow,
    ArchitectureGraph,
    ArchitectureNode,
    ArchitectureViewKind,
    Completeness,
    EdgeType,
    ImportantFile,
    NodeCategory,
    Provenance,
)
from repolab.architecture.subsystems import attach_components

#: Categories that are infrastructure the code *uses*, rather than code it runs.
INFRASTRUCTURE = {
    NodeCategory.DATABASE,
    NodeCategory.CACHE,
    NodeCategory.QUEUE,
    NodeCategory.STORAGE,
    NodeCategory.MODEL,
    NodeCategory.EXTERNAL_SERVICE,
}

#: Not flow: a static dependency says something is needed, not that it runs next.
NON_FLOW_EDGES = {EdgeType.DEPENDS_ON}

#: Where a flow may start.
FLOW_STARTS = {
    NodeCategory.ENTRY_POINT,
    NodeCategory.INPUT,
    NodeCategory.FRONTEND,
    NodeCategory.SCHEDULER,
    NodeCategory.CLI,
}

#: Important-file roles, in the order a newcomer should open them.
_ROLES: tuple[tuple[set[NodeCategory], str], ...] = (
    ({NodeCategory.ENTRY_POINT}, "Entry point"),
    ({NodeCategory.API, NodeCategory.SERVICE}, "Service / API definition"),
    ({NodeCategory.FRONTEND}, "Frontend"),
    ({NodeCategory.SCHEDULER, NodeCategory.TASK}, "Workflow definition"),
    (
        {NodeCategory.PIPELINE, NodeCategory.INPUT, NodeCategory.TRANSFORM, NodeCategory.OUTPUT},
        "Pipeline definition",
    ),
    ({NodeCategory.CLI, NodeCategory.COMMAND}, "Command-line interface"),
    ({NodeCategory.WORKER}, "Background processing"),
    (INFRASTRUCTURE, "Data access"),
    ({NodeCategory.LIBRARY, NodeCategory.CORE_ENGINE}, "Library API"),
    ({NodeCategory.CONFIGURATION}, "Configuration"),
)

MAX_IMPORTANT_FILES = 10
MAX_FLOWS = 3
_KEEP_PER_GROUP = 3


def assemble(context: AnalysisContext) -> ArchitectureGraph:
    graph = context.graph
    _link_usages(context)
    _link_entry_points(context)
    _prune_entry_points(graph)
    _link_surfaces(context)
    _disambiguate(graph)
    attach_components(graph, context.index)
    fit(graph)
    graph.primary_flows = find_flows(graph)
    graph.important_files = important_files(graph)
    graph.default_view = default_view(graph)
    _assess(context)
    return graph


# -- edges -----------------------------------------------------------------------


def _implementers(graph: ArchitectureGraph, path: str, exclude: str) -> list[ArchitectureNode]:
    """The most specific non-infrastructure components implemented in *path*."""
    found = [
        node
        for node in graph.nodes
        if path in node.paths
        and node.id != exclude
        and node.category not in INFRASTRUCTURE
        and node.category is not NodeCategory.CONFIGURATION
        and node.category is not NodeCategory.ENTRY_POINT
    ]
    # A step inside a pipeline is more specific than the pipeline itself.
    specific = [n for n in found if n.category not in {NodeCategory.PIPELINE, NodeCategory.CLI}]
    return specific or found


def _users_at(
    graph: ArchitectureGraph, path: str, line: int | None, target: str
) -> list[ArchitectureNode]:
    """The component in *path* that a use at *line* belongs to.

    A step whose line range contains the use owns it. A use outside every step --
    a config file opened in ``main()`` -- belongs to the enclosing component (the
    pipeline), not to whichever steps happen to share the file.
    """
    ranged = [
        n
        for n in graph.nodes
        if path in n.paths and isinstance(n.properties.get("line_start"), int) and n.id != target
    ]
    if line is not None and ranged:
        within = [
            n
            for n in ranged
            if n.properties["line_start"] <= line <= n.properties.get("line_end", line)  # type: ignore[operator]
        ]
        if within:
            return within
        container = [
            n
            for n in graph.nodes
            if path in n.paths
            and n.id != target
            and n not in ranged
            and n.category
            not in INFRASTRUCTURE | {NodeCategory.CONFIGURATION, NodeCategory.ENTRY_POINT}
        ]
        if container:
            return container
    return _implementers(graph, path, target)


def _link_usages(context: AnalysisContext) -> None:
    graph = context.graph
    for target, path, line, edge_type, user_id in context.usages:
        if graph.node(target) is None:
            continue
        known_user = graph.node(user_id) if user_id else None
        users = [known_user] if known_user is not None else _users_at(graph, path, line, target)
        for user in users[:2]:
            graph.add_edge(
                ArchitectureEdge(
                    source=user.id,
                    target=target,
                    type=edge_type,
                    evidence=[context.index.ref(path, line)],
                    confidence=0.75,
                )
            )


_PATH_IN_COMMAND = re.compile(r"([\w./-]+\.(?:py|js|ts|scala|java|go|rb|sh))|-m\s+([\w.]+)")


def _link_entry_points(context: AnalysisContext) -> None:
    graph = context.graph
    for entry_id in list(graph.entry_points):
        entry = graph.node(entry_id)
        if entry is None:
            continue
        paths = set(entry.paths)
        command = str(entry.properties.get("command", ""))
        for match in _PATH_IN_COMMAND.finditer(command):
            if match.group(1):
                paths.add(match.group(1).lstrip("./"))
            elif match.group(2):
                module = match.group(2).replace(".", "/")
                paths.update({f"{module}.py", f"{module}/__main__.py"})

        targets = [
            node
            for node in graph.nodes
            if node.id != entry.id
            and node.paths & paths
            and node.category
            not in INFRASTRUCTURE | {NodeCategory.CONFIGURATION, NodeCategory.ENTRY_POINT}
        ]
        # Start where the run starts: components nothing else in the file leads to.
        incoming = {edge.target for edge in graph.edges if edge.type not in NON_FLOW_EDGES}
        roots = [n for n in targets if n.id not in incoming] or targets
        for target in roots[:3]:
            graph.add_edge(
                ArchitectureEdge(
                    source=entry.id,
                    target=target.id,
                    type=EdgeType.CONTROL_FLOW,
                    evidence=list(entry.source_references[:1]),
                    description="starts",
                    confidence=0.8,
                )
            )


def _prune_entry_points(graph: ArchitectureGraph) -> None:
    """Drop entry points that lead nowhere, once at least one leads somewhere.

    A build script or an example command in a README is an entry point to
    *something*, but not to the system this graph describes; left in, it is a
    box with no arrows that a reader has to puzzle over.
    """
    linked = {e.source for e in graph.edges}
    leading = [e for e in graph.entry_points if e in linked]
    if leading:
        for entry in [e for e in graph.entry_points if e not in linked]:
            graph.remove_node(entry)


def _link_surfaces(context: AnalysisContext) -> None:
    manifest = context.manifest
    if manifest is None:
        return
    context.graph.interaction_surfaces = [s.type.value for s in manifest.interaction_surfaces]
    for surface in manifest.interaction_surfaces:
        paths = {ref.path for ref in surface.evidence}
        for node in context.graph.nodes:
            if node.surface is None and node.paths & paths:
                node.surface = surface.type.value


def _disambiguate(graph: ArchitectureGraph) -> None:
    """Two components with one name are told apart by the file that defines them."""
    counts = Counter(node.label for node in graph.nodes)
    for node in graph.nodes:
        if counts[node.label] > 1 and node.source_references:
            name = node.source_references[0].path.rsplit("/", 1)[-1]
            node.label = f"{node.label} ({name})"


# -- size ------------------------------------------------------------------------


def group_nodes(
    graph: ArchitectureGraph,
    members: list[ArchitectureNode],
    *,
    node_id: str,
    label: str,
    category: NodeCategory,
    description: str | None = None,
    provenance: Provenance = Provenance.STATIC,
) -> ArchitectureNode:
    """Fold *members* into one component, re-pointing their edges at it."""
    ids = {member.id for member in members}
    references = []
    for member in members:
        for ref in member.source_references:
            if ref not in references and len(references) < 6:
                references.append(ref)
    group = ArchitectureNode(
        id=node_id,
        label=label,
        category=category,
        description=description,
        source_references=references,
        children=[member.label for member in members],
        confidence=min((m.confidence for m in members), default=0.6),
        provenance=provenance,
        detected_because=f"groups {len(members)} related components: "
        + ", ".join(member.label for member in members[:5]),
        inferred_fields=["label", "description"] if provenance is Provenance.INFERRED else [],
        subsystem=homes.pop() if len(homes := {m.subsystem for m in members}) == 1 else None,
    )
    edges = graph.edges
    graph.nodes = [n for n in graph.nodes if n.id not in ids]
    graph.nodes.append(group)
    graph.edges = []
    for edge in edges:
        source = node_id if edge.source in ids else edge.source
        target = node_id if edge.target in ids else edge.target
        graph.add_edge(edge.model_copy(update={"source": source, "target": target}))
    graph.entry_points = [node_id if e in ids else e for e in graph.entry_points]
    graph.entry_points = list(dict.fromkeys(graph.entry_points))
    graph.primary_flows = []
    return group


def fit(graph: ArchitectureGraph) -> None:
    """Keep each subsystem's view readable by grouping its most repeated category.

    Grouping never crosses subsystems: a subsystem's drill-down shows its own
    components, and the repository map, not a grouped node, is what summarises
    the whole.
    """
    for home in [None, *(s.id for s in graph.subsystems)]:
        while True:
            members = [n for n in graph.nodes if n.subsystem == home]
            if len(members) <= MAX_NODES:
                break
            counts = Counter(n.category for n in members if not n.children)
            if not counts:
                break
            category, count = counts.most_common(1)[0]
            if count <= _KEEP_PER_GROUP:
                break
            folded = [n for n in members if n.category is category and not n.children][
                _KEEP_PER_GROUP:
            ]
            group_nodes(
                graph,
                folded,
                node_id=f"group:{slug(home or 'top')}:{category.value}",
                label=f"{len(folded)} more {category.value.replace('_', ' ')} components",
                category=category,
            )
    for subsystem in graph.subsystems:
        subsystem.component_ids = [n.id for n in graph.nodes if n.subsystem == subsystem.id]


# -- flows -----------------------------------------------------------------------


def find_flows(graph: ArchitectureGraph, *, limit: int = MAX_FLOWS) -> list[ArchitectureFlow]:
    """The longest paths from where execution or data starts. Branches stay branches."""
    adjacency: dict[str, list[str]] = {}
    incoming: set[str] = set()
    library = {NodeCategory.LIBRARY, NodeCategory.CORE_ENGINE}
    for edge in graph.edges:
        source = graph.node(edge.source)
        # In a library, "the public API uses the parser, which uses the lexer" is
        # the flow; elsewhere a static dependency is not something that runs next.
        if edge.type in NON_FLOW_EDGES and not (source is not None and source.category in library):
            continue
        adjacency.setdefault(edge.source, []).append(edge.target)
        incoming.add(edge.target)

    starts = [
        n.id
        for n in graph.nodes
        if (n.category in FLOW_STARTS or n.category is NodeCategory.LIBRARY) and n.id in adjacency
    ]
    starts += [n for n in adjacency if n not in incoming and n not in starts]

    def longest(node: str, seen: tuple[str, ...]) -> list[str]:
        best = [node]
        for nxt in adjacency.get(node, []):
            if nxt in seen:
                continue
            candidate = [node, *longest(nxt, (*seen, nxt))]
            if len(candidate) > len(best):
                best = candidate
        return best

    paths = sorted((longest(s, (s,)) for s in starts), key=len, reverse=True)
    flows: list[ArchitectureFlow] = []
    covered: list[set[str]] = []
    for path in paths:
        if len(path) < 2 or any(set(path) <= c for c in covered):
            continue
        covered.append(set(path))
        first, last = graph.node(path[0]), graph.node(path[-1])
        flows.append(
            ArchitectureFlow(
                name=f"{first.label if first else path[0]} → {last.label if last else path[-1]}",
                node_ids=path,
            )
        )
        if len(flows) >= limit:
            break
    return flows


# -- presentation ----------------------------------------------------------------


def important_files(graph: ArchitectureGraph) -> list[ImportantFile]:
    by_path: dict[str, ImportantFile] = {}
    rank: dict[str, int] = {}
    for node in graph.nodes:
        if node.properties.get("actor"):
            # A user's browser is part of the flow, not a file to read.
            continue
        role_index = next(
            (i for i, (cats, _) in enumerate(_ROLES) if node.category in cats), len(_ROLES)
        )
        role = _ROLES[role_index][1] if role_index < len(_ROLES) else "Component"
        for path in node.paths:
            entry = by_path.setdefault(path, ImportantFile(path=path, role=role))
            if node.id not in entry.node_ids:
                entry.node_ids.append(node.id)
            if role_index < rank.get(path, len(_ROLES) + 1):
                rank[path] = role_index
                entry.role = role
    ordered = sorted(
        by_path.values(), key=lambda f: (rank.get(f.path, 99), -len(f.node_ids), f.path)
    )
    return ordered[:MAX_IMPORTANT_FILES]


def default_view(graph: ArchitectureGraph) -> ArchitectureViewKind:
    categories = {n.category for n in graph.nodes}
    families = set()
    if categories & {NodeCategory.PIPELINE, NodeCategory.TRANSFORM} and not categories & {
        NodeCategory.SCHEDULER,
        NodeCategory.TASK,
    }:
        families.add("data")
    if categories & {NodeCategory.SCHEDULER, NodeCategory.TASK}:
        families.add("workflow")
    if NodeCategory.FRONTEND in categories:
        families.add("web")
    if NodeCategory.API in categories or any(n.subtype == "http_server" for n in graph.nodes):
        families.add("api")
    if categories & {NodeCategory.CLI, NodeCategory.COMMAND}:
        families.add("cli")
    if NodeCategory.WORKER in categories or any(n.subtype == "daemon" for n in graph.nodes):
        families.add("service")
    if NodeCategory.LIBRARY in categories:
        families.add("library")

    if families == {"web", "api"} or families == {"web"}:
        return ArchitectureViewKind.REQUEST_FLOW
    if len(families) >= 2:
        return ArchitectureViewKind.SYSTEM_OVERVIEW
    return {
        "data": ArchitectureViewKind.DATA_FLOW,
        "workflow": ArchitectureViewKind.WORKFLOW,
        "api": ArchitectureViewKind.SERVICE_FLOW,
        "cli": ArchitectureViewKind.COMMAND_FLOW,
        "service": ArchitectureViewKind.SERVICE_FLOW,
        "library": ArchitectureViewKind.COMPONENT_FLOW,
    }.get(next(iter(families), ""), ArchitectureViewKind.SYSTEM_OVERVIEW)


def _assess(context: AnalysisContext) -> None:
    """Say plainly what could not be determined."""
    graph = context.graph
    meaningful = [
        n
        for n in graph.nodes
        if n.category not in {NodeCategory.ENTRY_POINT, NodeCategory.CONFIGURATION}
    ]
    connected = {e.source for e in graph.edges} | {e.target for e in graph.edges}
    gaps: list[str] = []

    if not meaningful:
        gaps.append(
            "RepoLab found no runtime components it could tie to source in the files it read."
        )
    starters = {
        NodeCategory.SERVICE,
        NodeCategory.SCHEDULER,
        NodeCategory.FRONTEND,
        NodeCategory.CLI,
        NodeCategory.WORKER,
        NodeCategory.LIBRARY,
    }
    if not graph.entry_points and not any(n.category in starters for n in graph.nodes):
        gaps.append("No entry point was found, so where execution starts is not known.")
    isolated = [n for n in meaningful if n.id not in connected]
    for node in isolated[:3]:
        gaps.append(
            f"How {node.label} connects to the rest of the system "
            "could not be determined statically."
        )
    if len(isolated) > 3:
        gaps.append(
            f"{len(isolated) - 3} more components have no relationship RepoLab could evidence."
        )
    if meaningful and not graph.primary_flows:
        gaps.append("No end-to-end flow could be traced from the evidence.")
    if context.index.truncated:
        gaps.append(
            "Only the highest-priority source files were read; "
            "parts of a large repository may be missing."
        )

    graph.gaps = gaps
    graph.completeness = (
        Completeness.PARTIAL
        if len(meaningful) < 2 or not graph.primary_flows or len(isolated) > len(meaningful) // 2
        else Completeness.COMPLETE
    )
