/**
 * The workspace shell and the context every screen reads.
 *
 * This component owns the job's SSE connection and its accumulated events.
 * Next.js layouts preserve state across navigation, so switching tabs never
 * drops the stream or re-replays history — which is why the connection lives
 * here rather than in each screen.
 *
 * Spec: docs/design/00-overview.md
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { AgentActivityPanel } from "@/components/activity";
import { Badge, ErrorNote, Note } from "@/components/ui";
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
   * The tab list is gated on it, so a screen is never offered for a surface
   * RepoLab would refuse to run.
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
   * The top of the right-hand column, where a page can show context of its own
   * (Ask RepoLab's repository context) above the agent activity. `null` until
   * mounted.
   */
  asideSlot: HTMLElement | null;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside the workspace layout");
  return value;
}

/**
 * The tabs, and which of them this repository has.
 *
 * `surface` gates a tab on that surface being **runnable in this build**, not
 * merely detected. The difference matters: Airflow has a strong command-line
 * surface and RepoLab has no way to build Airflow, so offering a Learn tab for
 * it would be the product pretending an unsupported experience works. The
 * catalog decides, using the same checks the real paths use.
 */
const TABS: {
  href: string;
  label: string;
  needsManifest: boolean;
  surface?: string;
}[] = [
  { href: "", label: "Investigation", needsManifest: false },
  { href: "/experiences", label: "Experiences", needsManifest: true },
  { href: "/architecture", label: "Architecture", needsManifest: true },
  { href: "/playground", label: "Playground", needsManifest: true, surface: "data_playground" },
  { href: "/learn", label: "Learn", needsManifest: true, surface: "cli" },
  { href: "/ask", label: "Ask RepoLab", needsManifest: true },
];

export function WorkspaceShell({ jobId, children }: { jobId: string; children: ReactNode }) {
  const [job, setJob] = useState<JobState | null>(null);
  const [manifest, setManifest] = useState<RepoManifest | null>(null);
  const [architecture, setArchitecture] = useState<ArchitectureView | null>(null);
  const [experiences, setExperiences] = useState<ExperienceCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asideSlot, setAsideSlot] = useState<HTMLElement | null>(null);

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

  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className="flex min-h-screen flex-col">
        <header className="border-b border-neutral-200 dark:border-neutral-800">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3">
            <Link href="/" className="text-sm font-semibold tracking-tight">
              RepoLab
            </Link>
            <span aria-hidden className="text-neutral-300 dark:text-neutral-700">
              /
            </span>
            <span className="font-mono text-sm" data-testid="repo-slug">
              {job ? slug(job.repo_url) : "…"}
            </span>
            {manifest && (
              <span className="font-mono text-xs text-neutral-500">
                {shortHash(manifest.commit_sha)}
              </span>
            )}
            <JobBadge status={job?.status} stream={status} />
            <span className="ml-auto font-mono text-xs text-neutral-400">
              {job?.elapsed_seconds != null ? `${job.elapsed_seconds.toFixed(0)}s` : ""}
            </span>
          </div>

          <nav className="mx-auto max-w-[1400px] px-6" aria-label="Workspace">
            <ul className="flex gap-1">
              {TABS.filter(
                // A surface-gated tab appears only once the catalog has said it
                // is runnable. Showing it while that is unknown would offer an
                // experience and then withdraw it, which is the same lie more
                // briefly told.
                (tab) =>
                  tab.surface === undefined ||
                  (experiences?.runnable_surfaces.includes(tab.surface) ?? false),
              ).map((tab) => (
                <Tab
                  key={tab.href}
                  jobId={jobId}
                  href={tab.href}
                  label={tab.label}
                  disabled={tab.needsManifest && !manifest}
                />
              ))}
            </ul>
          </nav>
        </header>

        <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-6 px-6 py-6 lg:flex-row">
          <main className="min-w-0 flex-1">
            {error && <ErrorNote>{error}</ErrorNote>}
            {job?.status === "failed" && (failure || job.error) && (
              <div className="mb-4">
                {/* The backend sends the headline, the next step and the
                    original error separately on the `failed` event; `job.error`
                    is the same thing flattened, used when the event is not in
                    this browser's history. */}
                <ErrorNote nextStep={failure?.nextStep} technical={failure?.technical}>
                  {failure?.headline ?? job.error}
                </ErrorNote>
              </div>
            )}
            {job?.stalled && (
              <div className="mb-4">
                <Note>Stalled — {job.stall_summary}. Is the worker running?</Note>
              </div>
            )}
            {children}
          </main>

          <aside className="flex w-full shrink-0 flex-col gap-6 lg:w-80" aria-label="Context and agent activity">
            <div ref={setAsideSlot} className="empty:hidden flex flex-col gap-6" />
            <AgentActivityPanel
              events={events}
              done={job?.status === "completed"}
              summary={
                architecture
                  ? {
                      evidence: architecture.graph.nodes.reduce((n, c) => n + c.source_references.length, 0),
                      components: architecture.node_count,
                      flows: architecture.graph.primary_flows.length,
                    }
                  : null
              }
            />
          </aside>
        </div>
      </div>
    </WorkspaceContext.Provider>
  );
}

function Tab({
  jobId,
  href,
  label,
  disabled,
}: {
  jobId: string;
  href: string;
  label: string;
  disabled: boolean;
}) {
  const pathname = usePathname();
  const target = `/r/${jobId}${href}`;
  const active = href === "" ? pathname === target : pathname.startsWith(target);

  const classes = `block border-b-2 px-3 py-2 text-sm ${
    active
      ? "border-neutral-900 font-medium dark:border-neutral-100"
      : "border-transparent text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-200"
  }`;

  if (disabled) {
    return (
      <li>
        <span
          aria-disabled
          title="Available once the investigation has produced a manifest"
          className="block cursor-not-allowed border-b-2 border-transparent px-3 py-2 text-sm text-neutral-300 dark:text-neutral-700"
        >
          {label}
        </span>
      </li>
    );
  }

  return (
    <li>
      <Link href={target} className={classes} data-testid={`tab-${label}`}>
        {label}
      </Link>
    </li>
  );
}

function JobBadge({
  status,
  stream,
}: {
  status?: JobState["status"];
  stream: StreamStatus;
}) {
  if (!status) return null;
  // "live" is a claim about the connection, so it is only made while the
  // connection is actually open. A dropped stream says `reconnecting`.
  const live =
    stream === "open" ? "live" : stream === "connecting" ? "reconnecting" : "running";
  return (
    <span data-testid="job-status" data-status={status} data-stream={stream}>
      {status === "running" ? (
        <Badge tone="live">{live}</Badge>
      ) : status === "completed" ? (
        <Badge tone="good">ready</Badge>
      ) : status === "failed" ? (
        <Badge tone="bad">failed</Badge>
      ) : (
        <Badge>queued</Badge>
      )}
    </span>
  );
}
