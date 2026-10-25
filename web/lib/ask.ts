/**
 * Ask RepoLab helpers: how an answer is laid out, what it cites, and what the
 * page around it shows. Pure functions, so the screen can be tested without a
 * browser.
 *
 * Nothing here adds to an answer. The layout, the diagram and the sources are
 * what the backend verified; these functions only arrange them.
 */

import { CATEGORY_LABEL, focusFor, layout } from "./architecture";
import type {
  AnswerResult,
  ArchitectureEdge,
  ArchitectureGraph,
  ArchitectureNode,
  ArchitectureView,
  CodeReference,
  EvidenceBundle,
  ExplanationShape,
  GroundedAnswer,
  NodeCategory,
} from "./types";

export const SHAPE_LABEL: Record<ExplanationShape, string> = {
  flow: "How it connects",
  execution: "What happens, step by step",
  definition: "What it is",
  why: "Why it is this way",
  comparison: "Side by side",
  location: "Where it lives",
  architecture: "How it is organised",
  debugging: "What is going wrong",
  general: "Answer",
};

/** The direct answer: the summary, or the prose of an answer stored before summaries. */
export function summaryOf(answer: GroundedAnswer): string {
  if (answer.summary && answer.summary.trim()) return answer.summary.trim();
  return answer.answer;
}

/** The fuller explanation, when it says more than the summary. */
export function detailOf(answer: GroundedAnswer): string | null {
  const summary = summaryOf(answer);
  return answer.answer && answer.answer.trim() !== summary ? answer.answer : null;
}

// -- sources ---------------------------------------------------------------------

export type SourceCounts = { code: number; docs: number; runtime: number };

export function sourceCounts(evidence: EvidenceBundle): SourceCounts {
  return {
    code: new Set(evidence.code_references.map((ref) => ref.path)).size,
    docs: evidence.documentation.length,
    runtime: evidence.measurements.length,
  };
}

/** One line under the counts: what the answer rests on, in plain words. */
export function provenanceLine(counts: SourceCounts): string {
  if (counts.runtime > 0) return "Includes measured runtime evidence";
  if (counts.code > 0 && counts.docs > 0) return "Based on repository code and official documentation";
  if (counts.code > 0) return "Based on repository code";
  if (counts.docs > 0) return "Based on official documentation";
  return "No evidence was gathered";
}

/** A cited place in the repository: the file, the line, and why it was cited. */
export type CitedSource = {
  path: string;
  line: number | null;
  reason: string;
  reference: CodeReference | null;
};

/** `path` or `path:line` (or `path:12-20`), as the backend's sources write them. */
export function parseSource(cited: string): { path: string; line: number | null } {
  const match = cited.trim().match(/^(.*?):(\d+)(?:-\d+)?$/);
  if (match) return { path: match[1], line: Number(match[2]) };
  return { path: cited.trim(), line: null };
}

/**
 * The files an answer cites, in the order a reader meets them -- steps, the
 * diagram, the code map, sections, claims -- then any other evidence. Each
 * keeps the first reason it was cited for.
 */
export function citedSources(result: AnswerResult): CitedSource[] {
  const refs = result.evidence.code_references;
  const found = new Map<string, CitedSource>();
  const add = (cited: string, reason: string) => {
    if (/^https?:\/\//.test(cited)) return;
    const { path, line } = parseSource(cited);
    if (!path || found.has(path)) return;
    const reference = bestReference(refs, path, line);
    if (!reference && !isPath(path)) return;
    found.set(path, { path, line: line ?? reference?.start_line ?? null, reason, reference });
  };

  const answer = result.answer?.answer;
  if (answer) {
    (answer.steps ?? []).forEach((step) => step.sources.forEach((s) => add(s, step.title)));
    (answer.visual?.edges ?? []).forEach((edge) => {
      if (edge.provenance !== "inferred" && !edge.support.startsWith("edge:")) add(edge.support, edge.label || "Shows this relationship");
    });
    (answer.visual?.nodes ?? []).forEach((node) => node.source && add(node.source, node.label));
    (answer.locations ?? []).forEach((loc) => add(loc.line ? `${loc.path}:${loc.line}` : loc.path, loc.why || loc.role));
    (answer.sections ?? []).forEach((section) => section.sources.forEach((s) => add(s, section.title)));
    answer.claims.filter((claim) => claim.kind === "repository").forEach((claim) => add(claim.support, claim.statement));
  }
  for (const ref of refs) {
    if (!found.has(ref.path)) {
      found.set(ref.path, { path: ref.path, line: ref.start_line, reason: "Read while answering", reference: ref });
    }
  }
  return [...found.values()];
}

function isPath(text: string): boolean {
  return /[\w-]+\.[\w]+$/.test(text) || text.endsWith("/");
}

/** The evidence excerpt that covers *line*, else the file's first excerpt. */
function bestReference(refs: CodeReference[], path: string, line: number | null): CodeReference | null {
  const same = refs.filter((ref) => ref.path === path && ref.excerpt);
  if (line !== null) {
    const covering = same.find((ref) => covers(ref, line));
    if (covering) return covering;
  }
  return same[0] ?? null;
}

function covers(ref: CodeReference, line: number): boolean {
  if (!ref.excerpt || ref.start_line === null) return false;
  const end = ref.start_line + ref.excerpt.split("\n").length - 1;
  return line >= ref.start_line && line <= end;
}

export type ExcerptLine = { number: number | null; text: string; cited: boolean };

/**
 * The smallest useful range of an excerpt: a few lines either side of the
 * cited line, or the head of the excerpt when no line is cited. Never the
 * whole file.
 */
export function excerptRange(ref: CodeReference, line: number | null, radius = 5, head = 12): ExcerptLine[] {
  const lines = (ref.excerpt ?? "").replace(/\n+$/, "").split("\n");
  const start = ref.start_line;
  if (start === null) return lines.slice(0, head).map((text) => ({ number: null, text, cited: false }));
  const index = line !== null && covers(ref, line) ? line - start : null;
  const [from, to] = index === null ? [0, head] : [Math.max(0, index - radius), index + radius + 1];
  return lines.slice(from, to).map((text, i) => ({
    number: start + from + i,
    text,
    cited: index !== null && from + i === index,
  }));
}

// -- the diagram -----------------------------------------------------------------

const KIND_CATEGORY: [RegExp, NodeCategory][] = [
  [/browser|client|frontend|ui|page|user/i, "frontend"],
  [/route|router|api|endpoint|controller|handler/i, "api"],
  [/server|service|app/i, "service"],
  [/model|schema|orm|entity/i, "model"],
  [/database|db|store|table|collection/i, "database"],
  [/cache/i, "cache"],
  [/queue|topic|broker/i, "queue"],
  [/worker|job|task/i, "worker"],
  [/cli|command|shell/i, "cli"],
  [/config/i, "configuration"],
];

function categoryFor(kind: string, component: ArchitectureNode | null): NodeCategory {
  if (component) return component.category;
  return KIND_CATEGORY.find(([pattern]) => pattern.test(kind))?.[1] ?? "other";
}

/**
 * The answer's diagram as an architecture graph, so the shared layered layout
 * places it. Boxes and arrows are exactly the verified ones; nothing is added.
 */
export function answerGraph(answer: GroundedAnswer, architecture: ArchitectureGraph | null): ArchitectureGraph | null {
  const visual = answer.visual;
  if (!visual || visual.nodes.length < 2 || visual.edges.length === 0) return null;
  const known = new Map((architecture?.nodes ?? []).map((node) => [node.id, node]));
  const nodes: ArchitectureNode[] = visual.nodes.map((box) => {
    const component = box.component_id ? known.get(box.component_id) ?? null : null;
    return {
      id: box.id,
      label: box.label,
      category: categoryFor(box.kind, component),
      subtype: null,
      technology: box.kind || null,
      description: null,
      runtime_significance: null,
      source_references: [],
      properties: {},
      confidence: 0.7,
      provenance: "static",
      detected_because: "",
      inferred_fields: [],
      children: [],
      surface: null,
    };
  });
  const edges: ArchitectureEdge[] = visual.edges.map((edge) => ({
    source: edge.source,
    target: edge.target,
    type: "data_flow",
    description: edge.label || null,
    provenance: edge.provenance === "inferred" ? "inferred" : "static",
    evidence: [],
    confidence: 0.7,
  }));
  return {
    schema_version: 3,
    nodes,
    edges,
    entry_points: [],
    important_files: [],
    interaction_surfaces: [],
    summary: null,
    summary_provenance: "inferred",
    primary_flows: [],
    default_view: "data_flow",
    completeness: "complete",
    gaps: [],
    generated_by: "super_enriched",
    dropped: {},
  };
}

export type DiagramBox = { id: string; label: string; caption: string; layer: number; column: number };

/** Boxes in top-to-bottom layers, from the shared layered layout. */
export function diagramLayers(graph: ArchitectureGraph): DiagramBox[] {
  const placements = layout(graph);
  return graph.nodes.map((node) => {
    const place = placements.get(node.id)!;
    return {
      id: node.id,
      label: node.label,
      caption: node.technology ?? CATEGORY_LABEL[node.category],
      layer: place.rank,
      column: place.row,
    };
  });
}

// -- the context around the question ---------------------------------------------

export type ContextPanel = {
  label: string;
  kind: string;
  description: string | null;
  uses: string[];
  usedBy: string[];
  implementedWith: string | null;
  files: string[];
  related: string[];
};

/** What the sidebar says about the component, concept or part a question is about. */
export function contextFor(view: ArchitectureView, id: string | null): ContextPanel | null {
  if (!id) return null;
  const graph = view.graph;
  const labels = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const node = graph.nodes.find((n) => n.id === id);
  if (node) {
    const uses = graph.edges.filter((e) => e.source === id).map((e) => labels.get(e.target)).filter(isString);
    const usedBy = graph.edges.filter((e) => e.target === id).map((e) => labels.get(e.source)).filter(isString);
    return {
      label: node.label,
      kind: CATEGORY_LABEL[node.category],
      description: node.description,
      uses: unique(uses),
      usedBy: unique(usedBy),
      implementedWith: node.technology,
      files: unique(node.source_references.map((ref) => ref.path)).slice(0, 6),
      related: unique([...usedBy, ...uses]).slice(0, 6),
    };
  }
  const focus = focusFor(graph, id);
  if (!focus) return null;
  const concept = graph.system_model?.concepts.find((c) => c.id === id);
  const subsystem = graph.subsystems?.find((s) => s.id === id);
  const members = concept?.member_ids ?? subsystem?.component_ids ?? [];
  return {
    label: focus.label,
    kind: focus.level === "system" ? "concept" : "part of the repository",
    description: concept?.what_it_does || subsystem?.description || null,
    uses: [],
    usedBy: [],
    implementedWith: concept?.term ?? null,
    files: unique([...focus.source_references.map((ref) => ref.path), ...(focus.directories ?? [])]).slice(0, 6),
    related: members.map((m) => labels.get(m)).filter(isString).slice(0, 6),
  };
}

/** The architecture components an answer's diagram drew, for the sidebar. */
export function componentsIn(result: AnswerResult | null, view: ArchitectureView | null): string[] {
  if (!result?.answer?.answer.visual || !view) return [];
  const labels = new Map(view.graph.nodes.map((node) => [node.id, node.label]));
  return unique(
    result.answer.answer.visual.nodes
      .map((box) => (box.component_id ? labels.get(box.component_id) : undefined))
      .filter(isString),
  );
}

/**
 * Two to four questions worth asking from here: the selected subject's own
 * suggestions, else the repository's recommended start and learning path.
 */
export function suggestionsFor(view: ArchitectureView | null, id: string | null, limit = 4): string[] {
  if (!view) return [];
  if (id) {
    const actions = view.node_actions[id] ?? view.concept_actions?.[id];
    const own = [
      ...(actions?.experiment.question ? [actions.experiment.question] : []),
      ...(actions?.suggested_questions ?? []),
    ];
    if (own.length > 0) return unique(own).slice(0, limit);
  }
  const graph = view.graph;
  return unique(
    [
      graph.understanding?.recommended_start,
      ...(graph.learning_path ?? []).map((step) => step.question),
    ].filter(isString),
  ).slice(0, limit);
}

export type AnalysisSummary = { evidence: number; components: number; flows: number };

export function analysisSummary(view: ArchitectureView | null): AnalysisSummary | null {
  if (!view) return null;
  return {
    evidence: view.graph.nodes.reduce((n, node) => n + node.source_references.length, 0),
    components: view.node_count,
    flows: view.graph.primary_flows.length,
  };
}

function isString(value: string | null | undefined): value is string {
  return typeof value === "string" && value.length > 0;
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}
