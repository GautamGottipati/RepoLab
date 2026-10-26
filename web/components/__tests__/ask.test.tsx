/**
 * Ask RepoLab, offline: an answer laid out to teach -- simple answer, picture,
 * steps, details, then sources a click away -- and the context around it.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnswerView } from "../answer-view";
import { AskContext, QuestionBox, QuestionHistory } from "../ask-context";
import { webView } from "./fixtures";
import { citedSources, excerptRange, suggestionsFor } from "@/lib/ask";
import type { AnswerResult, ExperimentState, GroundedAnswer } from "@/lib/types";

afterEach(cleanup);

const COMMIT = "c".repeat(40);
const REPO = "https://github.com/example/events";

const appJs = Array.from({ length: 40 }, (_, i) => `line ${i + 1} of app.js`).join("\n");

function result(answer: Partial<GroundedAnswer> | null, extra: Partial<AnswerResult> = {}): AnswerResult {
  return {
    question: "How do the APIs use the database?",
    kind: "repository",
    shape: "flow",
    answer: answer
      ? {
          answer: {
            answer: "The APIs read and write through Mongoose models, which talk to MongoDB.",
            claims: [
              { kind: "repository", statement: "The server connects to MongoDB.", support: "backend/app.js" },
              { kind: "inferred", statement: "All APIs share one connection.", support: "the claim above" },
            ],
            caveats: ["The Users API's model is not in the evidence."],
            summary: "Each API uses a Mongoose model to store its data in MongoDB.",
            shape: "flow",
            ...answer,
          },
          unsupported: [],
          checked_claims: 1,
          grounded: true,
          not_established: [],
          dropped: {},
        }
      : null,
    evidence: {
      code_references: [
        { path: "backend/app.js", start_line: 1, end_line: 40, commit_sha: COMMIT, excerpt: appJs },
        { path: "backend/models/order.js", start_line: 1, end_line: 2, commit_sha: COMMIT, excerpt: "const Order = mongoose.model('Order');\nmodule.exports = Order;" },
        { path: "README.md", start_line: 1, end_line: 1, commit_sha: COMMIT, excerpt: "# Events" },
      ],
      measurements: [],
      logs: [],
      documentation: [],
      simulated: false,
    },
    findings: ["Read backend/app.js"],
    downgraded: null,
    error: null,
    follow_ups: ["Follow an order request", "Where are the models defined?"],
    ...extra,
  };
}

const flow: Partial<GroundedAnswer> = {
  visual: {
    nodes: [
      { id: "server", label: "Web server", kind: "server", component_id: "service:server", source: "backend/app.js" },
      { id: "orders", label: "Orders API", kind: "route", component_id: "api:orders", source: null },
      { id: "model", label: "Order model", kind: "model", component_id: "model:order", source: null },
      { id: "db", label: "MongoDB", kind: "database", component_id: "db:main", source: null },
    ],
    edges: [
      { source: "server", target: "orders", label: "routes requests", support: "edge:service:server->api:orders", provenance: "static" },
      { source: "orders", target: "model", label: "queries", support: "backend/routes/orders.js:2", provenance: "static" },
      { source: "model", target: "db", label: "persists", support: "inferred", provenance: "inferred" },
    ],
  },
  steps: [
    { title: "The server mounts the API", description: "Express routes /api/orders.", sources: ["backend/app.js:5"] },
    { title: "The API asks its model", description: "A Mongoose model called Order.", sources: ["backend/models/order.js"] },
  ],
};

function renderAnswer(r: AnswerResult, onAsk = vi.fn()) {
  render(<AnswerView result={r} repoUrl={REPO} commitSha={COMMIT} architecture={webView().graph} onAsk={onAsk} />);
  return onAsk;
}

const before = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe("the answer comes first", () => {
  it("shows the simple answer before the picture, the steps and the sources", () => {
    renderAnswer(result(flow));
    const simple = screen.getByTestId("simple-answer");
    expect(screen.getByTestId("answer").textContent).toBe("Each API uses a Mongoose model to store its data in MongoDB.");
    expect(before(simple, screen.getByTestId("answer-visual"))).toBe(true);
    expect(before(screen.getByTestId("answer-visual"), screen.getByTestId("how-it-works"))).toBe(true);
    expect(before(screen.getByTestId("how-it-works"), screen.getByTestId("sources"))).toBe(true);
    expect(before(screen.getByTestId("sources"), screen.getByTestId("continue-exploring"))).toBe(true);
  });

  it("falls back to the prose of an answer stored before summaries", () => {
    const old = result({});
    const stored = old.answer!.answer;
    delete stored.summary;
    delete stored.shape;
    old.shape = undefined;
    renderAnswer(old);
    expect(screen.getByTestId("answer").textContent).toContain("read and write through Mongoose");
    expect(screen.queryByTestId("answer-visual")).toBeNull();
    expect(screen.getByTestId("answer-view").getAttribute("data-shape")).toBe("general");
  });
});

describe("the picture", () => {
  it("draws exactly the verified boxes and arrows, inferred ones dashed", () => {
    renderAnswer(result(flow));
    const visual = screen.getByTestId("answer-visual");
    expect(within(visual).getAllByTestId("diagram-node")).toHaveLength(4);
    const edges = within(visual).getAllByTestId("diagram-edge");
    expect(edges.map((e) => e.getAttribute("data-provenance"))).toEqual(["static", "static", "inferred"]);
    expect(edges[2].getAttribute("stroke-dasharray")).toBe("4 3");
    expect(within(screen.getByTestId("relationships")).getByText("inferred")).toBeTruthy();
  });

  it("says what could not be established instead of drawing it", () => {
    const r = result(flow);
    r.answer!.not_established = ["Users API → MongoDB"];
    renderAnswer(r);
    expect(screen.getByTestId("not-established").textContent).toContain("Users API → MongoDB");
    expect(screen.queryByText("Users API")).toBeNull();
  });

  it("scales to its column rather than pushing the page sideways", () => {
    renderAnswer(result(flow));
    const svg = screen.getByTestId("answer-visual").querySelector("svg")!;
    expect(svg.getAttribute("width")).toBe("100%");
    expect(svg.getAttribute("viewBox")).toBeTruthy();
  });

  it("lays out a code map for a location question", () => {
    renderAnswer(
      result({
        shape: "location",
        locations: [
          { role: "Entry", path: "backend/app.js", line: 5, why: "Mounts the routes" },
          { role: "Persistence", path: "backend/models/", line: null, why: "" },
        ],
      }),
    );
    const entries = screen.getAllByTestId("code-map-entry");
    expect(entries.map((e) => e.textContent)).toEqual([
      expect.stringContaining("backend/app.js:5"),
      expect.stringContaining("backend/models/"),
    ]);
    fireEvent.click(within(entries[0]).getByRole("button"));
    expect(screen.getByTestId("drawer-reason").textContent).toBe("Mounts the routes");
  });

  it("marks comparison cells the evidence did not establish", () => {
    renderAnswer(
      result({
        shape: "comparison",
        comparison: {
          subjects: ["Order", "User"],
          rows: [
            { attribute: "Defined in", values: ["order.js", "user.js"], support: "backend/models/order.js", provenance: "static" },
            { attribute: "Indexed by", values: ["not established", "not established"], support: "", provenance: "unknown" },
          ],
        },
      }),
    );
    const rows = within(screen.getByTestId("comparison")).getAllByRole("row");
    expect(rows[2].getAttribute("data-provenance")).toBe("unknown");
    expect(rows[2].textContent).toContain("not established");
  });
});

describe("how it works and the details", () => {
  it("numbers the steps and links each to its source", () => {
    renderAnswer(result(flow));
    const steps = screen.getAllByTestId("answer-step");
    expect(steps.map((s) => s.textContent?.slice(0, 1))).toEqual(["1", "2"]);
    const chip = within(steps[0]).getByTestId("source-chip");
    expect(chip.textContent).toBe("📄 backend/app.js:5");
    fireEvent.click(chip);
    const drawer = screen.getByTestId("code-drawer");
    expect(drawer.querySelector("[data-cited]")!.textContent).toContain("line 5 of app.js");
    expect(screen.getByTestId("drawer-reason").textContent).toBe("The server mounts the API");
    expect(screen.getByTestId("drawer-link").getAttribute("href")).toBe(
      `https://github.com/example/events/blob/${COMMIT}/backend/app.js#L5`,
    );
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("code-drawer")).toBeNull();
  });

  it("labels why-sections by how firmly they are known", () => {
    renderAnswer(
      result({
        shape: "why",
        sections: [
          { key: "choice", title: "Design choice", body: "It uses MongoDB.", basis: "evidenced", sources: ["backend/app.js"] },
          { key: "helps", title: "Why it helps", body: "Probably flexible schemas.", basis: "inferred", sources: [] },
          { key: "tradeoffs", title: "Trade-offs", body: "The evidence does not say.", basis: "unknown", sources: [] },
        ],
      }),
    );
    const sections = screen.getAllByTestId("answer-section");
    expect(sections.map((s) => s.getAttribute("data-basis"))).toEqual(["evidenced", "inferred", "unknown"]);
    expect(sections[1].textContent).toContain("inference");
    expect(sections[2].textContent).toContain("not known from the evidence");
  });
});

describe("sources", () => {
  it("are counted, collapsed, and expand to a small range", () => {
    renderAnswer(result(flow));
    expect(screen.getByTestId("source-counts").textContent).toBe("Code 3Docs 0Runtime 0");
    expect(screen.getByTestId("provenance-line").textContent).toBe("✓ Based on repository code");
    expect(screen.queryByTestId("source-excerpt")).toBeNull();
    expect(screen.queryByTestId("evidence-panel")).toBeNull();

    const first = screen.getAllByTestId("source-item")[0];
    expect(first.textContent).toContain("backend/app.js:5");
    fireEvent.click(within(first).getByRole("button", { expanded: false }));
    const excerpt = within(first).getByTestId("source-excerpt");
    const lines = excerpt.querySelectorAll("pre > div");
    expect(lines.length).toBeLessThanOrEqual(11);
    expect(excerpt.textContent).not.toContain("line 30 of app.js");
  });

  it("keep the full evidence, with its labels, one click away", () => {
    renderAnswer(result(flow));
    // Collapsed by default: the answer leads, the evidence is disclosed on request.
    expect(screen.queryByTestId("all-evidence")).toBeNull();
    fireEvent.click(screen.getByTestId("show-all-evidence"));
    const all = screen.getByTestId("all-evidence");
    expect(within(all).getByTestId("evidence-panel")).toBeTruthy();
    expect(within(all).getByTestId("claims-repository").textContent).toContain("static · repository");
    expect(within(all).getByTestId("claims-inferred")).toBeTruthy();
  });

  it("say when the answer includes measured runtime evidence", () => {
    const r = result(flow);
    r.evidence.measurements = [{ name: "Orders API · statements run", value: 3, unit: "statements", source: "cli_output", variant_id: null }];
    renderAnswer(r);
    expect(screen.getByTestId("source-counts").textContent).toContain("Runtime 1");
    expect(screen.getByTestId("provenance-line").textContent).toBe("● Includes measured runtime evidence");
  });

  it("are listed in the order the reader meets them", () => {
    const paths = citedSources(result(flow)).map((s) => `${s.path}:${s.line}`);
    expect(paths).toEqual(["backend/app.js:5", "backend/models/order.js:1", "backend/routes/orders.js:2", "README.md:1"]);
  });

  it("never render a whole file", () => {
    const ref = { path: "a.js", start_line: 1, end_line: 40, commit_sha: COMMIT, excerpt: appJs };
    expect(excerptRange(ref, null)).toHaveLength(12);
    const around = excerptRange(ref, 20);
    expect(around).toHaveLength(11);
    expect(around.find((l) => l.cited)?.number).toBe(20);
  });
});

describe("partial evidence", () => {
  it("still shows the sources when no answer could be written", () => {
    renderAnswer(result(null, { error: "No model is configured, so RepoLab cannot write an answer." }));
    expect(screen.getByTestId("answer-error").textContent).toContain("No model is configured");
    expect(screen.getByTestId("source-counts").textContent).toContain("Code 3");
    expect(screen.queryByTestId("how-it-works")).toBeNull();
  });

  it("flags unsupported claims prominently", () => {
    const r = result(flow);
    r.answer!.unsupported = [{ statement: "Requests take 40 ms.", kind: "measured", problem: "nothing was run" }];
    r.answer!.grounded = false;
    renderAnswer(r);
    expect(within(screen.getByTestId("simple-answer")).getByTestId("unsupported-claims")).toBeTruthy();
  });
});

describe("continue exploring", () => {
  it("asks the next question through the same flow", () => {
    const onAsk = renderAnswer(result(flow));
    const buttons = screen.getAllByTestId("follow-up");
    expect(buttons.map((b) => b.textContent)).toEqual(["Follow an order request →", "Where are the models defined? →"]);
    fireEvent.click(buttons[0]);
    expect(onAsk).toHaveBeenCalledWith("Follow an order request");
  });
});

describe("the question box", () => {
  function renderBox(props: Partial<Parameters<typeof QuestionBox>[0]> = {}) {
    const onAsk = vi.fn();
    const onClear = vi.fn();
    render(
      <QuestionBox
        question=""
        onChange={() => {}}
        onAsk={onAsk}
        busy={false}
        disabled
        contextLabel="Database"
        onClearContext={onClear}
        suggestions={suggestionsFor(webView(), "db:main")}
        placeholder=""
        {...props}
      />,
    );
    return { onAsk, onClear };
  }

  it("shows the context as one chip that can be removed", () => {
    const { onClear } = renderBox();
    expect(screen.getByTestId("question-focus").textContent).toContain("Context: Database");
    expect(screen.getByText("Ask about Database")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ask without the Database context" }));
    expect(onClear).toHaveBeenCalled();
  });

  it("suggests questions from the selected component's own context", () => {
    const { onAsk } = renderBox();
    const suggestions = screen.getAllByTestId("suggestion").map((b) => b.textContent);
    expect(suggestions).toEqual(["Which components write to Database?", "How does data reach Database?"]);
    fireEvent.click(screen.getAllByTestId("suggestion")[1]);
    expect(onAsk).toHaveBeenCalledWith("How does data reach Database?");
  });

  it("asks about the repository when there is no component in context", () => {
    renderBox({ contextLabel: null, suggestions: [] });
    expect(screen.queryByTestId("question-focus")).toBeNull();
    expect(screen.getByText("Ask about this repository")).toBeTruthy();
    expect(screen.queryByTestId("suggestions")).toBeNull();
  });
});

describe("the repository context sidebar", () => {
  it("describes the selected component from the architecture", () => {
    render(<AskContext view={webView()} focusId="db:main" result={null} />);
    const selected = screen.getByTestId("context-selected");
    expect(selected.textContent).toContain("Database");
    expect(selected.textContent).toContain("Where orders and users are stored.");
    expect(screen.getByTestId("context-used-by").textContent).toContain("Order model");
    expect(selected.textContent).toContain("MongoDB");
    expect(screen.getByTestId("context-files").textContent).toBe("Important filesapp.js");
  });

  it("adapts to another component", () => {
    render(<AskContext view={webView()} focusId="service:server" result={null} />);
    expect(screen.getByTestId("context-uses").textContent).toContain("Orders API");
    expect(screen.getByTestId("context-uses").textContent).toContain("Users API");
  });

  it("without a component, shows the repository and what the answer drew", () => {
    render(<AskContext view={webView()} focusId={null} result={result(flow)} />);
    expect(screen.queryByTestId("context-selected")).toBeNull();
    expect(screen.getByTestId("context-repository")).toBeTruthy();
    expect(screen.getByTestId("context-in-answer").textContent).toContain("Orders API");
  });
});

describe("history", () => {
  it("is compact and closed by default", () => {
    const entry = { experiment_id: "e1", question: "Where are posts stored?", status: "answered" } as ExperimentState;
    render(<QuestionHistory jobId="job-1" history={[entry]} />);
    const history = screen.getByTestId("question-history") as HTMLDetailsElement;
    expect(history.open).toBe(false);
    expect(within(history).getByRole("link", { name: "Where are posts stored?" }).getAttribute("href")).toBe("/r/job-1/ask/e1");
  });
});

describe("small screens", () => {
  it("keep code and tables inside their own scrolling boxes", () => {
    renderAnswer(
      result({
        ...flow,
        comparison: {
          subjects: ["A", "B"],
          rows: [{ attribute: "x", values: ["1", "2"], support: "backend/app.js", provenance: "static" }],
        },
      }),
    );
    expect(screen.getByTestId("comparison").className).toContain("overflow-x-auto");
    expect(screen.getByTestId("answer-view").className).toContain("min-w-0");
    fireEvent.click(within(screen.getAllByTestId("source-item")[0]).getByRole("button", { expanded: false }));
    expect(screen.getByTestId("source-excerpt").querySelector("pre")!.className).toContain("overflow-auto");
  });
});
