/**
 * The runtime architecture: how the system works, from the code itself.
 *
 *   CoreIdeas         the few components a newcomer needs, numbered
 *   RuntimeDiagram    those components and what passes between them
 *   RuntimeInspector  one component: what it does, where, why RepoLab thinks so
 *   FlowsView         end-to-end paths, as steps and -- only when the order is
 *                     verified -- as a sequence diagram
 *   ModulesView       the repository's parts and their build dependencies
 *   SourceMapView     a reading path, then where each part lives
 *
 * Everything here renders the investigation's data; nothing is invented to
 * fill a gap. Each claim carries a `SourceRef` to verified lines at the
 * investigated commit.
 */

"use client";

import { useMemo, useState } from "react";

import { ArchitectureGraph } from "./architecture-graph";
import { SourceRef, STRENGTH_LABEL } from "./source-ref";
import { ActionLink, Badge, CheckIcon, EmptyState, Panel, Tabs } from "./ui";
import type {
  ArchitectureEdge,
  ArchitectureGraph as Graph,
  ArchitectureNode,
  ArchitectureView,
  CodeReference,
  Flow,
  RuntimeComponent,
  RuntimeLink,
} from "@/lib/types";

export function hasRuntime(graph: Graph): boolean {
  return (graph.runtime?.components.length ?? 0) > 0;
}

const EDGE_TYPE: Record<RuntimeLink["type"], ArchitectureEdge["type"]> = {
  calls: "calls",
  creates: "creates",
  extends: "depends_on",
  references: "depends_on",
};

/** The runtime model as a graph the layered SVG can draw. */
export function runtimeGraph(graph: Graph): Graph {
  const runtime = graph.runtime ?? { components: [], links: [], provenance: "static" as const };
  const names = new Map((graph.abstractions ?? []).map((a) => [a.id, a.name]));
  const nodes: ArchitectureNode[] = runtime.components.map((component) => ({
    id: component.id,
    label: component.name,
    category: "core_engine",
    subtype: null,
    technology: null,
    description: component.description,
    runtime_significance: null,
    source_references: component.source_references,
    properties: {},
    confidence: 0.8,
    provenance: component.provenance,
    detected_because: "",
    inferred_fields: component.inferred_fields,
    children: [],
    surface: null,
    level: "system",
    caption: component.members.map((m) => names.get(m) ?? m).join(", "),
  }) as ArchitectureNode);
  // The picture shows hand-overs -- calls and constructions. Plain references
  // stay in each component's panel, where they do not tangle the diagram.
  const handOvers = runtime.links.filter((link) => link.type === "calls" || link.type === "creates");
  const edges: ArchitectureEdge[] = (handOvers.length > 0 ? handOvers : runtime.links).map((link) => ({
    source: link.source,
    target: link.target,
    type: EDGE_TYPE[link.type],
    description: link.label ?? link.type,
    // The relationship is verified in code (solid); only its label may be inferred.
    provenance: "static",
    evidence: link.evidence,
    confidence: 0.8,
  }));
  const ordered = (graph.flows ?? []).find((flow) => flow.ordered) ?? graph.flows?.[0];
  return {
    ...graph,
    nodes,
    edges,
    entry_points: [],
    important_files: [],
    primary_flows: ordered
      ? [
          {
            name: ordered.name,
            node_ids: [...new Set(ordered.steps.map((s) => s.component))],
            description: null,
            provenance: ordered.provenance,
          },
        ]
      : [],
  };
}

function caption(node: ArchitectureNode): string {
  return (node as ArchitectureNode & { caption?: string }).caption ?? "";
}

export function CoreIdeas({
  graph,
  selected,
  onSelect,
}: {
  graph: Graph;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const components = graph.runtime?.components ?? [];
  return (
    <ol className="grid gap-3 sm:grid-cols-2" data-testid="core-ideas">
      {components.map((component, index) => (
        <li key={component.id}>
          <button
            type="button"
            onClick={() => onSelect(component.id)}
            aria-pressed={selected === component.id}
            className={`flex h-full w-full gap-3 rounded-lg border p-4 text-left transition-colors duration-150 ${
              selected === component.id
                ? "border-arch bg-arch-soft"
                : "border-line bg-surface hover:border-arch-line"
            }`}
          >
            <span className="font-mono text-sm font-semibold text-arch-fg">{String(index + 1).padStart(2, "0")}</span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-fg">{component.name}</span>
              <span className="mt-0.5 block text-sm text-fg-secondary">
                {component.description ?? "Defined in the code below; open it to see what it does."}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

export function RuntimeDiagram({
  graph,
  selected,
  onSelect,
}: {
  graph: Graph;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const drawn = useMemo(() => runtimeGraph(graph), [graph]);
  return (
    <div data-testid="runtime-diagram">
      <ArchitectureGraph
        graph={drawn}
        selected={selected}
        highlighted={new Set()}
        measured={{}}
        onSelect={onSelect}
        caption={caption}
        kind={() => "runtime component"}
        edgeLabel={(edge) => edge.description}
        columnGap={120}
      />
      <p className="mt-2 text-xs text-fg-muted">
        Every arrow is a line in the code where one part calls or creates another. Other uses are listed in each
        part&apos;s panel. Click a part to read its source.
      </p>
    </div>
  );
}

// -- one component ----------------------------------------------------------------

export function RuntimeInspector({
  jobId,
  view,
  componentId,
  onSelect,
  onShowFlow,
}: {
  jobId: string;
  view: ArchitectureView;
  componentId: string;
  onSelect: (id: string) => void;
  onShowFlow: (flowId: string) => void;
}) {
  const graph = view.graph;
  const runtime = graph.runtime!;
  const component = runtime.components.find((c) => c.id === componentId);
  if (!component) return <EmptyState title="Nothing selected" compact />;
  const names = new Map(runtime.components.map((c) => [c.id, c.name]));
  const abstractions = new Map((graph.abstractions ?? []).map((a) => [a.id, a]));
  const outgoing = runtime.links.filter((l) => l.source === component.id);
  const incoming = runtime.links.filter((l) => l.target === component.id);
  const flows = (graph.flows ?? []).filter((f) => f.steps.some((s) => s.component === component.id));
  const experiment = component.node_ids
    .map((id) => view.node_actions[id])
    .find((actions) => actions?.experiment.available && actions.experiment.question);
  const ask = (question: string) =>
    `/r/${jobId}/ask?${new URLSearchParams({ q: question }).toString()}`;
  const inferred = component.provenance === "inferred";

  return (
    <div className="flex flex-col gap-5" data-testid="runtime-inspector">
      <div>
        <h3 className="text-card font-semibold text-fg" data-testid="inspector-title">
          {component.name}
        </h3>
        {component.description && (
          <p className="mt-1 text-sm text-fg">
            {component.description}
            {inferred && <span className="ml-1 text-xs text-ai-fg">· Nemotron&apos;s wording</span>}
          </p>
        )}
      </div>

      {component.responsibilities.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">Responsibilities</h4>
          <ul className="mt-2 flex flex-col gap-2">
            {component.responsibilities.map((item) => (
              <li key={item.text} className="text-sm text-fg">
                <span>• {item.text}</span>
                <div className="mt-1">
                  <SourceRef reference={item.reference} compact reason={item.text} />
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">Source</h4>
        <ul className="mt-2 flex flex-col gap-1.5" data-testid="runtime-sources">
          {component.source_references.map((ref) => (
            <li key={`${ref.path}:${ref.start_line}`} className="min-w-0">
              <SourceRef reference={ref} reason={`Where ${component.name} is defined.`} />
            </li>
          ))}
        </ul>
      </section>

      {(outgoing.length > 0 || incoming.length > 0) && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">How it connects</h4>
          <ul className="mt-2 flex flex-col gap-2 text-sm" data-testid="runtime-connections">
            {outgoing.map((link) => (
              <Connection key={`o-${link.target}`} link={link} other={names.get(link.target) ?? link.target} direction="to" onSelect={() => onSelect(link.target)} />
            ))}
            {incoming.map((link) => (
              <Connection key={`i-${link.source}`} link={link} other={names.get(link.source) ?? link.source} direction="from" onSelect={() => onSelect(link.source)} />
            ))}
          </ul>
        </section>
      )}

      {flows.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">Related flows</h4>
          <div className="mt-2 flex flex-wrap gap-2">
            {flows.map((flow) => (
              <button
                key={flow.id}
                type="button"
                onClick={() => onShowFlow(flow.id)}
                className="rounded-full border border-arch-line px-3 py-1 text-xs text-arch-fg hover:bg-arch-soft"
              >
                Show flow: {flow.name}
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="flex flex-wrap gap-2">
        <ActionLink kind="ask" href={ask(`What does ${component.name} do, and who uses it?`)} data-testid="ask-why">
          Ask RepoLab about {component.name}
        </ActionLink>
        {experiment && (
          <ActionLink kind="experiment" href={ask(experiment.experiment.question!)}>
            Try an experiment
          </ActionLink>
        )}
      </section>
      <div className="flex flex-wrap gap-1.5" aria-label="Suggested questions">
        {[`Who calls ${component.name}?`, `What happens if ${component.name} fails?`, `Show me ${component.name}'s role in a flow.`].map(
          (question) => (
            <a
              key={question}
              href={ask(question)}
              className="rounded-full border border-line px-2.5 py-1 text-xs text-fg-secondary hover:border-ai-line hover:text-ai-fg"
            >
              {question}
            </a>
          ),
        )}
      </div>

      <details className="rounded-lg border border-line p-3" data-testid="why-repolab-thinks">
        <summary className="cursor-pointer text-sm font-medium text-fg">Why RepoLab thinks this</summary>
        <div className="mt-3 flex flex-col gap-3 text-sm">
          <p className="text-fg-secondary">
            {component.name} is made of {component.members.length === 1 ? "this definition" : "these definitions"},
            found in the code and ranked by how much of the repository uses them:
          </p>
          <ul className="flex flex-col gap-2">
            {component.members.map((id) => {
              const member = abstractions.get(id);
              if (!member) return null;
              return (
                <li key={id} className="flex flex-col gap-1">
                  <span className="text-fg">
                    <span className="font-mono">{member.name}</span> · {member.kind} · used in {member.referenced_by} other
                    files
                  </span>
                  <SourceRef reference={member.definition} />
                </li>
              );
            })}
          </ul>
          {[...outgoing, ...incoming].length > 0 && (
            <>
              <p className="text-fg-secondary">Each connection is a line in the code:</p>
              <ul className="flex flex-col gap-1.5">
                {[...outgoing, ...incoming].flatMap((link) =>
                  link.evidence.map((ref) => (
                    <li key={`${link.source}-${link.target}-${ref.path}-${ref.start_line}`} className="flex flex-col gap-1">
                      <span className="text-xs text-fg-secondary">
                        {names.get(link.source)} {link.type} {names.get(link.target)}
                        {ref.strength ? ` · ${STRENGTH_LABEL[ref.strength]}` : ""}
                      </span>
                      <SourceRef reference={ref} />
                    </li>
                  )),
                )}
              </ul>
            </>
          )}
          {inferred && (
            <p className="text-xs text-ai-fg">
              The name, description and responsibilities are Nemotron&apos;s wording; RepoLab kept them only where they
              point at these verified lines.
            </p>
          )}
        </div>
      </details>
    </div>
  );
}

function Connection({
  link,
  other,
  direction,
  onSelect,
}: {
  link: RuntimeLink;
  other: string;
  direction: "to" | "from";
  onSelect: () => void;
}) {
  return (
    <li className="flex flex-col gap-1">
      <span className="text-fg">
        {direction === "to" ? "→ " : "← "}
        <button type="button" onClick={onSelect} className="font-medium text-arch-fg hover:underline">
          {other}
        </button>
        <span className="text-fg-secondary">
          {" "}
          · {link.type}
          {link.label ? ` · ${link.label}` : ""}
        </span>
      </span>
      {link.evidence[0] && <SourceRef reference={link.evidence[0]} compact />}
    </li>
  );
}

// -- flows ----------------------------------------------------------------------------

export function FlowsView({
  graph,
  flowId,
  onSelectFlow,
  onSelectComponent,
}: {
  graph: Graph;
  flowId: string | null;
  onSelectFlow: (id: string) => void;
  onSelectComponent: (id: string) => void;
}) {
  const flows = graph.flows ?? [];
  const [mode, setMode] = useState("steps");
  if (flows.length === 0) {
    return (
      <EmptyState title="No end-to-end flow could be verified" data-testid="no-flows">
        RepoLab draws a flow only along calls and constructions it found in the code. None connected enough of the
        runtime components to follow one here.
      </EmptyState>
    );
  }
  const flow = flows.find((f) => f.id === flowId) ?? flows[0];
  const names = new Map((graph.runtime?.components ?? []).map((c) => [c.id, c.name]));
  return (
    <div className="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]" data-testid="flows-view">
      <ul className="flex flex-col gap-1" aria-label="Flows">
        {flows.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              onClick={() => onSelectFlow(item.id)}
              aria-current={item.id === flow.id ? "true" : undefined}
              data-testid="flow-item"
              className={`w-full rounded-md px-3 py-2 text-left text-sm ${
                item.id === flow.id ? "bg-arch-soft font-medium text-arch-fg" : "text-fg-secondary hover:bg-surface-hover"
              }`}
            >
              {item.name}
            </button>
          </li>
        ))}
      </ul>
      <Panel
        title={flow.name}
        subtitle={flow.description ?? undefined}
        action={
          flow.ordered ? (
            <Badge tone="run">
              <CheckIcon size={12} /> Order verified in code
            </Badge>
          ) : (
            <Badge>Order not verified</Badge>
          )
        }
      >
        <Tabs
          label="Flow view"
          value={flow.ordered ? mode : "steps"}
          onChange={setMode}
          size="sm"
          items={[
            { id: "steps", label: "Steps" },
            { id: "sequence", label: "Sequence", disabled: !flow.ordered },
          ]}
        >
          {flow.ordered && mode === "sequence" ? (
            <SequenceView flow={flow} names={names} />
          ) : (
            <FlowSteps flow={flow} names={names} onSelectComponent={onSelectComponent} />
          )}
        </Tabs>
      </Panel>
    </div>
  );
}

function FlowSteps({
  flow,
  names,
  onSelectComponent,
}: {
  flow: Flow;
  names: Map<string, string>;
  onSelectComponent: (id: string) => void;
}) {
  return (
    <ol className="flex flex-col" data-testid="flow-steps">
      {flow.steps.map((step, index) => (
        <li key={`${step.sequence}-${step.component}`} className="flex flex-col">
          <div className="rounded-lg border border-line bg-surface p-3" data-testid="flow-step">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-xs text-fg-muted">{String(step.sequence).padStart(2, "0")}</span>
              <button type="button" onClick={() => onSelectComponent(step.component)} className="text-sm font-semibold text-arch-fg hover:underline">
                {names.get(step.component) ?? step.component}
              </button>
              <span className="text-sm text-fg">{step.action}</span>
            </div>
            {(step.input || step.output) && (
              <p className="mt-1 text-xs text-fg-secondary">
                {step.input && <>Input: {step.input}</>}
                {step.input && step.output && " · "}
                {step.output && <>Output: {step.output}</>}
              </p>
            )}
            {step.references[0] && (
              <div className="mt-2">
                <SourceRef reference={step.references[0]} compact reason={`Where ${names.get(step.component)} hands over to the next step.`} />
              </div>
            )}
          </div>
          {index < flow.steps.length - 1 && <span aria-hidden className="ml-5 h-4 w-px bg-arch-line" />}
        </li>
      ))}
    </ol>
  );
}

/** Lifelines for each component, arrows in order. Only for flows whose order is verified. */
export function SequenceView({ flow, names }: { flow: Flow; names: Map<string, string> }) {
  const participants = [...new Set(flow.steps.map((s) => s.component))];
  const lane = 150;
  const rowHeight = 44;
  const top = 36;
  const width = participants.length * lane;
  const height = top + flow.steps.length * rowHeight + 16;
  const x = (id: string) => participants.indexOf(id) * lane + lane / 2;
  return (
    <div className="overflow-x-auto" data-testid="sequence-view">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Sequence of ${flow.name}`}>
        <defs>
          <marker id="seq-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-arch" />
          </marker>
        </defs>
        {participants.map((id) => (
          <g key={id}>
            <rect x={x(id) - 64} y={4} width={128} height={24} rx={6} className="fill-surface stroke-line-strong" />
            <text x={x(id)} y={20} textAnchor="middle" className="fill-fg text-[11px] font-medium">
              {(names.get(id) ?? id).slice(0, 20)}
            </text>
            <line x1={x(id)} x2={x(id)} y1={28} y2={height - 4} className="stroke-line-strong" strokeDasharray="3 3" />
          </g>
        ))}
        {flow.steps.slice(0, -1).map((step, index) => {
          const next = flow.steps[index + 1];
          const y = top + (index + 1) * rowHeight - 8;
          const x1 = x(step.component);
          const x2 = x(next.component);
          return (
            <g key={`${step.sequence}`}>
              <line x1={x1} x2={x2 === x1 ? x1 + 30 : x2} y1={y} y2={y} className="stroke-arch" strokeWidth={1.5} markerEnd="url(#seq-arrow)" />
              <text x={(x1 + x2) / 2} y={y - 6} textAnchor="middle" className="fill-fg-secondary text-[10px]">
                {step.action.slice(0, 28)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// -- modules and the source map -------------------------------------------------------

export function ModulesView({ graph, onExplore }: { graph: Graph; onExplore: (id: string) => void }) {
  const subsystems = (graph.subsystems ?? []).filter((s) => s.id !== "sub:other-modules");
  const label = new Map(subsystems.map((s) => [s.id, s.label]));
  if (subsystems.length === 0) {
    return <EmptyState title="No modules were found">The repository is a single part.</EmptyState>;
  }
  return (
    <ul className="grid gap-4 md:grid-cols-2" data-testid="modules-view">
      {subsystems.map((subsystem) => (
        <li key={subsystem.id} className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-4" data-testid="module-card">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-fg">{subsystem.label}</p>
              <p className="font-mono text-xs text-fg-muted">{subsystem.paths.map((p) => `${p || "."}/`).join(" ")}</p>
            </div>
            <button type="button" onClick={() => onExplore(subsystem.id)} className="shrink-0 text-xs font-medium text-arch-fg hover:underline">
              Explore →
            </button>
          </div>
          {subsystem.description && <p className="text-sm text-fg-secondary">{subsystem.description}</p>}
          {subsystem.outline.length > 0 && (
            <p className="text-xs text-fg-secondary">
              <span className="font-medium text-fg">Key areas:</span> {subsystem.outline.slice(0, 6).join(", ")}
            </p>
          )}
          {(subsystem.depends_on?.length ?? 0) > 0 && (
            <div className="text-xs" data-testid="module-depends-on">
              <p className="font-medium text-fg">Depends on</p>
              <ul className="mt-1 flex flex-col gap-1">
                {subsystem.depends_on!.map((dependency) => (
                  <li key={dependency.target} className="flex flex-wrap items-center gap-2">
                    <span className="text-fg">{label.get(dependency.target) ?? dependency.target}</span>
                    <span className="text-fg-muted">via {dependency.via}</span>
                    <SourceRef reference={dependency.evidence} compact />
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(subsystem.used_by?.length ?? 0) > 0 && (
            <p className="text-xs text-fg-secondary">
              <span className="font-medium text-fg">Used by:</span>{" "}
              {subsystem.used_by!.map((id) => label.get(id) ?? id).join(", ")}
            </p>
          )}
          <p className="mt-auto text-xs text-fg-muted">
            {subsystem.file_count} source files{subsystem.languages.length ? ` · ${subsystem.languages.join(", ")}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}

export function ReadingPath({ graph, onSelect }: { graph: Graph; onSelect: (id: string) => void }) {
  const items = graph.reading_path ?? [];
  if (items.length === 0) return null;
  return (
    <Panel title="Recommended reading path" subtitle="Where execution starts, then the parts in the order work flows through them">
      <ol className="flex flex-col gap-3" data-testid="reading-path">
        {items.map((item, index) => (
          <li key={`${item.reference.path}:${item.reference.start_line}`} className="flex gap-3">
            <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-arch-soft text-xs font-semibold text-arch-fg">
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-fg">
                {item.component ? (
                  <button type="button" onClick={() => onSelect(item.component!)} className="hover:underline">
                    {item.title}
                  </button>
                ) : (
                  item.title
                )}
              </p>
              <p className="text-sm text-fg-secondary">{item.why}</p>
              <div className="mt-1">
                <SourceRef reference={item.reference} reason={item.why} />
              </div>
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export function RepositoryMap({
  graph,
  onExplore,
  onSelect,
}: {
  graph: Graph;
  onExplore: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  const subsystems = (graph.subsystems ?? []).filter((s) => s.id !== "sub:other-modules");
  const components = graph.runtime?.components ?? [];
  if (subsystems.length === 0) return null;
  return (
    <Panel title="Repository map" subtitle="Each part of the repository, and the runtime components that live in it">
      <ul className="flex flex-col divide-y divide-line" data-testid="repository-map">
        {subsystems.map((subsystem) => {
          const here = components.filter((c) => c.subsystem_ids.includes(subsystem.id));
          return (
            <li key={subsystem.id} className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <button type="button" onClick={() => onExplore(subsystem.id)} className="text-sm font-semibold text-fg hover:text-arch-fg">
                  {subsystem.label}
                </button>
                <span className="font-mono text-xs text-fg-muted">{subsystem.paths.map((p) => `${p || "."}/`).join(" ")}</span>
              </div>
              {subsystem.description && <p className="text-sm text-fg-secondary">{subsystem.description}</p>}
              {here.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {here.map((component) => (
                    <button
                      key={component.id}
                      type="button"
                      onClick={() => onSelect(component.id)}
                      className="rounded-full border border-arch-line px-2.5 py-0.5 text-xs text-arch-fg hover:bg-arch-soft"
                    >
                      {component.name}
                    </button>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

export function refKey(ref: CodeReference): string {
  return `${ref.path}:${ref.start_line ?? ""}`;
}

export type { RuntimeComponent };
