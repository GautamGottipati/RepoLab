"""The Nemotron pass: names, purpose, grouping, flows and a summary.

Static analysis knows *that* ``transform_data`` joins two dataframes; it cannot
say what the step is for, or that three handler files are one "Order Service".
Super is asked exactly that, once per investigation, with bounded input:

    the deterministic graph   ids, categories, evidence excerpts
    the README's opening      the project's own description of itself
    a few file excerpts       from the important files, truncated

It may rename and describe existing components, group existing components,
propose relationships *between existing components* -- each citing a
``path:line`` that really exists -- name flows through existing components, and
summarise. It may not create files, functions, components or numbers.
:func:`apply_synthesis` enforces that, counting everything it refuses, and marks
everything it accepts as inferred.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, Field

from repolab.architecture.assemble import find_flows, group_nodes
from repolab.architecture.models import (
    ArchitectureEdge,
    ArchitectureFlow,
    ArchitectureGraph,
    EdgeType,
    NodeCategory,
    Provenance,
)
from repolab.architecture.source import SourceIndex
from repolab.llm import LLMError, Purpose
from repolab.manifest.architecture import GraphSource

log = structlog.get_logger(__name__)

SYNTHESIS_MAX_TOKENS = 8192
SYNTHESIS_TIMEOUT_SECONDS = 120.0
README_CHARS = 2000

#: Below this many components, a proposed group is refused.
MIN_NODES_TO_GROUP = 12

#: Edges that make two components consecutive steps of one flow.
_FLOW_TYPES = {EdgeType.DATA_FLOW, EdgeType.CONTROL_FLOW, EdgeType.TRIGGERS}
EXCERPT_CHARS = 1200
MAX_EXCERPT_FILES = 6

#: A measurement-shaped number. Static analysis measured nothing, so a model
#: writing "takes 200 ms" is inventing it.
_MEASUREMENT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|sec|seconds?|minutes?|MB|GB|KB|MiB|GiB|%|rps|qps|req/s|rows/s)\b",
    re.I,
)

SYSTEM_PROMPT = (
    "You describe the architecture of a software repository for a developer who "
    "has never seen it. You are given components RepoLab detected statically, "
    "each with the source lines that justify it.\n"
    "\n"
    "Explain how the software behaves, not how files import each other. You may:\n"
    "- give an existing component a clearer name, a one-sentence description, and "
    "its runtime significance;\n"
    "- only when there are more than 12 components, group existing components "
    "that form one logical component -- never consecutive steps of a flow;\n"
    "- propose relationships between existing components, each citing one "
    "'path:line' copied from the evidence;\n"
    "- name the important flows through existing components;\n"
    "- write a two-to-four sentence summary.\n"
    "\n"
    "Use only component ids that appear in the input. Never invent a file, "
    "function, component, timing, size or count. If the evidence does not show "
    "something, leave it out."
)


class ComponentRefinement(BaseModel):
    node_id: str = Field(description="An existing component id, exactly")
    label: str = Field(default="", description="A clearer name, or empty to keep it")
    description: str = Field(default="", description="What it does, one sentence")
    runtime_significance: str = Field(default="", description="Its role when the software runs")


class ComponentGroup(BaseModel):
    label: str
    category: str = Field(description="One of the categories used in the input")
    member_ids: list[str] = Field(description="Two or more existing component ids")
    description: str = ""


class ProposedRelationship(BaseModel):
    source: str
    target: str
    type: str = Field(description="One of the edge types listed in the input")
    description: str = ""
    evidence: str = Field(description="'path:line' copied from the evidence")


class ProposedFlow(BaseModel):
    name: str
    node_ids: list[str] = Field(description="Existing component ids, in order")
    description: str = ""


class ArchitectureSynthesis(BaseModel):
    summary: str
    components: list[ComponentRefinement] = Field(default_factory=list)
    groups: list[ComponentGroup] = Field(default_factory=list)
    relationships: list[ProposedRelationship] = Field(default_factory=list)
    flows: list[ProposedFlow] = Field(default_factory=list)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "component"


class SynthesisRouter(Protocol):
    async def super(self, **kwargs: Any) -> Any: ...


async def synthesise(
    graph: ArchitectureGraph,
    index: SourceIndex,
    router: SynthesisRouter | None,
    *,
    summaries: dict[str, str] | None = None,
) -> ArchitectureGraph:
    """Refine *graph* with Super, or return it unchanged if no model can be asked."""
    if router is None or not graph.nodes:
        return graph
    try:
        completion = await router.super(
            purpose=Purpose.REPO_ARCHITECTURE,
            schema=ArchitectureSynthesis,
            system=SYSTEM_PROMPT,
            prompt=build_prompt(graph, index, summaries=summaries),
            max_tokens=SYNTHESIS_MAX_TOKENS,
            timeout_seconds=SYNTHESIS_TIMEOUT_SECONDS,
        )
    except LLMError as exc:
        log.warning("architecture.synthesis_failed", error=str(exc))
        graph.gaps.append(
            "Nemotron could not be reached, so component names and the summary are RepoLab's own."
        )
        return graph
    return apply_synthesis(graph, completion.value, index)


def build_prompt(
    graph: ArchitectureGraph, index: SourceIndex, *, summaries: dict[str, str] | None = None
) -> str:
    lines = ["COMPONENTS (id | category | label | evidence)"]
    for node in graph.nodes:
        refs = "; ".join(
            f"{r.path}:{r.start_line}"
            + (f" `{(r.excerpt or '').strip()[:160]}`" if r.excerpt else "")
            for r in node.source_references[:3]
        )
        props = ", ".join(f"{k}={v}" for k, v in list(node.properties.items())[:4])
        lines.append(
            f"- {node.id} | {node.category.value} | {node.label} | {refs}"
            + (f" | {props}" if props else "")
        )
    lines += ["", "RELATIONSHIPS ALREADY DETECTED"]
    lines += [f"- {e.source} --{e.type.value}--> {e.target}" for e in graph.edges] or ["- (none)"]
    lines += ["", f"CATEGORIES: {', '.join(c.value for c in NodeCategory)}"]
    lines += [f"EDGE TYPES: {', '.join(t.value for t in EdgeType)}"]

    readme = next(
        (index.text(p) for p in ("README.md", "readme.md", "README.rst") if index.text(p)), None
    )
    if readme:
        lines += ["", "README (opening)", readme[:README_CHARS]]

    if summaries:
        lines += ["", "FILE PURPOSES (context only, not evidence)"]
        lines += [f"- {path}: {purpose[:200]}" for path, purpose in list(summaries.items())[:8]]

    for item in graph.important_files[:MAX_EXCERPT_FILES]:
        text = index.text(item.path)
        if text:
            lines += [
                "",
                f"FILE {item.path} (first {EXCERPT_CHARS} characters)",
                text[:EXCERPT_CHARS],
            ]
    lines += ["", "Describe this architecture."]
    return "\n".join(lines)


def apply_synthesis(
    graph: ArchitectureGraph, synthesis: ArchitectureSynthesis, index: SourceIndex
) -> ArchitectureGraph:
    """Merge what the model proposed, keeping only what the evidence allows."""
    shown = {
        f"{ref.path}:{ref.start_line}": ref
        for node in graph.nodes
        for ref in node.source_references[:3]
        if ref.start_line is not None
    }
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    accepted = False
    for refinement in synthesis.components:
        node = graph.node(refinement.node_id)
        if node is None:
            drop("unknown component")
            continue
        for field_name in ("label", "description", "runtime_significance"):
            value = getattr(refinement, field_name).strip()
            if not value:
                continue
            if _MEASUREMENT.search(value):
                drop("invented measurement")
                continue
            setattr(node, field_name, value[:300])
            if field_name not in node.inferred_fields:
                node.inferred_fields.append(field_name)
            accepted = True

    for proposal in synthesis.relationships:
        if graph.node(proposal.source) is None or graph.node(proposal.target) is None:
            drop("relationship to an unknown component")
            continue
        try:
            edge_type = EdgeType(proposal.type)
        except ValueError:
            drop("unknown relationship type")
            continue
        # The model may only copy a citation RepoLab showed it; a line number
        # of its own is never taken as evidence.
        cited = shown.get(proposal.evidence.strip().strip("`'\""))
        if cited is None:
            drop("relationship citing a line RepoLab did not show")
            continue
        if graph.add_edge(
            ArchitectureEdge(
                source=proposal.source,
                target=proposal.target,
                type=edge_type,
                description=proposal.description[:200] or None,
                provenance=Provenance.INFERRED,
                evidence=[cited],
                confidence=0.55,
            )
        ):
            accepted = True

    flows: list[ArchitectureFlow] = []
    for proposal in synthesis.flows:
        ids = list(dict.fromkeys(proposal.node_ids))
        if len(ids) < 2 or any(graph.node(i) is None for i in ids):
            drop("flow through unknown components")
            continue
        linked = {(e.source, e.target) for e in graph.edges} | {
            (e.target, e.source) for e in graph.edges
        }
        if any((a, b) not in linked for a, b in pairwise(ids)):
            # A flow is a path through relationships the graph actually has.
            drop("flow between unrelated components")
            continue
        flows.append(
            ArchitectureFlow(
                name=proposal.name[:80],
                node_ids=ids,
                description=proposal.description[:300] or None,
                provenance=Provenance.INFERRED,
            )
        )
    if flows:
        merged: list[ArchitectureFlow] = []
        for flow in [*flows, *graph.primary_flows]:
            if all(flow.node_ids != kept.node_ids for kept in merged):
                merged.append(flow)
        graph.primary_flows = merged[:3]
        accepted = True

    # Grouping last: it renames ids, and everything above refers to the originals.
    for group in synthesis.groups:
        members = [graph.node(i) for i in dict.fromkeys(group.member_ids)]
        if len(members) < 2 or any(m is None for m in members):
            drop("group of unknown components")
            continue
        present = [m for m in members if m is not None]
        if len({m.subsystem for m in present}) > 1:
            # Subsystems are the repository's own boundaries; a group never spans them.
            drop("grouping across subsystems")
            continue
        if len(graph.nodes) <= MIN_NODES_TO_GROUP:
            # A small graph is already readable; folding it only hides structure.
            drop("grouping a graph that is already small")
            continue
        ids = {m.id for m in present}
        if any(e.source in ids and e.target in ids and e.type in _FLOW_TYPES for e in graph.edges):
            # Consecutive steps of a flow are the flow; grouping them erases it.
            drop("grouping consecutive steps of a flow")
            continue
        try:
            category = NodeCategory(group.category)
        except ValueError:
            category = present[0].category
        if _MEASUREMENT.search(group.label + group.description):
            drop("invented measurement")
            continue
        flows_before = graph.primary_flows
        group_nodes(
            graph,
            present,
            node_id=f"group:{_slug(group.label)}",
            label=group.label[:80],
            category=category,
            description=group.description[:300] or None,
            provenance=Provenance.INFERRED,
        )
        known = {n.id for n in graph.nodes}
        graph.primary_flows = [f for f in flows_before if set(f.node_ids) <= known]
        accepted = True
    if not graph.primary_flows:
        graph.primary_flows = find_flows(graph)

    summary = synthesis.summary.strip()
    if summary and not _MEASUREMENT.search(summary):
        graph.summary = summary[:800]
        graph.summary_provenance = Provenance.INFERRED
        accepted = True
    elif summary:
        drop("invented measurement")

    if accepted:
        graph.generated_by = GraphSource.SUPER_ENRICHED
    for reason, count in dropped.items():
        graph.dropped[reason] = graph.dropped.get(reason, 0) + count
    return graph
