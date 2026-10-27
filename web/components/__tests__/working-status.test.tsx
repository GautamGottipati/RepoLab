/**
 * Long waits: what RepoLab is doing, said calmly. Elapsed time changes only the
 * tone; only a stopped heartbeat produces a warning.
 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Diagnostics } from "../diagnostics";
import { WorkingStatus, formatElapsed, workingWords } from "../working-status";

const T0 = new Date("2026-10-27T12:00:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

function advance(seconds: number) {
  act(() => {
    vi.advanceTimersByTime(seconds * 1000);
  });
}

describe("WorkingStatus", () => {
  it("follows the acceptance scenario: the same step, calmer words over time, never 'stalled'", () => {
    const view = render(
      <WorkingStatus activity="architecture.components" stage="architecture" startedAt={T0.toISOString()} health="working" />,
    );
    const text = () => screen.getByTestId("working-status").textContent ?? "";

    advance(5);
    expect(text()).toContain("Identifying major architecture components…");
    expect(text()).toContain("Working for 5s");
    expect(text()).not.toContain("longer");

    // At 25 s the backend moved on to a real new step.
    view.rerender(
      <WorkingStatus
        activity="architecture.runtime"
        stage="architecture"
        startedAt={new Date(T0.getTime() + 5000).toISOString()}
        health="working"
      />,
    );
    advance(20);
    expect(text()).toContain("Tracing runtime relationships…");
    expect(text()).toContain("Working for 20s");

    advance(25); // 45 s on this step (it began at 5 s)
    expect(text()).toContain("Tracing runtime relationships…");
    expect(text()).toContain("This step is taking a little longer than usual.");

    advance(20); // 65 s on this step
    expect(text()).toContain("Still working");
    expect(text()).toContain("Tracing runtime relationships.");
    expect(text()).toContain("Large repositories can take a little longer.");
    expect(text()).toContain("Working for 1m 5s");

    for (const forbidden of [/stall/i, /hung/i, /worker/i, /%/]) expect(text()).not.toMatch(forbidden);
  });

  it("keeps the headline until the activity key itself changes -- nothing cycles on a timer", () => {
    const first = workingWords("finding_entrypoints", "investigation", 10).headline;
    expect(workingWords("finding_entrypoints", "investigation", 25).headline).toBe(first);
    expect(workingWords("finding_entrypoints", "investigation", 59).headline).toBe(first);
  });

  it("falls back to the stage's words for an activity it does not know", () => {
    expect(workingWords("something.new", "architecture", 1).headline).toBe("Analyzing repository architecture…");
    expect(workingWords(null, null, null).headline).toBe("Working…");
  });

  it("warns only when the heartbeat has stopped, and offers Retry", () => {
    const retry = vi.fn();
    render(<WorkingStatus activity="architecture.runtime" startedAt={T0.toISOString()} health="interrupted" onRetry={retry} />);
    const status = screen.getByTestId("working-status");
    expect(status.getAttribute("role")).toBe("alert");
    expect(status.textContent).toContain("Processing may have been interrupted");
    expect(status.textContent).toContain("hasn't received a worker heartbeat recently");
    fireEvent.click(screen.getByTestId("working-status-retry"));
    expect(retry).toHaveBeenCalled();
  });

  it("a waiting task shows no clock", () => {
    render(<WorkingStatus activity={null} startedAt={T0.toISOString()} health="waiting" />);
    expect(screen.getByTestId("working-status").textContent).toContain("Waiting to start");
    expect(screen.queryByTestId("working-status-elapsed")).toBeNull();
  });

  it("formats elapsed time quietly", () => {
    expect(formatElapsed(38)).toBe("38s");
    expect(formatElapsed(72)).toBe("1m 12s");
  });
});

describe("developer diagnostics", () => {
  const job = {
    job_id: "j",
    status: "running" as const,
    repo_url: "https://github.com/apache/spark",
    attempts: 1,
    last_seq: 3,
    event_count: 3,
    elapsed_seconds: 40,
    health: "working" as const,
    activity: { stage: "architecture", key: "architecture.components", started_at: T0.toISOString() },
    heartbeat_at: T0.toISOString(),
    worker: "rq-worker-2",
    last_progress_at: T0.toISOString(),
    error: null,
    ingestion_id: 1,
  };

  it("are hidden from normal users", async () => {
    render(<Diagnostics job={job} events={[]} />);
    await act(async () => {});
    expect(screen.queryByTestId("diagnostics")).toBeNull();
  });

  it("appear with ?debug=1", async () => {
    window.history.replaceState(null, "", "/?debug=1");
    render(<Diagnostics job={job} stream="open" events={[]} />);
    await act(async () => {});
    const panel = screen.getByTestId("diagnostics").textContent ?? "";
    expect(panel).toContain("rq-worker-2");
    expect(panel).toContain("architecture / architecture.components");
    expect(panel).toContain("Last heartbeat");
  });
});
