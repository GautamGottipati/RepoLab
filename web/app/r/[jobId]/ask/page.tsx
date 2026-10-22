/**
 * Ask RepoLab — the question, then an answer or an experiment.
 *
 * A question is triaged first. A conceptual one, or one about the code, is
 * answered from the repository and its official docs. A testable one becomes an
 * experiment on whatever this repository really is — its Spark job, or CQL on
 * its built node — and nothing runs until it is approved.
 *
 * Spec: docs/design/05-ask.md
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ActivityFeed } from "@/components/activity";
import { AnswerCard, TriageNote } from "@/components/answer-card";
import { ApprovalCard, CliApprovalCard } from "@/components/approval-card";
import { Badge, Empty, ErrorNote, Note, Panel, Skeleton } from "@/components/ui";
import { api, ApiError, optional } from "@/lib/api";
import { useEventStream } from "@/lib/events";
import type { ExperimentState } from "@/lib/types";

import { useWorkspace } from "../workspace";

/** The question the Spark demo was built to answer. Offered only where it applies. */
const DEMO_QUESTION = "Why does this job use 200 shuffle partitions?";

/** Rows for a browser-initiated experiment, so a full sweep stays minutes not
 *  tens of minutes. The dataset stays large enough to keep the skew real. */
const EXPERIMENT_ROWS = 40_000;

export default function AskPage() {
  const router = useRouter();
  const { jobId, job, experiences } = useWorkspace();
  const repoUrl = job?.repo_url;
  const sparkDemo = experiences?.runnable_surfaces.includes("data_playground") ?? false;

  // The Spark demo question is a suggestion for a Spark repository, not a default
  // for every repository: on Cassandra it would be a question about nothing. Until
  // the learner types, the suggestion is derived rather than stored.
  const [draft, setQuestion] = useState<string | null>(null);
  const question = draft ?? (sparkDemo ? DEMO_QUESTION : "");
  const [experimentId, setExperimentId] = useState<string | null>(null);
  const [experiment, setExperiment] = useState<ExperimentState | null>(null);
  const [history, setHistory] = useState<ExperimentState[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  const { events } = useEventStream(
    experimentId ? api.experimentEventsUrl(experimentId) : null,
  );

  useEffect(() => {
    if (!repoUrl) return;
    let cancelled = false;
    void (async () => {
      try {
        const previous = await optional(api.experiments(repoUrl));
        if (!cancelled && previous) setHistory(previous);
      } catch (cause) {
        // Shown on the page rather than thrown into the void: an API that is
        // down is something to tell the learner, not an unhandled rejection.
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    })();
    return () => { cancelled = true; };
  }, [repoUrl, experiment?.status]);

  // Poll until the plan is presented or the proposal is refused.
  useEffect(() => {
    if (!experimentId) return;
    let cancelled = false;

    const poll = async () => {
      let next: ExperimentState | null;
      try {
        next = await optional(api.experiment(experimentId));
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
        return;
      }
      if (cancelled || !next) return;
      setExperiment(next);
      if (next.status === "failed" && next.rejection) setError(null);
    };

    void poll();
    const settled =
      experiment?.status === "awaiting_approval" ||
      experiment?.status === "failed" ||
      experiment?.status === "rejected" ||
      experiment?.status === "answered";
    if (settled) return () => { cancelled = true; };

    const timer = setInterval(poll, 1500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [experimentId, experiment?.status]);

  const ask = useCallback(async () => {
    if (!repoUrl) return;
    setError(null);
    setExperiment(null);
    setAsking(true);
    try {
      const created = await api.ask(repoUrl, question, EXPERIMENT_ROWS);
      setExperimentId(created.experiment_id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setAsking(false);
    }
  }, [repoUrl, question]);

  const approve = useCallback(async () => {
    if (!experimentId) return;
    try {
      await api.approve(experimentId, "browser");
      router.push(`/r/${jobId}/ask/${experimentId}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    }
  }, [experimentId, jobId, router]);

  const reject = useCallback(async () => {
    if (!experimentId) return;
    try {
      const result = await api.reject(experimentId, "browser");
      setExperiment((current) => (current ? { ...current, status: result.status } : current));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    }
  }, [experimentId]);

  const designing =
    experimentId !== null &&
    (experiment === null || experiment.status === "proposed");

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="Ask about this repository"
        subtitle="Conceptual questions are answered from the repository and its docs; testable ones become an experiment on this repository"
      >
        <div className="flex flex-col gap-2">
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={2}
            aria-label="Question"
            placeholder={
              sparkDemo
                ? DEMO_QUESTION
                : "e.g. What is this project? · Where is X implemented? · What happens if…?"
            }
            data-testid="question"
            className="w-full resize-y rounded border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900 dark:border-neutral-700 dark:bg-neutral-900 dark:focus:border-neutral-300"
          />
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={ask}
              disabled={asking || designing || question.trim().length < 8 || !repoUrl}
              data-testid="ask"
              className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
            >
              {asking || designing ? "Working…" : "Ask"}
            </button>
            {sparkDemo && question !== DEMO_QUESTION && (
              <button
                type="button"
                onClick={() => setQuestion(DEMO_QUESTION)}
                className="text-xs text-neutral-500 underline underline-offset-2"
              >
                Use the demo question
              </button>
            )}
          </div>
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}

      {designing && (
        <Panel
          title={
            experiment?.triage
              ? experiment.triage.route === "experiment"
                ? "Designing the experiment"
                : "Reading the repository to answer"
              : "Understanding the question"
          }
          subtitle="Nothing is executed at this stage"
        >
          {experiment?.triage && (
            <div className="mb-3">
              <TriageNote triage={experiment.triage} />
            </div>
          )}
          <Skeleton rows={2} />
          <div className="mt-3">
            <ActivityFeed events={events} height="h-40" />
          </div>
        </Panel>
      )}

      {experiment?.status === "failed" && experiment.rejection && (
        <Panel title="The proposed experiment was refused">
          <p className="text-sm font-medium">{experiment.rejection.reason}</p>
          <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
            {experiment.rejection.detail}
          </p>
          <p className="mt-3 text-xs text-neutral-500">
            Validation decides what may run, not the model. This is a real answer,
            not an error.
          </p>
        </Panel>
      )}

      {experiment?.status === "failed" && !experiment.rejection && experiment.error && (
        <ErrorNote>{experiment.error}</ErrorNote>
      )}

      {experiment?.status === "rejected" && (
        <Note>
          Declined — nothing ran, and nothing can be run for this experiment now.
          Ask again to design a new one.
        </Note>
      )}

      {experiment?.status === "answered" && experiment.answer && (
        <Panel title="Answer" subtitle="From the repository and its official documentation; nothing was run">
          <AnswerCard result={experiment.answer} triage={experiment.triage} />
        </Panel>
      )}

      {experiment?.status === "awaiting_approval" && experiment.triage && (
        <TriageNote triage={experiment.triage} />
      )}

      {experiment?.kind === "cli" && experiment.plan && experiment.status === "awaiting_approval" && (
        <CliApprovalCard plan={experiment.plan} onApprove={approve} onReject={reject} />
      )}

      {experiment?.kind !== "cli" && experiment?.plan && experiment.status === "awaiting_approval" && (
        <ApprovalCard plan={experiment.plan} onApprove={approve} onReject={reject} />
      )}

      {experiment?.plan &&
        (experiment.status === "approved" ||
          experiment.status === "running" ||
          experiment.status === "completed") && (
          <Panel title="Approved">
            <Link
              href={`/r/${jobId}/ask/${experiment.experiment_id}`}
              className="text-sm underline"
            >
              See the results →
            </Link>
          </Panel>
        )}

      <Panel title="Previous questions" subtitle="For this repository">
        {history.length === 0 ? (
          <Empty title="None yet" />
        ) : (
          <ul className="flex flex-col gap-2" data-testid="experiment-history">
            {history.map((entry) => (
              <li key={entry.experiment_id} className="flex items-center gap-3">
                <StatusBadge status={entry.status} />
                <Link
                  href={`/r/${jobId}/ask/${entry.experiment_id}`}
                  className="min-w-0 flex-1 truncate text-sm underline-offset-2 hover:underline"
                >
                  {entry.question}
                </Link>
                <span className="shrink-0 font-mono text-xs text-neutral-400">
                  {entry.variant_runs > 0 ? `${entry.variant_runs} runs` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function StatusBadge({ status }: { status: ExperimentState["status"] }) {
  if (status === "completed") return <Badge tone="good">completed</Badge>;
  if (status === "running") return <Badge tone="live">running</Badge>;
  if (status === "awaiting_approval") return <Badge tone="attention">needs approval</Badge>;
  if (status === "failed") return <Badge tone="bad">refused</Badge>;
  if (status === "rejected") return <Badge>declined</Badge>;
  if (status === "answered") return <Badge tone="good">answered</Badge>;
  return <Badge>{status}</Badge>;
}
