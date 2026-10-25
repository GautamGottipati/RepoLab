/**
 * One experience, in use: prepare it, do something real, see the result, then
 * understand it -- what ran, which parts of the repository were involved, where
 * they live in source -- and turn a question into an experiment.
 *
 * Progressive disclosure, not a separate expert mode: the result first, then the
 * measured runtime, then what happened, then plan, logs and raw measurements.
 * Every number on this screen came from the run (Spark's event log or the
 * process); none is generated.
 */

"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AnswerView } from "./answer-view";
import { Badge } from "./ui";
import { api, ApiError } from "@/lib/api";
import { useEventStream } from "@/lib/events";
import type { ExperienceRun, ExperienceSessionView, Reality, StarterAction } from "@/lib/types";

export const REALITY: Record<Reality, { label: string; hint: string; tone: "good" | "attention" | "neutral" }> = {
  real_repository: { label: "REAL REPOSITORY", hint: "Runs code built from the investigated commit.", tone: "good" },
  official_runtime: { label: "OFFICIAL RUNTIME", hint: "Runs an official release of the project, version disclosed.", tone: "attention" },
  mini_lab: { label: "SIMULATED / EDUCATIONAL", hint: "An educational simulation. It does not run this repository.", tone: "neutral" },
};

export function RealityBadge({ reality }: { reality: Reality }) {
  const info = REALITY[reality];
  return (
    <span title={info.hint} data-testid="reality-badge" data-reality={reality}>
      <Badge tone={info.tone === "neutral" ? undefined : info.tone}>{info.label}</Badge>
    </span>
  );
}

export function ExperienceWorkspace({
  sessionId,
  jobId,
  repoUrl,
  commitSha,
  onClose,
}: {
  sessionId: string;
  jobId: string;
  repoUrl: string;
  commitSha: string;
  onClose?: () => void;
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

  if (error) return <p className="text-sm text-red-700 dark:text-red-300">{error}</p>;
  if (!view) return <p className="text-sm text-neutral-500">Loading the experience…</p>;

  const experience = view.experience;
  const latest = [...events].reverse().find((e) => e.message)?.message ?? null;

  return (
    <section className="flex flex-col gap-5 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800" data-testid="experience-workspace" data-status={view.status}>
      <header className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-semibold">{experience?.title ?? "Experience"}</h2>
        <RealityBadge reality={view.reality} />
        {view.state.runtime.label && <span className="text-xs text-neutral-500">{view.state.runtime.label}</span>}
        {onClose && (
          <button type="button" onClick={onClose} className="ml-auto text-xs underline underline-offset-2">
            Back to experiences
          </button>
        )}
      </header>
      {view.state.runtime.disclosure && (
        <p className="rounded bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" data-testid="runtime-disclosure">
          {view.state.runtime.disclosure}
        </p>
      )}

      {view.status === "preparing" && <Preparing view={view} latest={latest} />}
      {view.status === "failed" && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm dark:border-red-900 dark:bg-red-950/40" data-testid="experience-failed">
          <p className="font-medium">This experience could not be prepared</p>
          <p className="mt-1">{view.state.failure}</p>
          <Steps view={view} />
          {view.state.failure_log && (
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer">View log</summary>
              <pre className="mt-1 max-h-72 overflow-auto rounded bg-white p-2 font-mono text-[11px] dark:bg-neutral-950">{view.state.failure_log}</pre>
            </details>
          )}
        </div>
      )}

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

function Steps({ view }: { view: ExperienceSessionView }) {
  return (
    <ol className="mt-2 flex flex-col gap-1 text-sm" data-testid="preparation-steps">
      {view.state.steps.map((step, i) => (
        <li key={`${step.name}-${i}`} className={step.status === "done" ? "" : "text-neutral-500"}>
          <span aria-hidden className="mr-2 inline-block w-3">{step.status === "done" ? "✓" : "→"}</span>
          {step.detail}
        </li>
      ))}
    </ol>
  );
}

function Preparing({ view, latest }: { view: ExperienceSessionView; latest: string | null }) {
  return (
    <div data-testid="experience-preparing">
      <p className="text-sm font-medium">Preparing {view.experience?.title ?? "the experience"}…</p>
      <Steps view={view} />
      {latest && <p className="mt-2 font-mono text-xs text-neutral-500" data-testid="experience-latest">{latest}</p>}
    </div>
  );
}

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

  const run = async () => {
    setError(null);
    try {
      const created = await api.runExperience(view.id, code, spark ? partitions : undefined, starter?.id);
      onStream(created.events_url);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {view.experience && (
        <dl className="grid gap-2 text-sm sm:grid-cols-3" data-testid="what-you-do">
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-neutral-500">What you&apos;ll do</dt><dd>{view.experience.what_you_do}</dd></div>
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-neutral-500">What you&apos;ll learn</dt><dd>{view.experience.what_you_learn}</dd></div>
          {view.experience.what_to_notice && <div><dt className="text-xs font-semibold uppercase tracking-wide text-neutral-500">What to notice</dt><dd>{view.experience.what_to_notice}</dd></div>}
        </dl>
      )}

      {spark && view.state.dataset && (
        <div data-testid="dataset">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Input data · <span className="font-mono normal-case">{view.state.dataset.name}</span>{" "}
            <span className="font-normal normal-case">({view.state.dataset.rows} rows, {view.state.dataset.generated_by}, sha256 {view.state.dataset.sha256.slice(0, 12)})</span>
          </h3>
          <div className="mt-1 overflow-x-auto">
            <table className="min-w-full text-left font-mono text-xs">
              <thead><tr className="text-neutral-500">{view.state.dataset.columns.map(([name]) => <th key={name} className="pr-4">{name}</th>)}</tr></thead>
              <tbody>{view.state.dataset.preview.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j} className="pr-4">{cell}</td>)}</tr>)}</tbody>
            </table>
          </div>
        </div>
      )}

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
              className={`rounded-full border px-3 py-1 text-xs ${starter?.id === action.id ? "border-neutral-900 dark:border-neutral-100" : "border-neutral-300 dark:border-neutral-700"}`}
            >
              {action.title}
              {action.source === "readme" && action.evidence[0] && (
                <span className="ml-1 text-neutral-500">(README:{action.evidence[0].start_line})</span>
              )}
            </button>
          ))}
        </div>
      )}
      {starter && (
        <p className="text-xs text-neutral-500" data-testid="starter-source">
          {starter.source === "repolab" ? "RepoLab's starter — edit it freely." : starter.source === "readme" ? "From the repository's README." : "The repository's own example."} {starter.description}
        </p>
      )}

      <div>
        <div className="flex items-center gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{spark ? "PySpark" : "Python"}</h3>
          {spark && (
            <label className="ml-auto flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
              Shuffle partitions
              <input
                type="number"
                min={1}
                max={64}
                value={partitions}
                onChange={(e) => setPartitions(Math.max(1, Math.min(64, Number(e.target.value) || 1)))}
                data-testid="shuffle-partitions"
                className="w-16 rounded border border-neutral-300 px-2 py-0.5 font-mono dark:border-neutral-700 dark:bg-neutral-900"
              />
            </label>
          )}
        </div>
        <textarea
          value={code}
          onChange={(e) => setCode(e.target.value)}
          rows={Math.min(16, Math.max(5, code.split("\n").length + 1))}
          spellCheck={false}
          aria-label="Code"
          data-testid="experience-code"
          className="mt-1 w-full rounded border border-neutral-300 bg-neutral-50 p-2 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-900"
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={run}
            disabled={running || !code.trim()}
            data-testid="run-experience"
            className="rounded bg-neutral-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
          >
            {running ? "Running…" : "Run ▶"}
          </button>
          {running && latest && <span className="font-mono text-xs text-neutral-500">{latest}</span>}
          {spark && <span className="text-xs text-neutral-500">{"Your code sees `df` (the table above), `spark` and `F`; assign what you want to see to `result`."}</span>}
        </div>
        {error && <p className="mt-1 text-xs text-red-700 dark:text-red-300">{error}</p>}
      </div>

      {last && last.status !== "running" && (
        spark ? (
          <SparkRunView run={last} view={view} jobId={jobId} repoUrl={repoUrl} commitSha={commitSha} onStream={onStream} />
        ) : (
          <LibraryRunView run={last} />
        )
      )}
    </div>
  );
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
  const m = result?.measurements ?? null;
  const partitions = run.shuffle_partitions ?? result?.config.shuffle_partitions ?? 4;
  const question = useMemo(
    () => `How does spark.sql.shuffle.partitions affect my query? I ran it with ${partitions}.`,
    [partitions],
  );
  if (!result) return null;

  if (!result.ok) {
    return (
      <div className="rounded border border-red-200 bg-red-50 p-3 text-sm dark:border-red-900 dark:bg-red-950/40" data-testid="run-error">
        <p className="font-medium">Spark reported an error</p>
        <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap font-mono text-xs">{result.error}</pre>
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

  return (
    <div className="flex flex-col gap-4" data-testid="run-result">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Result</h3>
        {result.columns.length > 0 ? (
          <div className="mt-1 overflow-x-auto">
            <table className="min-w-full text-left font-mono text-xs" data-testid="result-table">
              <thead><tr className="border-b border-neutral-200 dark:border-neutral-800">{result.columns.map((c) => <th key={c} className="py-1 pr-4">{c}</th>)}</tr></thead>
              <tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className="py-0.5 pr-4">{String(v)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        ) : (
          <p className="mt-1 font-mono text-sm" data-testid="result-value">{String(result.value)}</p>
        )}
      </div>

      {m && (
        <div data-testid="runtime-measurements">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Runtime · measured from Spark&apos;s event log</h3>
          <dl className="mt-1 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
            <Stat label="Jobs" value={result.jobs} />
            <Stat label="Stages" value={m.stage_count} />
            <Stat label="Tasks" value={m.task_count} />
            <Stat label="Shuffle partitions" value={m.distribution.partitions || partitions} hint={m.distribution.partitions ? `${m.distribution.empty_partitions} empty` : "set"} />
            <Stat label="Spark runtime" value={m.total_runtime_ms != null ? `${(m.total_runtime_ms / 1000).toFixed(2)} s` : "—"} />
            <Stat label="Shuffle read" value={`${m.shuffle_bytes_read} B`} />
            <Stat label="Slowest task" value={m.tasks.slowest_ms ? `${m.tasks.slowest_ms} ms` : "—"} />
            <Stat label="Spark version" value={result.spark_version ?? "—"} />
          </dl>
          <p className="mt-1 text-[11px] text-neutral-400">Event log sha256 {result.event_log_sha256?.slice(0, 16)} · single run, not an experiment</p>
        </div>
      )}

      {run.mapping.length > 0 && (
        <div data-testid="what-happened">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">What just happened?</h3>
          {view.reality === "official_runtime" && (
            <p className="text-xs text-neutral-500">The links show the same components in the investigated source; this run used Spark {result.spark_version}.</p>
          )}
          <ol className="mt-2 flex flex-col">
            {run.mapping.map((step, i) => (
              <li key={`${step.title}-${i}`} className="flex flex-col" data-testid="mapped-step">
                {i > 0 && <span aria-hidden className="ml-3 text-neutral-400">↓</span>}
                <div className="rounded border border-neutral-200 px-3 py-1.5 text-sm dark:border-neutral-800">
                  <span className="font-medium">{step.title}</span>
                  <span className="ml-2 text-xs text-neutral-500">{step.run_evidence}</span>
                  {step.architecture_label && (
                    <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs">
                      <Link
                        href={`/r/${jobId}/architecture?${new URLSearchParams({ ...(step.subsystem_id ? { subsystem: step.subsystem_id } : {}), node: step.architecture_id ?? "" }).toString()}`}
                        data-testid="architecture-link"
                        className="underline underline-offset-2"
                      >
                        {step.architecture_label} in Architecture →
                      </Link>
                      {step.source_paths.slice(0, 2).map((path) => (
                        <a key={path} href={treeUrl(repoUrl, commitSha, path)} target="_blank" rel="noreferrer" data-testid="source-link" className="font-mono text-neutral-500 underline-offset-2 hover:underline">
                          📁 {path}
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

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={explain} disabled={explaining} data-testid="explain-run" className="rounded border border-neutral-300 px-3 py-1.5 text-xs dark:border-neutral-700">
          {explaining ? "Explaining…" : "Explain this run"}
        </button>
        <Link
          href={`/r/${jobId}/ask?${new URLSearchParams({ q: question, experience: view.id }).toString()}`}
          data-testid="suggest-experiment"
          className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white"
        >
          Run an experiment: what happens with fewer or more partitions? →
        </Link>
      </div>
      {run.explanation_error && <p className="text-xs text-neutral-500">{run.explanation_error}</p>}
      {run.explanation && (
        <div className="rounded border border-neutral-200 p-3 dark:border-neutral-800" data-testid="run-explanation">
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

      <details className="text-xs" data-testid="run-advanced">
        <summary className="cursor-pointer text-neutral-500">Execution plan, logs and raw measurements</summary>
        {result.plan && <pre className="mt-2 max-h-64 overflow-auto rounded bg-neutral-100 p-2 font-mono text-[11px] dark:bg-neutral-900">{result.plan}</pre>}
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-neutral-100 p-2 font-mono text-[11px] dark:bg-neutral-900">{result.stdout_tail}</pre>
        {m && <pre className="mt-2 max-h-64 overflow-auto rounded bg-neutral-100 p-2 font-mono text-[11px] dark:bg-neutral-900">{JSON.stringify(m, null, 2)}</pre>}
      </details>
    </div>
  );
}

function LibraryRunView({ run }: { run: ExperienceRun }) {
  const result = run.result;
  if (!result) return null;
  return (
    <div data-testid="library-result">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
        Output <span className="font-normal normal-case">· exit {result.exit_code} · {result.wall_clock_seconds.toFixed(2)} s (process time, measured)</span>
      </h3>
      {result.stdout && <pre className="mt-1 max-h-80 overflow-auto rounded bg-neutral-100 p-2 font-mono text-xs dark:bg-neutral-900" data-testid="library-stdout">{result.stdout}</pre>}
      {result.stderr && <pre className={`mt-1 max-h-80 overflow-auto rounded p-2 font-mono text-xs ${result.ok ? "bg-neutral-100 dark:bg-neutral-900" : "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-200"}`} data-testid="library-stderr">{result.stderr}</pre>}
      {!result.stdout && !result.stderr && <p className="mt-1 text-xs text-neutral-500">(no output)</p>}
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
    <div className="flex flex-col gap-3" data-testid="minilab">
      <p className="rounded border-2 border-dashed border-amber-400 bg-amber-50 px-3 py-2 text-xs font-semibold uppercase text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
        Simulated / educational — nothing here runs this repository, and nothing here is a measurement.
      </p>
      <div className="flex flex-wrap gap-2">
        {(view.state.minilab?.entries ?? []).slice(0, 6).map((entry) => (
          <button key={entry.command} type="button" onClick={() => void send(entry.command)} className="rounded-full border border-neutral-300 px-3 py-1 font-mono text-xs dark:border-neutral-700">
            {entry.command}
          </button>
        ))}
      </div>
      <ul className="flex flex-col gap-2 font-mono text-xs" data-testid="minilab-transcript">
        {view.state.transcript.map((line, i) => (
          <li key={i}>
            <p>$ {line.command}</p>
            <p className="text-neutral-500">[{line.label}] {line.output ?? line.explanation}</p>
          </li>
        ))}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(command);
        }}
        className="flex gap-2"
      >
        <input value={command} onChange={(e) => setCommand(e.target.value)} aria-label="Mini Lab command" className="flex-1 rounded border border-neutral-300 px-2 py-1 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-900" />
        <button type="submit" className="rounded border border-neutral-300 px-3 py-1 text-xs dark:border-neutral-700">Send</button>
      </form>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="font-mono">
        {value}
        {hint && <span className="ml-1 text-xs text-neutral-400">{hint}</span>}
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
