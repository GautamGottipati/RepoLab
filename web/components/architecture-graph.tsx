/**
 * The data-flow graph, drawn in SVG.
 *
 * Laid out in columns by node kind — entry points, config, services, external —
 * because that is the direction information actually travels in these
 * repositories, and a force layout would move on every render.
 *
 * The same relationships are listed in text beneath the diagram by the screen
 * that uses this, so the information never depends on being able to read a
 * picture.
 */

"use client";

import { useState } from "react";

import type { ArchitectureEdge, ArchitectureNode } from "@/lib/types";

/** Columns, left to right. Anything unlisted lands in the final column. */
const COLUMNS: ArchitectureNode["kind"][][] = [
  ["entrypoint"],
  ["config", "module"],
  ["service", "datastore"],
  ["external"],
];

const KIND_LABEL: Record<ArchitectureNode["kind"], string> = {
  entrypoint: "entry point",
  module: "module",
  service: "service",
  config: "config",
  datastore: "datastore",
  external: "dependency",
};

const BOX_WIDTH = 150;
const BOX_HEIGHT = 40;
const COLUMN_GAP = 80;
const ROW_GAP = 16;

export function ArchitectureGraph({
  nodes,
  edges,
}: {
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
}) {
  const [hovered, setHovered] = useState<string | null>(null);

  const columns = COLUMNS.map((kinds) => nodes.filter((node) => kinds.includes(node.kind)));
  const placed = nodes.filter((node) => columns.some((column) => column.includes(node)));
  const leftover = nodes.filter((node) => !placed.includes(node));
  if (leftover.length) columns[columns.length - 1] = [...columns[columns.length - 1], ...leftover];

  const used = columns.filter((column) => column.length > 0);
  const rows = Math.max(1, ...used.map((column) => column.length));
  const width = used.length * BOX_WIDTH + Math.max(0, used.length - 1) * COLUMN_GAP;
  const height = rows * (BOX_HEIGHT + ROW_GAP);

  const position = new Map<string, { x: number; y: number }>();
  used.forEach((column, columnIndex) => {
    const offset = (height - column.length * (BOX_HEIGHT + ROW_GAP)) / 2;
    column.forEach((node, rowIndex) => {
      position.set(node.id, {
        x: columnIndex * (BOX_WIDTH + COLUMN_GAP),
        y: offset + rowIndex * (BOX_HEIGHT + ROW_GAP),
      });
    });
  });

  const drawable = edges.filter(
    (edge) => position.has(edge.source) && position.has(edge.target),
  );

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`-8 -8 ${width + 16} ${height + 16}`}
        width={width + 16}
        height={height + 16}
        role="img"
        aria-label={`${nodes.length} components and ${drawable.length} relationships`}
        data-testid="architecture-graph"
        className="max-w-full"
      >
        <defs>
          <marker
            id="arrow"
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

        {drawable.map((edge, index) => {
          const from = position.get(edge.source)!;
          const to = position.get(edge.target)!;
          const startX = from.x + BOX_WIDTH;
          const startY = from.y + BOX_HEIGHT / 2;
          const endY = to.y + BOX_HEIGHT / 2;
          const midX = (startX + to.x) / 2;
          const active = hovered === edge.source || hovered === edge.target;

          return (
            <path
              key={`${edge.source}-${edge.target}-${index}`}
              d={`M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${to.x} ${endY}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={active ? 1.8 : 1}
              markerEnd="url(#arrow)"
              className={
                active
                  ? "text-sky-500"
                  : "text-neutral-300 dark:text-neutral-700"
              }
            />
          );
        })}

        {nodes.map((node) => {
          const at = position.get(node.id);
          if (!at) return null;
          const active = hovered === node.id;

          return (
            <g
              key={node.id}
              transform={`translate(${at.x} ${at.y})`}
              onMouseEnter={() => setHovered(node.id)}
              onMouseLeave={() => setHovered(null)}
              className="cursor-default"
            >
              <rect
                width={BOX_WIDTH}
                height={BOX_HEIGHT}
                rx="6"
                className={
                  active
                    ? "fill-sky-50 stroke-sky-500 dark:fill-sky-950"
                    : "fill-white stroke-neutral-300 dark:fill-neutral-900 dark:stroke-neutral-700"
                }
                strokeWidth="1"
              />
              <text
                x="10"
                y="17"
                className="fill-neutral-900 text-[11px] font-medium dark:fill-neutral-100"
              >
                {truncate(node.name, 20)}
              </text>
              <text x="10" y="30" className="fill-neutral-500 text-[9px]">
                {KIND_LABEL[node.kind]}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}
