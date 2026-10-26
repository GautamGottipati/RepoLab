/**
 * Overview: what the repository is and what to do first -- from the
 * investigation's own data, with nothing invented where data is missing.
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExperienceDefinition, ExperienceListing, JobEvent } from "@/lib/types";

const state = vi.hoisted(() => ({
  workspace: {} as Record<string, unknown>,
  listing: null as unknown,
}));

vi.mock("@/lib/api", () => ({
  api: {
    experienceList: vi.fn(async () => state.listing),
    startExperience: vi.fn(),
  },
  optional: (p: Promise<unknown>) => p,
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../workspace", () => ({ useWorkspace: () => state.workspace }));

import OverviewPage from "../page";

const MANIFEST = {
  commit_sha: "a".repeat(40),
  languages: ["Scala", "Python"],
  frameworks: [],
  interaction_surfaces: [],
  entrypoints: [],
  concepts: [],
  confidence: 0.8,
};

function experience(overrides: Partial<ExperienceDefinition> = {}): ExperienceDefinition {
  return {
    id: "spark:transform",
    title: "Transform a Dataset",
    experience_type: "data_playground",
    adapter: "spark",
    summary: "Run a real Spark transformation.",
    what_you_do: "Filter and group",
    what_you_learn: "Jobs, stages and tasks",
    what_to_notice: "",
    surfaces: [],
    state: "runnable",
    why_not: null,
    options: [
      {
        strategy: "official_release",
        reality: "official_runtime",
        label: "Official Apache Spark 3.5.1",
        ready: true,
        estimate: "~20 seconds",
        disclosure: null,
        evidence: [],
      },
    ],
    starter_actions: [],
    evidence: [],
    route: null,
    rank: 1,
    rank_signals: {},
    primary: true,
    session_id: null,
    ...overrides,
  } as ExperienceDefinition;
}

function listing(experiences: ExperienceDefinition[]): ExperienceListing {
  return {
    repo_url: "https://github.com/apache/spark",
    name: "Apache Spark",
    headline: "",
    experiences,
    also: [],
    fallback: null,
    nothing_runs_because: ["RepoLab has no build strategy for this kind of repository."],
    supported_kinds: [],
  } as ExperienceListing;
}

function phase(index: number, label: string): JobEvent {
  return {
    job_id: "job-1",
    seq: index,
    type: "agent_status",
    message: label,
    step: null,
    created_at: "2026-10-25T00:00:00Z",
    data: { phase: `p${index}`, label, index, total: 5 },
  } as JobEvent;
}

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    // jsdom without storage: the hint simply shows.
  }
  state.listing = listing([experience()]);
  state.workspace = {
    jobId: "job-1",
    job: { status: "completed" },
    manifest: MANIFEST,
    architecture: null,
    experiences: null,
    events: [],
    environmentReady: null,
    openActivity: vi.fn(),
  };
});
afterEach(cleanup);

async function renderOverview() {
  render(<OverviewPage />);
  await act(async () => {});
}

describe("Overview", () => {
  it("shows the agent's real steps while it is still reading", async () => {
    state.workspace = {
      ...state.workspace,
      job: { status: "running" },
      manifest: null,
      events: [phase(1, "Inspecting repository"), phase(2, "Mapping architecture")],
    };
    await renderOverview();
    const steps = screen.getByTestId("analysis-steps");
    const items = steps.querySelectorAll("li");
    expect([...items].map((item) => item.getAttribute("data-state"))).toEqual(["done", "current"]);
    expect(steps.textContent).toContain("Mapping architecture");
    expect(screen.getByText("Step 2 of 5")).toBeTruthy();
  });

  it("leads with the recommended experience and one obvious action", async () => {
    await renderOverview();
    const card = screen.getByTestId("overview-primary");
    expect(card.textContent).toContain("Transform a Dataset");
    expect(card.textContent).toContain("Official Apache Spark 3.5.1");
    expect(screen.getAllByTestId("overview-start")).toHaveLength(1);
    expect(screen.getByTestId("start-hint").textContent).toContain("one way");
  });

  it("explains a repository nothing can run in, and offers the ways that work", async () => {
    state.listing = listing([]);
    await renderOverview();
    const empty = screen.getByTestId("overview-nothing-runs");
    expect(empty.textContent).toContain("no build strategy");
    expect(screen.getByRole("link", { name: "Explore architecture" })).toBeTruthy();
    expect(screen.queryByTestId("overview-start")).toBeNull();
  });

  it("leaves out what the investigation did not produce instead of guessing", async () => {
    await renderOverview();
    expect(screen.queryByTestId("what-is-this")).toBeNull();
    expect(screen.queryByTestId("key-components")).toBeNull();
    // The heading falls back to the experience listing's own name.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Apache Spark");
  });

  it("keeps the raw findings under 'View analysis details'", async () => {
    await renderOverview();
    const details = screen.getByTestId("analysis-details");
    expect(details.tagName).toBe("DETAILS");
    expect(details.hasAttribute("open")).toBe(false);
    expect(details.textContent).toContain("Confidence");
  });
});
