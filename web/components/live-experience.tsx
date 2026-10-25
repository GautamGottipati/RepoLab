/**
 * Running a repository's web app and API, inside RepoLab.
 *
 * Three states, never blurred: the plan (what RepoLab *would* run, from the
 * repository's own scripts), the launch (each step as it really happens), and
 * LIVE -- shown only once a service answered HTTP through RepoLab's proxy. A
 * failure says what succeeded, what failed and why, with the evidence and the
 * log it came from. Nothing here invents a reason.
 */

"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "./ui";
import { api, ApiError } from "@/lib/api";
import { useEventStream } from "@/lib/events";
import type {
  ExplorerResponse,
  RouteRef,
  RuntimeDependency,
  RuntimeStatus,
  RuntimeView,
  ServicePlan,
  ServiceRuntime,
} from "@/lib/types";

/** The lifecycle, in order, as a reader sees it. */
const PHASES: { status: RuntimeStatus; label: string }[] = [
  { status: "preparing", label: "Preparing environment" },
  { status: "installing", label: "Installing packages" },
  { status: "building", label: "Building application" },
  { status: "starting", label: "Starting server" },
  { status: "health_checking", label: "Waiting for it to answer" },
];

/** Where in the lifecycle each failure happened. */
const FAILED_AT: Partial<Record<RuntimeStatus, RuntimeStatus>> = {
  build_failed: "installing",
  timeout: "installing",
  start_failed: "starting",
  port_not_found: "starting",
  dependency_missing: "preparing",
  unsupported: "preparing",
  health_check_failed: "health_checking",
};

const FAILED = new Set<RuntimeStatus>(Object.keys(FAILED_AT) as RuntimeStatus[]);
const ACTIVE = new Set<RuntimeStatus>(["planning", "preparing", "installing", "building", "starting", "health_checking"]);

const DEPENDENCY_LABEL: Record<RuntimeDependency["state"], string> = {
  provisionable: "RepoLab will start an ephemeral one",
  external_required: "external — not reachable from the sandbox",
  unsupported: "cannot be provided",
};

export function LiveExperience({ jobId, repoUrl, commitSha }: { jobId: string; repoUrl: string; commitSha: string }) {
  const [view, setView] = useState<RuntimeView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await api.runtime(jobId);
        if (!cancelled) setView(next);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  // Progress arrives on the launch job's event stream; each event re-reads the
  // runtime's persisted state, which is the source of truth.
  const { events } = useEventStream(view?.job_id && view.status && ACTIVE.has(view.status) ? api.eventsUrl(view.job_id) : null);
  const seen = events.length;
  useEffect(() => {
    if (seen > 0 && view?.runtime_id) {
      void api.runtimeById(view.runtime_id).then(
        (next) => setView((current) => ({ ...next, plan: next.plan ?? current?.plan ?? null })),
        () => undefined,
      );
    }
  }, [seen, view?.runtime_id]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      setView(await api.startRuntime(jobId));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setStarting(false);
    }
  };

  const stop = async () => {
    if (!view?.runtime_id) return;
    await api.stopRuntime(view.runtime_id).catch(() => undefined);
    setView((current) => (current?.state ? { ...current, status: "stopped", state: { ...current.state, status: "stopped" } } : current));
  };

  const plan = view?.plan;
  if (!plan || plan.services.length === 0) return null;
  const state = view?.state ?? null;
  const launched = state !== null && state.status !== "stopped";
  const lastMessage = [...events].reverse().find((e) => e.message)?.message ?? null;

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800" data-testid="live-experience">
      <header className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold">Run it in RepoLab</h2>
        {state && <StatusBadge status={state.status} />}
        <div className="ml-auto flex gap-2">
          {!launched && (
            <button
              type="button"
              onClick={start}
              disabled={starting}
              data-testid="start-runtime"
              className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
            >
              {starting ? "Starting…" : state?.status === "stopped" ? "Start again" : "Start application"}
            </button>
          )}
          {launched && (
            <button type="button" onClick={stop} data-testid="stop-runtime" className="rounded border border-neutral-300 px-3 py-1.5 text-xs dark:border-neutral-700">
              Stop
            </button>
          )}
        </div>
      </header>
      {!launched && (
        <p className="text-xs text-neutral-500">
          Each service runs in its own container, on a private network with no internet access,
          reachable only through RepoLab. Starting it installs its dependencies, which can take a few minutes.
        </p>
      )}
      {error && <p className="text-sm text-red-700 dark:text-red-300">{error}</p>}
      {launched && lastMessage && state && ACTIVE.has(state.status) && (
        <p className="font-mono text-xs text-neutral-500" data-testid="runtime-latest">{lastMessage}</p>
      )}

      {plan.dependencies.length > 0 && <Dependencies dependencies={plan.dependencies} />}

      <ul className="flex flex-col gap-4">
        {plan.services.map((service) => (
          <ServiceCard
            key={service.name}
            plan={service}
            runtime={launched ? state?.services.find((s) => s.name === service.name) ?? null : null}
            origin={view?.services.find((s) => s.name === service.name)?.origin ?? null}
            runtimeId={view?.runtime_id ?? null}
            repoUrl={repoUrl}
            commitSha={commitSha}
          />
        ))}
      </ul>
    </section>
  );
}

function Dependencies({ dependencies }: { dependencies: RuntimeDependency[] }) {
  return (
    <div data-testid="runtime-dependencies">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Needs</h3>
      <ul className="mt-1 flex flex-col gap-1 text-sm">
        {dependencies.map((dependency) => (
          <li key={dependency.kind} data-state={dependency.state}>
            <span className="font-medium">{dependency.kind === "mongodb" ? "MongoDB" : dependency.kind === "postgres" ? "PostgreSQL" : "Redis"}</span>
            <span className={`ml-2 text-xs ${dependency.state === "provisionable" ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>
              {DEPENDENCY_LABEL[dependency.state]}
            </span>
            {dependency.state !== "provisionable" && <p className="text-xs text-neutral-500">{dependency.reason}.</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ServiceCard({
  plan,
  runtime,
  origin,
  runtimeId,
  repoUrl,
  commitSha,
}: {
  plan: ServicePlan;
  runtime: ServiceRuntime | null;
  origin: string | null;
  runtimeId: string | null;
  repoUrl: string;
  commitSha: string;
}) {
  const isWeb = plan.surfaces.includes("web");
  const isApi = plan.surfaces.includes("api");
  const live = runtime?.status === "running" && origin !== null;

  return (
    <li className="rounded border border-neutral-200 dark:border-neutral-800" data-testid={`service-${plan.name}`} data-status={runtime?.status ?? "planned"}>
      <div className="flex flex-wrap items-center gap-2 border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
        <span aria-hidden>{isWeb ? "🌐" : "🔌"}</span>
        <span className="text-sm font-semibold">{isWeb ? "Web application" : "HTTP API"}</span>
        <span className="font-mono text-xs text-neutral-500">{plan.working_directory || "./"}</span>
        {runtime && <StatusBadge status={runtime.status} />}
        {live && isWeb && (
          <a href={origin} target="_blank" rel="noreferrer" data-testid={`open-${plan.name}`} className="ml-auto rounded bg-neutral-900 px-3 py-1 text-xs font-medium text-white dark:bg-neutral-100 dark:text-neutral-900">
            Open application ↗
          </a>
        )}
      </div>

      <div className="flex flex-col gap-3 px-3 py-3">
        {!runtime && <PlanSummary plan={plan} />}
        {runtime && !live && <Progress plan={plan} runtime={runtime} repoUrl={repoUrl} commitSha={commitSha} />}
        {live && isWeb && <Preview origin={origin} name={plan.name} />}
        {live && isApi && runtimeId && <Explorer runtimeId={runtimeId} service={plan.name} origin={origin} routes={plan.routes} repoUrl={repoUrl} commitSha={commitSha} />}
        {(runtime?.warnings ?? plan.warnings).length > 0 && (
          <Warnings warnings={runtime?.warnings ?? plan.warnings} repoUrl={repoUrl} commitSha={commitSha} />
        )}
      </div>
    </li>
  );
}

function PlanSummary({ plan }: { plan: ServicePlan }) {
  const rows: [string, string, string | null][] = [
    ["Runtime", plan.image, plan.image_reason],
    ["Install", plan.install, null],
    ...(plan.build ? ([["Build", plan.build, null]] as [string, string, string | null][]) : []),
    ["Start", plan.start, `the repository's "${plan.script}" script`],
    ["Port", String(plan.port), plan.port_source],
  ];
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs" data-testid={`plan-${plan.name}`}>
      {rows.map(([label, value, why]) => (
        <div key={label} className="contents">
          <dt className="text-neutral-500">{label}</dt>
          <dd className="min-w-0">
            <code className="break-all font-mono">{value}</code>
            {why && (
              <span className="ml-2 text-neutral-500">
                — {why}
                {plan.inferred_fields.includes(label === "Runtime" ? "image" : label.toLowerCase()) && <em className="ml-1">(inferred)</em>}
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Progress({ plan, runtime, repoUrl, commitSha }: { plan: ServicePlan; runtime: ServiceRuntime; repoUrl: string; commitSha: string }) {
  const phases = PHASES.filter((phase) => phase.status !== "building" || plan.build);
  const failed = FAILED.has(runtime.status);
  const at = failed ? FAILED_AT[runtime.status]! : runtime.status;
  const index = phases.findIndex((phase) => phase.status === at);
  const cached = runtime.steps.some((step) => step.name === "install" && step.status === "skipped");

  return (
    <div className="flex flex-col gap-2" data-testid={`progress-${plan.name}`}>
      <ol className="flex flex-col gap-1 text-sm">
        {phases.map((phase, i) => {
          const mark = i < index || runtime.status === "running" ? "done" : i === index ? (failed ? "failed" : "active") : "pending";
          return (
            <li key={phase.status} data-mark={mark} className={mark === "pending" ? "text-neutral-400" : mark === "failed" ? "text-red-700 dark:text-red-300" : ""}>
              <span aria-hidden className="mr-2 inline-block w-3">{mark === "done" ? "✓" : mark === "active" ? "→" : mark === "failed" ? "✗" : "○"}</span>
              {phase.label}
              {phase.status === "installing" && cached && mark === "done" && <span className="ml-2 text-xs text-neutral-500">reused from an earlier run</span>}
            </li>
          );
        })}
      </ol>
      {failed && runtime.failure && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm dark:border-red-900 dark:bg-red-950/40" data-testid={`failure-${plan.name}`}>
          <p className="font-medium">{plan.surfaces.includes("web") ? "The web application" : "The API"} could not start</p>
          <p className="mt-1">{runtime.failure.reason}</p>
          {runtime.failure.evidence.length > 0 && (
            <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
              Detected from: <Refs refs={runtime.failure.evidence} repoUrl={repoUrl} commitSha={commitSha} />
            </p>
          )}
          {runtime.failure.next_step && <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">Next: {runtime.failure.next_step}</p>}
        </div>
      )}
      {(runtime.failure?.log_tail || runtime.steps.some((s) => s.log_tail)) && (
        <details className="text-xs" data-testid={`logs-${plan.name}`}>
          <summary className="cursor-pointer text-neutral-500">View logs</summary>
          <pre className="mt-1 max-h-72 overflow-auto rounded bg-neutral-100 p-2 font-mono text-[11px] leading-relaxed dark:bg-neutral-900">
            {[...runtime.steps.filter((s) => s.log_tail).map((s) => `$ ${s.command ?? s.name}\n${s.log_tail}`), runtime.failure?.log_tail ?? ""]
              .filter(Boolean)
              .join("\n\n")}
          </pre>
        </details>
      )}
    </div>
  );
}

function Preview({ origin, name }: { origin: string; name: string }) {
  const [key, setKey] = useState(0);
  return (
    <div className="flex flex-col gap-2" data-testid={`preview-${name}`}>
      <div className="flex items-center gap-3 text-xs text-neutral-500">
        <span className="min-w-0 truncate font-mono">{origin}</span>
        <button type="button" onClick={() => setKey((k) => k + 1)} className="ml-auto underline underline-offset-2">Reload</button>
      </div>
      <iframe
        key={key}
        src={origin}
        title={`Live ${name}`}
        sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
        className="h-[32rem] w-full rounded border border-neutral-200 bg-white dark:border-neutral-800"
      />
    </div>
  );
}

function Explorer({
  runtimeId,
  service,
  origin,
  routes,
  repoUrl,
  commitSha,
}: {
  runtimeId: string;
  service: string;
  origin: string;
  routes: RouteRef[];
  repoUrl: string;
  commitSha: string;
}) {
  const [selected, setSelected] = useState<RouteRef | null>(routes.find((r) => r.method === "GET") ?? routes[0] ?? null);
  const [method, setMethod] = useState(selected?.method ?? "GET");
  const [path, setPath] = useState(selected?.path ?? "/");
  const [body, setBody] = useState("");
  const [response, setResponse] = useState<ExplorerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const choose = (route: RouteRef) => {
    setSelected(route);
    setMethod(route.method);
    setPath(route.path);
    setResponse(null);
  };

  const send = async () => {
    setSending(true);
    setError(null);
    try {
      setResponse(await api.runtimeRequest(runtimeId, { service, method, path, body: ["GET", "HEAD", "DELETE"].includes(method) ? null : body || null }));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setSending(false);
    }
  };

  const pretty = useMemo(() => {
    if (!response || response.binary) return response?.body ?? "";
    try {
      return JSON.stringify(JSON.parse(response.body), null, 2);
    } catch {
      return response.body;
    }
  }, [response]);

  return (
    <div className="flex flex-col gap-3" data-testid={`explorer-${service}`}>
      <p className="text-xs text-neutral-500">
        Base URL <code className="font-mono">{origin}</code>
      </p>
      {routes.length > 0 ? (
        <ul className="flex max-h-56 flex-col overflow-y-auto rounded border border-neutral-200 text-xs dark:border-neutral-800" data-testid="endpoints">
          {routes.map((route) => (
            <li key={`${route.method} ${route.path}`}>
              <button
                type="button"
                onClick={() => choose(route)}
                aria-pressed={selected === route}
                className={`flex w-full items-baseline gap-3 px-2 py-1 text-left ${selected === route ? "bg-neutral-100 dark:bg-neutral-900" : ""}`}
              >
                <span className="w-14 shrink-0 font-mono font-semibold">{route.method}</span>
                <span className="min-w-0 flex-1 break-all font-mono">{route.path}</span>
                <span className="shrink-0 text-neutral-400">{route.file.split("/").pop()}:{route.line}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-neutral-500">RepoLab found no route declarations in the source; you can still send a request to any path.</p>
      )}
      {selected && (
        <p className="text-xs text-neutral-500">
          Declared in <Refs refs={[{ path: selected.file, start_line: selected.line, end_line: null, commit_sha: commitSha, excerpt: null }]} repoUrl={repoUrl} commitSha={commitSha} />.
          RepoLab shows the route as the code declares it; it does not know the request body the handler expects.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <select value={method} onChange={(e) => setMethod(e.target.value)} aria-label="Method" className="rounded border border-neutral-300 px-2 py-1 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-900">
          {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
        <input value={path} onChange={(e) => setPath(e.target.value)} aria-label="Path" data-testid="explorer-path" className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-900" />
        <button type="button" onClick={send} disabled={sending || !path.startsWith("/")} data-testid="explorer-send" className="rounded bg-neutral-900 px-3 py-1 text-xs font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900">
          {sending ? "Sending…" : "Send request"}
        </button>
      </div>
      {path.includes("/:") && <p className="text-xs text-amber-700 dark:text-amber-300">Replace the :parameters in the path with real values.</p>}
      {!["GET", "HEAD", "DELETE"].includes(method) && (
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} aria-label="Request body (JSON)" placeholder='{ "title": "…" }' className="rounded border border-neutral-300 px-2 py-1 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-900" />
      )}
      {error && <p className="text-xs text-red-700 dark:text-red-300">{error}</p>}
      {response && (
        <div data-testid="explorer-response">
          <p className="text-xs">
            <span className={`font-mono font-semibold ${response.status_code < 400 ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300"}`}>{response.status_code}</span>
            <span className="ml-2 text-neutral-500">{response.elapsed_ms} ms{response.truncated ? " · truncated" : ""}</span>
          </p>
          <pre className="mt-1 max-h-72 overflow-auto rounded bg-neutral-100 p-2 font-mono text-[11px] dark:bg-neutral-900">{pretty || "(empty body)"}</pre>
        </div>
      )}
    </div>
  );
}

function Warnings({ warnings, repoUrl, commitSha }: { warnings: { message: string; evidence: ServicePlan["evidence"] }[]; repoUrl: string; commitSha: string }) {
  const important = warnings.filter((w) => !w.message.startsWith("To be reachable"));
  const adapted = warnings.filter((w) => w.message.startsWith("To be reachable"));
  return (
    <div className="flex flex-col gap-1 text-xs" data-testid="runtime-warnings">
      {important.map((warning) => (
        <p key={warning.message} className="text-amber-800 dark:text-amber-300">
          ⚠ {warning.message}
          {warning.evidence.length > 0 && (
            <span className="ml-1 text-neutral-500">
              (<Refs refs={warning.evidence} repoUrl={repoUrl} commitSha={commitSha} />)
            </span>
          )}
        </p>
      ))}
      {adapted.map((warning) => (
        <p key={warning.message} className="text-neutral-500">{warning.message}</p>
      ))}
    </div>
  );
}

function Refs({ refs, repoUrl, commitSha }: { refs: ServicePlan["evidence"]; repoUrl: string; commitSha: string }) {
  const match = repoUrl.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/);
  return (
    <>
      {refs.map((ref, i) => {
        const label = `${ref.path}${ref.start_line ? `:${ref.start_line}` : ""}`;
        const href = match && commitSha ? `https://github.com/${match[1]}/${match[2]}/blob/${commitSha}/${ref.path}${ref.start_line ? `#L${ref.start_line}` : ""}` : null;
        return (
          <span key={`${label}-${i}`}>
            {i > 0 && ", "}
            {href ? (
              <a href={href} target="_blank" rel="noreferrer" className="font-mono underline-offset-2 hover:underline">{label}</a>
            ) : (
              <span className="font-mono">{label}</span>
            )}
          </span>
        );
      })}
    </>
  );
}

export function StatusBadge({ status }: { status: RuntimeStatus }) {
  if (status === "running") return <Badge tone="good">● LIVE</Badge>;
  if (status === "stopped") return <Badge>stopped</Badge>;
  if (FAILED.has(status)) return <Badge tone="bad">{status.replace(/_/g, " ")}</Badge>;
  return <Badge tone="live">{status.replace(/_/g, " ")}…</Badge>;
}
