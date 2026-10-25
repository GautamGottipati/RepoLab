/**
 * The architecture graph, as a layered SVG that reads left to right.
 *
 * A component's column is where it falls in the flow, so "browser → frontend →
 * API → database" or "read → join → aggregate → write" reads in order. Primary
 * flow edges are emphasised; inferred relationships are dashed, so a reader can
 * tell at a glance which arrows were read from code and which were interpreted.
 *
 * Components are buttons: click or press Enter to inspect. The same
 * relationships are listed as text by the page, so nothing depends on reading a
 * picture.
 */

"use client";

import { CATEGORY_LABEL, CATEGORY_TONE, flowEdges, layout } from "@/lib/architecture";
import type { ArchitectureGraph as Graph, ArchitectureNode, MeasuredValue } from "@/lib/types";

const BOX_WIDTH = 170;
const BOX_HEIGHT = 48;
const COLUMN_GAP = 64;
const ROW_GAP = 18;

const TONE_CLASS: Record<string, string> = {
  slate: "fill-slate-50 stroke-slate-400 dark:fill-slate-900 dark:stroke-slate-600",
  teal: "fill-teal-50 stroke-teal-500 dark:fill-teal-950 dark:stroke-teal-700",
  violet: "fill-violet-50 stroke-violet-500 dark:fill-violet-950 dark:stroke-violet-700",
  sky: "fill-sky-50 stroke-sky-500 dark:fill-sky-950 dark:stroke-sky-700",
  amber: "fill-amber-50 stroke-amber-500 dark:fill-amber-950 dark:stroke-amber-700",
  emerald: "fill-emerald-50 stroke-emerald-500 dark:fill-emerald-950 dark:stroke-emerald-700",
  rose: "fill-rose-50 stroke-rose-500 dark:fill-rose-950 dark:stroke-rose-700",
  indigo: "fill-indigo-50 stroke-indigo-500 dark:fill-indigo-950 dark:stroke-indigo-700",
  fuchsia: "fill-fuchsia-50 stroke-fuchsia-500 dark:fill-fuchsia-950 dark:stroke-fuchsia-700",
};

export function ArchitectureGraph({
  graph,
  selected,
  highlighted,
  measured,
  onSelect,
  caption = defaultCaption,
  kind = (node) => CATEGORY_LABEL[node.category],
}: {
  graph: Graph;
  selected: string | null;
  highlighted: Set<string>;
  measured: Record<string, MeasuredValue[]>;
  onSelect: (nodeId: string) => void;
  /** The small second line of a box. Defaults to category and technology. */
  caption?: (node: ArchitectureNode) => string;
  /** What a box is, for screen readers. */
  kind?: (node: ArchitectureNode) => string;
}) {
  const placements = layout(graph);
  const emphasised = flowEdges(graph);
  const columns = Math.max(1, ...[...placements.values()].map((p) => p.rank + 1));
  const rows = Math.max(1, ...[...placements.values()].map((p) => p.row + 1));
  const width = columns * BOX_WIDTH + (columns - 1) * COLUMN_GAP;
  const height = rows * BOX_HEIGHT + (rows - 1) * ROW_GAP;
  const at = (id: string) => {
    const p = placements.get(id);
    return p ? { x: p.rank * (BOX_WIDTH + COLUMN_GAP), y: p.row * (BOX_HEIGHT + ROW_GAP) } : null;
  };

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`-8 -8 ${width + 16} ${height + 16}`}
        width={width + 16}
        height={height + 16}
        role="group"
        aria-label={`${graph.nodes.length} components and ${graph.edges.length} relationships`}
        data-testid="architecture-graph"
        className="max-w-none"
      >
        <defs>
          <marker
            id="arch-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>

        {graph.edges.map((edge, index) => {
          const from = at(edge.source);
          const to = at(edge.target);
          if (!from || !to) return null;
          const forward = to.x > from.x;
          const startX = forward ? from.x + BOX_WIDTH : from.x + BOX_WIDTH / 2;
          const startY = forward ? from.y + BOX_HEIGHT / 2 : from.y + BOX_HEIGHT;
          const endX = forward ? to.x : to.x + BOX_WIDTH / 2;
          const endY = forward ? to.y + BOX_HEIGHT / 2 : to.y;
          const midX = (startX + endX) / 2;
          const onFlow = emphasised.has(`${edge.source}->${edge.target}`);
          const active = selected === edge.source || selected === edge.target;
          return (
            <path
              key={`${edge.source}-${edge.target}-${index}`}
              data-testid="architecture-edge"
              data-provenance={edge.provenance}
              d={
                forward
                  ? `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`
                  : `M ${startX} ${startY} L ${endX} ${endY}`
              }
              fill="none"
              stroke="currentColor"
              strokeWidth={active || onFlow ? 1.8 : 1}
              strokeDasharray={edge.provenance === "inferred" ? "4 3" : undefined}
              markerEnd="url(#arch-arrow)"
              className={
                active
                  ? "text-sky-600"
                  : onFlow
                    ? "text-neutral-500 dark:text-neutral-400"
                    : "text-neutral-300 dark:text-neutral-700"
              }
            >
              <title>{edge.type.replace(/_/g, " ")}</title>
            </path>
          );
        })}

        {graph.nodes.map((node) => {
          const position = at(node.id);
          if (!position) return null;
          const isSelected = selected === node.id;
          const isHighlighted = highlighted.has(node.id);
          const values = measured[node.id] ?? [];
          return (
            <g
              key={node.id}
              transform={`translate(${position.x} ${position.y})`}
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              aria-label={`${node.label}, ${kind(node)}`}
              data-testid="architecture-node"
              data-node-id={node.id}
              data-highlighted={isHighlighted || undefined}
              onClick={() => onSelect(node.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(node.id);
                }
              }}
              className="cursor-pointer outline-none"
            >
              <rect
                width={BOX_WIDTH}
                height={BOX_HEIGHT}
                rx="7"
                strokeWidth={isSelected || isHighlighted ? 2.5 : 1}
                strokeDasharray={node.children.length ? "5 3" : undefined}
                className={`${TONE_CLASS[CATEGORY_TONE[node.category]]} ${
                  isHighlighted ? "stroke-sky-600" : ""
                }`}
              />
              <text x="10" y="19" className="fill-neutral-900 text-[11px] font-medium dark:fill-neutral-100">
                {truncate(node.label, 24)}
              </text>
              <text x="10" y="34" className="fill-neutral-500 text-[9px]">
                {truncate(caption(node), 30)}
              </text>
              {values.length > 0 && (
                <g data-testid="measured-badge">
                  <rect x={BOX_WIDTH - 16} y="-6" width="20" height="14" rx="7" className="fill-emerald-600" />
                  <text x={BOX_WIDTH - 6} y="4" textAnchor="middle" className="fill-white text-[8px] font-semibold">
                    M
                  </text>
                  <title>{`${values.length} measured value(s)`}</title>
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function defaultCaption(node: ArchitectureNode): string {
  return CATEGORY_LABEL[node.category] + (node.technology ? ` · ${truncate(node.technology, 14)}` : "");
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}
