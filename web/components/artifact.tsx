/**
 * Analysis artifacts on screen: what RepoLab knows so far, piece by piece.
 *
 * The backend owns every status. The workspace loads them with
 * `GET /jobs/{id}/artifacts` (on mount and after each reconnect) and reloads
 * them whenever an `artifact_*` event arrives, then provides them here.
 *
 * `ArtifactSection` renders its children as soon as their data exists; until
 * then it holds the space the content will take and says, in plain words, what
 * is happening. A failure shows its own message and a Retry, and nothing else
 * on the page is affected. No percentages: nothing here can honestly estimate
 * one.
 */

"use client";

import { createContext, useContext, type ReactNode } from "react";

import { Button } from "@/components/ui";
import { WorkingStatus } from "@/components/working-status";
import type { ArtifactState, ArtifactStatus, ArtifactType } from "@/lib/types";

export type ArtifactsValue = {
  states: Partial<Record<ArtifactType, ArtifactState>>;
  /** Ensure an artifact is (being) generated; with `retry`, re-run a failed one. */
  request: (type: ArtifactType, retry?: boolean) => void;
  /** True once the job has finished: an artifact still not requested will not come. */
  settled: boolean;
};

const ArtifactsContext = createContext<ArtifactsValue>({
  states: {},
  request: () => {},
  settled: false,
});

export const ArtifactsProvider = ArtifactsContext.Provider;

export function useArtifacts(): ArtifactsValue {
  return useContext(ArtifactsContext);
}

export function useArtifact(type: ArtifactType) {
  const { states, request, settled } = useContext(ArtifactsContext);
  return {
    state: states[type] ?? null,
    request: () => request(type),
    retry: () => request(type, true),
    settled,
  };
}

/** What each artifact is called in checklists. What is said while one is
 * being made lives with every other activity, in `ACTIVITY_COPY`. */
export const ARTIFACT_COPY: Record<ArtifactType, { label: string }> = {
  "repo.facts": { label: "facts" },
  "repo.manifest": { label: "manifest" },
  "architecture.structure": { label: "structure" },
  "architecture.runtime": { label: "runtime" },
  "architecture.source": { label: "sources" },
  "architecture.components": { label: "components" },
  "architecture.summary": { label: "summary" },
  "architecture.runtime_naming": { label: "names" },
};

export function isActive(status: ArtifactStatus | undefined): boolean {
  return status === "queued" || status === "generating" || status === "verifying";
}

export function isUsable(status: ArtifactStatus | undefined): boolean {
  return status === "ready" || status === "partial";
}

/** The activity key a pending artifact is on: generating it, or verifying it. */
export function pendingActivity(type: ArtifactType, status: ArtifactStatus | undefined): string {
  return status === "verifying" ? `${type}:verifying` : type;
}

export function ArtifactSection({
  type,
  available,
  minHeight = "6rem",
  skeleton,
  children,
  quiet = false,
  "data-testid": testId,
}: {
  type: ArtifactType;
  /** Whether the content's data is already here (from cache, or an older job). */
  available: boolean;
  /** Reserved while pending, so the page does not jump when content arrives. */
  minHeight?: string;
  skeleton?: ReactNode;
  children: ReactNode;
  /**
   * A second section fed by the same artifact: holds its space with the
   * skeleton, but leaves the status line and any failure to the first one.
   */
  quiet?: boolean;
  "data-testid"?: string;
}) {
  const { state, retry, settled } = useArtifact(type);
  const status = state?.status;
  const id = testId ?? `artifact-${type}`;

  if (available) {
    return (
      <div data-testid={id} data-artifact-status={status ?? "ready"}>
        {children}
      </div>
    );
  }

  if (quiet && (status === "failed" || status === "blocked")) return null;

  if (status === "failed") {
    return (
      <div
        data-testid={id}
        data-artifact-status="failed"
        role="status"
        className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface-subtle p-4 text-sm"
      >
        <span className="text-fg-secondary">
          RepoLab couldn&apos;t finish this part{state?.error?.message ? `: ${state.error.message}` : "."}
        </span>
        {state?.error?.retryable !== false && (
          <Button size="sm" onClick={retry} data-testid={`${id}-retry`}>
            Retry
          </Button>
        )}
      </div>
    );
  }

  if (status === "blocked") {
    return (
      <p data-testid={id} data-artifact-status="blocked" role="status" className="text-sm text-fg-muted">
        Waiting on an earlier step that didn&apos;t finish. Retrying it will continue here.
      </p>
    );
  }

  // Nothing to show and nothing coming: an older analysis, or a finished job.
  if (settled && !isActive(status) && !isUsable(status)) return null;

  return (
    <div
      data-testid={id}
      data-artifact-status={status ?? "pending"}
      aria-busy="true"
      style={{ minHeight }}
      className="flex flex-col gap-3"
    >
      {!quiet && (
        <WorkingStatus
          compact
          activity={pendingActivity(type, status)}
          stage={type.split(".")[0]}
          // Queued, or not started yet: waiting, with no clock running.
          health={status === "generating" || status === "verifying" ? "working" : "waiting"}
          startedAt={status === "verifying" ? state?.updated_at : state?.started_at}
          data-testid={`${id}-working`}
        />
      )}
      {skeleton}
    </div>
  );
}
