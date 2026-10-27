import { describe, expect, it } from "vitest";

import { askUrl, flowWalkthroughs, focusFor, layout, sourceUrl } from "../architecture";
import type { ArchitectureGraph, Walkthrough } from "../types";
import { pipelineView } from "@/components/__tests__/fixtures";

describe("layout", () => {
  it("places each step one column after the step that feeds it", () => {
    const placements = layout(pipelineView().graph);
    const rank = (id: string) => placements.get(id)!.rank;
    expect(rank("entry:job")).toBeLessThan(rank("pipeline:job"));
    expect(rank("step:read")).toBeLessThan(rank("step:join"));
    expect(rank("step:join")).toBeLessThan(rank("step:write"));
  });

  it("puts stores at the far right", () => {
    const placements = layout(pipelineView().graph);
    const max = Math.max(...[...placements.values()].map((p) => p.rank));
    expect(placements.get("store:s3")!.rank).toBe(max);
  });

  it("survives a cycle", () => {
    const view = pipelineView();
    view.graph.edges.push({
      source: "step:write",
      target: "step:read",
      type: "data_flow",
      description: null,
      provenance: "static",
      evidence: [],
      confidence: 0.5,
    });
    expect(layout(view.graph).size).toBe(view.graph.nodes.length);
  });
});

describe("links and context", () => {
  it("only links to GitHub, at the commit", () => {
    const ref = { path: "a/b.py", start_line: 3, end_line: 3, commit_sha: null, excerpt: null };
    expect(sourceUrl("https://github.com/o/r", "abc", ref)).toBe("https://github.com/o/r/blob/abc/a/b.py#L3");
    expect(sourceUrl("https://gitlab.com/o/r", "abc", ref)).toBeNull();
  });

  it("sends a bounded description of the component to Ask", () => {
    const focus = focusFor(pipelineView().graph, "step:join")!;
    expect(focus.label).toBe("Join Customers");
    expect(focus.source_references[0].path).toBe("jobs/job.py");
    expect(focus.relationships).toContain("Read Orders data flow Join Customers");
    expect(focusFor(pipelineView().graph, "missing")).toBeNull();
  });

  it("encodes the question in the Ask URL", () => {
    expect(askUrl("j", "n:1", "Why & how?")).toBe("/r/j/ask?node=n%3A1&q=Why+%26+how%3F");
  });
});

describe("flowWalkthroughs", () => {
  const walk = (id: string, level: string, subsystem: string | null = null) =>
    ({ id, level, subsystem, name: id, kind: "call", steps: [], description: "", provenance: "static" }) as unknown as Walkthrough;

  it("lists each walkthrough once: overview first, then those inside a part", () => {
    // walk:0 belongs to no part and is not the system walk, so it is in both sets.
    const graph = {
      walkthroughs: [walk("walk:system", "system"), walk("walk:0", "component"), walk("walk:1", "component", "sql")],
    } as unknown as ArchitectureGraph;
    expect(flowWalkthroughs(graph).map((w) => w.id)).toEqual(["walk:system", "walk:0", "walk:1"]);
  });
});
