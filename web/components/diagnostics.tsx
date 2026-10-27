/**
 * Operational numbers for developers: when the last event and the last
 * heartbeat arrived, which worker holds the job, the raw activity key.
 *
 * Hidden from normal users. Turned on with `?debug=1` (remembered in this
 * browser; `?debug=0` turns it off) or `NEXT_PUBLIC_REPOLAB_DEBUG=1`.
 */

"use client";

import { useEffect, useState } from "react";

import { formatElapsed } from "@/components/working-status";
import type { StreamStatus } from "@/lib/events";
import type { JobEvent, JobState, TaskActivity, TaskHealth } from "@/lib/types";

const KEY = "repolab.debug";

/** Whether developer diagnostics are on. False until mounted, so SSR matches. */
export function useDebug(): boolean {
  const [debug, setDebug] = useState(false);
  useEffect(() => {
    let on = process.env.NEXT_PUBLIC_REPOLAB_DEBUG === "1";
    try {
      const param = new URLSearchParams(window.location.search).get("debug");
      if (param === "1") window.localStorage.setItem(KEY, "1");
      if (param === "0") window.localStorage.removeItem(KEY);
      on = on || window.localStorage.getItem(KEY) === "1";
    } catch {
      // Storage can be unavailable (private mode): the URL alone still works.
      on = on || new URLSearchParams(window.location.search).get("debug") === "1";
    }
    // Read once from the browser after mount; nothing to subscribe to.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDebug(on);
  }, []);
  return debug;
}

function age(iso: string | null | undefined, now: number): string {
  if (!iso) return "—";
  const at = Date.parse(iso);
  return Number.isNaN(at) ? "—" : `${formatElapsed((now - at) / 1000)} ago`;
}

export function DiagnosticsTable({
  rows,
}: {
  rows: [string, string][];
}) {
  return (
    <details className="rounded-md border border-line p-3 text-xs" data-testid="diagnostics" open>
      <summary className="cursor-pointer font-medium text-fg-secondary">Diagnostics (developer)</summary>
      <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 font-mono">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-fg-muted">{label}</dt>
            <dd className="text-fg">{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function Diagnostics({
  job,
  stream,
  events,
  health,
  activity,
  heartbeatAt,
}: {
  job?: JobState | null;
  stream?: StreamStatus;
  events: JobEvent[];
  health?: TaskHealth | null;
  activity?: TaskActivity | null;
  heartbeatAt?: string | null;
}) {
  const debug = useDebug();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!debug) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [debug]);
  if (!debug) return null;

  const current = activity ?? job?.activity ?? null;
  const lastEvent = events.at(-1);
  return (
    <DiagnosticsTable
      rows={[
        ["Job state", job ? `${job.status}${job.attempts > 1 ? ` (attempt ${job.attempts})` : ""}` : "—"],
        ["Health", health ?? job?.health ?? "—"],
        ["Worker", job?.worker ?? "—"],
        ["Last heartbeat", age(heartbeatAt ?? job?.heartbeat_at, now)],
        ["Last progress", age(job?.last_progress_at, now)],
        ["Last SSE event", lastEvent ? `${age(lastEvent.created_at, now)} (${lastEvent.type})` : "—"],
        ["Stream", stream ?? "—"],
        ["Activity", current ? `${current.stage} / ${current.key}` : "—"],
        ["Activity since", age(current?.started_at, now)],
      ]}
    />
  );
}
