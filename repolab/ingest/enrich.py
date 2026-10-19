"""Optional LLM enrichment of an ingestion.

The repository is never sent to a model. Two strictly bounded things happen:

* **Nano** summarises a handful of *selected* important files -- at most
  ``max_llm_files``, each truncated to ``max_llm_file_bytes``, and never more
  than ``max_llm_total_bytes`` in total.
* **Super** receives the already-built deterministic graph plus those summaries
  and proposes labels, groupings and extra relationships. Anything it proposes
  that cannot be tied to a file already in the graph is dropped, because an
  architecture claim without a file reference is not evidence.

With no router configured both steps are skipped and the reason is recorded.
Ingestion still produces a full manifest and graph without any model.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from repolab.ingest.content import ReadBudget, read_text
from repolab.ingest.detectors import DetectionContext, DetectionOutcome
from repolab.ingest.tree import FileEntry
from repolab.llm import LLMError, ModelRouter, Purpose
from repolab.manifest.architecture import (
    ArchitectureEdge,
    ArchitectureGraph,
    EdgeKind,
    GraphSource,
)

log = structlog.get_logger(__name__)

#: Filenames that are worth a model's attention, most important first.
PRIORITY_FILENAMES: tuple[str, ...] = (
    "readme.md",
    "pyproject.toml",
    "package.json",
    "docker-compose.yml",
    "dockerfile",
    "makefile",
)


class FileSummary(BaseModel):
    """Nano's summary of one file."""

    purpose: str = Field(description="What this file is for, in one sentence")
    key_elements: list[str] = Field(
        default_factory=list, description="Up to three notable functions, classes or settings"
    )


class ProposedEdge(BaseModel):
    """A relationship Super proposes between two existing nodes."""

    source: str = Field(description="Exact id of an existing node")
    target: str = Field(description="Exact id of an existing node")
    kind: str = Field(description="One of: depends_on, invokes, configures, reads, writes, exposes")
    description: str = Field(description="Why these are related, in one short phrase")


class NodeLabel(BaseModel):
    """A grouping label Super proposes for an existing node."""

    node_id: str = Field(description="Exact id of an existing node")
    group: str = Field(description="Short architectural grouping, e.g. 'ingestion', 'serving'")


class ArchitectureEnrichment(BaseModel):
    """Super's proposed additions to the deterministic graph."""

    summary: str = Field(description="Two sentences describing the overall architecture")
    labels: list[NodeLabel] = Field(default_factory=list)
    edges: list[ProposedEdge] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    """What enrichment produced, and what it chose not to trust."""

    summaries: dict[str, FileSummary] = Field(default_factory=dict)
    architecture_summary: str | None = None
    notes: list[str] = Field(default_factory=list)
    dropped_edges: int = 0
    dropped_labels: int = 0

    @property
    def ran(self) -> bool:
        return bool(self.summaries) or self.architecture_summary is not None


def select_files_for_summary(
    outcome: DetectionOutcome, context: DetectionContext, budget: ReadBudget
) -> list[FileEntry]:
    """Choose the few files worth summarising.

    Selection is evidence-driven: files that detection already found interesting,
    plus the conventional top-level documents, capped hard.
    """
    limit = budget.limits.max_llm_files
    wanted: list[str] = []

    for kind in ("spark_session", "spark_config", "route_decorator", "entrypoint"):
        for finding in outcome.run.of_kind(kind):
            for ref in finding.references:
                if ref.path not in wanted:
                    wanted.append(ref.path)

    for name in PRIORITY_FILENAMES:
        for entry in context.tree.by_name(name):
            if entry.path not in wanted:
                wanted.append(entry.path)

    by_path = {entry.path: entry for entry in context.tree.files}
    selected: list[FileEntry] = []
    for path in wanted:
        entry = by_path.get(path)
        if entry is not None:
            selected.append(entry)
        if len(selected) >= limit:
            break
    return selected


async def summarise_files(
    router: ModelRouter,
    context: DetectionContext,
    entries: list[FileEntry],
    budget: ReadBudget,
) -> tuple[dict[str, FileSummary], list[str]]:
    """Summarise *entries* with Nano, respecting the total byte budget."""
    summaries: dict[str, FileSummary] = {}
    notes: list[str] = []
    spent = 0

    for entry in entries:
        if spent >= budget.limits.max_llm_total_bytes:
            notes.append("file summaries stopped at max_llm_total_bytes")
            budget.report.record("max_llm_total_bytes")
            break

        text = read_text(context.path_for(entry.path), budget=budget)
        if not text:
            continue

        excerpt = text[: budget.limits.max_llm_file_bytes]
        if len(text) > len(excerpt):
            budget.report.record("max_llm_file_bytes")
        spent += len(excerpt)

        try:
            completion = await router.nano(
                purpose=Purpose.FILE_SUMMARY,
                schema=FileSummary,
                system="You summarise one source file. Be factual and brief.",
                prompt=f"File: {entry.path}\n\n{excerpt}",
                max_tokens=512,
            )
        except LLMError as exc:
            notes.append(f"summary failed for {entry.path}: {type(exc).__name__}")
            continue
        summaries[entry.path] = completion.value

    return summaries, notes


async def enrich_architecture(
    router: ModelRouter,
    graph: ArchitectureGraph,
    summaries: dict[str, FileSummary],
) -> tuple[ArchitectureEnrichment | None, str | None]:
    """Ask Super to label and relate the existing nodes."""
    if not graph.nodes:
        return None, "architecture enrichment skipped: graph is empty"

    node_lines = "\n".join(
        f"- {node.id} ({node.kind.value}): {node.name}"
        f"{' -- ' + node.description if node.description else ''}"
        for node in graph.nodes
    )
    summary_lines = "\n".join(f"- {path}: {summary.purpose}" for path, summary in summaries.items())
    valid_kinds = ", ".join(kind.value for kind in EdgeKind)

    prompt = (
        "These architecture nodes were detected deterministically from a repository.\n\n"
        f"Nodes:\n{node_lines}\n\n"
        f"File summaries:\n{summary_lines or '(none)'}\n\n"
        "Group related nodes and propose relationships between them. "
        f"Use only these edge kinds: {valid_kinds}. "
        "Reference nodes only by the exact ids listed above. Invent nothing."
    )

    try:
        completion = await router.super(
            purpose=Purpose.REPO_ARCHITECTURE,
            schema=ArchitectureEnrichment,
            system=(
                "You describe software architecture strictly from the evidence given. "
                "Never invent components or files."
            ),
            prompt=prompt,
            max_tokens=2048,
        )
    except LLMError as exc:
        return None, f"architecture enrichment failed: {type(exc).__name__}"
    return completion.value, None


def apply_enrichment(
    graph: ArchitectureGraph, enrichment: ArchitectureEnrichment
) -> tuple[int, int]:
    """Apply *enrichment* to *graph*, dropping anything ungrounded.

    Returns ``(dropped_edges, dropped_labels)``.
    """
    known = {node.id for node in graph.nodes}
    dropped_edges = 0
    dropped_labels = 0

    for label in enrichment.labels:
        node = graph.node(label.node_id)
        if node is None:
            dropped_labels += 1
            continue
        node.group = label.group

    for proposed in enrichment.edges:
        if proposed.source not in known or proposed.target not in known:
            dropped_edges += 1
            continue
        try:
            kind = EdgeKind(proposed.kind)
        except ValueError:
            dropped_edges += 1
            continue

        source = graph.node(proposed.source)
        target = graph.node(proposed.target)
        # The edge inherits its endpoints' references: the claim is only as
        # grounded as the files that justified those nodes.
        references = [
            *(source.file_references[:1] if source else []),
            *(target.file_references[:1] if target else []),
        ]
        if not references:
            dropped_edges += 1
            continue

        added = graph.add_edge(
            ArchitectureEdge(
                source=proposed.source,
                target=proposed.target,
                kind=kind,
                description=proposed.description,
                file_references=references,
            )
        )
        if not added:
            dropped_edges += 1

    if enrichment.labels or enrichment.edges:
        graph.generated_by = GraphSource.SUPER_ENRICHED
    return dropped_edges, dropped_labels


async def enrich(
    router: ModelRouter | None,
    context: DetectionContext,
    outcome: DetectionOutcome,
    graph: ArchitectureGraph,
    budget: ReadBudget,
) -> EnrichmentResult:
    """Run bounded enrichment, or record why it was skipped."""
    result = EnrichmentResult()
    if router is None:
        result.notes.append(
            "LLM enrichment skipped: no model router configured "
            "(the manifest and architecture below are fully deterministic)"
        )
        return result

    selected = select_files_for_summary(outcome, context, budget)
    result.summaries, notes = await summarise_files(router, context, selected, budget)
    result.notes.extend(notes)

    enrichment, error = await enrich_architecture(router, graph, result.summaries)
    if error:
        result.notes.append(error)
        return result
    if enrichment is not None:
        result.architecture_summary = enrichment.summary
        dropped_edges, dropped_labels = apply_enrichment(graph, enrichment)
        result.dropped_edges = dropped_edges
        result.dropped_labels = dropped_labels
        if dropped_edges or dropped_labels:
            result.notes.append(
                f"dropped {dropped_edges} ungrounded edge(s) and "
                f"{dropped_labels} unknown label(s) proposed by the model"
            )

    log.info(
        "ingest.enrichment_finished",
        summarised=len(result.summaries),
        dropped_edges=result.dropped_edges,
    )
    return result
