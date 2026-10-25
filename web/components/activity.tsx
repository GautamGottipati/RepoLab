/**
 * Agent activity: the phase tracker and the event feed.
 *
 * Both read the same stream. The tracker renders from `agent_status` events
 * carrying `{phase, label, index, total}` — the labels come from the backend
 * (`repolab/events/activity.py`), so the browser holds no copy of that
 * vocabulary and the two cannot disagree.
 *
 * **Neither renders anything but `message` and named `data` keys.** An unknown
 * `data` blob never reaches the DOM, so this path cannot become a way for model
 * reasoning to surface — the backend does not persist any, and the UI could not
 * show it if it did.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { field, isNumber, isString } from "@/lib/events";
import type { JobEvent } from "@/lib/types";

export type Phase = { key: string; label: string; index: number; total: number };

/** Phases announced so far, in order, deduplicated. */
export function phasesFrom(events: JobEvent[]): Phase[] {
  const seen = new Map<string, Phase>();
  for (const event of events) {
    if (event.type !== "agent_status") continue;
    const key = field(event, "phase", isString);
    const label = field(event, "label", isString);
    const index = field(event, "index", isNumber);
    const total = field(event, "total", isNumber);
    if (key && label && index && total && !seen.has(key)) {
      seen.set(key, { key, label, index, total });
    }
  }
  return [...seen.values()].sort((a, b) => a.index - b.index);
}

export function PhaseTracker({ events, done }: { events: JobEvent[]; done: boolean }) {
  const announced = phasesFrom(events);
  const total = announced[0]?.total ?? 4;
  const current = announced.at(-1);

  // Every slot is shown from the start, so the shape of the work is visible
  // before it happens. A slot with no event yet is pending and unnamed.
  const slots = Array.from({ length: total }, (_, position) =>
    announced.find((phase) => phase.index === position + 1),
  );

  return (
    <ol className="flex flex-col gap-1" aria-label="Investigation phases">
      {slots.map((phase, position) => {
        const state = !phase
          ? "pending"
          : done || (current && phase.index < current.index)
            ? "done"
            : "active";
        return (
          <li
            key={phase?.key ?? `pending-${position}`}
            data-phase={phase?.key}
            data-state={state}
            className="flex items-center gap-2 text-sm"
          >
            <span
              aria-hidden
              className={`flex size-5 shrink-0 items-center justify-center rounded-full border text-[10px] ${
                state === "done"
                  ? "border-emerald-500 bg-emerald-500 text-white"
                  : state === "active"
                    ? "animate-pulse border-sky-500 bg-sky-500 text-white"
                    : "border-neutral-300 text-neutral-400 dark:border-neutral-700"
              }`}
            >
              {state === "done" ? "✓" : position + 1}
            </span>
            <span
              className={
                state === "pending"
                  ? "text-neutral-400 dark:text-neutral-600"
                  : state === "active"
                    ? "font-medium"
                    : ""
              }
            >
              {phase?.label ?? "…"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** How each event type reads in the feed. */
const TONE: Record<string, string> = {
  failed: "text-red-600 dark:text-red-400",
  approval_required: "text-violet-700 dark:text-violet-300",
  approval_received: "text-violet-700 dark:text-violet-300",
  experiment_variant_started: "text-sky-700 dark:text-sky-300",
  experiment_variant_completed: "text-emerald-700 dark:text-emerald-300",
  partial_result: "text-emerald-700 dark:text-emerald-300",
  agent_status: "font-medium",
};

const MARK: Record<string, string> = {
  agent_started: "▶",
  agent_status: "◆",
  tool_requested: "›",
  tool_started: "·",
  tool_completed: "·",
  step_started: "▸",
  step_done: "✓",
  partial_result: "•",
  progress: "·",
  log_line: "·",
  failed: "!",
  approval_required: "?",
  approval_received: "+",
  experiment_variant_started: "▸",
  experiment_variant_completed: "✓",
  heartbeat: "·",
};

export function ActivityFeed({
  events,
  className = "",
  height = "h-96",
}: {
  events: JobEvent[];
  className?: string;
  height?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [events]);

  return (
    <div
      ref={scroller}
      data-testid="activity-feed"
      aria-live="polite"
      aria-label="Agent activity"
      className={`${height} overflow-y-auto rounded border border-neutral-200 bg-neutral-50 p-2 font-mono text-xs dark:border-neutral-800 dark:bg-neutral-950 ${className}`}
    >
      {events.length === 0 && <p className="p-2 text-neutral-500">Waiting for the agent…</p>}
      {events.map((event) => (
        <div key={event.seq} className="flex gap-2 py-0.5">
          <span aria-hidden className="w-3 shrink-0 text-neutral-400">
            {MARK[event.type] ?? "·"}
          </span>
          <span className={`break-words ${TONE[event.type] ?? ""}`}>
            {/* Only `message` — a concise, user-facing status by construction. */}
            {event.message || event.type}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * The agent activity sidebar. While the investigation runs, the live phases and
 * feed are shown. Once it is done they fold into a one-line summary -- useful
 * for trust, distracting while learning -- and expand on request. Nothing is
 * dropped.
 */
export function AgentActivityPanel({
  events,
  done,
  summary,
}: {
  events: JobEvent[];
  done: boolean;
  summary: { evidence: number; components: number; flows: number } | null;
}) {
  const [open, setOpen] = useState(false);
  const phases = phasesFrom(events);
  const expanded = !done || open;
  return (
    <div data-testid="agent-activity">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Agent activity</h2>
      {done && (
        <div className="mt-3 rounded border border-neutral-200 p-3 text-xs dark:border-neutral-800" data-testid="activity-summary">
          <p className="font-medium">✓ Repository analyzed</p>
          {summary && (
            <ul className="mt-1 text-neutral-600 dark:text-neutral-400">
              <li>{summary.evidence} pieces of evidence</li>
              <li>{summary.components} components discovered</li>
              <li>
                {summary.flows} major flow{summary.flows === 1 ? "" : "s"} identified
              </li>
            </ul>
          )}
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            data-testid="toggle-activity"
            className="mt-2 underline underline-offset-2"
          >
            {open ? "Hide analysis details" : "View analysis details"}
          </button>
        </div>
      )}
      {expanded && (
        <div data-testid="activity-details">
          <div className="mt-3">
            <PhaseTracker events={events} done={done} />
          </div>
          <p className="mt-3 text-xs text-neutral-400">
            {phases.length} of {phases[0]?.total ?? 4} phases · {events.length} events
          </p>
          <div className="mt-2">
            <ActivityFeed events={events} height="h-[28rem]" />
          </div>
        </div>
      )}
    </div>
  );
}
