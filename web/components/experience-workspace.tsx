/**
 * One experience, in use: prepare it, do something real, see the result, then
 * understand it -- what ran, which parts of the repository were involved, where
 * they live in source -- and turn a question into an experiment.
 *
 * Laid out as a guide (where you are), a workspace (the code) and a result
 * (what came back), so a beginner is led without an expert being held back:
 * every step of the guide can be opened at any time.
 *
 * Every number on this screen came from the run (Spark's event log or the
 * process); none is generated. The guide's ticks come from the session's own
 * state -- a run that finished, an explanation that exists -- never assumed.
 */

"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { AnswerView } from "./answer-view";
import { Diagnostics, useDebug } from "./diagnostics";
import { WorkingStatus } from "./working-status";
import {
  ActionButton,
  ActionLink,
  Badge,
  Button,
  CodeSurface,
  ProgressSteps,
  RunIcon,
  StatusBadge,
  Tabs,
  type ProgressStep,
  type Status,
} from "./ui";
import { api, ApiError } from "@/lib/api";
import { useEventStream } from "@/lib/events";
import type { ExperienceRun, ExperienceSessionView, JobEvent, Reality, StarterAction } from "@/lib/types";

export const REALITY: Record<Reality, { label: string; hint: string; tone: "run" | "exp" | "neutral" }> = {
  real_repository: { label: "REAL REPOSITORY", hint: "Runs code built from the investigated commit.", tone: "run" },
  official_runtime: {
    label: "OFFICIAL RUNTIME",
    hint: "Runs an official release of the project, version disclosed.",
    tone: "neutral",
  },
  mini_lab: {
    label: "SIMULATED / EDUCATIONAL",
    hint: "An educational simulation. It does not run this repository.",
    tone: "exp",
  },
};

export function RealityBadge({ reality }: { reality: Reality }) {
  const info = REALITY[reality];
  return (
    <span title={info.hint} data-testid="reality-badge" data-reality={reality}>
      <Badge tone={info.tone}>{info.label}</Badge>
    </span>
  );
}

const SESSION_STATUS: Record<ExperienceSessionView["status"], { status: Status; label: string }> = {
  preparing: { status: "preparing", label: "Preparing" },
  ready: { status: "live", label: "Live" },
  failed: { status: "failed", label: "Failed" },
  stopped: { status: "unavailable", label: "Stopped" },
};

export function ExperienceWorkspace({
  sessionId,
  jobId,
  repoUrl,
  commitSha,
  onClose,
  onSession,
}: {
  sessionId: string;
  jobId: string;
  repoUrl: string;
  commitSha: string;
  onClose?: () => void;
  /** Switch to another session, e.g. one started again after an interruption. */
  onSession?: (id: string) => void;
}) {
  const [view, setView] = useState<ExperienceSessionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streamPath, setStreamPath] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await api.experienceSession(sessionId);
        if (!cancelled) {
          setView(next);
          if (next.status === "preparing" && next.events_url) setStreamPath(next.events_url);
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Progress arrives on the active job's event stream; each event re-reads the
  // session, which Postgres holds -- so a refresh lands on the same state.
  const { events } = useEventStream(streamPath ? api.eventsPath(streamPath) : null);
  const seen = events.length;
  const finished = events.some((e) => e.type === "failed" || e.type === "step_done");
  useEffect(() => {
    if (seen === 0) return;
    let cancelled = false;
    void api.experienceSession(sessionId).then(
      (next) => {
        if (cancelled) return;
        setView(next);
        const running = next.state.runs.some((r) => r.status === "running");
        if (next.status !== "preparing" && !running && finished) setStreamPath(null);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [seen, sessionId, finished]);

  // While preparing, re-read the session on a slow clock too: a long build can
  // go minutes without an event, and the heartbeat-based health and current
  // step must stay current through it.
  const preparing = view?.status === "preparing";
  useEffect(() => {
    if (!preparing) return;
    const timer = setInterval(() => {
      void api.experienceSession(sessionId).then(setView, () => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, [preparing, sessionId]);

  if (error) return <p className="text-sm text-danger-fg">{error}</p>;
  if (!view) return <p className="text-sm text-fg-secondary">Loading the experience…</p>;

  // The backend retires an interrupted preparation and starts a fresh one.
  const restart = () => {
    void api.startExperience(jobId, view.experience_id, view.strategy).then(
      (next) => (onSession ? onSession(next.id) : setView(next)),
      (cause: unknown) => setError(cause instanceof ApiError ? cause.message : String(cause)),
    );
  };

  const experience = view.experience;
  const latest = [...events].reverse().find((e) => e.message)?.message ?? null;
  const status = SESSION_STATUS[view.status];

  return (
    <section className="flex flex-col gap-6" data-testid="experience-workspace" data-status={view.status}>
      <header className="flex flex-col gap-3">
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="self-start text-meta text-fg-secondary hover:text-fg"
          >
            ← All experiences
          </button>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-page font-semibold tracking-tight text-fg">{experience?.title ?? "Experience"}</h1>
          <StatusBadge status={status.status}>{status.label}</StatusBadge>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-meta text-fg-secondary">
          <RealityBadge reality={view.reality} />
          {view.state.runtime.label && <span>{view.state.runtime.label}</span>}
        </div>
        {view.state.runtime.disclosure && (
          <p
            className="rounded-lg border border-line bg-surface-subtle px-3 py-2 text-sm text-fg-secondary"
            data-testid="runtime-disclosure"
          >
            {view.state.runtime.disclosure}
          </p>
        )}
      </header>

      {view.status === "preparing" && (
        <Preparing view={view} latest={latest} events={events} onRestart={restart} />
      )}
      {view.status === "failed" && <PreparationFailed view={view} jobId={jobId} />}

      {view.status === "ready" && view.adapter === "minilab" && <MiniLabConsole view={view} onUpdate={setView} />}
      {view.status === "ready" && (view.adapter === "spark" || view.adapter === "library") && (
        <CodeExperience
          view={view}
          jobId={jobId}
          repoUrl={repoUrl}
          commitSha={commitSha}
          starters={experience?.starter_actions ?? []}
          onStream={setStreamPath}
          latest={streamPath ? latest : null}
        />
      )}
    </section>
  );
}

function stepsOf(view: ExperienceSessionView): ProgressStep[] {
  return view.state.steps.map((step, index) => ({
    key: `${step.name}-${index}`,
    label: step.detail,
    state:
      step.status === "done"
        ? "done"
        : step.status === "failed" || view.status === "failed"
          ? "failed"
          : "current",
  }));
}

function Preparing({
  view,
  latest,
  events,
  onRestart,
}: {
  view: ExperienceSessionView;
  latest: string | null;
  events: JobEvent[];
  onRestart: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-5" data-testid="experience-preparing">
      <div>
        <p className="text-card font-semibold text-fg">Preparing {view.experience?.title ?? "the experience"}</p>
        <p className="mt-1 text-sm text-fg-secondary">
          RepoLab is setting up an isolated environment. Each step below is reported as it happens.
        </p>
      </div>
      <WorkingStatus
        activity={view.activity?.key}
        stage={view.activity?.stage ?? "experience"}
        startedAt={view.activity?.started_at}
        health={view.health}
        onRetry={onRestart}
      />
      <ProgressSteps steps={stepsOf(view)} label="Preparation" data-testid="preparation-steps" />
      <Diagnostics events={events} health={view.health} activity={view.activity} />
      {latest && <DebugLine>{latest}</DebugLine>}
    </div>
  );
}

/** The raw last log line: for developers, behind the same switch as Diagnostics. */
function DebugLine({ children }: { children: string }) {
  const debug = useDebug();
  if (!debug) return null;
  return (
    <p className="truncate border-t border-line pt-3 font-mono text-xs text-fg-muted" data-testid="experience-latest">
      {children}
    </p>
  );
}

function PreparationFailed({ view, jobId }: { view: ExperienceSessionView; jobId: string }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-4 rounded-lg border border-danger-line bg-surface p-5"
      data-testid="experience-failed"
    >
      <div>
        <p className="text-card font-semibold text-danger-fg">This experience could not be prepared</p>
        <p className="mt-1 text-sm text-fg">{view.state.failure}</p>
      </div>
      {view.state.steps.length > 0 && (
        <ProgressSteps steps={stepsOf(view)} label="Preparation" data-testid="preparation-steps" />
      )}
      {view.state.failure_log && (
        <details>
          <summary className="cursor-pointer text-sm text-fg-secondary hover:text-fg">View log</summary>
          <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-code p-3 font-mono text-xs text-code-fg">
            {view.state.failure_log}
          </pre>
        </details>
      )}
      <div className="flex flex-wrap gap-2">
        <ActionLink
          kind="ask"
          href={`/r/${jobId}/ask?q=${encodeURIComponent(`Why might this fail: ${view.state.failure ?? "the build"}`)}`}
        >
          Ask RepoLab about this error
        </ActionLink>
        <ActionLink kind="arch" href={`/r/${jobId}/architecture`}>
          Explore architecture instead
        </ActionLink>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The guide
// ---------------------------------------------------------------------------

const GUIDE: { key: string; label: string; anchor: string }[] = [
  { key: "intro", label: "Introduction", anchor: "exp-intro" },
  { key: "run", label: "Run the example", anchor: "exp-run" },
  { key: "understand", label: "Understand it", anchor: "exp-understand" },
  { key: "experiment", label: "Experiment", anchor: "exp-experiment" },
  { key: "source", label: "Explore the source", anchor: "exp-source" },
  { key: "next", label: "Next steps", anchor: "exp-next" },
];

/** The guide's state, from what the session really holds. */
export function guideSteps(view: ExperienceSessionView, spark: boolean): ProgressStep[] {
  const last = view.state.runs.at(-1) ?? null;
  const ran = view.state.runs.some((run) => run.status === "done" && run.result?.ok);
  const explained = view.state.runs.some((run) => run.explanation !== null);
  const done: Record<string, boolean> = {
    intro: view.status === "ready",
    run: ran,
    understand: explained,
    experiment: false,
    source: false,
    next: false,
  };
  const applicable = GUIDE.filter((step) => spark || ["intro", "run", "next"].includes(step.key));
  const current = applicable.find((step) => !done[step.key])?.key;
  return applicable.map((step) => ({
    key: step.key,
    label: step.label,
    state: done[step.key] ? "done" : step.key === current ? "current" : "upcoming",
    detail: step.key === "run" && last?.status === "running" ? "Running…" : undefined,
  }));
}

function Guide({ steps }: { steps: ProgressStep[] }) {
  const go = (key: string) => {
    const anchor = GUIDE.find((step) => step.key === key)?.anchor;
    if (anchor) document.getElementById(anchor)?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  const doneCount = steps.filter((step) => step.state === "done").length;
  return (
    <nav aria-label="Experience guide" data-testid="experience-guide">
      {/* A collapsible stepper on small screens; a rail beside the work on large. */}
      <details className="rounded-lg border border-line bg-surface lg:hidden">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-fg">
          Guide · {doneCount} of {steps.length} done
        </summary>
        <div className="px-2 pb-2">
          <ProgressSteps steps={steps} label="Guide" onSelect={go} />
        </div>
      </details>
      <div className="hidden lg:sticky lg:top-36 lg:block">
        <p className="mb-2 px-2 text-xs font-semibold uppercase tracking-wider text-fg-muted">Guide</p>
        <ProgressSteps steps={steps} label="Guide" onSelect={go} />
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Code experiences: Spark and library
// ---------------------------------------------------------------------------

function CodeExperience({
  view,
  jobId,
  repoUrl,
  commitSha,
  starters,
  onStream,
  latest,
}: {
  view: ExperienceSessionView;
  jobId: string;
  repoUrl: string;
  commitSha: string;
  starters: StarterAction[];
  onStream: (path: string) => void;
  latest: string | null;
}) {
  const runnable = starters.filter((s) => s.code);
  const last = view.state.runs.at(-1) ?? null;
  const [starter, setStarter] = useState<StarterAction | null>(runnable[0] ?? null);
  const [code, setCode] = useState(last?.code ?? runnable[0]?.code ?? "");
  const [partitions, setPartitions] = useState(last?.shuffle_partitions ?? 4);
  const [error, setError] = useState<string | null>(null);
  const spark = view.adapter === "spark";
  const running = view.state.runs.some((r) => r.status === "running");
  const steps = guideSteps(view, spark);

  const run = async () => {
    setError(null);
    try {
      const created = await api.runExperience(view.id, code, spark ? partitions : undefined, starter?.id);
      onStream(created.events_url);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    }
  };

  const shown = last && last.status !== "running" ? last : null;

  return (
    <div className="grid gap-6 lg:grid-cols-[12rem_minmax(0,1fr)] xl:grid-cols-[12rem_minmax(0,1fr)_minmax(0,1fr)]">
      <Guide steps={steps} />

      <div className="flex min-w-0 flex-col gap-6">
        {view.experience && (
          <div id="exp-intro" className="scroll-mt-36">
            <dl className="grid gap-4 text-sm sm:grid-cols-3 xl:grid-cols-1 2xl:grid-cols-3" data-testid="what-you-do">
              <Detail label="What you'll do">{view.experience.what_you_do}</Detail>
              <Detail label="What you'll learn">{view.experience.what_you_learn}</Detail>
              {view.experience.what_to_notice && <Detail label="What to notice">{view.experience.what_to_notice}</Detail>}
            </dl>
          </div>
        )}

        {spark && view.state.dataset && (
          <div data-testid="dataset" className="min-w-0">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
              Input data · <span className="font-mono normal-case">{view.state.dataset.name}</span>{" "}
              <span className="font-normal normal-case tracking-normal">
                ({view.state.dataset.rows} rows, {view.state.dataset.generated_by}, sha256{" "}
                {view.state.dataset.sha256.slice(0, 12)})
              </span>
            </h3>
            <div className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface">
              <table className="min-w-full text-left font-mono text-xs">
                <thead>
                  <tr className="border-b border-line text-fg-muted">
                    {view.state.dataset.columns.map(([name]) => (
                      <th key={name} className="px-3 py-2 font-medium">
                        {name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-fg">
                  {view.state.dataset.preview.map((row, i) => (
                    <tr key={i} className="border-b border-line last:border-0">
                      {row.map((cell, j) => (
                        <td key={j} className="px-3 py-1.5">
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div id="exp-run" className="flex scroll-mt-36 flex-col gap-3">
          {runnable.length > 1 && (
            <div className="flex flex-wrap gap-2" data-testid="starters">
              {runnable.map((action) => (
                <button
                  key={action.id}
                  type="button"
                  aria-pressed={starter?.id === action.id}
                  onClick={() => {
                    setStarter(action);
                    setCode(action.code ?? "");
                  }}
                  className={`h-8 rounded-full border px-3 text-meta transition-colors ${
                    starter?.id === action.id
                      ? "border-brand bg-brand-soft text-brand-fg"
                      : "border-line bg-surface text-fg-secondary hover:text-fg"
                  }`}
                >
                  {action.title}
                  {action.source === "readme" && action.evidence[0] && (
                    <span className="ml-1 text-fg-muted">(README:{action.evidence[0].start_line})</span>
                  )}
                </button>
              ))}
            </div>
          )}
          {starter && (
            <p className="text-sm text-fg-secondary" data-testid="starter-source">
              {starter.source === "repolab"
                ? "RepoLab's starter — edit it freely."
                : starter.source === "readme"
                  ? "From the repository's README."
                  : "The repository's own example."}{" "}
              {starter.description}
            </p>
          )}

          <CodeSurface
            label={spark ? "PySpark (Python)" : "Python"}
            actions={
              spark ? (
                <label className="flex items-center gap-2 text-xs text-code-muted">
                  Shuffle partitions
                  <input
                    type="number"
                    min={1}
                    max={64}
                    value={partitions}
                    onChange={(e) => setPartitions(Math.max(1, Math.min(64, Number(e.target.value) || 1)))}
                    data-testid="shuffle-partitions"
                    className="h-7 w-16 rounded-md border border-code-line bg-code-raised px-2 font-mono text-code-fg"
                  />
                </label>
              ) : null
            }
          >
            <textarea
              value={code}
              onChange={(e) => setCode(e.target.value)}
              rows={Math.min(18, Math.max(6, code.split("\n").length + 1))}
              spellCheck={false}
              aria-label="Code"
              data-testid="experience-code"
              className="block w-full resize-y bg-transparent p-3 font-mono text-meta leading-6 text-code-fg outline-none placeholder:text-code-muted"
            />
            <div className="flex flex-wrap items-center gap-3 border-t border-code-line px-3 py-2">
              {spark && (
                <span className="text-xs text-code-muted">
                  Your code sees <code>df</code> (the table above), <code>spark</code> and <code>F</code>; assign what
                  you want to see to <code>result</code>.
                </span>
              )}
              <Button
                variant="primary"
                tone="run"
                size="sm"
                onClick={run}
                disabled={running || !code.trim()}
                data-testid="run-experience"
                className="ml-auto"
                icon={<RunIcon size={12} />}
              >
                {running ? "Running…" : "Run"}
              </Button>
            </div>
          </CodeSurface>
          {running && latest && <p className="font-mono text-xs text-fg-muted">{latest}</p>}
          {error && <p className="text-sm text-danger-fg">{error}</p>}
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-6 lg:col-start-2 xl:col-start-auto">
        {shown ? (
          spark ? (
            <SparkRunView run={shown} view={view} jobId={jobId} repoUrl={repoUrl} commitSha={commitSha} onStream={onStream} />
          ) : (
            <LibraryRunView run={shown} />
          )
        ) : (
          <div className="rounded-lg border border-dashed border-line-strong px-5 py-10 text-center">
            <p className="text-sm font-medium text-fg">{running ? "Running…" : "No result yet"}</p>
            <p className="mx-auto mt-1 max-w-xs text-sm text-fg-secondary">
              {running
                ? "The result, and what really happened when it ran, will appear here."
                : "Run the code to see its output here, with what really happened when it ran."}
            </p>
          </div>
        )}
        <NextSteps jobId={jobId} />
      </div>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wider text-fg-muted">{label}</dt>
      <dd className="mt-1 text-fg">{children}</dd>
    </div>
  );
}

function NextSteps({ jobId }: { jobId: string }) {
  return (
    <div id="exp-next" className="scroll-mt-36 border-t border-line pt-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">Next steps</h3>
      <div className="mt-3 flex flex-wrap gap-2">
        <ActionLink kind="run" href={`/r/${jobId}/experiences`}>
          Other experiences
        </ActionLink>
        <ActionLink kind="arch" href={`/r/${jobId}/architecture`}>
          Explore architecture
        </ActionLink>
        <ActionLink kind="ask" href={`/r/${jobId}/ask`}>
          Ask RepoLab
        </ActionLink>
      </div>
    </div>
  );
}

function architectureHref(jobId: string, step: { subsystem_id: string | null; architecture_id: string | null }): string {
  const params = new URLSearchParams({
    ...(step.subsystem_id ? { subsystem: step.subsystem_id } : {}),
    node: step.architecture_id ?? "",
  });
  return `/r/${jobId}/architecture?${params.toString()}`;
}

function SparkRunView({
  run,
  view,
  jobId,
  repoUrl,
  commitSha,
  onStream,
}: {
  run: ExperienceRun;
  view: ExperienceSessionView;
  jobId: string;
  repoUrl: string;
  commitSha: string;
  onStream: (path: string) => void;
}) {
  const result = run.result;
  const [explaining, setExplaining] = useState(false);
  const [tab, setTab] = useState("table");
  const m = result?.measurements ?? null;
  const partitions = run.shuffle_partitions ?? result?.config.shuffle_partitions ?? 4;
  const question = useMemo(
    () => `How does spark.sql.shuffle.partitions affect my query? I ran it with ${partitions}.`,
    [partitions],
  );
  if (!result) return null;

  if (!result.ok) {
    return (
      <div role="alert" className="rounded-lg border border-danger-line bg-danger-soft p-4 text-sm" data-testid="run-error">
        <p className="font-semibold text-danger-fg">Spark reported an error</p>
        <p className="mt-1 text-fg-secondary">Your code reached Spark, which rejected it. Its own message:</p>
        <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-code p-3 font-mono text-xs text-code-fg">
          {result.error}
        </pre>
      </div>
    );
  }

  const explain = async () => {
    setExplaining(true);
    try {
      const created = await api.explainExperienceRun(view.id, run.id);
      onStream(created.events_url);
    } finally {
      setExplaining(false);
    }
  };

  const mapped = run.mapping.find((step) => step.architecture_id) ?? null;
  const firstSource = run.mapping.flatMap((step) => step.source_paths)[0] ?? null;

  return (
    <div className="flex flex-col gap-6" data-testid="run-result">
      <div data-testid="run-advanced">
        <Tabs
          label="Result"
          value={tab}
          onChange={setTab}
          size="sm"
          items={[
            { id: "table", label: "Result" },
            { id: "plan", label: "Plan", disabled: !result.plan },
            { id: "logs", label: "Logs" },
            { id: "raw", label: "Raw" },
          ]}
        >
          {tab === "table" &&
            (result.columns.length > 0 ? (
              <div className="overflow-x-auto rounded-lg border border-line bg-surface">
                <table className="min-w-full text-left font-mono text-xs" data-testid="result-table">
                  <thead>
                    <tr className="border-b border-line text-fg-muted">
                      {result.columns.map((c) => (
                        <th key={c} className="px-3 py-2 font-medium">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="text-fg">
                    {result.rows.map((row, i) => (
                      <tr key={i} className="border-b border-line last:border-0">
                        {row.map((v, j) => (
                          <td key={j} className="px-3 py-1.5">
                            {String(v)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="font-mono text-sm text-fg" data-testid="result-value">
                {String(result.value)}
              </p>
            ))}
          {tab === "plan" && result.plan && <Pre>{result.plan}</Pre>}
          {tab === "logs" && <Pre>{result.stdout_tail || "(no output)"}</Pre>}
          {tab === "raw" && <Pre>{m ? JSON.stringify(m, null, 2) : "(no measurements were recorded)"}</Pre>}
        </Tabs>
      </div>

      {m && (
        <div data-testid="runtime-measurements">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
            Execution details · measured from Spark&apos;s event log
          </h3>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
            <Measure label="Jobs" value={result.jobs} />
            <Measure label="Stages" value={m.stage_count} />
            <Measure label="Tasks" value={m.task_count} />
            <Measure
              label="Shuffle partitions"
              value={m.distribution.partitions || partitions}
              hint={m.distribution.partitions ? `${m.distribution.empty_partitions} empty` : "set"}
            />
            <Measure label="Spark runtime" value={m.total_runtime_ms != null ? `${(m.total_runtime_ms / 1000).toFixed(2)} s` : "—"} />
            <Measure label="Shuffle read" value={`${m.shuffle_bytes_read} B`} />
            <Measure label="Slowest task" value={m.tasks.slowest_ms ? `${m.tasks.slowest_ms} ms` : "—"} />
            <Measure label="Spark version" value={result.spark_version ?? "—"} />
          </dl>
          <p className="mt-2 text-xs text-fg-muted">
            Event log sha256 {result.event_log_sha256?.slice(0, 16)} · single run, not an experiment
          </p>
        </div>
      )}

      {run.mapping.length > 0 && (
        <div id="exp-understand" className="scroll-mt-36" data-testid="what-happened">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">What just happened?</h3>
          {view.reality === "official_runtime" && (
            <p className="mt-1 text-sm text-fg-secondary">
              The links show the same components in the investigated source; this run used Spark {result.spark_version}.
            </p>
          )}
          <ol id="exp-source" className="mt-3 flex scroll-mt-36 flex-col">
            {run.mapping.map((step, i) => (
              <li key={`${step.title}-${i}`} className="flex flex-col" data-testid="mapped-step">
                {i > 0 && <span aria-hidden className="ml-4 h-3 w-px bg-line-strong" />}
                <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm">
                  <span className="font-medium text-fg">{step.title}</span>
                  <span className="ml-2 text-xs text-fg-secondary">{step.run_evidence}</span>
                  {step.architecture_label && (
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                      <Link
                        href={architectureHref(jobId, step)}
                        data-testid="architecture-link"
                        className="font-medium text-arch-fg hover:underline"
                      >
                        {step.architecture_label} in Architecture →
                      </Link>
                      {step.source_paths.slice(0, 2).map((path) => (
                        <a
                          key={path}
                          href={treeUrl(repoUrl, commitSha, path)}
                          target="_blank"
                          rel="noreferrer"
                          data-testid="source-link"
                          className="font-mono text-fg-secondary hover:text-fg hover:underline"
                        >
                          {path}/
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div id="exp-experiment" className="flex scroll-mt-36 flex-wrap items-center gap-2">
        <ActionButton kind="ask" onClick={explain} disabled={explaining} data-testid="explain-run">
          {explaining ? "Explaining…" : "Explain this run"}
        </ActionButton>
        {mapped && (
          <ActionLink kind="arch" href={architectureHref(jobId, mapped)}>
            Show architecture
          </ActionLink>
        )}
        <ActionLink
          kind="experiment"
          href={`/r/${jobId}/ask?${new URLSearchParams({ q: question, experience: view.id }).toString()}`}
          data-testid="suggest-experiment"
        >
          Experiment: fewer or more partitions?
        </ActionLink>
        {firstSource && (
          <ActionLink kind="source" external href={treeUrl(repoUrl, commitSha, firstSource)}>
            View source
          </ActionLink>
        )}
      </div>
      {run.explanation_error && <p className="text-sm text-fg-secondary">{run.explanation_error}</p>}
      {run.explanation && (
        <div className="rounded-lg border border-ai-line bg-surface p-4" data-testid="run-explanation">
          <AnswerView
            result={{
              question: "Explain this run",
              kind: "repository",
              shape: "execution",
              answer: run.explanation,
              evidence: { code_references: [], measurements: [], logs: [], documentation: [], simulated: false },
              findings: [],
              downgraded: null,
              error: null,
            }}
            repoUrl={repoUrl}
            commitSha={commitSha}
          />
        </div>
      )}
    </div>
  );
}

function Pre({ children }: { children: ReactNode }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-lg bg-code p-3 font-mono text-xs leading-5 text-code-fg">{children}</pre>
  );
}

function LibraryRunView({ run }: { run: ExperienceRun }) {
  const result = run.result;
  if (!result) return null;
  return (
    <div data-testid="library-result" className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
        Output{" "}
        <span className="font-normal normal-case tracking-normal">
          · exit {result.exit_code} · {result.wall_clock_seconds.toFixed(2)} s (process time, measured)
        </span>
      </h3>
      {result.stdout && (
        <pre className="max-h-80 overflow-auto rounded-lg bg-code p-3 font-mono text-xs text-code-fg" data-testid="library-stdout">
          {result.stdout}
        </pre>
      )}
      {result.stderr && (
        <pre
          className={`max-h-80 overflow-auto rounded-lg p-3 font-mono text-xs ${
            result.ok ? "bg-code text-code-muted" : "border border-danger-line bg-danger-soft text-danger-fg"
          }`}
          data-testid="library-stderr"
        >
          {result.stderr}
        </pre>
      )}
      {!result.stdout && !result.stderr && <p className="text-sm text-fg-secondary">(no output)</p>}
    </div>
  );
}

function MiniLabConsole({ view, onUpdate }: { view: ExperienceSessionView; onUpdate: (v: ExperienceSessionView) => void }) {
  const [command, setCommand] = useState("");
  const send = async (text: string) => {
    if (!text.trim()) return;
    const entry = await api.miniLabCommand(view.id, text);
    onUpdate({ ...view, state: { ...view.state, transcript: [...view.state.transcript, entry] } });
    setCommand("");
  };
  return (
    <div className="flex flex-col gap-4" data-testid="minilab">
      <p className="rounded-lg border-2 border-dashed border-exp-line bg-exp-soft px-4 py-3 text-sm font-semibold text-exp-fg">
        SIMULATED / EDUCATIONAL — nothing here runs this repository, and nothing here is a measurement.
      </p>
      <div className="flex flex-wrap gap-2">
        {(view.state.minilab?.entries ?? []).slice(0, 6).map((entry) => (
          <button
            key={entry.command}
            type="button"
            onClick={() => void send(entry.command)}
            className="h-8 rounded-full border border-line bg-surface px-3 font-mono text-xs text-fg-secondary hover:border-line-strong hover:text-fg"
          >
            {entry.command}
          </button>
        ))}
      </div>
      <CodeSurface label="Mini Lab console · simulated">
        <ul className="flex max-h-96 flex-col gap-3 overflow-auto p-3 font-mono text-xs" data-testid="minilab-transcript">
          {view.state.transcript.length === 0 && (
            <li className="text-code-muted">Pick a command above, or type one below.</li>
          )}
          {view.state.transcript.map((line, i) => (
            <li key={i}>
              <p className="text-code-fg">$ {line.command}</p>
              <p className="mt-0.5 text-code-muted">
                [{line.label}] {line.output ?? line.explanation}
              </p>
            </li>
          ))}
        </ul>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(command);
          }}
          className="flex gap-2 border-t border-code-line p-2"
        >
          <input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            aria-label="Mini Lab command"
            placeholder="Type a command"
            className="h-8 flex-1 rounded-md border border-code-line bg-code-raised px-2 font-mono text-xs text-code-fg placeholder:text-code-muted"
          />
          <Button type="submit" size="sm" tone="exp">
            Send
          </Button>
        </form>
      </CodeSurface>
    </div>
  );
}

function Measure({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-fg">
        {value}
        {hint && <span className="ml-1 text-xs font-normal text-fg-muted">{hint}</span>}
      </dd>
    </div>
  );
}

function treeUrl(repoUrl: string, commit: string, path: string): string {
  const match = repoUrl.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/);
  if (!match) return "#";
  const kind = /\.[A-Za-z0-9]+$/.test(path) ? "blob" : "tree";
  return `https://github.com/${match[1]}/${match[2]}/${kind}/${commit}/${path}`;
}
