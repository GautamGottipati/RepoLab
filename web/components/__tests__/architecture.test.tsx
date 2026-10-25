/**
 * The component view, rendered offline from a graph stored before the
 * progressive levels existed -- the fallback every older investigation gets.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ArchitectureExperience } from "../architecture-experience";
import { manifest, pipelineView } from "./fixtures";
import type { RuntimeOverlay } from "@/lib/types";

afterEach(cleanup);

function renderScreen(view = pipelineView(), overlay: RuntimeOverlay | null = null) {
  return render(<ArchitectureExperience jobId="job-1" view={view} manifest={manifest} overlay={overlay} />);
}

const nodeButton = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label},`) });

describe("the graph", () => {
  it("renders every component, and inferred relationships differently", () => {
    renderScreen();
    expect(screen.getAllByTestId("architecture-node")).toHaveLength(6);
    const provenance = screen.getAllByTestId("architecture-edge").map((e) => e.getAttribute("data-provenance"));
    expect(provenance).toContain("inferred");
    expect(provenance).toContain("static");
  });

  it("names the view chosen for this repository and lists its flow", () => {
    renderScreen();
    expect(screen.getAllByText("Data flow").length).toBeGreaterThan(0);
    expect(screen.getByTestId("primary-flows").textContent).toContain(
      "job → Sales pipeline → Read Orders → Join Customers → Write Report",
    );
  });
});

describe("the inspector", () => {
  it("opens on the start of the flow, and follows selection", () => {
    renderScreen();
    expect(screen.getByTestId("inspector-title").textContent).toContain("job");
    fireEvent.click(nodeButton("Join Customers"));
    expect(screen.getByTestId("inspector-title").textContent).toContain("Join Customers");
    expect(screen.getByTestId("detected-because").textContent).toContain("jobs/job.py:6");
  });

  it("is reachable from the keyboard", () => {
    renderScreen();
    fireEvent.keyDown(nodeButton("Write Report"), { key: "Enter" });
    expect(screen.getByTestId("inspector-title").textContent).toContain("Write Report");
  });

  it("links source references to the investigated commit", () => {
    renderScreen();
    fireEvent.click(nodeButton("Join Customers"));
    const link = within(screen.getByTestId("source-references")).getByRole("link");
    expect(link.getAttribute("href")).toBe(
      `https://github.com/example/shop/blob/${"f".repeat(40)}/jobs/job.py#L6`,
    );
    expect(screen.getByTestId("open-code").getAttribute("href")).toBe(link.getAttribute("href"));
  });

  it("marks a model-written description as inferred", () => {
    renderScreen();
    fireEvent.click(nodeButton("Sales pipeline"));
    expect(screen.getByTestId("architecture-inspector").textContent).toContain("Totals revenue per region.inferred");
  });

  it("sends Ask Why to Ask RepoLab with the component and a question", () => {
    renderScreen();
    fireEvent.click(nodeButton("Sales pipeline"));
    const href = screen.getByTestId("ask-why").getAttribute("href")!;
    const url = new URL(href, "http://x");
    expect(url.pathname).toBe("/r/job-1/ask");
    expect(url.searchParams.get("node")).toBe("pipeline:job");
    expect(url.searchParams.get("q")).toBe("Why does this use spark.sql.shuffle.partitions=200?");
  });

  it("offers Experiment only where RepoLab can run something, without dwelling on it", () => {
    renderScreen();
    fireEvent.click(nodeButton("Sales pipeline"));
    expect(screen.getByTestId("experiment")).toBeTruthy();

    fireEvent.click(nodeButton("job"));
    expect(screen.queryByTestId("experiment")).toBeNull();
    expect(screen.getByTestId("architecture-inspector").textContent).not.toContain("does not exercise");
  });

  it("says this investigation predates the guided overview", () => {
    renderScreen();
    expect(screen.getByText(/Investigate the repository again/)).toBeTruthy();
    expect(screen.queryByTestId("orientation")).toBeNull();
  });
});

describe("important files", () => {
  it("highlights the components a file implements", () => {
    renderScreen();
    const highlighted = () =>
      screen
        .getAllByTestId("architecture-node")
        .filter((n) => n.getAttribute("data-highlighted"))
        .map((n) => n.getAttribute("data-node-id"));
    expect(highlighted()).toEqual([]);
    fireEvent.click(screen.getAllByTestId("important-file")[0]);
    expect(highlighted().sort()).toEqual(["entry:job", "pipeline:job", "step:join"]);
  });
});

describe("partial architecture", () => {
  it("says what was found and what could not be determined", () => {
    renderScreen(
      pipelineView({
        completeness: "partial",
        gaps: ["How Object storage (S3) connects to the rest of the system could not be determined statically."],
      }),
    );
    const panel = screen.getByTestId("partial-architecture");
    expect(panel.textContent).toContain("Architecture partially detected");
    expect(panel.textContent).toContain("could not be determined statically");
  });
});

describe("runtime overlay", () => {
  it("shows nothing as measured until a run has happened", () => {
    renderScreen();
    fireEvent.click(nodeButton("Sales pipeline"));
    expect(screen.getByTestId("nothing-measured")).toBeTruthy();
    expect(screen.queryByTestId("measured-badge")).toBeNull();
    expect(screen.queryByTestId("runtime-overlay")).toBeNull();
  });

  it("shows only values the overlay returned, with their run", () => {
    renderScreen(pipelineView(), {
      measurements: {
        "pipeline:job": [
          { name: "total runtime", value: 8200, unit: "ms", source: "spark run abc12345", provenance: "measured" },
        ],
      },
      runs: ["spark run abc12345"],
      notes: [],
    });
    fireEvent.click(nodeButton("Sales pipeline"));
    const values = screen.getByTestId("measured-values").textContent!;
    expect(values).toContain("total runtime");
    expect(values).toContain("8.2 s");
    expect(values).toContain("spark run abc12345");
    expect(screen.getAllByTestId("measured-badge")).toHaveLength(1);

    fireEvent.click(nodeButton("Join Customers"));
    expect(screen.getByTestId("nothing-measured")).toBeTruthy();
  });
});
