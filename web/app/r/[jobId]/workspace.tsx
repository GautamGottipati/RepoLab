/**
 * The workspace shell and the context every screen reads.
 *
 * This component owns the job's SSE connection and its accumulated events.
 * Next.js layouts preserve state across navigation, so switching tabs never
 * drops the stream or re-replays history — which is why the connection lives
 * here rather than in each screen.
 *
 * The chrome is a repository header (the name dominates; commit and status are
 * secondary), four tabs — Overview, Experiences, Architecture, Ask RepoLab —
 * and an activity chip that opens the agent's activity in a drawer, so the
 * page's own content gets the width.
 *
 * Spec: docs/design/00-overview.md
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { ActivityFeed, PhaseTracker, phasesFrom } from "@/components/activity";
import {
  ActivityIcon,
  ChevronDownIcon,
  Drawer,
  ErrorNote,
  Note,
  StatusBadge,
  type Status,
} from "@/components/ui";
import { api, ApiError, optional } from "@/lib/api";
import { field, isBoolean, isString, useEventStream, type StreamStatus } from "@/lib/events";
import { shortHash, slug } from "@/lib/format";
import type {
  ArchitectureView,
  ExperienceCatalog,
  JobEvent,
  JobState,
  RepoManifest,
} from "@/lib/types";

type Workspace = {
  jobId: string;
  job: JobState | null;
  manifest: RepoManifest | null;
  architecture: ArchitectureView | null;
  /**
   * Every surface this repository offers and which of them this build can open.
   * Screens that link to the Playground or Learn gate on it, so a screen is
   * never offered for a surface RepoLab would refuse to run.
   */
  experiences: ExperienceCatalog | null;
  events: JobEvent[];
  /** True once the investigation has produced a manifest. */
  ready: boolean;
  /**
   * Whether the Spark image is available, as reported by the investigation's
   * environment phase. `null` means it never said.
   */
  environmentReady: boolean | null;
  error: string | null;
  /**
   * A right-hand column a page can fill with context of its own (Ask RepoLab's
   * repository context). It only takes space while something is in it.
   */
  asideSlot: HTMLElement | null;
  /** Open the agent activity drawer. */
  openActivity: () => void;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside the workspace layout");
  return value;
}

/**
 * The tabs. "Overview" is the investigation's screen, under the name a new
 * user understands; the route is unchanged. The Playground and Learn screens
 * are experiences, so they are reached from Experiences and keep that tab
 * selected (`also`).
 */
export const TABS: { href: string; label: string; needsManifest: boolean; also?: string[] }[] = [
  { href: "", label: "Overview", needsManifest: false },
  { href: "/experiences", label: "Experiences", needsManifest: true, also: ["/playground", "/learn"] },
  { href: "/architecture", label: "Architecture", needsManifest: true },
  { href: "/ask", label: "Ask RepoLab", needsManifest: true },
];

export function WorkspaceShell({ jobId, children }: { jobId: string; children: ReactNode }) {
  const [job, setJob] = useState<JobState | null>(null);
  const [manifest, setManifest] = useState<RepoManifest | null>(null);
  const [architecture, setArchitecture] = useState<ArchitectureView | null>(null);
  const [experiences, setExperiences] = useState<ExperienceCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asideSlot, setAsideSlot] = useState<HTMLElement | null>(null);
  const [activityOpen, setActivityOpen] = useState(false);

  const { events, status } = useEventStream(api.eventsUrl(jobId));
  const terminal = job?.status === "completed" || job?.status === "failed";

  // Poll while the job is live; stop once it is not. The event stream carries
  // progress, so this only needs to track status, elapsed and stall detection.
  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const next = await api.job(jobId);
        if (!cancelled) {
          setJob(next);
          setError(null);
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    };

    void poll();
    if (terminal) return () => { cancelled = true; };

    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [jobId, terminal]);

  // The manifest and architecture appear together, once the investigation has
  // saved an ingestion. Both 404 until then, which `optional` turns into null.
  useEffect(() => {
    if (job?.ingestion_id == null || manifest) return;
    let cancelled = false;

    void (async () => {
      const [nextManifest, nextArchitecture, nextExperiences] = await Promise.all([
        optional(api.manifest(jobId)),
        optional(api.architecture(jobId)),
        optional(api.experiences(jobId)),
      ]);
      if (cancelled) return;
      if (nextManifest) setManifest(nextManifest);
      if (nextArchitecture) setArchitecture(nextArchitecture);
      if (nextExperiences) setExperiences(nextExperiences);
    })();

    return () => { cancelled = true; };
  }, [jobId, job?.ingestion_id, manifest]);

  // Reported by the investigation's environment phase rather than probed from
  // the browser, which has no way to see a Docker daemon.
  const environmentReady = useMemo(() => {
    const environmentEvents = events.filter((event) => event.step === "environment");
    for (const event of [...environmentEvents].reverse()) {
      const ready = field(event, "ready", isBoolean);
      if (ready !== undefined) return ready;
      if (event.type === "step_done" && field(event, "image", isString)) return true;
    }
    return null;
  }, [events]);

  const workspace: Workspace = {
    jobId,
    job,
    manifest,
    architecture,
    experiences,
    events,
    ready: manifest !== null,
    environmentReady,
    error,
    asideSlot,
    openActivity: () => setActivityOpen(true),
  };

  // The last *reported* failure, in the words the backend chose. The stream's
  // own terminal frame is also a `failed` event and carries no explanation, so
  // taking the last one of that type finds the frame and loses the message.
  const failed = [...events]
    .reverse()
    .find((event) => event.type === "failed" && field(event, "headline", isString));
  const failure = failed
    ? {
        headline: field(failed, "headline", isString) ?? failed.message,
        nextStep: field(failed, "next_step", isString),
        technical: field(failed, "technical", isString),
      }
    : null;

  const summary = architecture
    ? {
        evidence: architecture.graph.nodes.reduce((n, c) => n + c.source_references.length, 0),
        components: architecture.node_count,
        flows: architecture.graph.primary_flows.length,
      }
    : null;
  const name = architecture?.graph.understanding?.name ?? null;
  const done = job?.status === "completed";

  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className="flex min-h-screen flex-col bg-canvas">
        <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur supports-[backdrop-filter]:bg-surface/85">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-start justify-between gap-x-6 gap-y-2 px-4 pt-3 sm:px-6">
            <div className="min-w-0">
              <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-meta text-fg-muted">
                <Link href="/" className="font-semibold text-fg hover:text-brand-fg">
                  RepoLab
                </Link>
                <span aria-hidden>/</span>
                <span className="truncate font-mono" data-testid="repo-slug">
                  {job ? slug(job.repo_url) : "…"}
                </span>
              </nav>
              <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <p className="truncate text-xl font-semibold tracking-tight text-fg" data-testid="repo-name">
                  {name ?? (job ? slug(job.repo_url).split("/").pop() : "Loading…")}
                </p>
                {manifest && (
                  <span className="font-mono text-xs text-fg-muted" title={manifest.commit_sha}>
                    commit {shortHash(manifest.commit_sha)}
                  </span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <JobBadge status={job?.status} stream={status} />
            </div>
          </div>

          <div className="mx-auto flex max-w-[1400px] items-end justify-between gap-4 px-4 sm:px-6">
            <nav aria-label="Workspace" className="-mb-px min-w-0 overflow-x-auto">
              <ul className="flex gap-1">
                {TABS.map((tab) => (
                  <Tab
                    key={tab.href}
                    jobId={jobId}
                    href={tab.href}
                    also={tab.also}
                    label={tab.label}
                    disabled={tab.needsManifest && !manifest}
                  />
                ))}
              </ul>
            </nav>
            <ActivityChip
              events={events}
              done={done}
              failed={job?.status === "failed"}
              summary={summary}
              elapsed={job?.elapsed_seconds ?? null}
              onOpen={() => setActivityOpen(true)}
            />
          </div>
        </header>

        {(error || (job?.status === "failed" && (failure || job.error)) || job?.stalled) && (
          <div className="mx-auto w-full max-w-[1400px] px-4 pt-4 sm:px-6">
            <div className="flex flex-col gap-3">
              {error && <ErrorNote>{error}</ErrorNote>}
              {job?.status === "failed" && (failure || job.error) && (
                // The backend sends the headline, the next step and the
                // original error separately on the `failed` event; `job.error`
                // is the same thing flattened, used when the event is not in
                // this browser's history.
                <ErrorNote nextStep={failure?.nextStep} technical={failure?.technical}>
                  {failure?.headline ?? job.error}
                </ErrorNote>
              )}
              {job?.stalled && <Note>Stalled — {job.stall_summary}. Is the worker running?</Note>}
            </div>
          </div>
        )}

        <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-8 px-4 py-6 sm:px-6 sm:py-8 lg:flex-row">
          <main className="min-w-0 flex-1">{children}</main>
          <aside
            ref={setAsideSlot}
            aria-label="Context"
            className="flex w-full shrink-0 flex-col gap-6 empty:hidden lg:w-80"
          />
        </div>
      </div>

      <Drawer
        open={activityOpen}
        onClose={() => setActivityOpen(false)}
        title="Agent activity"
        description="What RepoLab did to understand this repository. Status updates only — never the model's private reasoning."
        width="lg"
        data-testid="activity-drawer"
      >
        <div data-testid="agent-activity" className="flex flex-col gap-5">
          {done && summary && (
            <div className="rounded-lg border border-line bg-surface-subtle p-4 text-sm" data-testid="activity-summary">
              <p className="font-medium text-fg">✓ Repository analyzed</p>
              <ul className="mt-1 text-fg-secondary">
                <li>{summary.evidence} pieces of evidence</li>
                <li>{summary.components} components discovered</li>
                <li>
                  {summary.flows} major flow{summary.flows === 1 ? "" : "s"} identified
                </li>
              </ul>
            </div>
          )}
          <PhaseTracker events={events} done={done} />
          <ActivityFeed events={events} height="h-[28rem]" />
        </div>
      </Drawer>
    </WorkspaceContext.Provider>
  );
}

function Tab({
  jobId,
  href,
  also = [],
  label,
  disabled,
}: {
  jobId: string;
  href: string;
  also?: string[];
  label: string;
  disabled: boolean;
}) {
  const pathname = usePathname();
  const target = `/r/${jobId}${href}`;
  const active =
    href === ""
      ? pathname === target
      : pathname.startsWith(target) || also.some((path) => pathname.startsWith(`/r/${jobId}${path}`));

  if (disabled) {
    return (
      <li>
        <span
          aria-disabled
          title="Available once RepoLab has finished reading the repository"
          className="block cursor-not-allowed border-b-2 border-transparent px-3 py-2.5 text-sm text-fg-muted opacity-60"
        >
          {label}
        </span>
      </li>
    );
  }

  return (
    <li>
      <Link
        href={target}
        aria-current={active ? "page" : undefined}
        data-testid={`tab-${label}`}
        className={`block whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors duration-150 ${
          active
            ? "border-brand font-medium text-fg"
            : "border-transparent text-fg-secondary hover:border-line-strong hover:text-fg"
        }`}
      >
        {label}
      </Link>
    </li>
  );
}

/**
 * The agent's activity, reduced to one line: what it is doing, or what it found.
 * The detail is a click away, in a drawer.
 */
function ActivityChip({
  events,
  done,
  failed,
  summary,
  elapsed,
  onOpen,
}: {
  events: JobEvent[];
  done: boolean;
  failed: boolean;
  summary: { evidence: number; components: number; flows: number } | null;
  elapsed: number | null;
  onOpen: () => void;
}) {
  const phases = phasesFrom(events);
  const current = phases.at(-1);
  const text = done
    ? summary
      ? `Analyzed · ${summary.evidence} evidence · ${summary.components} components`
      : "Analyzed"
    : failed
      ? "Analysis stopped"
      : current
        ? `${current.label} · ${current.index} of ${current.total}`
        : "Starting analysis";
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="activity-chip"
      className="mb-1.5 hidden shrink-0 items-center gap-2 rounded-md px-2.5 py-1.5 text-meta text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg sm:inline-flex"
    >
      <ActivityIcon size={14} className={done ? "text-run-fg" : failed ? "text-danger-fg" : "text-exp-fg"} />
      <span className="max-w-[22rem] truncate">{text}</span>
      {!done && elapsed != null && <span className="font-mono text-xs text-fg-muted">{elapsed.toFixed(0)}s</span>}
      <span className="text-brand-fg">Details</span>
      <ChevronDownIcon size={12} className="-rotate-90" />
    </button>
  );
}

function JobBadge({ status, stream }: { status?: JobState["status"]; stream: StreamStatus }) {
  if (!status) return null;
  // "live" is a claim about the connection, so it is only made while the
  // connection is actually open. A dropped stream says `reconnecting`.
  const look: { state: Status; label: string } =
    status === "running"
      ? {
          state: "running",
          label: stream === "open" ? "Analyzing" : stream === "connecting" ? "Reconnecting" : "Analyzing",
        }
      : status === "completed"
        ? { state: "ready", label: "Ready" }
        : status === "failed"
          ? { state: "failed", label: "Failed" }
          : { state: "unknown", label: "Queued" };
  return (
    <span data-testid="job-status" data-status={status} data-stream={stream}>
      <StatusBadge status={look.state}>{look.label}</StatusBadge>
    </span>
  );
}
