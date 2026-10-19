/**
 * Architecture — components, data flow, important files, concepts.
 *
 * Every claim on this screen cites a file. A node with no file reference is not
 * rendered: the backend already drops unsupported nodes, and the screen must not
 * reintroduce them.
 *
 * Spec: docs/design/03-architecture.md
 */

"use client";

import { ArchitectureGraph } from "@/components/architecture-graph";
import { Badge, Empty, FileRefs, Panel, Skeleton, Stat } from "@/components/ui";
import { count } from "@/lib/format";
import type { ArchitectureNode, CodeReference } from "@/lib/types";

import { useWorkspace } from "../workspace";

const KIND_ORDER: ArchitectureNode["kind"][] = [
  "entrypoint",
  "config",
  "service",
  "datastore",
  "module",
  "external",
];

const KIND_TITLE: Record<ArchitectureNode["kind"], string> = {
  entrypoint: "Entry points",
  config: "Configuration",
  service: "Services",
  datastore: "Datastores",
  module: "Modules",
  external: "Dependencies",
};

export default function ArchitecturePage() {
  const { architecture, manifest } = useWorkspace();

  if (!architecture || !manifest) {
    return (
      <Panel title="Architecture">
        <Skeleton rows={4} />
        <p className="mt-3 text-xs text-neutral-500">
          Available once the investigation has produced a manifest.
        </p>
      </Panel>
    );
  }

  // A component without evidence is a diagram, not an architecture.
  const nodes = architecture.graph.nodes.filter((node) => node.file_references.length > 0);
  const edges = architecture.graph.edges;
  const primary = manifest.interaction_surfaces.find((surface) => surface.primary);

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="What this repository is"
        action={
          <Badge tone={architecture.generated_by === "deterministic" ? "neutral" : "attention"}>
            {architecture.generated_by === "deterministic"
              ? "deterministic"
              : "model-enriched"}
          </Badge>
        }
      >
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Primary experience" value={primary?.type ?? "—"} mono={false} />
          <Stat label="Languages" value={manifest.languages.join(", ") || "—"} mono={false} />
          <Stat
            label="Frameworks"
            value={manifest.frameworks.join(", ") || "none detected"}
            mono={false}
          />
          <Stat
            label="Confidence"
            value={manifest.confidence.toFixed(2)}
            hint="Capped below certainty by design"
          />
        </dl>
        {primary && (
          <p className="mt-4 text-sm text-neutral-600 dark:text-neutral-400">
            {primary.reason}
          </p>
        )}
      </Panel>

      <Panel
        title="Components"
        subtitle={`${count(nodes.length)} components, each citing the files that justify it`}
      >
        {nodes.length === 0 ? (
          <Empty title="No evidence-backed components">
            Detection found nothing it could tie to a file in this repository.
          </Empty>
        ) : (
          <div className="flex flex-col gap-5" data-testid="components">
            {KIND_ORDER.map((kind) => {
              const group = nodes.filter((node) => node.kind === kind);
              if (group.length === 0) return null;
              return (
                <div key={kind}>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                    {KIND_TITLE[kind]}
                  </h3>
                  <ul className="mt-2 grid gap-3 sm:grid-cols-2">
                    {group.map((node) => (
                      <li
                        key={node.id}
                        className="rounded border border-neutral-200 p-3 dark:border-neutral-800"
                      >
                        <p className="font-mono text-sm font-medium">{node.name}</p>
                        {node.description && (
                          <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
                            {node.description}
                          </p>
                        )}
                        <FileRefs refs={node.file_references} limit={3} />
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <Panel
        title="Data flow"
        subtitle={`${count(edges.length)} relationships between components`}
      >
        {edges.length === 0 ? (
          <Empty title="No relationships detected" />
        ) : (
          <>
            <ArchitectureGraph nodes={nodes} edges={edges} />
            <ul className="mt-4 flex flex-col gap-1 border-t border-neutral-200 pt-3 dark:border-neutral-800">
              {edges.slice(0, 12).map((edge, index) => (
                <li key={`${edge.source}-${edge.target}-${index}`} className="font-mono text-xs">
                  <span>{label(edge.source)}</span>
                  <span className="mx-2 text-neutral-400">{edge.kind.replace(/_/g, " ")}</span>
                  <span>{label(edge.target)}</span>
                </li>
              ))}
              {edges.length > 12 && (
                <li className="text-xs text-neutral-400">+{edges.length - 12} more</li>
              )}
            </ul>
          </>
        )}
      </Panel>

      <Panel
        title="Important files"
        subtitle="Ranked by how many claims cite them"
      >
        <ol className="flex flex-col gap-1" data-testid="important-files">
          {rankFiles(nodes, manifest.concepts.flatMap((concept) => concept.code_references))
            .slice(0, 10)
            .map((entry) => (
              <li key={entry.path} className="flex items-baseline justify-between gap-4">
                <span className="font-mono text-xs">{entry.path}</span>
                <span className="shrink-0 text-xs text-neutral-400">
                  {entry.citations} {entry.citations === 1 ? "citation" : "citations"}
                </span>
              </li>
            ))}
        </ol>
      </Panel>

      <Panel
        title="Concepts"
        subtitle="What is worth understanding about this repository, with the code behind it"
      >
        {manifest.concepts.length === 0 ? (
          <Empty title="No concepts proposed">
            Concepts are only claimed where detection found real evidence for them.
          </Empty>
        ) : (
          <ul className="flex flex-col gap-4" data-testid="concepts">
            {manifest.concepts.map((concept) => (
              <li key={concept.name}>
                <p className="text-sm font-medium">{concept.name}</p>
                <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">
                  {concept.summary}
                </p>
                <FileRefs refs={concept.code_references} />
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

/** `entrypoint:etl_job` reads better as `etl_job`. */
function label(id: string): string {
  const colon = id.indexOf(":");
  return colon === -1 ? id : id.slice(colon + 1);
}

function rankFiles(
  nodes: ArchitectureNode[],
  extra: CodeReference[],
): { path: string; citations: number }[] {
  const counts = new Map<string, number>();
  for (const ref of [...nodes.flatMap((node) => node.file_references), ...extra]) {
    counts.set(ref.path, (counts.get(ref.path) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([path, citations]) => ({ path, citations }))
    .sort((a, b) => b.citations - a.citations || a.path.localeCompare(b.path));
}
