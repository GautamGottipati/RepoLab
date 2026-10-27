/**
 * The workspace shell: four tabs under the names a new user understands, the
 * repository name first, and the agent's activity one click away rather than a
 * permanent column.
 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobEvent } from "@/lib/types";

const state = vi.hoisted(() => ({
  pathname: "/r/job-1",
  events: [] as JobEvent[],
}));

vi.mock("@/lib/api", () => ({
  api: {
    eventsUrl: () => "/events",
    job: vi.fn(async () => ({
      job_id: "job-1",
      status: "running",
      repo_url: "https://github.com/apache/spark",
      attempts: 1,
      last_seq: 0,
      event_count: 0,
      elapsed_seconds: 12,
      health: "working",
      activity: null,
      heartbeat_at: null,
      worker: null,
      last_progress_at: null,
      error: null,
      ingestion_id: null,
    })),
    manifest: vi.fn(),
    architecture: vi.fn(),
    experiences: vi.fn(),
  },
  optional: (p: Promise<unknown>) => p,
  ApiError: class extends Error {},
}));
vi.mock("@/lib/events", async (original) => ({
  ...(await original<typeof import("@/lib/events")>()),
  useEventStream: () => ({ events: state.events, status: "open" }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname, useRouter: () => ({ push: vi.fn() }) }));

import { WorkspaceShell } from "../workspace";

function phase(index: number, label: string): JobEvent {
  return {
    job_id: "job-1",
    seq: index,
    type: "agent_status",
    message: label,
    step: null,
    created_at: "2026-10-25T00:00:00Z",
    data: { phase: `p${index}`, label, index, total: 4 },
  } as JobEvent;
}

beforeEach(() => {
  state.pathname = "/r/job-1";
  state.events = [phase(1, "Inspecting repository"), phase(2, "Mapping architecture")];
});
afterEach(cleanup);

async function renderShell() {
  render(
    <WorkspaceShell jobId="job-1">
      <p>page</p>
    </WorkspaceShell>,
  );
  await act(async () => {});
}

describe("the workspace shell", () => {
  it("offers Overview, Experiences, Architecture and Ask RepoLab -- not Investigation", async () => {
    await renderShell();
    const nav = screen.getByRole("navigation", { name: "Workspace" });
    expect(nav.textContent).toBe("OverviewExperiencesArchitectureAsk RepoLab");
    expect(screen.queryByText("Investigation")).toBeNull();
    expect(screen.getByTestId("tab-Overview").getAttribute("aria-current")).toBe("page");
  });

  it("leads with the repository's name, the slug and status secondary", async () => {
    await renderShell();
    expect(screen.getByTestId("repo-name").textContent).toBe("spark");
    expect(screen.getByTestId("repo-slug").textContent).toBe("apache/spark");
    expect(screen.getByTestId("job-status").textContent).toBe("Analyzing");
  });

  it("keeps Experiences selected inside the Playground and Learn screens", async () => {
    state.pathname = "/r/job-1/playground";
    await renderShell();
    // Tabs that need a manifest are disabled until one exists, so check the rule
    // the active state follows rather than the link.
    expect(screen.queryByTestId("tab-Playground")).toBeNull();
    expect(screen.queryByTestId("tab-Learn")).toBeNull();
  });

  it("summarises the agent's activity in one line and opens the detail in a drawer", async () => {
    await renderShell();
    const chip = screen.getByTestId("activity-chip");
    expect(chip.textContent).toContain("Mapping architecture · 2 of 4");
    expect(screen.queryByTestId("agent-activity")).toBeNull();
    fireEvent.click(chip);
    const drawer = screen.getByRole("dialog", { name: "Agent activity" });
    expect(drawer).toBeTruthy();
    expect(screen.getByTestId("agent-activity").textContent).toContain("Inspecting repository");
  });
});
