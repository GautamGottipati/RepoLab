/**
 * The Experiences screen: things to do, best first, honestly labelled -- and,
 * when nothing real can run, why, what RepoLab can run, and a Mini Lab that says
 * it is a simulation. The detected surfaces stay, underneath, as evidence.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AvailableExperience,
  ExperienceCatalog,
  ExperienceDefinition,
  ExperienceListing,
  RuntimeOption,
} from "@/lib/types";

const NOT_RUNNABLE = "Detected, but not yet runnable in this RepoLab build.";
const COMMIT = "c".repeat(40);

const calls = vi.hoisted(() => ({
  experiences: vi.fn(),
  experienceList: vi.fn(),
  startExperience: vi.fn(),
  experienceSession: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    experiences: calls.experiences,
    experienceList: calls.experienceList,
    startExperience: calls.startExperience,
    experienceSession: calls.experienceSession,
    eventsPath: (p: string) => p,
    runtime: () => new Promise(() => undefined),
  },
  optional: (p: Promise<unknown>) => p,
  ApiError: class extends Error {},
}));
vi.mock("@/lib/events", () => ({ useEventStream: () => ({ events: [], status: "idle" }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: calls.replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("../../workspace", () => ({
  useWorkspace: () => ({ jobId: "job-1", manifest: {}, architecture: null }),
}));

import ExperiencesPage from "../page";

function surface(name: string): AvailableExperience {
  return {
    surface: name,
    icon: ">_",
    label: name,
    name,
    description: "",
    confidence: 0.9,
    primary: false,
    reason: "detected from interactive_shell",
    evidence: [],
    status: "detected_only",
    route: null,
    adapter: null,
    unavailable_reason: `${NOT_RUNNABLE} RepoLab has no build strategy for this repository yet.`,
    runnable: false,
    weak: false,
    supported: true,
  };
}

function catalog(): ExperienceCatalog {
  return {
    repo_url: "https://github.com/example/engine",
    commit_sha: COMMIT,
    experiences: [surface("cli")],
    hybrid: false,
    primary_type: "cli",
    rationale: "cli scored highest",
    surface_count: 1,
    runnable_count: 0,
    runnable_surfaces: [],
  } as ExperienceCatalog;
}

const official: RuntimeOption = {
  strategy: "official_release",
  reality: "official_runtime",
  label: "Official Apache Spark 3.5.1",
  ready: true,
  estimate: "~20 seconds",
  disclosure: "An official Spark 3.5.1 release, not built from this commit (5.0.0-SNAPSHOT).",
  evidence: [],
};
const source: RuntimeOption = {
  strategy: "source_build",
  reality: "real_repository",
  label: "Spark built from this commit (5.0.0-SNAPSHOT)",
  ready: false,
  estimate: "the first build takes 45-90 minutes; later runs take seconds",
  disclosure: null,
  evidence: [],
};

function experience(id: string, title: string, extra: Partial<ExperienceDefinition> = {}): ExperienceDefinition {
  return {
    id,
    title,
    experience_type: "data_playground",
    adapter: "spark",
    summary: `${title} summary`,
    what_you_do: "Filter and group a small sales table.",
    what_you_learn: "Spark turns a transformation into jobs, stages and tasks.",
    what_to_notice: "One groupBy becomes two stages.",
    surfaces: ["data_playground"],
    state: "runnable",
    why_not: null,
    options: [official, source],
    starter_actions: [],
    evidence: [],
    route: null,
    rank: 7,
    rank_signals: {},
    primary: false,
    session_id: null,
    ...extra,
  };
}

function sparkListing(): ExperienceListing {
  return {
    repo_url: "https://github.com/example/engine",
    name: "Apache Spark",
    headline: "Experience Apache Spark: do something real with it, then see how it works.",
    experiences: [
      experience("spark:transform", "Transform a Dataset", { primary: true }),
      experience("spark:partitions", "Explore Partitions & Shuffles"),
      experience("spark:sql", "Try Spark SQL", { experience_type: "query_console" }),
    ],
    also: [experience("cli:terminal", "Try the CLI", { state: "supported", adapter: "cli", options: [], why_not: "no build strategy" })],
    fallback: null,
    nothing_runs_because: [],
    supported_kinds: [],
  };
}

beforeEach(() => {
  Object.values(calls).forEach((fn) => fn.mockReset());
  calls.experiences.mockResolvedValue(catalog());
});
afterEach(cleanup);

describe("experiences, not surfaces", () => {
  it("leads with a primary experience saying what you will do and learn, and how real it is", async () => {
    calls.experienceList.mockResolvedValue(sparkListing());
    render(<ExperiencesPage />);
    expect((await screen.findByTestId("experiences-header")).textContent).toContain("Experience Apache Spark");
    const primary = screen.getByTestId("primary-experience");
    expect(primary.textContent).toContain("★");
    expect(primary.textContent).toContain("Transform a Dataset");
    expect(primary.textContent).toContain("What you'll do");
    expect(primary.textContent).toContain("What you'll learn");
    const realities = within(primary).getAllByTestId("reality-badge").map((b) => b.textContent);
    expect(realities).toEqual(["OFFICIAL RUNTIME", "REAL REPOSITORY"]);
    expect(within(primary).getByTestId("option-disclosure").textContent).toContain("not built from this commit");
    expect(screen.getAllByTestId("experience-card").map((c) => c.getAttribute("data-experience-id"))).toEqual([
      "spark:partitions",
      "spark:sql",
    ]);
    // Surfaces are evidence now, collapsed, with what else was detected.
    const surfaces = screen.getByTestId("detected-surfaces") as HTMLDetailsElement;
    expect(surfaces.open).toBe(false);
    expect(surfaces.textContent).toContain("also detected: Try the CLI");
    expect(screen.queryByTestId("nothing-runs")).toBeNull();
  });

  it("starts the runtime the learner chose, and opens its session", async () => {
    calls.experienceList.mockResolvedValue(sparkListing());
    calls.startExperience.mockResolvedValue({ id: "sess123" });
    render(<ExperiencesPage />);
    const primary = await screen.findByTestId("primary-experience");
    const buttons = within(primary).getAllByTestId("start-experience");
    expect(buttons.map((b) => [b.getAttribute("data-strategy"), b.textContent])).toEqual([
      ["official_release", "Start"],
      ["source_build", "Build and start"],
    ]);
    fireEvent.click(buttons[0]);
    await vi.waitFor(() => expect(calls.replace).toHaveBeenCalledWith("/r/job-1/experiences?session=sess123"));
    expect(calls.startExperience).toHaveBeenCalledWith("job-1", "spark:transform", "official_release");
  });

  it("opens an existing experience on its own screen", async () => {
    calls.experienceList.mockResolvedValue({
      ...sparkListing(),
      experiences: [experience("spark:pipeline", "Run the Repository's Pipeline", { primary: true, route: "/playground", options: [{ ...source, ready: true, strategy: "existing", label: "The repository's own job" }] })],
    });
    render(<ExperiencesPage />);
    expect((await screen.findByTestId("open-experience")).getAttribute("href")).toBe("/r/job-1/playground");
  });

  it("lets a learner continue a running session", async () => {
    calls.experienceList.mockResolvedValue({
      ...sparkListing(),
      experiences: [experience("spark:transform", "Transform a Dataset", { primary: true, state: "running", session_id: "abc" })],
    });
    render(<ExperiencesPage />);
    fireEvent.click(await screen.findByTestId("continue-experience"));
    expect(calls.replace).toHaveBeenCalledWith("/r/job-1/experiences?session=abc");
  });
});

describe("when nothing real can run", () => {
  it("says why, what RepoLab runs, and offers a Mini Lab marked as a simulation", async () => {
    const lab = experience("minilab:lab", "Guided Mini Lab", {
      adapter: "minilab",
      experience_type: "mini_lab",
      options: [{ ...official, strategy: "mini_lab", reality: "mini_lab", label: "Simulated / educational", disclosure: null }],
    });
    calls.experienceList.mockResolvedValue({
      ...sparkListing(),
      name: "airflow",
      headline: "airflow cannot run in RepoLab yet.",
      experiences: [],
      fallback: lab,
      nothing_runs_because: ["Try the CLI: RepoLab has no build strategy for this repository yet."],
      supported_kinds: ["Spark data playgrounds", "Node.js web applications and HTTP APIs"],
    });
    render(<ExperiencesPage />);
    const block = await screen.findByTestId("nothing-runs");
    expect(within(block).getByTestId("why-not").textContent).toContain("no build strategy");
    expect(block.textContent).toContain("Node.js web applications and HTTP APIs");
    expect(within(screen.getByTestId("minilab-card")).getByTestId("reality-badge").textContent).toBe("SIMULATED / EDUCATIONAL");
    expect(within(block).getByTestId("explore-architecture").getAttribute("href")).toBe("/r/job-1/architecture");
    expect(within(block).getByTestId("ask-run-locally").getAttribute("href")).toContain("/r/job-1/ask?q=");
    // The surfaces, with the specification's sentence, are open as evidence.
    expect((screen.getByTestId("detected-surfaces") as HTMLDetailsElement).open).toBe(true);
    expect(screen.getByTestId("unavailable-cli").textContent).toContain(NOT_RUNNABLE);
  });
});
