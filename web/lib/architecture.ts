/**
 * Architecture helpers: layout, labels, links and the context sent to Ask.
 *
 * Pure functions, so the screen's behaviour can be tested without a browser.
 */

import type {
  ArchitectureEdge,
  ArchitectureFocus,
  ArchitectureGraph,
  ArchitectureNode,
  ArchitectureViewKind,
  CodeReference,
  EvidenceKind,
  NodeCategory,
  RepositoryKind,
  Subsystem,
  SystemConcept,
  Walkthrough,
} from "./types";

/** How a reader names each category. */
export const CATEGORY_LABEL: Record<NodeCategory, string> = {
  entry_point: "entry point",
  input: "input",
  output: "output",
  frontend: "frontend",
  api: "API",
  service: "service",
  worker: "worker",
  database: "database",
  cache: "cache",
  queue: "queue",
  storage: "storage",
  pipeline: "pipeline",
  transform: "transform",
  processor: "processor",
  scheduler: "scheduler",
  task: "task",
  cli: "CLI",
  command: "command",
  model: "model",
  inference: "inference",
  external_service: "external service",
  library: "library",
  core_engine: "core",
  configuration: "configuration",
  other: "component",
};

/** Colour families, so a reader sees roles before reading labels. */
export const CATEGORY_TONE: Record<NodeCategory, string> = {
  entry_point: "slate",
  input: "teal",
  output: "teal",
  frontend: "violet",
  api: "sky",
  service: "sky",
  worker: "sky",
  database: "amber",
  cache: "amber",
  queue: "amber",
  storage: "amber",
  pipeline: "emerald",
  transform: "emerald",
  processor: "emerald",
  scheduler: "rose",
  task: "rose",
  cli: "indigo",
  command: "indigo",
  model: "fuchsia",
  inference: "fuchsia",
  external_service: "slate",
  library: "indigo",
  core_engine: "indigo",
  configuration: "slate",
  other: "slate",
};

export const VIEW_LABEL: Record<ArchitectureViewKind, string> = {
  data_flow: "Data flow",
  request_flow: "Request flow",
  service_flow: "Service flow",
  workflow: "Workflow",
  command_flow: "Command flow",
  component_flow: "Component flow",
  system_overview: "System overview",
};

/** Edges that mean "this runs before that" rather than "this needs that". */
const ORDERING = (edge: ArchitectureEdge) => edge.type !== "depends_on";

/** Where a category sits when nothing orders it: inputs left, stores right. */
const NATURAL_RANK: Partial<Record<NodeCategory, number>> = {
  entry_point: 0,
  input: 0,
  scheduler: 0,
  frontend: 1,
  cli: 1,
  library: 1,
  configuration: 0,
  database: 9,
  cache: 9,
  queue: 9,
  storage: 9,
  model: 9,
  external_service: 9,
};

export type Placement = { id: string; rank: number; row: number };

/**
 * Layered layout: a node's column is the longest chain of ordering edges
 * leading to it, so a flow reads left to right. Cycle-safe. Nodes nothing
 * orders take their category's natural column.
 */
export function layout(graph: ArchitectureGraph): Map<string, Placement> {
  const ids = graph.nodes.map((n) => n.id);
  const incoming = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const edge of graph.edges) {
    if (ORDERING(edge) && incoming.has(edge.target) && incoming.has(edge.source)) {
      incoming.get(edge.target)!.push(edge.source);
    }
  }

  const memo = new Map<string, number>();
  const visiting = new Set<string>();
  const rankOf = (id: string): number => {
    const known = memo.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const parents = incoming.get(id) ?? [];
    const rank = parents.length ? Math.max(...parents.map(rankOf)) + 1 : 0;
    visiting.delete(id);
    memo.set(id, rank);
    return rank;
  };

  const connected = new Set(graph.edges.flatMap((e) => [e.source, e.target]));
  const maxRank = Math.max(0, ...ids.map(rankOf));
  const raw = new Map<string, number>();
  for (const node of graph.nodes) {
    let rank = rankOf(node.id);
    const natural = NATURAL_RANK[node.category];
    // Stores sit at the far right of whatever uses them.
    if (natural === 9) rank = Math.max(rank, maxRank);
    else if (!connected.has(node.id) && natural !== undefined) rank = natural;
    raw.set(node.id, rank);
  }

  // Compact: no empty columns.
  const used = [...new Set(raw.values())].sort((a, b) => a - b);
  const column = new Map(used.map((rank, index) => [rank, index]));

  // Within a column, order by first appearance in a primary flow, then by id.
  const flowOrder = new Map<string, number>();
  graph.primary_flows.forEach((flow, f) =>
    flow.node_ids.forEach((id, i) => {
      if (!flowOrder.has(id)) flowOrder.set(id, f * 100 + i);
    }),
  );
  const byColumn = new Map<number, ArchitectureNode[]>();
  for (const node of graph.nodes) {
    const c = column.get(raw.get(node.id)!)!;
    byColumn.set(c, [...(byColumn.get(c) ?? []), node]);
  }

  const placements = new Map<string, Placement>();
  for (const [c, nodes] of byColumn) {
    nodes
      .sort(
        (a, b) =>
          (flowOrder.get(a.id) ?? 1e6) - (flowOrder.get(b.id) ?? 1e6) ||
          a.id.localeCompare(b.id),
      )
      .forEach((node, row) => placements.set(node.id, { id: node.id, rank: c, row }));
  }
  return placements;
}

/** Edges on any primary flow, for emphasis. */
export function flowEdges(graph: ArchitectureGraph): Set<string> {
  const keys = new Set<string>();
  for (const flow of graph.primary_flows) {
    for (let i = 0; i + 1 < flow.node_ids.length; i++) {
      keys.add(`${flow.node_ids[i]}->${flow.node_ids[i + 1]}`);
    }
  }
  return keys;
}

/** A link to the cited line, at the investigated commit. GitHub only; otherwise none. */
export function sourceUrl(repoUrl: string, commit: string, ref: CodeReference): string | null {
  const match = repoUrl.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/);
  if (!match || !commit) return null;
  const line = ref.start_line ? `#L${ref.start_line}` : "";
  return `https://github.com/${match[1]}/${match[2]}/blob/${commit}/${ref.path}${line}`;
}

/** Plain sentences describing a node's connections. */
export function relationships(graph: ArchitectureGraph, nodeId: string): string[] {
  const label = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id;
  return graph.edges
    .filter((e) => e.source === nodeId || e.target === nodeId)
    .map((e) => `${label(e.source)} ${e.type.replace(/_/g, " ")} ${label(e.target)}`);
}

/** What Ask RepoLab is told about what a question came from: a component, concept or subsystem. */
export function focusFor(graph: ArchitectureGraph, id: string): ArchitectureFocus | null {
  const node = graph.nodes.find((n) => n.id === id);
  if (node) {
    return {
      node_id: node.id,
      label: node.label.slice(0, 200),
      category: node.category,
      source_references: node.source_references.slice(0, 8),
      relationships: relationships(graph, id).slice(0, 10),
      level: "component",
      subsystem: subsystemFor(graph, node)?.label ?? null,
    };
  }
  const concept = graph.system_model?.concepts.find((c) => c.id === id);
  if (concept) {
    const members = memberNodes(graph, concept.member_ids);
    const cited = concept.evidence
      .filter((e) => e.path && e.kind !== "subsystem" && !isDirectory(graph, e.path))
      .map((e) => ({ path: e.path!, start_line: e.line, end_line: e.line, commit_sha: null, excerpt: e.excerpt }));
    const subsystems = (graph.subsystems ?? []).filter((s) => concept.subsystem_ids.includes(s.id));
    return {
      node_id: concept.id,
      label: concept.label.slice(0, 200),
      category: "concept",
      source_references: [...cited, ...members.map((m) => m.source_references[0])].filter(Boolean).slice(0, 8),
      relationships: conceptRelationships(graph, concept.id).slice(0, 10),
      level: "system",
      subsystem: subsystems[0]?.label ?? null,
      directories: [
        ...subsystems.flatMap((s) => s.starting_points.filter((p) => p.is_directory).map((p) => p.path)),
        ...concept.evidence.filter((e) => e.path && isDirectory(graph, e.path)).map((e) => e.path!),
      ].slice(0, 6),
    };
  }
  const subsystem = graph.subsystems?.find((s) => s.id === id);
  if (subsystem) {
    const files = subsystem.starting_points.filter((p) => !p.is_directory);
    return {
      node_id: subsystem.id,
      label: subsystem.label.slice(0, 200),
      category: "subsystem",
      source_references: files
        .map((p) => ({ path: p.path, start_line: null, end_line: null, commit_sha: null, excerpt: null }))
        .slice(0, 8),
      relationships: memberNodes(graph, subsystem.component_ids)
        .slice(0, 10)
        .map((n) => `contains ${n.label}`),
      level: "subsystem",
      subsystem: subsystem.label,
      directories: [
        ...subsystem.starting_points.filter((p) => p.is_directory).map((p) => p.path),
        ...subsystem.paths.filter((p) => p && !p.includes(".")),
      ].slice(0, 6),
    };
  }
  return null;
}

/** The Ask URL for a question -- about a component, concept or subsystem when *id* is given. */
export function askUrl(jobId: string, id: string | null, question: string): string {
  const params = new URLSearchParams(id ? { node: id, q: question } : { q: question });
  return `/r/${jobId}/ask?${params.toString()}`;
}

export function formatMeasured(value: number, unit: string): string {
  if (unit === "ms") return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`;
  if (unit === "bytes") {
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
    if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
    return `${value} B`;
  }
  return `${value.toLocaleString()} ${unit}`;
}

// -- progressive levels -------------------------------------------------------

export const REPOSITORY_KIND_LABEL: Record<RepositoryKind, string> = {
  distributed_system: "Distributed system",
  framework: "Framework",
  library: "Library",
  web_application: "Web application",
  api_service: "API service",
  data_platform: "Data processing platform",
  workflow_system: "Workflow orchestration",
  database: "Database / data store",
  cli_application: "Command-line application",
  ml_system: "Machine-learning system",
  developer_tool: "Developer tool",
  hybrid: "Hybrid repository",
  unknown: "Not yet identified",
};

export const EVIDENCE_KIND_LABEL: Record<EvidenceKind, string> = {
  readme: "README",
  source: "Source",
  configuration: "Configuration",
  build: "Build file",
  documentation: "Official docs",
  component: "Detected component",
  subsystem: "Repository part",
};

/** Whether this graph has the progressive levels (schema 3), or only components. */
export function hasLevels(graph: ArchitectureGraph): boolean {
  return Boolean(graph.understanding);
}

/** What an id names: a concept, a subsystem, or a component. */
export type SubjectKind = "concept" | "subsystem" | "node";

export function subjectKind(graph: ArchitectureGraph, id: string): SubjectKind | null {
  if (graph.system_model?.concepts.some((c) => c.id === id)) return "concept";
  if (graph.subsystems?.some((s) => s.id === id)) return "subsystem";
  if (graph.nodes.some((n) => n.id === id)) return "node";
  return null;
}

export function memberNodes(graph: ArchitectureGraph, ids: string[]): ArchitectureNode[] {
  return ids
    .map((id) => graph.nodes.find((n) => n.id === id))
    .filter((n): n is ArchitectureNode => Boolean(n));
}

export function subsystemFor(graph: ArchitectureGraph, node: ArchitectureNode): Subsystem | null {
  return graph.subsystems?.find((s) => s.id === node.subsystem) ?? null;
}

/** Whether *path* names a directory of the repository rather than a file. */
function isDirectory(graph: ArchitectureGraph, path: string): boolean {
  const known = new Set(graph.nodes.flatMap((n) => n.source_references.map((r) => r.path)));
  if (known.has(path)) return false;
  const name = path.split("/").pop() ?? "";
  return !name.includes(".");
}

/** Plain sentences describing a concept's links. */
export function conceptRelationships(graph: ArchitectureGraph, conceptId: string): string[] {
  const concepts = graph.system_model?.concepts ?? [];
  const label = (id: string) => concepts.find((c) => c.id === id)?.label ?? id;
  return (graph.system_model?.links ?? [])
    .filter((l) => l.source === conceptId || l.target === conceptId)
    .map((l) => `${label(l.source)} ${l.type.replace(/_/g, " ")} ${label(l.target)}`);
}

/** The most common category among a concept's components, for its colour. */
function conceptCategory(graph: ArchitectureGraph, concept: SystemConcept): NodeCategory {
  const counts = new Map<NodeCategory, number>();
  for (const node of memberNodes(graph, concept.member_ids)) {
    counts.set(node.category, (counts.get(node.category) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "core_engine";
}

/**
 * The system model as a graph the layered SVG can draw: one box per concept,
 * one arrow per link, the system walkthrough as its flow.
 */
export function conceptGraph(graph: ArchitectureGraph): ArchitectureGraph {
  const model = graph.system_model ?? { concepts: [], links: [], provenance: "static" as const };
  const nodes: ArchitectureNode[] = model.concepts.map((concept) => ({
    id: concept.id,
    label: concept.label,
    category: conceptCategory(graph, concept),
    subtype: null,
    technology: concept.term,
    description: concept.what_it_does,
    runtime_significance: concept.why_it_matters || null,
    source_references: [],
    properties: {},
    confidence: concept.confidence,
    provenance: concept.provenance,
    detected_because: "",
    inferred_fields: [],
    children: [],
    surface: null,
    level: "system",
  }));
  const edges: ArchitectureEdge[] = model.links.map((link) => ({
    source: link.source,
    target: link.target,
    type: link.type,
    description: link.description,
    provenance: link.provenance,
    evidence: [],
    confidence: 0.6,
  }));
  const walk = (graph.walkthroughs ?? []).find((w) => w.level === "system");
  return {
    ...graph,
    nodes,
    edges,
    entry_points: [],
    important_files: [],
    primary_flows: walk
      ? [{ name: walk.name, node_ids: walk.steps.map((s) => s.target_id), description: null, provenance: walk.provenance }]
      : [],
  };
}

/** One subsystem's components and the relationships among them. */
export function subsystemGraph(graph: ArchitectureGraph, subsystemId: string): ArchitectureGraph {
  const ids = new Set(graph.nodes.filter((n) => n.subsystem === subsystemId).map((n) => n.id));
  return {
    ...graph,
    nodes: graph.nodes.filter((n) => ids.has(n.id)),
    edges: graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    entry_points: graph.entry_points.filter((id) => ids.has(id)),
    important_files: graph.important_files
      .map((f) => ({ ...f, node_ids: f.node_ids.filter((id) => ids.has(id)) }))
      .filter((f) => f.node_ids.length > 0),
    primary_flows: graph.primary_flows.filter((f) => f.node_ids.every((id) => ids.has(id))),
  };
}

/** Walkthroughs for the repository overview; the rest belong to a subsystem. */
export function overviewWalkthroughs(graph: ArchitectureGraph): Walkthrough[] {
  return (graph.walkthroughs ?? []).filter((w) => !w.subsystem);
}

/** Where a subsystem lives, as a short friendly path: `sql/`, `connector/`. */
export function subsystemPath(subsystem: Subsystem): string {
  const dirs = subsystem.paths.filter((p) => p && !p.split("/").pop()!.includes("."));
  if (dirs.length) return dirs.slice(0, 2).map((p) => `${p}/`).join(", ") + (dirs.length > 2 ? ", …" : "");
  return subsystem.paths.length ? `${subsystem.paths.length} files` : "";
}
