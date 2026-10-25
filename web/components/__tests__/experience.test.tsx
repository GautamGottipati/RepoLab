/**
 * An experience in use, offline: preparing, running real code, the result and
 * its measured runtime, what happened mapped to the repository, explaining,
 * experimenting -- and a library cell and a Mini Lab console.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExperienceRun, ExperienceSessionView } from "@/lib/types";

const calls = vi.hoisted(() => ({
  experienceSession: vi.fn(),
  runExperience: vi.fn(),
  explainExperienceRun: vi.fn(),
  miniLabCommand: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { ...calls, eventsPath: (p: string) => p },
  ApiError: class extends Error {},
}));
vi.mock("@/lib/events", () => ({ useEventStream: () => ({ events: [], status: "idle" }) }));

import { ExperienceWorkspace } from "../experience-workspace";

const COMMIT = "c".repeat(40);

function sparkRun(extra: Partial<ExperienceRun> = {}): ExperienceRun {
  return {
    id: "run1",
    status: "done",
    code: "result = df.groupBy('country').count()",
    action_id: "transform",
    shuffle_partitions: 4,
    result: {
      run_id: "r",
      ok: true,
      spark_version: "3.5.1",
      columns: ["country", "total"],
      rows: [["US", 154893], ["India", 43408]],
      value: null,
      error: null,
      plan: "*(2) HashAggregate\n+- Exchange hashpartitioning(country, 4)",
      measurements: {
        stage_count: 2,
        task_count: 5,
        total_runtime_ms: 5576,
        shuffle_bytes_read: 368,
        shuffle_bytes_written: 368,
        distribution: { partitions: 4, empty_partitions: 0 },
        tasks: { slowest_ms: 900, median_ms: 120 },
      },
      jobs: 1,
      event_log_sha256: "a".repeat(64),
      dataset_sha256: "b".repeat(64),
      wall_clock_seconds: 9.1,
      stdout_tail: "REPOLAB_EXPERIENCE_OK 3.5.1",
      config: { shuffle_partitions: 4 },
      exit_code: 0,
      stdout: "",
      stderr: "",
      truncated: false,
    },
    mapping: [
      { title: "Your code", run_evidence: "the snippet you ran", architecture_id: null, architecture_label: null, subsystem_id: null, source_paths: [] },
      { title: "Scheduler", run_evidence: "1 job split into 2 stages", architecture_id: "concept:scheduler", architecture_label: "Core Scheduler (DAGScheduler)", subsystem_id: "sub:core", source_paths: ["core"] },
    ],
    explanation: null,
    explanation_error: null,
    events_job_id: null,
    ...extra,
  };
}

function session(extra: Partial<ExperienceSessionView> = {}, state: Partial<ExperienceSessionView["state"]> = {}): ExperienceSessionView {
  return {
    id: "sess1",
    job_id: "job-1",
    experience_id: "spark:transform",
    adapter: "spark",
    strategy: "official_release",
    reality: "official_runtime",
    status: "ready",
    events_url: "/jobs/ev/events",
    state: {
      runtime: { image: "apache/spark:3.5.1-python3", version: "3.5.1", disclosure: "This is an official Spark 3.5.1 release, not this commit (5.0.0-SNAPSHOT).", label: "Official Apache Spark 3.5.1" },
      steps: [{ name: "ready", status: "done", detail: "Spark 3.5.1 answered: spark.range(1000).count() = 1000" }],
      failure: null,
      failure_log: "",
      runs: [],
      dataset: { name: "sales.csv", rows: 2000, columns: [["order_id", "INT"], ["country", "STRING"]], preview: [["1", "US"]], sha256: "d".repeat(64), generated_by: "RepoLab (deterministic, seeded)" },
      minilab: null,
      transcript: [],
      ...state,
    },
    experience: {
      id: "spark:transform",
      title: "Transform a Dataset",
      experience_type: "data_playground",
      adapter: "spark",
      summary: "",
      what_you_do: "Filter and group a small sales table.",
      what_you_learn: "Spark turns a transformation into jobs.",
      what_to_notice: "One groupBy becomes two stages.",
      surfaces: [],
      state: "running",
      why_not: null,
      options: [],
      starter_actions: [
        { id: "transform", title: "Transform a Dataset", description: "RepoLab's starter.", code: "result = df.groupBy('country').count()", source: "repolab", evidence: [] },
        { id: "readme", title: "The README's first example", description: "", code: "result = spark.range(1000 * 1000).count()", source: "readme", evidence: [{ path: "README.md", start_line: 149, end_line: null, commit_sha: COMMIT, excerpt: null }] },
      ],
      evidence: [],
      route: null,
      rank: 7,
      rank_signals: {},
      primary: true,
      session_id: "sess1",
    },
    ...extra,
  };
}

function renderWorkspace() {
  return render(<ExperienceWorkspace sessionId="sess1" jobId="job-1" repoUrl="https://github.com/apache/spark" commitSha={COMMIT} />);
}

beforeEach(() => Object.values(calls).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

describe("preparing", () => {
  it("shows each step as it happens", async () => {
    calls.experienceSession.mockResolvedValue(
      session({ status: "preparing" }, { steps: [{ name: "runtime", status: "done", detail: "selected the official release" }, { name: "smoke", status: "started", detail: "running a tiny Spark job" }] }),
    );
    renderWorkspace();
    const steps = await screen.findByTestId("preparation-steps");
    expect(steps.textContent).toContain("✓selected the official release");
    expect(steps.textContent).toContain("→running a tiny Spark job");
  });

  it("says why it could not prepare, with the log", async () => {
    calls.experienceSession.mockResolvedValue(session({ status: "failed" }, { failure: "the source build of this commit failed", failure_log: "Exit value: 137" }));
    renderWorkspace();
    const failed = await screen.findByTestId("experience-failed");
    expect(failed.textContent).toContain("source build of this commit failed");
    expect(failed.textContent).toContain("Exit value: 137");
  });
});

describe("a Spark experience", () => {
  it("labels the runtime honestly and shows the data the code will see", async () => {
    calls.experienceSession.mockResolvedValue(session());
    renderWorkspace();
    expect((await screen.findByTestId("reality-badge")).textContent).toBe("OFFICIAL RUNTIME");
    expect(screen.getByTestId("runtime-disclosure").textContent).toContain("not this commit");
    expect(screen.getByTestId("dataset").textContent).toContain("sales.csv");
    expect(screen.getByTestId("starters").textContent).toContain("(README:149)");
  });

  it("runs the learner's code with their partition count", async () => {
    calls.experienceSession.mockResolvedValue(session());
    calls.runExperience.mockResolvedValue({ run_id: "run1", events_url: "/jobs/r/events" });
    renderWorkspace();
    fireEvent.change(await screen.findByTestId("shuffle-partitions"), { target: { value: "8" } });
    fireEvent.click(screen.getByTestId("run-experience"));
    expect(calls.runExperience).toHaveBeenCalledWith("sess1", "result = df.groupBy('country').count()", 8, "transform");
  });

  it("shows the real result, the measured runtime, and what happened in the repository", async () => {
    calls.experienceSession.mockResolvedValue(session({}, { runs: [sparkRun()] }));
    renderWorkspace();
    const table = await screen.findByTestId("result-table");
    expect(table.textContent).toContain("US154893");
    const runtime = screen.getByTestId("runtime-measurements");
    expect(runtime.textContent).toContain("Jobs1");
    expect(runtime.textContent).toContain("Stages2");
    expect(runtime.textContent).toContain("Tasks5");
    expect(runtime.textContent).toContain("5.58 s");
    expect(runtime.textContent).toContain("single run, not an experiment");
    const happened = screen.getByTestId("what-happened");
    expect(happened.textContent).toContain("this run used Spark 3.5.1");
    expect(within(happened).getByTestId("architecture-link").getAttribute("href")).toBe(
      "/r/job-1/architecture?subsystem=sub%3Acore&node=concept%3Ascheduler",
    );
    expect(within(happened).getByTestId("source-link").getAttribute("href")).toBe(`https://github.com/apache/spark/tree/${COMMIT}/core`);
  });

  it("explains a run and suggests an experiment on the same code", async () => {
    calls.experienceSession.mockResolvedValue(session({}, { runs: [sparkRun()] }));
    calls.explainExperienceRun.mockResolvedValue({ run_id: "run1", events_url: "/jobs/x/events" });
    renderWorkspace();
    fireEvent.click(await screen.findByTestId("explain-run"));
    expect(calls.explainExperienceRun).toHaveBeenCalledWith("sess1", "run1");
    const experiment = new URL(screen.getByTestId("suggest-experiment").getAttribute("href")!, "http://x");
    expect(experiment.pathname).toBe("/r/job-1/ask");
    expect(experiment.searchParams.get("experience")).toBe("sess1");
    expect(experiment.searchParams.get("q")).toContain("I ran it with 4");
  });

  it("shows Spark's own error when the code fails", async () => {
    const failed = sparkRun({ status: "failed" });
    failed.result = { ...failed.result!, ok: false, error: "AnalysisException: cannot resolve 'cuntry'" };
    calls.experienceSession.mockResolvedValue(session({}, { runs: [failed] }));
    renderWorkspace();
    expect((await screen.findByTestId("run-error")).textContent).toContain("cannot resolve 'cuntry'");
    expect(screen.queryByTestId("runtime-measurements")).toBeNull();
  });
});

describe("a library and a Mini Lab", () => {
  it("runs Python and shows the interpreter's own output", async () => {
    const run = sparkRun();
    run.result = { ...run.result!, ok: true, exit_code: 0, stdout: "Hello, World!\n", stderr: "", wall_clock_seconds: 0.8 };
    calls.experienceSession.mockResolvedValue(session({ adapter: "library", reality: "real_repository" }, { runs: [run], dataset: null }));
    renderWorkspace();
    expect((await screen.findByTestId("library-stdout")).textContent).toBe("Hello, World!\n");
    expect(screen.getByTestId("library-result").textContent).toContain("process time, measured");
    expect(screen.queryByTestId("shuffle-partitions")).toBeNull();
  });

  it("is a labelled simulation and answers from its table", async () => {
    calls.experienceSession.mockResolvedValue(
      session({ adapter: "minilab", reality: "mini_lab" }, { minilab: { entries: [{ command: "airflow dags list", summary: "" }] }, dataset: null }),
    );
    calls.miniLabCommand.mockResolvedValue({ command: "airflow dags list", label: "SIMULATED", explanation: "Lists DAGs from dags/." });
    renderWorkspace();
    const lab = await screen.findByTestId("minilab");
    expect(lab.textContent).toContain("nothing here runs this repository");
    expect(screen.getByTestId("reality-badge").textContent).toBe("SIMULATED / EDUCATIONAL");
    fireEvent.click(within(lab).getByRole("button", { name: "airflow dags list" }));
    expect(await within(lab).findByText("[SIMULATED] Lists DAGs from dags/.")).toBeTruthy();
  });
});
