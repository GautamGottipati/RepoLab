/**
 * ArtifactSection: content as soon as it exists; until then reserved space and
 * plain words; a failure stays inside its own section, with Retry.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArtifactSection, ArtifactsProvider, type ArtifactsValue } from "../artifact";
import type { ArtifactState, ArtifactStatus } from "@/lib/types";

afterEach(cleanup);

function state(status: ArtifactStatus, extra: Partial<ArtifactState> = {}): ArtifactState {
  return {
    type: "architecture.summary",
    status,
    priority: "P1",
    version: "1",
    lazy: false,
    depends_on: [],
    description: "",
    updated_at: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    cache_hits: 0,
    error: null,
    ...extra,
  };
}

function renderWith(
  status: ArtifactStatus | null,
  { available = false, settled = false, quiet = false, extra = {} as Partial<ArtifactState> } = {},
) {
  const request = vi.fn();
  const value: ArtifactsValue = {
    states: status ? { "architecture.summary": state(status, extra) } : {},
    request,
    settled,
  };
  render(
    <ArtifactsProvider value={value}>
      <ArtifactSection type="architecture.summary" available={available} minHeight="7rem" quiet={quiet}>
        <p>What is this? It is Spark.</p>
      </ArtifactSection>
    </ArtifactsProvider>,
  );
  return request;
}

describe("ArtifactSection", () => {
  it.each([
    ["queued", "Waiting to start…"],
    ["generating", "Writing a plain-language summary…"],
    ["verifying", "Verifying architecture against source code…"],
  ] as const)("holds the space and says what is happening while %s, never a percentage", (status, words) => {
    renderWith(status);
    const section = screen.getByTestId("artifact-architecture.summary");
    expect(section.getAttribute("aria-busy")).toBe("true");
    expect(section.style.minHeight).toBe("7rem");
    expect(section.textContent).toContain(words);
    expect(section.textContent).not.toMatch(/%|stall/i);
    expect(screen.queryByText(/It is Spark/)).toBeNull();
  });

  it("renders the content once it is ready", () => {
    renderWith("ready", { available: true });
    expect(screen.getByText("What is this? It is Spark.")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders content that already exists whatever the status says (a cache, an older job)", () => {
    renderWith(null, { available: true });
    expect(screen.getByText(/It is Spark/)).toBeTruthy();
  });

  it("shows a failure with the backend's message and retries only this artifact", () => {
    const request = renderWith("failed", {
      extra: {
        error: { error_code: "LLMError", stage: "orientation", message: "the model timed out", retryable: true },
      },
    });
    expect(screen.getByText(/the model timed out/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("artifact-architecture.summary-retry"));
    expect(request).toHaveBeenCalledWith("architecture.summary", true);
  });

  it("explains a blocked section without offering a retry of its own", () => {
    renderWith("blocked");
    expect(screen.getByText(/earlier step/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows nothing once the job is finished and the artifact never came", () => {
    renderWith("not_requested", { settled: true });
    expect(screen.queryByTestId("artifact-architecture.summary")).toBeNull();
  });

  it("a quiet section keeps its space but leaves the words and failures to the main one", () => {
    renderWith("generating", { quiet: true });
    expect(screen.getByTestId("artifact-architecture.summary").textContent).toBe("");
    cleanup();
    renderWith("failed", { quiet: true });
    expect(screen.queryByTestId("artifact-architecture.summary")).toBeNull();
  });
});
