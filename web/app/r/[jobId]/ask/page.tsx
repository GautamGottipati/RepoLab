/**
 * Ask RepoLab — the question, then an answer or an experiment.
 *
 * A question is triaged first. A conceptual one, or one about the code, is
 * answered from the repository and its official docs, laid out to teach: the
 * direct answer, a small picture, how it works, then the sources. A testable
 * one becomes an experiment on whatever this repository really is — its Spark
 * job, or CQL on its built node — and nothing runs until it is approved.
 *
 * The right-hand column carries the repository context of the question and
 * the history; the agent's raw activity stays one click away.
 *
 * Spec: docs/design/05-ask.md
 */

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { ActivityFeed } from "@/components/activity";
import { TriageNote } from "@/components/answer-card";
import { AnswerView } from "@/components/answer-view";
import { AskContext, QuestionBox, QuestionHistory } from "@/components/ask-context";
import { ApprovalCard, CliApprovalCard } from "@/components/approval-card";
import { ErrorNote, Note, Panel, Skeleton } from "@/components/ui";
import { api, ApiError, optional } from "@/lib/api";
import { suggestionsFor } from "@/lib/ask";
import { focusFor } from "@/lib/architecture";
import { useEventStream } from "@/lib/events";
import type { ExperimentState, JobEvent } from "@/lib/types";

import { useWorkspace } from "../workspace";

/** The question the Spark demo was built to answer. Offered only where it applies. */
const DEMO_QUESTION = "Why does this job use 200 shuffle partitions?";

/** Rows for a browser-initiated experiment, so a full sweep stays minutes not
 *  tens of minutes. The dataset stays large enough to keep the skew real. */
const EXPERIMENT_ROWS = 40_000;

export default function AskPage() {
  const router = useRouter();
  const { jobId, job, experiences, architecture, asideSlot } = useWorkspace();
  const searchParams = useSearchParams();
  const fromArchitecture = searchParams.get("q");
  const nodeId = searchParams.get("node");
  // Asked from an experience: an experiment runs the learner's own code there.
  const experienceSession = searchParams.get("experience");
  const [focusDismissed, setFocusDismissed] = useState(false);

  // Asked from a component on the Architecture page: the component's files and
  // relationships travel with the question, so the answer starts from them.
  const focus = useMemo(
    () => (nodeId && architecture && !focusDismissed ? focusFor(architecture.graph, nodeId) : null),
    [nodeId, architecture, focusDismissed],
  );
  const repoUrl = job?.repo_url;
  const sparkDemo = experiences?.runnable_surfaces.includes("data_playground") ?? false;

  // The Spark demo question is a suggestion for a Spark repository, not a default
  // for every repository: on Cassandra it would be a question about nothing. Until
  // the learner types, the suggestion is derived rather than stored.
  const [draft, setQuestion] = useState<string | null>(null);
  const question = draft ?? fromArchitecture ?? (sparkDemo ? DEMO_QUESTION : "");
  const [experimentId, setExperimentId] = useState<string | null>(null);
  const [experiment, setExperiment] = useState<ExperimentState | null>(null);
  const [history, setHistory] = useState<ExperimentState[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [showActivity, setShowActivity] = useState(false);
  const focusId = focus ? nodeId : null;
  const suggestions = useMemo(() => suggestionsFor(architecture, focusId), [architecture, focusId]);

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

  /** Ask the question in the box, or *next* -- a suggestion or a follow-up. */
  const ask = useCallback(async (next?: string) => {
    if (!repoUrl) return;
    const asked = (next ?? question).trim();
    if (asked.length < 8) return;
    if (next !== undefined) setQuestion(next);
    setError(null);
    setExperiment(null);
    setShowActivity(false);
    setAsking(true);
    try {
      const created = await api.ask(
        repoUrl,
        asked,
        EXPERIMENT_ROWS,
        focus ?? undefined,
        experienceSession ?? undefined,
      );
      setExperimentId(created.experiment_id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setAsking(false);
    }
  }, [repoUrl, question, focus, experienceSession]);

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
      <Panel>
        <QuestionBox
          question={question}
          onChange={setQuestion}
          onAsk={(next) => void ask(next)}
          busy={asking || designing}
          disabled={asking || designing || question.trim().length < 8 || !repoUrl}
          contextLabel={focus?.label ?? null}
          onClearContext={() => setFocusDismissed(true)}
          suggestions={experiment?.status === "answered" ? [] : suggestions}
          placeholder={
            sparkDemo
              ? DEMO_QUESTION
              : focus
                ? `e.g. How does ${focus.label} work?`
                : "e.g. What is this project? · Where is X implemented? · What happens if…?"
          }
        >
          {experienceSession && (
            <span className="text-xs text-violet-700 dark:text-violet-300" data-testid="experience-target">
              An experiment will run your code from the experience, on the same runtime.
            </span>
          )}
          {sparkDemo && question !== DEMO_QUESTION && (
            <button
              type="button"
              onClick={() => setQuestion(DEMO_QUESTION)}
              className="text-xs text-neutral-500 underline underline-offset-2"
            >
              Use the demo question
            </button>
          )}
        </QuestionBox>
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
          <p className="mt-3 text-xs text-neutral-500" data-testid="answer-status">
            {latestStatus(events) ?? "Starting…"}
          </p>
          <button
            type="button"
            onClick={() => setShowActivity((v) => !v)}
            aria-expanded={showActivity}
            className="mt-1 text-xs underline underline-offset-2"
          >
            {showActivity ? "Hide what the agent is doing" : "Show what the agent is doing"}
          </button>
          {showActivity && (
            <div className="mt-3">
              <ActivityFeed events={events} height="h-40" />
            </div>
          )}
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
        <Panel>
          <AnswerView
            result={experiment.answer}
            repoUrl={experiment.repo_url}
            commitSha={experiment.commit_sha}
            architecture={architecture?.graph ?? null}
            onAsk={(next) => void ask(next)}
          />
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

      {asideSlot &&
        createPortal(
          <>
            <AskContext
              view={architecture}
              focusId={focusId}
              result={experiment?.status === "answered" ? experiment.answer : null}
            />
            <QuestionHistory jobId={jobId} history={history} />
          </>,
          asideSlot,
        )}
    </div>
  );
}

/** The agent's latest user-facing status line, while it works. */
function latestStatus(events: JobEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if ((event.type === "agent_status" || event.type === "step_started" || event.type === "tool_started") && event.message) {
      return event.message;
    }
  }
  return null;
}
