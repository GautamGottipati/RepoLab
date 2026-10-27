/**
 * The guided Architecture experience: repository -> system -> subsystem ->
 * component -> source, rendered offline from a fixture view.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { AgentActivityPanel } from "../activity";
import { ArchitectureExperience } from "../architecture-experience";
import { guidedView, manifest } from "./fixtures";
import { askUrl, focusFor } from "@/lib/architecture";
import type { JobEvent, RuntimeOverlay } from "@/lib/types";

afterEach(cleanup);

function renderGuided(view = guidedView(), overlay: RuntimeOverlay | null = null, initial: { subsystem?: string; node?: string } = {}) {
  return render(
    <ArchitectureExperience
      jobId="job-1"
      view={view}
      manifest={manifest}
      overlay={overlay}
      initialSubsystem={initial.subsystem ?? null}
      initialSelection={initial.node ?? null}
    />,
  );
}

const box = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label},`) });
const before = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe("level 0: what is this repository?", () => {
  it("comes first, in the project's terms, with its type and provenance", () => {
    renderGuided();
    const orientation = screen.getByTestId("orientation");
    expect(before(orientation, screen.getByTestId("system-model"))).toBe(true);
    expect(screen.getByTestId("repository-name").textContent).toBe("Example Engine");
    expect(screen.getByTestId("repository-type").textContent).toBe("Distributed system");
    expect(screen.getByTestId("beginner-explanation").textContent).toContain("inferred");
    expect(screen.getByTestId("surfaces").textContent).toBe("Python API · CLI");
  });

  it("answers how do you know, with README and documentation evidence", () => {
    renderGuided();
    const known = screen.getByTestId("how-do-you-know");
    const kinds = within(known)
      .getAllByRole("listitem")
      .map((li) => li.getAttribute("data-evidence-kind"));
    expect(kinds).toEqual(expect.arrayContaining(["readme", "documentation"]));
    expect(within(known).getByRole("link", { name: "Cluster overview" }).getAttribute("href")).toContain("engine.example.org");
  });

  it("offers a first question for Ask RepoLab", () => {
    renderGuided();
    const href = screen.getByTestId("recommended-start").getAttribute("href")!;
    const url = new URL(href, "http://x");
    expect(url.pathname).toBe("/r/job-1/ask");
    expect(url.searchParams.get("q")).toBe("How does a program become work on many machines?");
    expect(url.searchParams.has("node")).toBe(false);
  });
});

describe("level 1 and 2: the system model and the repository map", () => {
  it("shows a handful of concepts and the parts, not implementation detail", () => {
    renderGuided();
    const concepts = within(screen.getByTestId("system-model")).getAllByTestId("architecture-node");
    expect(concepts.length).toBeGreaterThanOrEqual(3);
    expect(concepts.length).toBeLessThanOrEqual(8);
    expect(screen.getAllByTestId("subsystem-card")).toHaveLength(3);
    expect(screen.queryByText(/Kmeans Wrapper/)).toBeNull();
    expect(screen.getByTestId("walkthrough").getAttribute("data-walkthrough-id")).toBe("walk:system");
  });

  it("opens a concept with its internal name explained", () => {
    renderGuided();
    fireEvent.click(box("Scheduler"));
    expect(screen.getByTestId("inspector-title").textContent).toContain("Scheduler");
    expect(screen.getByTestId("concept-term").textContent).toBe("Internal name: DAGScheduler");
    const ask = new URL(screen.getByTestId("ask-why").getAttribute("href")!, "http://x");
    expect(ask.searchParams.get("node")).toBe("concept:scheduler");
  });

  it("puts meaning before evidence, and keeps static / inferred / measured", () => {
    renderGuided();
    fireEvent.click(box("Scheduler"));
    const inspector = screen.getByTestId("architecture-inspector");
    expect(before(within(inspector).getByTestId("what-is-it"), within(inspector).getByTestId("evidence-sections"))).toBe(true);
    expect(inspector.textContent).toContain("Inferred");
    expect(inspector.textContent).toContain("Measured");
  });
});

describe("drilling down: subsystem -> component -> source", () => {
  it("reveals a part's components only once it is explored", () => {
    renderGuided();
    const card = screen.getAllByTestId("subsystem-card").find((c) => c.getAttribute("data-subsystem-id") === "sub:mllib")!;
    fireEvent.click(card);

    expect(screen.getByTestId("subsystem-view").getAttribute("data-subsystem-id")).toBe("sub:mllib");
    expect(screen.getByTestId("breadcrumb").textContent).toContain("Example Engine›Machine learning (MLlib)");
    expect(screen.queryByTestId("orientation")).toBeNull();
    const nodes = screen.getAllByTestId("architecture-node").map((n) => n.getAttribute("data-node-id"));
    expect(nodes.sort()).toEqual(["pipeline:kmeans", "step:kmeans-read"]);

    fireEvent.click(box("Kmeans Wrapper pipeline"));
    expect(screen.getByTestId("inspector-title").textContent).toContain("Kmeans Wrapper pipeline");
    expect(screen.getByTestId("breadcrumb").textContent).toContain("›Kmeans Wrapper pipeline");
    const link = within(screen.getByTestId("source-references")).getByRole("link");
    expect(link.getAttribute("href")).toBe(`https://github.com/example/shop/blob/${"f".repeat(40)}/mllib/src/kmeans_wrapper.py#L4`);

    fireEvent.click(within(screen.getByTestId("breadcrumb")).getByRole("button", { name: "Example Engine" }));
    expect(screen.getByTestId("orientation")).toBeTruthy();
  });

  it("jumps from a concept to the part that implements it", () => {
    renderGuided();
    fireEvent.click(box("Scheduler"));
    fireEvent.click(screen.getByTestId("explore-subsystem"));
    expect(screen.getByTestId("subsystem-view").getAttribute("data-subsystem-id")).toBe("sub:core");
    expect(screen.getAllByTestId("starting-point")[0].textContent).toContain("Learn about: scheduler • executor");
  });

  it("restores the drill-down from the URL", () => {
    renderGuided(guidedView(), null, { subsystem: "sub:mllib", node: "pipeline:kmeans" });
    expect(screen.getByTestId("subsystem-view")).toBeTruthy();
    expect(screen.getByTestId("inspector-title").textContent).toContain("Kmeans Wrapper pipeline");
  });

  it("sends the level and part with Ask RepoLab", () => {
    const graph = guidedView().graph;
    const component = focusFor(graph, "pipeline:kmeans")!;
    expect(component.level).toBe("component");
    expect(component.subsystem).toBe("Machine learning (MLlib)");
    const concept = focusFor(graph, "concept:scheduler")!;
    expect(concept.level).toBe("system");
    expect(concept.directories).toContain("core/src/scheduler");
    expect(concept.relationships).toContain("Your application calls Scheduler");
    const part = focusFor(graph, "sub:core")!;
    expect(part.level).toBe("subsystem");
    expect(part.source_references.map((r) => r.path)).toEqual(["core/src/deploy/MasterDaemon.java"]);
    expect(askUrl("j", null, "Why?")).toBe("/r/j/ask?q=Why%3F");
  });
});

describe("experiments", () => {
  it("are offered where RepoLab can run something, and otherwise stay quiet", () => {
    renderGuided(guidedView(), null, { subsystem: "sub:jobs", node: "pipeline:job" });
    expect(screen.getByTestId("experiment")).toBeTruthy();

    fireEvent.click(box("job"));
    expect(screen.queryByTestId("experiment")).toBeNull();
    expect(screen.queryByTestId("experiment-later")).toBeNull();
    expect(screen.getByTestId("architecture-inspector").textContent).not.toContain("does not exercise");
  });

  it("say once, quietly, when only a built environment is missing", () => {
    renderGuided(guidedView(), null, { subsystem: "sub:core", node: "service:master" });
    expect(screen.queryByTestId("experiment")).toBeNull();
    expect(screen.getByTestId("experiment-later").textContent).toBe("Experiments available after building the environment.");
  });

  it("appear in the learning path only when one is available", () => {
    renderGuided();
    // The learning path is its own perspective now, one click from the overview.
    fireEvent.click(screen.getByTestId("perspective-learning"));
    expect(screen.getByTestId("learning-experiment").textContent).toContain("spark.sql.shuffle.partitions=200");
  });

  it("are listed together under Related experiments", () => {
    renderGuided();
    fireEvent.click(screen.getByTestId("perspective-experiments"));
    expect(screen.getByTestId("related-experiments").textContent).toContain("spark.sql.shuffle.partitions=200");
  });
});

describe("perspectives", () => {
  it("look at the same architecture from different sides, starting with the map", () => {
    renderGuided();
    const nav = screen.getByRole("navigation", { name: "Architecture perspectives" });
    expect(screen.getByTestId("perspective-overview").getAttribute("aria-current")).toBe("page");
    expect(nav.textContent).toContain("Modules");
    fireEvent.click(screen.getByTestId("perspective-modules"));
    expect(screen.queryByTestId("orientation")).toBeNull();
    expect(screen.getAllByTestId("module-card")).toHaveLength(3);
    fireEvent.click(screen.getByTestId("perspective-overview"));
    expect(screen.getByTestId("orientation")).toBeTruthy();
  });

  it("dims what a selection does not touch, and opens its details", () => {
    renderGuided();
    const model = screen.getByTestId("system-model");
    const nodes = within(model).getAllByTestId("architecture-node");
    fireEvent.click(nodes[0]);
    expect(nodes[0].getAttribute("aria-pressed")).toBe("true");
    const states = nodes.slice(1).map((n) => n.getAttribute("data-connected") ?? n.getAttribute("data-dimmed"));
    // Every other concept is either connected to the selection or dimmed.
    expect(states.every((value) => value === "true")).toBe(true);
    expect(screen.getByRole("complementary", { name: "Selected" })).toBeTruthy();
  });
});

describe("runtime evolution", () => {
  it("shows measured values on the concept and part that hold the component", () => {
    const value = { name: "Sales pipeline · total runtime", value: 8200, unit: "ms", source: "spark run abc", provenance: "measured" as const };
    renderGuided(guidedView(), {
      measurements: { "pipeline:job": [{ ...value, name: "total runtime" }] },
      by_group: { "concept:app": [value], "sub:jobs": [value] },
      runs: ["spark run abc"],
      notes: [],
    });
    const model = screen.getByTestId("system-model");
    expect(within(model).getAllByTestId("measured-badge")).toHaveLength(1);
    fireEvent.click(box("Your application"));
    expect(screen.getByTestId("measured-values").textContent).toContain("8.2 s");
    expect(screen.getByTestId("measured-values").textContent).toContain("spark run abc");
  });
});

describe("analysis details", () => {
  it("are collapsed by default and keep the refusals", () => {
    const view = guidedView();
    view.graph.dropped = { "concept without evidence": 2 };
    renderGuided(view);
    const details = screen.getByTestId("analysis-details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(screen.getByTestId("dropped").textContent).toContain("2 × concept without evidence");
  });
});

describe("agent activity", () => {
  beforeAll(() => {
    // jsdom has no layout, so no scrolling; the feed scrolls itself to the end.
    Element.prototype.scrollTo = () => {};
  });
  const events: JobEvent[] = [
    { seq: 1, type: "tool_completed", message: "read_file failed", data: {}, created_at: "" } as unknown as JobEvent,
  ];

  it("folds into a summary once the investigation is done", () => {
    render(<AgentActivityPanel events={events} done summary={{ evidence: 63, components: 26, flows: 3 }} />);
    expect(screen.getByTestId("activity-summary").textContent).toContain("63 pieces of evidence");
    expect(screen.queryByTestId("activity-details")).toBeNull();
    fireEvent.click(screen.getByTestId("toggle-activity"));
    expect(screen.getByTestId("activity-feed").textContent).toContain("read_file failed");
  });

  it("stays visible while the investigation runs", () => {
    render(<AgentActivityPanel events={events} done={false} summary={null} />);
    expect(screen.queryByTestId("activity-summary")).toBeNull();
    expect(screen.getByTestId("activity-feed")).toBeTruthy();
  });
});
