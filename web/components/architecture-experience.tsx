/**
 * The Architecture screen, given its data -- a guided path from "what is this?"
 * down to source lines.
 *
 *     repository   what it is, what problem it solves, how we know
 *     system       3-8 concepts and how they connect
 *     subsystems   the repository's major parts -> Explore
 *     components   one part's runtime components (drill-down)
 *     source       the inspector's references, at the investigated commit
 *
 * Complexity is revealed by drilling in, not by a mode switch. The drill-down
 * state lives in the URL (`?subsystem=…&node=…`) so it survives a refresh and
 * can be linked. Graphs stored before these levels existed render as the
 * component view, with a note to investigate again.
 *
 * Separated from the page's data loading so it can be rendered and tested
 * directly. See app/r/[jobId]/architecture/page.tsx.
 */

"use client";

import { useEffect, useMemo, useState } from "react";

import { ArchitectureGraph } from "./architecture-graph";
import { ArchitectureInspector, ConceptInspector, EvidenceList } from "./architecture-inspector";
import {
  AnalysisDetails,
  ImportantFiles,
  LearningPath,
  MeasuredList,
  Orientation,
  StartingPointList,
  StartingPoints,
  SubsystemCards,
  Walkthroughs,
} from "./architecture-overview";
import { Empty, FileRefs, Note, Panel } from "./ui";
import {
  CATEGORY_LABEL,
  VIEW_LABEL,
  conceptGraph,
  hasLevels,
  overviewWalkthroughs,
  subjectKind,
  subsystemGraph,
  subsystemPath,
} from "@/lib/architecture";
import type {
  ArchitectureGraph as Graph,
  ArchitectureView,
  ImportantFile,
  LearningStep,
  MeasuredValue,
  RepoManifest,
  RuntimeOverlay,
  Subsystem,
} from "@/lib/types";

type Props = {
  jobId: string;
  view: ArchitectureView;
  manifest: RepoManifest;
  overlay: RuntimeOverlay | null;
  initialSubsystem?: string | null;
  initialSelection?: string | null;
};

export function ArchitectureExperience(props: Props) {
  return hasLevels(props.view.graph) ? <GuidedArchitecture {...props} /> : <LegacyArchitecture {...props} />;
}

// ---------------------------------------------------------------------------
// The guided experience (schema 3)
// ---------------------------------------------------------------------------

function GuidedArchitecture({ jobId, view, manifest, overlay, initialSubsystem = null, initialSelection = null }: Props) {
  const graph = view.graph;
  const understanding = graph.understanding!;
  const subsystems = useMemo(() => graph.subsystems ?? [], [graph.subsystems]);
  const known = (id: string | null) => (id && subjectKind(graph, id) ? id : null);

  const [subsystemId, setSubsystemId] = useState<string | null>(
    initialSubsystem && subsystems.some((s) => s.id === initialSubsystem) ? initialSubsystem : null,
  );
  const [selected, setSelected] = useState<string | null>(known(initialSelection));

  useEffect(() => {
    // Keep the drill-down in the URL without a navigation.
    try {
      const url = new URL(window.location.href);
      for (const [key, value] of [
        ["subsystem", subsystemId],
        ["node", selected],
      ] as const) {
        if (value) url.searchParams.set(key, value);
        else url.searchParams.delete(key);
      }
      window.history.replaceState(window.history.state, "", url);
    } catch {
      // Not in a browser that allows it; the state still works in memory.
    }
  }, [subsystemId, selected]);

  const explore = (id: string | null) => {
    setSubsystemId(id);
    setSelected(null);
  };
  const select = (id: string) => {
    const kind = subjectKind(graph, id);
    if (kind === "subsystem") return explore(id);
    if (kind === "node") {
      const node = graph.nodes.find((n) => n.id === id);
      if (node?.subsystem && node.subsystem !== subsystemId) setSubsystemId(node.subsystem);
    }
    if (kind === "concept") setSubsystemId(null);
    setSelected(id);
  };
  const openStep = (step: LearningStep) => {
    if (!step.target_id) return;
    if (step.target_kind === "walkthrough") {
      const walk = graph.walkthroughs?.find((w) => w.id === step.target_id);
      if (walk?.steps[0]) select(walk.steps[0].target_id);
      return;
    }
    select(step.target_id);
  };

  const measured = overlay?.measurements ?? {};
  const grouped = overlay?.by_group ?? {};
  const subsystem = subsystems.find((s) => s.id === subsystemId) ?? null;
  const experiment = Object.entries(view.node_actions).find(([, a]) => a.experiment.available && a.experiment.question);
  const surfaces = understanding.main_interaction_surfaces.length
    ? understanding.main_interaction_surfaces
    : manifest.interaction_surfaces.map((s) => s.type);

  const inspector = selected ? (
    <Inspector
      jobId={jobId}
      view={view}
      id={selected}
      measured={{ ...measured, ...grouped }}
      onSelect={select}
      onExplore={explore}
    />
  ) : null;

  return (
    <div className="flex flex-col gap-6">
      <Breadcrumb
        name={understanding.name}
        subsystem={subsystem}
        selected={selected ? labelOf(graph, selected) : null}
        onHome={() => explore(null)}
        onSubsystem={() => subsystem && explore(subsystem.id)}
      />

      <div className={inspector ? "grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]" : ""}>
        <div className="flex min-w-0 flex-col gap-6">
          {subsystem ? (
            <SubsystemView
              view={view}
              subsystem={subsystem}
              selected={selected}
              measured={measured}
              groupMeasured={grouped[subsystem.id] ?? []}
              onSelect={select}
            />
          ) : (
            <>
              <Orientation
                jobId={jobId}
                repoUrl={view.repo_url}
                commit={view.commit_sha}
                understanding={understanding}
                surfaces={surfaces}
                partial={graph.completeness === "partial"}
                onExplore={explore}
                onSelect={select}
              />
              <SystemModelPanel graph={graph} selected={selected} measured={grouped} onSelect={select} />
              {subsystems.length > 0 && (
                <SubsystemCards subsystems={subsystems} measured={grouped} onExplore={explore} />
              )}
              <Walkthroughs walkthroughs={overviewWalkthroughs(graph)} onSelect={select} />
              <LearningPath
                jobId={jobId}
                steps={graph.learning_path ?? []}
                experiment={experiment ? { id: experiment[0], question: experiment[1].experiment.question! } : null}
                onOpen={openStep}
              />
              {subsystems.some((s) => s.starting_points.length > 0) && (
                <StartingPoints
                  repoUrl={view.repo_url}
                  commit={view.commit_sha}
                  subsystems={subsystems}
                  files={graph.important_files}
                  onExplore={explore}
                  onOpenFile={(file: ImportantFile) => file.node_ids[0] && select(file.node_ids[0])}
                />
              )}
              {manifest.concepts.length > 0 && (
                <details className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
                  <summary className="cursor-pointer text-sm font-medium">Concepts the investigation noted</summary>
                  <ul className="mt-3 flex flex-col gap-4" data-testid="concepts">
                    {manifest.concepts.map((concept) => (
                      <li key={concept.name}>
                        <p className="text-sm font-medium">{concept.name}</p>
                        <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">{concept.summary}</p>
                        <FileRefs refs={concept.code_references} />
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
          <AnalysisDetails graph={graph} overlay={overlay} />
        </div>

        {inspector && (
          <aside className="lg:sticky lg:top-4 lg:self-start" aria-label="Selected">
            <Panel
              title="Selected"
              action={
                <button type="button" onClick={() => setSelected(null)} className="text-xs text-neutral-500 underline">
                  Close
                </button>
              }
            >
              {inspector}
            </Panel>
          </aside>
        )}
      </div>
    </div>
  );
}

function labelOf(graph: Graph, id: string): string {
  return (
    graph.nodes.find((n) => n.id === id)?.label ??
    graph.system_model?.concepts.find((c) => c.id === id)?.label ??
    id
  );
}

function Breadcrumb({
  name,
  subsystem,
  selected,
  onHome,
  onSubsystem,
}: {
  name: string;
  subsystem: Subsystem | null;
  selected: string | null;
  onHome: () => void;
  onSubsystem: () => void;
}) {
  if (!subsystem && !selected) return null;
  return (
    <nav aria-label="Breadcrumb" data-testid="breadcrumb" className="flex flex-wrap items-center gap-1.5 text-sm">
      <button type="button" onClick={onHome} className="underline underline-offset-2">
        {name}
      </button>
      {subsystem && (
        <>
          <span aria-hidden className="text-neutral-400">›</span>
          <button type="button" onClick={onSubsystem} className="underline underline-offset-2">
            {subsystem.label}
          </button>
        </>
      )}
      {selected && (
        <>
          <span aria-hidden className="text-neutral-400">›</span>
          <span className="font-medium">{selected}</span>
        </>
      )}
    </nav>
  );
}

function SystemModelPanel({
  graph,
  selected,
  measured,
  onSelect,
}: {
  graph: Graph;
  selected: string | null;
  measured: Record<string, MeasuredValue[]>;
  onSelect: (id: string) => void;
}) {
  const model = graph.system_model;
  const concepts = useMemo(() => conceptGraph(graph), [graph]);
  return (
    <Panel
      title="How does it work?"
      subtitle={
        model?.provenance === "inferred"
          ? "The main ideas, proposed by Nemotron and kept only where they cite evidence. Click one to learn more."
          : "The main parts, from the repository's own structure. Click one to learn more."
      }
    >
      {concepts.nodes.length === 0 ? (
        <Empty title="No high-level model yet">
          RepoLab could not tie the repository&apos;s major ideas to its files, so it shows none rather than guess.
        </Empty>
      ) : (
        <div data-testid="system-model">
          <ArchitectureGraph
            graph={concepts}
            selected={selected}
            highlighted={new Set()}
            measured={measured}
            onSelect={onSelect}
            caption={(node) => (node.technology ? `Internal name: ${node.technology}` : "concept")}
            kind={() => "concept"}
          />
        </div>
      )}
    </Panel>
  );
}

function SubsystemView({
  view,
  subsystem,
  selected,
  measured,
  groupMeasured,
  onSelect,
}: {
  view: ArchitectureView;
  subsystem: Subsystem;
  selected: string | null;
  measured: Record<string, MeasuredValue[]>;
  groupMeasured: MeasuredValue[];
  onSelect: (id: string) => void;
}) {
  const graph = view.graph;
  const sub = useMemo(() => subsystemGraph(graph, subsystem.id), [graph, subsystem.id]);
  const walks = (graph.walkthroughs ?? []).filter((w) => w.subsystem === subsystem.id);
  return (
    <>
      <Panel title={subsystem.label} subtitle={subsystemPath(subsystem)}>
        <div data-testid="subsystem-view" data-subsystem-id={subsystem.id} className="flex flex-col gap-3">
          {subsystem.description && <p className="text-sm">{subsystem.description}</p>}
          <p className="text-xs text-neutral-500">
            {subsystem.file_count} source files
            {subsystem.languages.length > 0 && ` · ${subsystem.languages.join(", ")}`} · {sub.nodes.length} components
            found
          </p>
          <details className="text-xs">
            <summary className="cursor-pointer text-neutral-500">How do you know this is a part?</summary>
            <p className="mt-2">Found as: {subsystem.declared_by}.</p>
            <div className="mt-1">
              <EvidenceList evidence={subsystem.evidence} repoUrl={view.repo_url} commit={view.commit_sha} />
            </div>
            {Object.keys(subsystem.importance_signals).length > 0 && (
              <p className="mt-1 text-neutral-500">
                Ranked by:{" "}
                {Object.entries(subsystem.importance_signals)
                  .map(([signal, value]) => `${signal} (${value > 0 ? "+" : ""}${value})`)
                  .join(", ")}
              </p>
            )}
          </details>
          {subsystem.starting_points.length > 0 && (
            <div>
              <p className="text-xs font-semibold">Start here</p>
              <StartingPointList repoUrl={view.repo_url} commit={view.commit_sha} subsystem={subsystem} />
            </div>
          )}
        </div>
      </Panel>

      <ComponentPanel
        graph={sub}
        title="Components"
        subtitle="Runtime components RepoLab found in this part. Dashed arrows are inferred, solid ones read from code."
        selected={selected}
        measured={measured}
        onSelect={onSelect}
        emptyText="RepoLab found no runtime components in this part's code. The starting points above are the place to read; Ask RepoLab can explain it."
      />
      {walks.length > 0 && <Walkthroughs walkthroughs={walks} onSelect={onSelect} />}
      {groupMeasured.length > 0 && (
        <Panel title="Measured in real runs" subtitle="Values from components in this part">
          <MeasuredList values={groupMeasured} />
        </Panel>
      )}
    </>
  );
}

/** One graph of components, its flows as text, and its important files. */
function ComponentPanel({
  graph,
  title,
  subtitle,
  selected,
  measured,
  onSelect,
  emptyText,
}: {
  graph: Graph;
  title: string;
  subtitle: string;
  selected: string | null;
  measured: Record<string, MeasuredValue[]>;
  onSelect: (id: string) => void;
  emptyText: string;
}) {
  const [file, setFile] = useState<string | null>(null);
  const highlighted = useMemo(
    () => new Set(graph.important_files.find((f) => f.path === file)?.node_ids ?? []),
    [graph.important_files, file],
  );
  const label = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id;
  const ranked = [...graph.nodes].sort((a, b) => (b.learning_importance ?? 0) - (a.learning_importance ?? 0));

  return (
    <Panel title={title} subtitle={subtitle}>
      {graph.nodes.length === 0 ? (
        <Empty title="No components detected here">{emptyText}</Empty>
      ) : (
        <div className="flex flex-col gap-4">
          <ArchitectureGraph graph={graph} selected={selected} highlighted={highlighted} measured={measured} onSelect={onSelect} />
          {graph.primary_flows.length > 0 && (
            <ol className="flex flex-col gap-1 border-t border-neutral-200 pt-3 text-xs dark:border-neutral-800" data-testid="primary-flows">
              {graph.primary_flows.map((flow) => (
                <li key={flow.name + flow.node_ids.join()}>
                  <span className="font-medium">{flow.name}</span>
                  {flow.provenance === "inferred" && <span className="ml-1 text-[10px] uppercase text-neutral-400">inferred</span>}
                  <span className="ml-2 font-mono text-neutral-500">{flow.node_ids.map(label).join(" → ")}</span>
                </li>
              ))}
            </ol>
          )}
          <details className="text-xs" data-testid="component-list">
            <summary className="cursor-pointer text-neutral-500">All {graph.nodes.length} components, most useful first</summary>
            <ul className="mt-2 flex flex-col gap-1">
              {ranked.map((node) => (
                <li key={node.id}>
                  <button type="button" onClick={() => onSelect(node.id)} className="underline underline-offset-2">
                    {node.label}
                  </button>
                  <span className="ml-1 text-neutral-500">{CATEGORY_LABEL[node.category]}</span>
                </li>
              ))}
            </ul>
          </details>
          {graph.important_files.length > 0 && (
            <details className="text-xs" open={file !== null}>
              <summary className="cursor-pointer text-neutral-500">Important implementation files (click to highlight)</summary>
              <ImportantFiles
                files={graph.important_files}
                active={file}
                onOpen={(f) => setFile((current) => (current === f.path ? null : f.path))}
              />
            </details>
          )}
        </div>
      )}
    </Panel>
  );
}

function Inspector({
  jobId,
  view,
  id,
  measured,
  onSelect,
  onExplore,
}: {
  jobId: string;
  view: ArchitectureView;
  id: string;
  measured: Record<string, MeasuredValue[]>;
  onSelect: (id: string) => void;
  onExplore: (id: string) => void;
}) {
  const graph = view.graph;
  const common = {
    jobId,
    repoUrl: view.repo_url,
    commit: view.commit_sha,
    graph,
    measured: measured[id] ?? [],
    onSelect,
    onExplore,
  };
  const node = graph.nodes.find((n) => n.id === id);
  if (node) return <ArchitectureInspector {...common} node={node} actions={view.node_actions[id]} />;
  const concept = graph.system_model?.concepts.find((c) => c.id === id);
  if (concept) return <ConceptInspector {...common} concept={concept} actions={view.concept_actions?.[id]} />;
  return <Empty title="Nothing selected" />;
}

// ---------------------------------------------------------------------------
// Graphs stored before the progressive levels
// ---------------------------------------------------------------------------

function LegacyArchitecture({ jobId, view, overlay, initialSelection = null }: Props) {
  const graph = view.graph;
  const first = graph.primary_flows[0]?.node_ids[0] ?? graph.nodes[0]?.id ?? null;
  const [selected, setSelected] = useState<string | null>(
    initialSelection && graph.nodes.some((n) => n.id === initialSelection) ? initialSelection : first,
  );
  const measured = overlay?.measurements ?? {};
  return (
    <div className="flex flex-col gap-6">
      <Note>
        This investigation was mapped by an earlier version of RepoLab, so the guided overview is not available.
        Investigate the repository again to see what it is, its major parts and where to start.
      </Note>
      {graph.summary && (
        <p className="text-sm" data-testid="architecture-summary">
          {graph.summary}
        </p>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="flex min-w-0 flex-col gap-6">
          <ComponentPanel
            graph={graph}
            title={VIEW_LABEL[graph.default_view]}
            subtitle="Click a component to see its evidence. Dashed arrows are inferred, solid ones read from code."
            selected={selected}
            measured={measured}
            onSelect={setSelected}
            emptyText="RepoLab will not draw an architecture it cannot tie to the repository's files."
          />
          <AnalysisDetails graph={graph} overlay={overlay} />
        </div>
        {selected && (
          <aside aria-label="Selected">
            <Panel title="Selected component">
              <Inspector jobId={jobId} view={view} id={selected} measured={measured} onSelect={setSelected} onExplore={() => {}} />
            </Panel>
          </aside>
        )}
      </div>
    </div>
  );
}
