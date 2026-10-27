/**
 * What RepoLab is doing right now, said calmly.
 *
 * One component for every long wait -- the repository analysis, a section
 * still being made, an experience being prepared -- so the rules live in one
 * place:
 *
 * - The words come from the backend's *current activity*, a machine key set
 *   where the work really begins, mapped to copy here. Nothing is cycled on a
 *   timer: the headline changes only when the activity does.
 * - Time spent on one activity only changes the tone: past 30 s "taking a
 *   little longer", past 60 s "Still working". Silence is never called a
 *   stall.
 * - The one warning, "may have been interrupted", needs the backend's
 *   heartbeat-based `health`, never elapsed time.
 *
 * Spec: docs/design/00-overview.md ("Long waits")
 */

"use client";

import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui";
import type { ArtifactType, TaskHealth } from "@/lib/types";

type Copy = { headline: string; detail?: string };

const ARTIFACT_WORK: Record<ArtifactType, Copy> = {
  "repo.facts": { headline: "Understanding repository structure", detail: "Reading the file tree, languages and README." },
  "repo.manifest": {
    headline: "Finding important modules and entry points",
    detail: "Working out how this repository is built and run.",
  },
  "architecture.structure": {
    headline: "Mapping repository dependencies",
    detail: "Grouping the code into its main parts.",
  },
  "architecture.runtime": {
    headline: "Tracing runtime relationships",
    detail: "Following how the major components call and create each other.",
  },
  "architecture.source": {
    headline: "Resolving source references",
    detail: "Keeping a copy of every file the architecture cites.",
  },
  "architecture.components": {
    headline: "Identifying major architecture components",
    detail: "Naming the main parts of the system from the code.",
  },
  "architecture.summary": {
    headline: "Writing a plain-language summary",
    detail: "Explaining what this repository is and how it fits together.",
  },
  "architecture.runtime_naming": {
    headline: "Naming the runtime components",
    detail: "Until then they keep their type names, which are real.",
  },
};

/** Every activity key the backend sets, and what a person reads for it. */
export const ACTIVITY_COPY: Record<string, Copy> = {
  // repository analysis: agent phases (repolab/events/activity.py)
  inspecting_repository: {
    headline: "Understanding repository structure",
    detail: "Fetching the repository and reading its layout.",
  },
  finding_entrypoints: {
    headline: "Finding important modules and entry points",
    detail: "Checking how the project is started and what it exposes.",
  },
  understanding_data_flow: {
    headline: "Following the main execution flow",
    detail: "Reading the code that does the core work.",
  },
  preparing_environment: {
    headline: "Checking which experiences can run",
    detail: "Looking for a runtime this repository can use.",
  },
  // analysis artifacts (repolab/artifacts/registry.py), and their verification
  ...ARTIFACT_WORK,
  ...Object.fromEntries(
    Object.entries(ARTIFACT_WORK).map(([key, copy]) => [
      `${key}:verifying`,
      { headline: "Verifying architecture against source code", detail: copy.headline + "." },
    ]),
  ),
  // experience preparation steps (worker/experience_tasks.py)
  generate: { headline: "Writing a guided Mini Lab", detail: "Built from the repository's own concepts." },
  runtime: { headline: "Preparing a safe runtime environment" },
  image: {
    headline: "Preparing a safe runtime environment",
    detail: "Fetching the container image. The first time can take a few minutes.",
  },
  cache: { headline: "Reusing an earlier build of this commit" },
  preflight: { headline: "Checking this machine can build the project" },
  clone: { headline: "Fetching the repository to build it" },
  plan: { headline: "Planning the build", detail: "Using the repository's own build command." },
  python: { headline: "Preparing repository dependencies" },
  install: {
    headline: "Preparing repository dependencies",
    detail: "Installing the package in an isolated environment.",
  },
  build: { headline: "Building the project", detail: "Large projects can take many minutes to build." },
  smoke: {
    headline: "Starting the repository in an isolated environment",
    detail: "Running a tiny job to prove the runtime works.",
  },
  ready: { headline: "Starting the interactive environment" },
  // Ask RepoLab (adopted later)
  "ask.investigating": { headline: "Investigating your question" },
  "ask.sources": { headline: "Finding relevant source code" },
  "ask.verifying": { headline: "Verifying the explanation against the repository" },
};

const STAGE_FALLBACK: Record<string, Copy> = {
  repository: { headline: "Understanding repository structure" },
  investigation: { headline: "Analyzing the repository" },
  architecture: { headline: "Analyzing repository architecture" },
  experience: { headline: "Preparing the runtime environment" },
  ask: { headline: "Investigating your question" },
};

export function activityCopy(key: string | null | undefined, stage?: string | null): Copy {
  if (key && ACTIVITY_COPY[key]) return ACTIVITY_COPY[key];
  if (stage && STAGE_FALLBACK[stage]) return STAGE_FALLBACK[stage];
  return { headline: "Working" };
}

/** Past this, the step is "taking a little longer than usual". */
export const LONGER_AFTER_SECONDS = 30;
/** Past this, the headline becomes "Still working". */
export const STILL_WORKING_AFTER_SECONDS = 60;

export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}

/** Seconds since *startedAt*, ticking once a second while mounted. */
export function useElapsed(startedAt: string | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  if (!startedAt) return null;
  const started = Date.parse(startedAt);
  return Number.isNaN(started) ? null : Math.max(0, (now - started) / 1000);
}

/** What the component will say, as data, so the rules are testable without a DOM. */
export function workingWords(
  activity: string | null | undefined,
  stage: string | null | undefined,
  elapsed: number | null,
): { headline: string; detail?: string; note?: string } {
  const copy = activityCopy(activity, stage);
  if (elapsed !== null && elapsed >= STILL_WORKING_AFTER_SECONDS) {
    return {
      headline: "Still working",
      detail: copy.detail ? `${copy.headline}. ${copy.detail}` : `${copy.headline}…`,
      note: "Large repositories can take a little longer.",
    };
  }
  return {
    headline: `${copy.headline}…`,
    detail: copy.detail,
    note: elapsed !== null && elapsed >= LONGER_AFTER_SECONDS ? "This step is taking a little longer than usual." : undefined,
  };
}

export function WorkingStatus({
  activity,
  stage,
  startedAt,
  health,
  compact = false,
  onRetry,
  "data-testid": testId = "working-status",
}: {
  /** The backend's current activity key, or null when it has not said. */
  activity: string | null | undefined;
  stage?: string | null;
  /** When this activity began (ISO). Times the step, not the whole job. */
  startedAt?: string | null;
  health?: TaskHealth | null;
  compact?: boolean;
  /** Offered only when processing may have been interrupted. */
  onRetry?: () => void;
  "data-testid"?: string;
}) {
  const elapsed = useElapsed(health === "interrupted" ? null : startedAt);

  if (health === "interrupted") {
    return (
      <div
        role="alert"
        data-testid={testId}
        data-health="interrupted"
        className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-exp-line bg-exp-soft p-4 text-sm"
      >
        <div>
          <p className="font-medium text-exp-fg">⚠ Processing may have been interrupted</p>
          <p className="mt-1 text-fg-secondary">RepoLab hasn&apos;t received a worker heartbeat recently.</p>
        </div>
        {onRetry && (
          <Button size="sm" onClick={onRetry} data-testid={`${testId}-retry`}>
            Retry
          </Button>
        )}
      </div>
    );
  }

  const words =
    health === "waiting"
      ? { headline: "Waiting to start…" }
      : workingWords(activity, stage, elapsed);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid={testId}
      data-health={health ?? "working"}
      data-activity={activity ?? ""}
      className={compact ? "flex flex-col gap-0.5 text-sm" : "flex flex-col gap-1"}
    >
      <p className={`flex items-center gap-2 ${compact ? "text-fg-secondary" : "text-sm font-medium text-fg"}`}>
        <Pulse />
        <span>{words.headline}</span>
      </p>
      {words.detail && <Line compact={compact}>{words.detail}</Line>}
      {"note" in words && words.note && <Line compact={compact}>{words.note}</Line>}
      {elapsed !== null && health !== "waiting" && (
        <p className="pl-3.5 text-xs text-fg-muted" data-testid={`${testId}-elapsed`}>
          Working for {formatElapsed(elapsed)}
        </p>
      )}
    </div>
  );
}

function Line({ compact, children }: { compact: boolean; children: ReactNode }) {
  return <p className={`pl-3.5 ${compact ? "text-xs" : "text-sm"} text-fg-secondary`}>{children}</p>;
}

function Pulse() {
  return <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-brand motion-safe:animate-pulse" />;
}
