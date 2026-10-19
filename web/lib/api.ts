/**
 * The RepoLab API client.
 *
 * The only place `NEXT_PUBLIC_API_URL` appears, and the only place a URL is
 * built, so a screen never assembles one by hand. Every call returns typed data
 * or throws `ApiError`, which carries the backend's own message — a 400 from URL
 * validation or a 409 from the approval gate is a real answer, and showing it
 * verbatim beats inventing a friendlier one.
 */

import type {
  ApprovalResult,
  ArchitectureView,
  Dashboard,
  DatasetPreview,
  EnvironmentResolving,
  EnvironmentView,
  ExperienceCatalog,
  ExperimentCreated,
  ExperimentState,
  ExplanationView,
  JobCreated,
  JobState,
  LessonGenerating,
  LessonProgress,
  LessonView,
  RepoManifest,
  RunStarted,
  RunView,
  SessionView,
  StepResult,
  WhatHappenedView,
} from "./types";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** A non-2xx response, carrying what the backend said about it. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when the resource simply is not ready yet, which is routine here. */
  get notReady(): boolean {
    return this.status === 404;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (cause) {
    // A network failure here almost always means the API is not running, which
    // is worth saying plainly rather than surfacing "Failed to fetch".
    throw new ApiError(
      0,
      `cannot reach the RepoLab API at ${API} — is it running? (${
        cause instanceof Error ? cause.message : "network error"
      })`,
    );
  }

  if (!response.ok) {
    throw new ApiError(response.status, await detail(response));
  }
  // 204 has no body to parse; callers of such endpoints ask for `void`.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** FastAPI puts the useful message in `detail`; fall back to the raw body. */
async function detail(response: Response): Promise<string> {
  const body = await response.text();
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const value = (parsed as { detail: unknown }).detail;
      if (typeof value === "string") return value;
      return JSON.stringify(value);
    }
  } catch {
    // Not JSON; the body itself is the best available message.
  }
  return body || `${response.status} ${response.statusText}`;
}

const post = <T>(path: string, body: unknown): Promise<T> =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

/** `null` instead of throwing, for resources that are legitimately not ready yet. */
export async function optional<T>(call: Promise<T>): Promise<T | null> {
  try {
    return await call;
  } catch (cause) {
    if (cause instanceof ApiError && cause.notReady) return null;
    throw cause;
  }
}

export const api = {
  // -- jobs -----------------------------------------------------------------
  submitRepository: (repoUrl: string) => post<JobCreated>("/jobs", { repo_url: repoUrl }),
  job: (jobId: string) => request<JobState>(`/jobs/${jobId}`),
  manifest: (jobId: string) => request<RepoManifest>(`/jobs/${jobId}/manifest`),
  architecture: (jobId: string) => request<ArchitectureView>(`/jobs/${jobId}/architecture`),

  /**
   * Every interaction surface this repository offers, and which of them this
   * build can actually open. The tab list is gated on `runnable_surfaces`.
   */
  experiences: (jobId: string) => request<ExperienceCatalog>(`/jobs/${jobId}/experiences`),

  /** The SSE URL for a job. `EventSource` is constructed by the caller. */
  eventsUrl: (jobId: string) => `${API}/jobs/${jobId}/events`,

  // -- the KPI dashboard ----------------------------------------------------
  metrics: () => request<Dashboard>("/metrics"),

  // -- the playground -------------------------------------------------------
  datasetPreview: (rows?: number) =>
    request<DatasetPreview>(`/runs/dataset${rows ? `?rows=${rows}` : ""}`),
  startRun: (repoUrl: string, shufflePartitions: number, rows?: number) =>
    post<RunStarted>("/runs", {
      repo_url: repoUrl,
      shuffle_partitions: shufflePartitions,
      ...(rows ? { rows } : {}),
    }),
  runForJob: (jobId: string) => request<RunView>(`/jobs/${jobId}/run`),
  run: (runId: string) => request<RunView>(`/runs/${runId}`),
  runs: (repoUrl: string) => request<RunView[]>(`/runs?repo_url=${encodeURIComponent(repoUrl)}`),

  // -- experiments ----------------------------------------------------------
  ask: (repoUrl: string, question: string, rows?: number) =>
    post<ExperimentCreated>("/experiments", {
      repo_url: repoUrl,
      question,
      ...(rows ? { rows } : {}),
    }),
  experiment: (id: string) => request<ExperimentState>(`/experiments/${id}`),
  experiments: (repoUrl: string) =>
    request<ExperimentState[]>(`/experiments?repo_url=${encodeURIComponent(repoUrl)}`),
  approve: (id: string, approvedBy?: string) =>
    post<ApprovalResult>(`/experiments/${id}/approve`, { approved_by: approvedBy ?? null }),
  reject: (id: string, approvedBy?: string) =>
    post<ApprovalResult>(`/experiments/${id}/reject`, { approved_by: approvedBy ?? null }),
  explanation: (id: string) => request<ExplanationView>(`/experiments/${id}/explanation`),
  experimentEventsUrl: (id: string) => `${API}/experiments/${id}/events`,

  // -- the CLI experience ---------------------------------------------------
  environment: (repoUrl: string) =>
    request<EnvironmentView>(`/cli/environment?repo_url=${encodeURIComponent(repoUrl)}`),

  /**
   * Build an environment through the whole fidelity ladder.
   *
   * Asked for explicitly: it compiles source, pulls a release and starts a 4 GB
   * node, and starting expensive services needs the learner's say-so.
   */
  resolveEnvironment: (repoUrl: string, slowSourceSeconds?: number) =>
    post<EnvironmentResolving>("/cli/environment", {
      repo_url: repoUrl,
      ...(slowSourceSeconds ? { slow_source_seconds: slowSourceSeconds } : {}),
    }),
  startSession: (repoUrl: string, jobId?: string) =>
    post<SessionView>("/cli/sessions", { repo_url: repoUrl, job_id: jobId ?? null }),
  session: (sessionId: string) => request<SessionView>(`/cli/sessions/${sessionId}`),
  upgradeSession: (sessionId: string) =>
    post<SessionView>(`/cli/sessions/${sessionId}/upgrade`, {}),
  stopSession: (sessionId: string) =>
    request<void>(`/cli/sessions/${sessionId}`, { method: "DELETE" }).catch(() => undefined),

  /**
   * The terminal's WebSocket URL.
   *
   * Derived from the API origin rather than configured separately, so the socket
   * can never point somewhere the HTTP calls do not.
   */
  terminalUrl: (sessionId: string) =>
    `${API.replace(/^http/, "ws")}/cli/sessions/${sessionId}/terminal`,

  // -- lessons --------------------------------------------------------------
  lesson: (repoUrl: string) =>
    request<LessonView>(`/lessons?repo_url=${encodeURIComponent(repoUrl)}`),
  generateLesson: (repoUrl: string) =>
    post<LessonGenerating>("/lessons", { repo_url: repoUrl }),
  lessonProgress: (lessonId: string, sessionId: string) =>
    request<LessonProgress>(
      `/lessons/${lessonId}/progress?session_id=${encodeURIComponent(sessionId)}`,
    ),
  checkStep: (lessonId: string, step: number, sessionId: string, sequence?: number) =>
    post<StepResult>(`/lessons/${lessonId}/check`, {
      step,
      session_id: sessionId,
      ...(sequence != null ? { sequence } : {}),
    }),
  explainStep: (lessonId: string, step: number, sessionId: string, sequence?: number) =>
    post<WhatHappenedView>(`/lessons/${lessonId}/explain`, {
      step,
      session_id: sessionId,
      ...(sequence != null ? { sequence } : {}),
    }),
};
