/**
 * An answered question: what kind it was, the answer, and what backs it.
 *
 * Shown when triage decided a question was conceptual or about the code, or was
 * testable with nothing here to test it on. The last case says so plainly — a
 * question that could have been an experiment is never quietly presented as if
 * explaining it were the same thing.
 */

"use client";

import { AnswerView } from "./answer-view";
import { Badge, Panel } from "./ui";
import type { ArchitectureGraph, ExperimentState, Triage } from "@/lib/types";

const KIND_LABEL: Record<Triage["kind"], string> = {
  conceptual: "Conceptual question",
  repository: "Question about this repository",
  testable: "Testable question",
};

/** How a question was classified, and what RepoLab chose to do about it. */
export function TriageNote({ triage }: { triage: Triage }) {
  const action =
    triage.route === "experiment"
      ? `designing an experiment on this repository's ${
          triage.backend === "cli" ? "built environment" : "Spark job"
        }`
      : "answering from the repository and its official documentation";

  return (
    <p className="text-xs text-fg-secondary" data-testid="triage">
      <Badge>{KIND_LABEL[triage.kind]}</Badge>{" "}
      <span className="ml-1">
        {triage.reason} — {action}.
      </span>
      {triage.decided_by === "model" && (
        <span className="ml-1 text-fg-muted">(classified by Nemotron Nano)</span>
      )}
    </p>
  );
}

/** The results screen for a question that was answered rather than tested. */
export function AnsweredQuestion({
  experiment,
  jobId,
  architecture = null,
}: {
  experiment: ExperimentState;
  jobId?: string;
  architecture?: ArchitectureGraph | null;
}) {
  const result = experiment.answer;
  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={experiment.question}
        subtitle="Answered from the repository and its official documentation; nothing was run"
        action={
          result?.answer ? (
            result.answer.grounded ? (
              <Badge tone="good">{result.answer.checked_claims} claims verified</Badge>
            ) : (
              <Badge tone="bad">unverified claims</Badge>
            )
          ) : undefined
        }
      >
        {result ? (
          <AnswerView
            result={result}
            repoUrl={experiment.repo_url}
            commitSha={experiment.commit_sha}
            architecture={architecture}
            askHref={jobId ? (question) => `/r/${jobId}/ask?q=${encodeURIComponent(question)}` : undefined}
          />
        ) : (
          <p className="text-sm text-fg-secondary">The answer has not been recorded.</p>
        )}
      </Panel>
    </div>
  );
}
