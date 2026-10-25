/**
 * The live experience, offline: the plan before anything runs, progress while
 * it starts, LIVE only once a service answered, and failures with evidence.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RuntimeView, ServicePlan, ServiceRuntime } from "@/lib/types";


const calls = vi.hoisted(() => ({
  runtime: vi.fn(),
  startRuntime: vi.fn(),
  runtimeById: vi.fn(),
  stopRuntime: vi.fn(),
  runtimeRequest: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { ...calls, eventsUrl: (id: string) => `/jobs/${id}/events` },
  ApiError: class extends Error {},
}));
vi.mock("@/lib/events", () => ({ useEventStream: () => ({ events: [], status: "idle" }) }));

import { LiveExperience } from "../live-experience";

const COMMIT = "c".repeat(40);

function service(name: string, surfaces: string[], extra: Partial<ServicePlan> = {}): ServicePlan {
  return {
    name,
    surfaces,
    working_directory: name === "backend" ? "backend" : "",
    runtime: "node",
    framework: surfaces.includes("web") ? "angular" : "express",
    image: "node:14",
    image_reason: "package-lock.json v1 was written by the npm that ships with Node 14",
    package_manager: "npm",
    install: "npm ci --no-audit --no-fund",
    build: null,
    start: "npm run start",
    script: "start",
    port: surfaces.includes("web") ? 4200 : 3000,
    port_source: surfaces.includes("web") ? "the angular dev server's default port (4200)" : "PORT || 3000 in backend/bin/www:15",
    env: [],
    health_path: "/",
    routes: surfaces.includes("api")
      ? [
          { method: "GET", path: "/events/api/posts", file: "backend/routes/events.js", line: 38 },
          { method: "POST", path: "/user/api/login", file: "backend/routes/user.js", line: 60 },
        ]
      : [],
    evidence: [],
    inferred_fields: surfaces.includes("web") ? ["image", "port"] : ["image"],
    warnings: surfaces.includes("web")
      ? [{ message: "The frontend calls http://localhost:3000 directly (the backend service's port).", evidence: [{ path: "src/app/auth.service.ts", start_line: 41, end_line: 41, commit_sha: COMMIT, excerpt: null }] }]
      : [],
    ...extra,
  };
}

const plan = {
  services: [service("event-app", ["web"]), service("backend", ["api"])],
  dependencies: [
    {
      kind: "mongodb" as const,
      state: "external_required" as const,
      env_var: null,
      reason: "the MongoDB connection string is written into the code (backend/app.js:19)",
      evidence: [],
      services: ["backend"],
    },
  ],
  unplanned: {},
  adapter_version: "node-1",
};

function runtime(name: string, surfaces: string[], extra: Partial<ServiceRuntime> = {}): ServiceRuntime {
  return {
    name,
    surfaces,
    status: "running",
    steps: [],
    port: 3000,
    port_source: null,
    host_port: 41001,
    health: { path: "/", status_code: 200, ok: true, detail: "HTTP 200" },
    failure: null,
    warnings: [],
    log_tail: "",
    ...extra,
  };
}

function view(services: ServiceRuntime[] | null, origins: Record<string, string | null> = {}): RuntimeView {
  const status = services ? (services.every((s) => s.status === "running") ? "running" : services[0].status) : null;
  return {
    runtime_id: services ? "abcdef123456" : null,
    job_id: services ? "job-run" : null,
    events_url: null,
    status,
    state:
      services && status
        ? { id: "abcdef123456", repo_url: "https://github.com/e/app", commit_sha: COMMIT, status, services, dependencies: [] }
        : null,
    plan,
    services: Object.entries(origins).map(([name, origin]) => ({ name, origin })),
  };
}

function renderLive() {
  return render(<LiveExperience jobId="job-1" repoUrl="https://github.com/e/app" commitSha={COMMIT} />);
}

beforeEach(() => {
  Object.values(calls).forEach((fn) => fn.mockReset());
  calls.stopRuntime.mockResolvedValue({ runtime_id: "abcdef123456", status: "stopping" });
});
afterEach(cleanup);

describe("before anything runs", () => {
  it("shows the plan, from the repository's own scripts, with what was inferred", async () => {
    calls.runtime.mockResolvedValue(view(null));
    renderLive();
    const web = await screen.findByTestId("plan-event-app");
    expect(web.textContent).toContain("node:14");
    expect(web.textContent).toContain("(inferred)");
    expect(screen.getByTestId("plan-backend").textContent).toContain("PORT || 3000 in backend/bin/www:15");
    expect(screen.getByTestId("runtime-dependencies").textContent).toContain("external — not reachable from the sandbox");
    expect(screen.getByTestId("runtime-warnings").textContent).toContain("localhost:3000");
    // Nothing is called LIVE before it has run.
    expect(screen.queryByText("● LIVE")).toBeNull();
  });

  it("starts only when asked", async () => {
    calls.runtime.mockResolvedValue(view(null));
    calls.startRuntime.mockResolvedValue(view([runtime("event-app", ["web"], { status: "installing" }), runtime("backend", ["api"], { status: "preparing" })]));
    renderLive();
    expect(calls.startRuntime).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("start-runtime"));
    expect(calls.startRuntime).toHaveBeenCalledWith("job-1");
    const progress = await screen.findByTestId("progress-event-app");
    const marks = within(progress).getAllByRole("listitem").map((li) => li.getAttribute("data-mark"));
    expect(marks).toEqual(["done", "active", "pending", "pending"]);
  });
});

describe("once it answers", () => {
  it("embeds the live web app and opens it on its own origin", async () => {
    calls.runtime.mockResolvedValue(
      view([runtime("event-app", ["web"]), runtime("backend", ["api"])], {
        "event-app": "http://rt-abcdef123456-event-app.localhost:8000",
        backend: "http://rt-abcdef123456-backend.localhost:8000",
      }),
    );
    renderLive();
    const preview = await screen.findByTestId("preview-event-app");
    expect(preview.querySelector("iframe")!.getAttribute("src")).toBe("http://rt-abcdef123456-event-app.localhost:8000");
    expect(screen.getByTestId("open-event-app").getAttribute("href")).toBe("http://rt-abcdef123456-event-app.localhost:8000");
    expect(screen.getAllByText("● LIVE").length).toBeGreaterThanOrEqual(2);
  });

  it("lets you call the API's declared routes and shows the real response", async () => {
    calls.runtime.mockResolvedValue(view([runtime("backend", ["api"])], { backend: "http://rt-abcdef123456-backend.localhost:8000" }));
    calls.runtimeRequest.mockResolvedValue({ status_code: 200, headers: [], body: '{"posts":[]}', truncated: false, binary: false, elapsed_ms: 12 });
    renderLive();
    const explorer = await screen.findByTestId("explorer-backend");
    const endpoints = within(explorer).getByTestId("endpoints");
    expect(endpoints.textContent).toContain("/events/api/posts");
    expect(endpoints.textContent).toContain("events.js:38");
    fireEvent.click(within(explorer).getByTestId("explorer-send"));
    expect(calls.runtimeRequest).toHaveBeenCalledWith("abcdef123456", { service: "backend", method: "GET", path: "/events/api/posts", body: null });
    const response = await within(explorer).findByTestId("explorer-response");
    expect(response.textContent).toContain("200");
    expect(response.textContent).toContain('"posts": []');
  });

  it("is not LIVE without an origin, even if a status says running", async () => {
    calls.runtime.mockResolvedValue(view([runtime("backend", ["api"])], { backend: null }));
    renderLive();
    await screen.findByTestId("service-backend");
    expect(screen.queryByTestId("explorer-backend")).toBeNull();
  });
});

describe("when it fails", () => {
  it("says what succeeded, what failed and why, with evidence and logs", async () => {
    calls.runtime.mockResolvedValue(
      view([
        runtime("event-app", ["web"], {
          status: "build_failed",
          failure: {
            status: "build_failed",
            reason: "`npm ci --no-audit --no-fund` exited with code 1",
            evidence: [{ path: "package-lock.json", start_line: null, end_line: null, commit_sha: COMMIT, excerpt: null }],
            next_step: "Read the install log below.",
            log_tail: "npm ERR! node-sass@4 failed to build",
          },
        }),
      ]),
    );
    renderLive();
    const failure = await screen.findByTestId("failure-event-app");
    expect(failure.textContent).toContain("could not start");
    expect(failure.textContent).toContain("exited with code 1");
    expect(within(failure).getByRole("link", { name: "package-lock.json" }).getAttribute("href")).toContain(`/blob/${COMMIT}/package-lock.json`);
    const marks = within(screen.getByTestId("progress-event-app")).getAllByRole("listitem").map((li) => li.getAttribute("data-mark"));
    expect(marks).toEqual(["done", "failed", "pending", "pending"]);
    expect(screen.getByTestId("logs-event-app").textContent).toContain("node-sass@4 failed");
    expect(screen.queryByText("● LIVE")).toBeNull();
  });
});

describe("stopping", () => {
  it("releases the runtime", async () => {
    calls.runtime.mockResolvedValue(view([runtime("backend", ["api"])], { backend: "http://rt-abcdef123456-backend.localhost:8000" }));
    renderLive();
    fireEvent.click(await screen.findByTestId("stop-runtime"));
    expect(calls.stopRuntime).toHaveBeenCalledWith("abcdef123456");
  });
});
