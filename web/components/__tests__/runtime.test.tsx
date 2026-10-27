/**
 * The runtime architecture and the source viewer: every claim one click from
 * exact, commit-pinned lines -- and nothing drawn that the code did not show.
 */

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ArchitectureExperience } from "../architecture-experience";
import { SourceRef, SourceViewerProvider } from "../source-ref";
import { guidedView, manifest } from "./fixtures";
import type { ArchitectureView, CodeReference, SourceFile } from "@/lib/types";

const calls = vi.hoisted(() => ({ source: vi.fn(), sourceAt: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { source: calls.source, sourceAt: calls.sourceAt },
  ApiError: class extends Error {},
}));

const COMMIT = "0aa4f9136bcc0aa4f9136bcc0aa4f9136bcc0aa4";
const REPO = "https://github.com/example/engine";

function ref(path: string, start: number, end: number, symbol: string | null = null): CodeReference {
  return {
    path,
    start_line: start,
    end_line: end,
    commit_sha: COMMIT,
    excerpt: null,
    symbol,
    strength: "direct_source",
    verified: true,
    verification: symbol ? `class ${symbol}` : "anchor found",
  };
}

function runtimeView(ordered = true): ArchitectureView {
  const view = guidedView();
  view.commit_sha = COMMIT;
  view.graph.abstractions = [
    { id: "sym:ctx", name: "EngineContext", kind: "class", definition: ref("core/Engine.scala", 3, 8, "EngineContext"), referenced_by: 40, subsystem: null, usages: [] },
    { id: "sym:sched", name: "JobScheduler", kind: "class", definition: ref("core/JobScheduler.scala", 3, 10, "JobScheduler"), referenced_by: 22, subsystem: null, usages: [] },
    { id: "sym:run", name: "TaskRunner", kind: "class", definition: ref("core/TaskRunner.scala", 3, 7, "TaskRunner"), referenced_by: 12, subsystem: null, usages: [] },
  ];
  view.graph.runtime = {
    provenance: "inferred",
    components: [
      { id: "rt:driver", name: "Driver", description: "Coordinates a job.", members: ["sym:ctx"], responsibilities: [{ text: "Submits jobs", reference: ref("core/Engine.scala", 3, 8, "EngineContext"), provenance: "inferred" }], source_references: [ref("core/Engine.scala", 3, 8, "EngineContext")], subsystem_ids: [], node_ids: [], provenance: "inferred", inferred_fields: ["name"] },
      { id: "rt:scheduler", name: "Scheduler", description: "Splits a job into stages.", members: ["sym:sched"], responsibilities: [], source_references: [ref("core/JobScheduler.scala", 3, 10, "JobScheduler")], subsystem_ids: [], node_ids: [], provenance: "inferred", inferred_fields: [] },
      { id: "rt:executor", name: "Executor", description: null, members: ["sym:run"], responsibilities: [], source_references: [ref("core/TaskRunner.scala", 3, 7, "TaskRunner")], subsystem_ids: [], node_ids: [], provenance: "inferred", inferred_fields: [] },
    ],
    links: [
      { source: "rt:driver", target: "rt:scheduler", type: "calls", label: "jobs", label_provenance: "inferred", evidence: [ref("core/Engine.scala", 6, 6)] },
      { source: "rt:scheduler", target: "rt:executor", type: ordered ? "calls" : "references", label: "task sets", label_provenance: "inferred", evidence: [ref("core/JobScheduler.scala", 7, 7)] },
    ],
  };
  view.graph.flows = [
    {
      id: "flow:job",
      name: "Running a job",
      description: null,
      ordered,
      provenance: "inferred",
      steps: [
        { sequence: 1, component: "rt:driver", action: "submits the job", input: null, output: "job", references: [ref("core/Engine.scala", 6, 6)] },
        { sequence: 2, component: "rt:scheduler", action: "plans stages", input: "job", output: "task sets", references: [ref("core/JobScheduler.scala", 7, 7)] },
        { sequence: 3, component: "rt:executor", action: "runs tasks", input: null, output: null, references: [] },
      ],
    },
  ];
  view.graph.reading_path = [
    { title: "Driver", why: "Coordinates a job.", reference: ref("core/Engine.scala", 3, 8, "EngineContext"), component: "rt:driver" },
  ];
  return view;
}

function renderRuntime(view = runtimeView()) {
  return render(
    <SourceViewerProvider jobId="job-1" repoUrl={REPO} commitSha={COMMIT}>
      <ArchitectureExperience jobId="job-1" view={view} manifest={manifest} overlay={null} />
    </SourceViewerProvider>,
  );
}

function file(lines: string[], extra: Partial<SourceFile> = {}): SourceFile {
  return {
    repo_url: REPO,
    commit_sha: COMMIT,
    path: "core/Engine.scala",
    captured: true,
    sha256: "f".repeat(64),
    first_line: 1,
    lines,
    line_count: lines.length,
    host_url: `${REPO}/blob/${COMMIT}/core/Engine.scala`,
    ...extra,
  };
}

beforeEach(() => {
  calls.source.mockReset();
  calls.sourceAt.mockReset();
});
afterEach(cleanup);

describe("the runtime architecture", () => {
  it("leads with numbered core ideas and the commit it was analyzed at", () => {
    renderRuntime();
    expect(screen.getByTestId("analyzed-commit").textContent).toContain("0aa4f9136bcc");
    const ideas = within(screen.getByTestId("core-ideas")).getAllByRole("button");
    expect(ideas.map((b) => b.textContent)).toEqual([
      expect.stringContaining("01Driver"),
      expect.stringContaining("02Scheduler"),
      expect.stringContaining("03Executor"),
    ]);
  });

  it("labels what passes along each arrow", () => {
    renderRuntime();
    const labels = screen.getAllByTestId("edge-label").map((l) => l.textContent);
    expect(labels).toContain("jobs");
    expect(labels).toContain("task sets");
  });

  it("opens a component with its responsibilities, sources and why RepoLab thinks so", () => {
    renderRuntime();
    fireEvent.click(within(screen.getByTestId("core-ideas")).getAllByRole("button")[0]);
    const panel = screen.getByTestId("runtime-inspector");
    expect(within(panel).getByTestId("inspector-title").textContent).toBe("Driver");
    expect(panel.textContent).toContain("Submits jobs");
    expect(within(panel).getByTestId("runtime-sources").textContent).toContain("L3–8");
    const why = within(panel).getByTestId("why-repolab-thinks");
    expect(why.textContent).toContain("EngineContext");
    expect(why.textContent).toContain("used in 40 other files");
    expect(within(panel).getByTestId("ask-why").getAttribute("href")).toContain("Driver");
  });

  it("draws a sequence only when the order is verified in code", () => {
    renderRuntime(runtimeView(true));
    fireEvent.click(screen.getByTestId("perspective-flows"));
    expect(screen.getByText("Order verified in code")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Sequence" }));
    expect(screen.getByTestId("sequence-view")).toBeTruthy();
    cleanup();

    renderRuntime(runtimeView(false));
    fireEvent.click(screen.getByTestId("perspective-flows"));
    expect(screen.getByText("Order not verified")).toBeTruthy();
    expect((screen.getByRole("tab", { name: "Sequence" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("sequence-view")).toBeNull();
    expect(screen.getAllByTestId("flow-step")).toHaveLength(3);
  });

  it("puts a reading path on the source map", () => {
    renderRuntime();
    fireEvent.click(screen.getByTestId("perspective-source"));
    expect(screen.getByTestId("reading-path").textContent).toContain("Coordinates a job.");
  });

  it("keeps the earlier view for investigations without a runtime model", () => {
    render(<ArchitectureExperience jobId="job-1" view={guidedView()} manifest={manifest} overlay={null} />);
    expect(screen.queryByTestId("core-ideas")).toBeNull();
    expect(screen.queryByTestId("perspective-runtime")).toBeNull();
  });
});

describe("the source viewer", () => {
  it("shows the exact lines at the investigated commit, highlighted", async () => {
    calls.source.mockResolvedValue(file(["package x", "", "class EngineContext {", "  a", "  b", "}", "", "x"]));
    render(
      <SourceViewerProvider jobId="job-1" repoUrl={REPO} commitSha={COMMIT}>
        <SourceRef reference={ref("core/Engine.scala", 3, 6, "EngineContext")} />
      </SourceViewerProvider>,
    );
    fireEvent.click(screen.getByTestId("source-ref"));
    await act(async () => {});
    expect(calls.source).toHaveBeenCalledWith("job-1", "core/Engine.scala", 3);
    expect(screen.getByTestId("viewer-commit").textContent).toContain("0aa4f91");
    expect(screen.getByTestId("viewer-range").textContent).toContain("L3–6");
    const highlighted = screen.getByTestId("viewer-code").querySelectorAll("tr[data-highlighted]");
    expect([...highlighted].map((row) => row.textContent)).toEqual(["3class EngineContext {", "4  a", "5  b", "6}"]);
    expect(screen.getByTestId("viewer-verified")).toBeTruthy();
  });

  it("fetches a citation from another commit at that commit, and says so", async () => {
    const other = "9".repeat(40);
    calls.sourceAt.mockResolvedValue(file(["a"], { commit_sha: other }));
    render(
      <SourceViewerProvider jobId="job-1" repoUrl={REPO} commitSha={COMMIT}>
        <SourceRef reference={{ ...ref("core/Engine.scala", 1, 1), commit_sha: other }} />
      </SourceViewerProvider>,
    );
    fireEvent.click(screen.getByTestId("source-ref"));
    await act(async () => {});
    expect(calls.sourceAt).toHaveBeenCalledWith(REPO, other, "core/Engine.scala", 1);
    expect(calls.source).not.toHaveBeenCalled();
    expect(screen.getByTestId("viewer-commit").textContent).toContain("different from the investigated");
  });

  it("says when RepoLab kept no copy, instead of showing another version", async () => {
    calls.source.mockResolvedValue(file([], { captured: false }));
    render(
      <SourceViewerProvider jobId="job-1" repoUrl={REPO} commitSha={COMMIT}>
        <SourceRef reference={ref("core/Engine.scala", 1, 1)} />
      </SourceViewerProvider>,
    );
    fireEvent.click(screen.getByTestId("source-ref"));
    await act(async () => {});
    const note = screen.getByTestId("viewer-not-captured");
    expect(note.textContent).toContain("did not keep a copy");
    expect(within(note).getByRole("link").getAttribute("href")).toContain(`/blob/${COMMIT}/`);
  });

  it("never links an unverified reference", () => {
    render(
      <SourceRef
        reference={{ path: "core/Missing.scala", start_line: null, end_line: null, verified: false, verification: "file not in the investigated snapshot" }}
        repoUrl={REPO}
        commitSha={COMMIT}
      />,
    );
    const chip = screen.getByTestId("source-unverified");
    expect(chip.textContent).toContain("Source location could not be verified");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("falls back to the same commit on the host outside the workspace", () => {
    render(<SourceRef reference={ref("core/Engine.scala", 3, 8)} repoUrl={REPO} commitSha={COMMIT} />);
    expect(screen.getByRole("link").getAttribute("href")).toBe(`${REPO}/blob/${COMMIT}/core/Engine.scala#L3-L8`);
  });
});
