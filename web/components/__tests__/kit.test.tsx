/**
 * The design-system primitives: status is never colour alone, tabs and the
 * drawer work from the keyboard, progress and errors say what really happened.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  ActionLink,
  Button,
  Drawer,
  ErrorState,
  ProgressSteps,
  StatusBadge,
  Tabs,
} from "../ui";

afterEach(cleanup);

describe("StatusBadge", () => {
  it("carries the state as text, not only a colour", () => {
    render(<StatusBadge status="failed">Build failed</StatusBadge>);
    const badge = screen.getByText("Build failed");
    expect(badge.getAttribute("data-state")).toBe("failed");
    expect(badge.textContent).toBe("Build failed");
  });
});

describe("Button", () => {
  it("is a real button with a type, and can be disabled", () => {
    render(
      <Button variant="primary" disabled>
        Run
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Run" });
    expect(button.getAttribute("type")).toBe("button");
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("ActionLink", () => {
  it("labels the action in text and colours it by what it leads to", () => {
    render(
      <ActionLink kind="ask" href="/ask">
        Explain this run
      </ActionLink>,
    );
    const link = screen.getByRole("link", { name: "Explain this run" });
    expect(link.className).toContain("text-ai-fg");
  });
});

function TabsHarness() {
  const [value, setValue] = useState("table");
  return (
    <Tabs
      label="Result"
      value={value}
      onChange={setValue}
      items={[
        { id: "table", label: "Table" },
        { id: "logs", label: "Logs" },
        { id: "raw", label: "Raw" },
      ]}
    >
      <p>panel:{value}</p>
    </Tabs>
  );
}

describe("Tabs", () => {
  it("moves with the arrow keys and Home/End, and shows the selected panel", () => {
    render(<TabsHarness />);
    const table = screen.getByRole("tab", { name: "Table" });
    expect(table.getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(table, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Logs" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("panel:logs")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Logs" }), { key: "End" });
    expect(screen.getByRole("tab", { name: "Raw" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Raw" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Table" }).getAttribute("aria-selected")).toBe("true");
    // Only the selected tab is in the tab order.
    expect(screen.getByRole("tab", { name: "Logs" }).getAttribute("tabindex")).toBe("-1");
  });
});

function DrawerHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open details
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Details">
        <button type="button">Inside</button>
      </Drawer>
    </>
  );
}

describe("Drawer", () => {
  it("is a modal dialog that Escape closes, returning focus to its opener", () => {
    render(<DrawerHarness />);
    const opener = screen.getByRole("button", { name: "Open details" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Details" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    // Focus moves into the dialog (its first control is Close).
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

describe("ProgressSteps", () => {
  it("marks done, current and upcoming steps, and the current one for assistive tech", () => {
    render(
      <ProgressSteps
        label="Preparing"
        steps={[
          { key: "a", label: "Repository cloned", state: "done" },
          { key: "b", label: "Mapping architecture", state: "current" },
          { key: "c", label: "Finding experiences", state: "upcoming" },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("data-state"))).toEqual(["done", "current", "upcoming"]);
    expect(items[1].getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("done")).toBeTruthy();
  });
});

describe("ErrorState", () => {
  it("says what worked, what failed and why", () => {
    render(
      <ErrorState
        title="Experience couldn't start"
        succeeded={["Repository analyzed", "Runtime detected"]}
        failed="Application failed its health check"
        reason="It did not answer on its port within 60 seconds."
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Repository analyzed");
    expect(alert.textContent).toContain("Application failed its health check");
    expect(alert.textContent).toContain("60 seconds");
  });
});
