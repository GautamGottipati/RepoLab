/**
 * Subscribing to a job's event stream.
 *
 * `EventSource` resends the last id it saw as `Last-Event-ID` after a reconnect,
 * and the backend serves replay and live streaming from one ordered query — so a
 * refresh mid-run restores history and continues with no gap and no duplicate.
 * That property is demo-critical, and this hook is where the browser's half of
 * it lives.
 *
 * Events are accumulated here rather than in a screen, so switching tabs does
 * not drop them. The hook is used once per stream, by a layout or a screen that
 * outlives the work it is watching.
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { EventType, JobEvent } from "./types";

export type StreamStatus = "idle" | "connecting" | "open" | "closed";

export type EventStream = {
  events: JobEvent[];
  status: StreamStatus;
  /** Events of one type, oldest first. */
  ofType: (type: EventType) => JobEvent[];
  /** The most recent event of a type, if any. */
  latest: (type: EventType) => JobEvent | undefined;
};

type State = {
  url: string | null;
  events: JobEvent[];
  status: StreamStatus;
  /** Highest sequence applied so far. See the dedup note in the reducer below. */
  highest: number;
  /**
   * Set once the backend has said there is nothing more coming.
   *
   * `EventSource` reconnects whenever the server closes, and the backend closes
   * every stream for a finished job — so without this the browser reconnected
   * roughly every three seconds for as long as the tab stayed open, each one a
   * request and a database query for events that will never exist. Measured at
   * four reconnects in twelve idle seconds.
   */
  finished: boolean;
};

/**
 * Follow an SSE endpoint, accumulating its events.
 *
 * Passing `null` disconnects, which is how a screen stops watching a finished
 * run without unmounting.
 */
export function useEventStream(url: string | null): EventStream {
  const [state, setState] = useState<State>({
    url,
    events: [],
    status: "idle",
    highest: 0,
    finished: false,
  });

  // Adjusted during render rather than in an effect. Resetting accumulated
  // events is a *derivation* from the url changing, and doing it in an effect
  // would mean one render showing the previous stream's events under the new
  // url — besides triggering a cascading render.
  if (state.url !== url) {
    setState({
      url,
      events: [],
      status: url ? "connecting" : "idle",
      highest: 0,
      finished: false,
    });
  }

  useEffect(() => {
    if (!url || state.finished) return;

    const source = new EventSource(url);

    const open = () => setState((current) => ({ ...current, status: "open" }));

    const handle = (message: MessageEvent<string>) => {
      let event: JobEvent;
      try {
        event = JSON.parse(message.data) as JobEvent;
      } catch {
        return; // A frame we cannot parse is not worth breaking the stream over.
      }
      // The backend's last frame for a finished job. Applied like any other
      // event, and then the connection is not re-established.
      const terminal = event.data?.["terminal"] === true;

      setState((current) => {
        if (current.url !== url) return current;
        // A reconnect replays from Last-Event-ID, so a frame already applied can
        // arrive again. The backend's sequence is contiguous and ascending per
        // job, which makes a monotonic check both sufficient and O(1) — no set
        // of seen ids to keep, and nothing to reset.
        if (event.seq <= current.highest) return current;
        return {
          ...current,
          events: [...current.events, event],
          highest: event.seq,
          finished: current.finished || terminal,
          status: terminal ? "closed" : current.status,
        };
      });

      if (terminal) source.close();
    };

    const failed = () => {
      // EventSource reconnects on its own unless the stream is finished; the
      // backend sends a terminal frame and closes, which surfaces here.
      //
      // Reporting `connecting` while it retries matters: the status drives a
      // "live" badge, and claiming live while the backend is down is the
      // product telling a confident lie about its own state.
      const status: StreamStatus =
        source.readyState === EventSource.CLOSED ? "closed" : "connecting";
      setState((current) => (current.url === url ? { ...current, status } : current));
    };

    source.addEventListener("open", open);
    source.addEventListener("error", failed);

    // The backend names each frame with its event type, so `onmessage` — which
    // only receives unnamed frames — never fires. Every type is registered.
    source.addEventListener("message", handle as EventListener);
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, handle as EventListener);
    }

    return () => source.close();
  }, [url, state.finished]);

  // Memoised so the callbacks below do not get a new dependency every render.
  const events = useMemo(
    () => (state.url === url ? state.events : []),
    [state.url, state.events, url],
  );

  const ofType = useCallback(
    (type: EventType) => events.filter((event) => event.type === type),
    [events],
  );
  const latest = useCallback(
    (type: EventType) => [...events].reverse().find((event) => event.type === type),
    [events],
  );

  return { events, status: state.url === url ? state.status : "idle", ofType, latest };
}

/** Every event type the backend names its frames with. */
const EVENT_TYPES: EventType[] = [
  "agent_started",
  "agent_status",
  "tool_requested",
  "tool_started",
  "tool_completed",
  "step_started",
  "progress",
  "log_line",
  "partial_result",
  "step_done",
  "failed",
  "heartbeat",
  "approval_required",
  "approval_received",
  "experiment_variant_started",
  "experiment_variant_completed",
  "upgrade_available",
  "upgrade_applied",
];

/**
 * Read a named key from an event's `data`, with a type guard.
 *
 * Screens use this rather than reaching into `data` directly, so every field a
 * screen renders is one it named — and an unknown blob can never reach the DOM.
 */
export function field<T>(
  event: JobEvent | undefined,
  key: string,
  guard: (value: unknown) => value is T,
): T | undefined {
  if (!event) return undefined;
  const value = event.data?.[key];
  return guard(value) ? value : undefined;
}

export const isNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
export const isString = (value: unknown): value is string => typeof value === "string";
export const isBoolean = (value: unknown): value is boolean => typeof value === "boolean";
