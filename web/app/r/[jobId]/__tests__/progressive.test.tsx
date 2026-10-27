/**
 * The workspace as artifacts arrive: statuses from GET (so a refresh restores
 * them), only the affected data fetched again on `artifact_ready`, a dot on the
 * tabs still working, and an on-demand artifact requested once.
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useArtifact } from "@/components/artifact";
import type { ArtifactState, ArtifactStatus, ArtifactType, JobEvent } from "@/lib/types";

const state = vi.hoisted(() => ({
  events: [] as JobEvent[],
  statuses: {} as Record<string, string>,
}));

const api = vi.hoisted(() => ({
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
    last_progress_at: null as string | null,
    error: null,
    ingestion_id: 7,
  })),
  artifacts: vi.fn(),
  requestArtifact: vi.fn(),
  manifest: vi.fn(async () => ({ commit_sha: "a".repeat(40), languages: [], frameworks: [] })),
  architecture: vi.fn(async () => null),
  experiences: vi.fn(async () => null),
}));

vi.mock("@/lib/api", () => ({
  api,
  optional: (p: Promise<unknown>) => p,
  ApiError: class extends Error {},
}));
vi.mock("@/lib/events", async (original) => ({
  ...(await original<typeof import("@/lib/events")>()),
  useEventStream: () => ({ events: state.events, status: "open" }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/r/job-1", useRouter: () => ({ push: vi.fn() }) }));

import { WorkspaceShell } from "../workspace";

function artifact(type: ArtifactType, status: ArtifactStatus, updated = "t0"): ArtifactState {
  return {
    type,
    status,
    priority: "P0",
    version: "1",
    lazy: type === "architecture.runtime_naming",
    depends_on: [],
    description: "",
    updated_at: updated,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    cache_hits: 0,
    error: null,
  };
}

function listing() {
  return {
    job_id: "job-1",
    commit_sha: "a".repeat(40),
    artifacts: Object.entries(state.statuses).map(([type, status]) =>
      artifact(type as ArtifactType, status as ArtifactStatus, `${type}:${status}`),
    ),
  };
}

function ready(seq: number, type: ArtifactType): JobEvent {
  return {
    job_id: "job-1",
    seq,
    type: "artifact_ready",
    message: "",
    step: "artifacts",
    created_at: "2026-10-26T00:00:00Z",
    data: { artifact_type: type, status: "ready", version: "1", cache_hit: false },
  };
}

function Requester() {
  const naming = useArtifact("architecture.runtime_naming");
  return (
    <button type="button" onClick={naming.request}>
      name
    </button>
  );
}

function renderShell() {
  return render(
    <WorkspaceShell jobId="job-1">
      <Requester />
    </WorkspaceShell>,
  );
}

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  state.events = [];
  state.statuses = {
    "repo.facts": "ready",
    "repo.manifest": "ready",
    "architecture.structure": "ready",
    "architecture.runtime": "generating",
    "architecture.summary": "queued",
    "architecture.runtime_naming": "not_requested",
  };
  api.artifacts.mockImplementation(async () => listing());
  api.requestArtifact.mockImplementation(async () => artifact("architecture.runtime_naming", "queued"));
  for (const fn of [api.manifest, api.architecture, api.experiences, api.artifacts, api.requestArtifact]) {
    fn.mockClear();
  }
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the progressive workspace", () => {
  it("restores every artifact's status from the API, so a refresh loses nothing", async () => {
    renderShell();
    await settle();
    expect(api.artifacts).toHaveBeenCalled();
    expect(screen.getByTestId("activity-chip").textContent).toContain(
      "Still learning · ✓ facts ✓ manifest → runtime",
    );
    expect(screen.getByTestId("tab-Architecture-working")).toBeTruthy();
    expect(screen.queryByTestId("tab-Overview-working")).toBeTruthy(); // the summary is queued
  });

  it("fetches again only what a newly ready artifact feeds", async () => {
    const view = renderShell();
    await settle();
    const before = {
      manifest: api.manifest.mock.calls.length,
      architecture: api.architecture.mock.calls.length,
      experiences: api.experiences.mock.calls.length,
    };
    state.statuses["architecture.runtime"] = "ready";
    state.events = [ready(1, "architecture.runtime")];
    view.rerender(
      <WorkspaceShell jobId="job-1">
        <Requester />
      </WorkspaceShell>,
    );
    await settle();
    await settle();
    expect(api.architecture.mock.calls.length).toBe(before.architecture + 1);
    expect(api.manifest.mock.calls.length).toBe(before.manifest);
    expect(api.experiences.mock.calls.length).toBe(before.experiences);
  });

  it("requests an on-demand artifact once, however often a screen asks", async () => {
    renderShell();
    await settle();
    await act(async () => {
      screen.getByText("name").click();
      screen.getByText("name").click();
      screen.getByText("name").click();
    });
    expect(api.requestArtifact).toHaveBeenCalledTimes(1);
    expect(api.requestArtifact).toHaveBeenCalledWith("job-1", "architecture.runtime_naming", false);
  });

  it("never says 'Stalled': silence is normal, and only a stopped heartbeat warns", async () => {
    const base = await api.job();
    api.job.mockImplementation(async () => ({ ...base, health: "working", last_progress_at: "2026-10-26T00:00:00Z" }));
    const view = renderShell();
    await settle();
    expect(document.body.textContent).not.toMatch(/stall|is the worker running/i);
    expect(screen.queryByTestId("job-interrupted")).toBeNull();
    view.unmount();

    api.job.mockImplementation(async () => ({ ...base, health: "interrupted" }));
    renderShell();
    await settle();
    expect(screen.getByTestId("job-interrupted").textContent).toContain("Processing may have been interrupted");
    expect(screen.getByTestId("job-interrupted-retry")).toBeTruthy();
    api.job.mockImplementation(async () => base);
  });

  it("stops saying 'Still learning' once nothing is being made", async () => {
    for (const type of Object.keys(state.statuses)) {
      if (type !== "architecture.runtime_naming") state.statuses[type] = "ready";
    }
    renderShell();
    await settle();
    expect(screen.getByTestId("activity-chip").textContent).not.toContain("Still learning");
    expect(screen.queryByTestId("tab-Architecture-working")).toBeNull();
  });
});
