/**
 * The lesson panel: one step at a time, with the evidence behind it.
 *
 * Interaction over reading (CLAUDE.md): each step is a statement the learner
 * runs, and the panel's job is to say why it matters, what to look for, and —
 * once they have run it — what RepoLab's check concluded and *who concluded it*.
 * A pass decided by a model is labelled as such; a learner should never have to
 * guess whether they were checked or judged.
 *
 * Every step shows the repository lines and documentation pages behind it, so a
 * claim about the software can be followed to its source.
 *
 * Spec: docs/design/00-overview.md
 */

"use client";

import { Badge, Note, Panel } from "./ui";
import type { Lesson, LessonStep, SimulatedLesson, StepResult } from "@/lib/types";

const CHECK_LABEL: Record<string, string> = {
  output_contains: "the output must contain something specific",
  output_matches: "the output must match a pattern",
  succeeds: "the server must accept the statement",
  fails_with: "the server must reject it, with a particular message",
};

export function LessonProgressBar({
  lesson,
  completed,
}: {
  lesson: Lesson;
  completed: number[];
}) {
  return (
    <ol className="flex flex-wrap gap-1.5" data-testid="lesson-progress">
      {lesson.steps.map((step) => {
        const done = completed.includes(step.number);
        return (
          <li
            key={step.number}
            data-testid={`lesson-dot-${step.number}`}
            data-done={done}
            title={`${step.number}. ${step.concept}`}
            className={`h-1.5 w-10 rounded-full ${
              done ? "bg-run" : "bg-line"
            }`}
          />
        );
      })}
    </ol>
  );
}

export function LessonStepCard({
  step,
  total,
  result,
  pending,
  canRun,
  onRun,
  onCheck,
  onExplain,
  onNext,
  explainable,
}: {
  step: LessonStep;
  total: number;
  result: StepResult | null;
  pending: boolean;
  canRun: boolean;
  onRun: () => void;
  onCheck: () => void;
  onExplain: () => void;
  onNext: () => void;
  explainable: boolean;
}) {
  const passed = result?.passed === true;

  return (
    <Panel
      title={`Step ${step.number} of ${total} · ${step.concept}`}
      subtitle={step.goal}
      action={
        passed ? (
          <Badge tone="good">passed</Badge>
        ) : result ? (
          <Badge tone="bad">not yet</Badge>
        ) : null
      }
    >
      <div data-testid={`lesson-step-${step.number}`}>
        <p className="text-sm leading-relaxed text-fg">
          {step.context}
        </p>

        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            Run this
          </h3>
          <pre
            data-testid={`lesson-action-${step.number}`}
            className="mt-1.5 overflow-x-auto rounded border border-line bg-surface-subtle p-3 font-mono text-xs"
          >
            {step.action}
          </pre>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onRun}
              disabled={!canRun || pending}
              data-testid="run-step"
              className="rounded bg-brand px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
            >
              Run in the terminal
            </button>
            <button
              type="button"
              onClick={onCheck}
              disabled={pending}
              data-testid="check-step"
              className="rounded border border-line-strong px-3 py-1.5 text-xs disabled:opacity-40"
            >
              Check my work
            </button>
            {explainable && (
              <button
                type="button"
                onClick={onExplain}
                disabled={pending}
                data-testid="explain-step"
                className="rounded border border-line-strong px-3 py-1.5 text-xs disabled:opacity-40"
              >
                What just happened?
              </button>
            )}
            {passed && (
              <button
                type="button"
                onClick={onNext}
                data-testid="next-step"
                className="ml-auto rounded bg-run px-3 py-1.5 text-xs font-medium text-white"
              >
                Next step →
              </button>
            )}
          </div>
          <p className="mt-2 text-xs text-fg-muted">
            You can also type it yourself — the terminal is a real cqlsh.
          </p>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
              What you should see
            </h3>
            <p className="mt-1 text-sm text-fg">
              {step.expected_observation}
            </p>
          </div>
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
              How RepoLab checks it
            </h3>
            <p className="mt-1 text-sm text-fg">
              {step.validation.describe || CHECK_LABEL[step.validation.kind] || "deterministically"}
            </p>
          </div>
        </div>

        {result && <Verdict result={result} hint={step.hint} />}

        <References step={step} />
      </div>
    </Panel>
  );
}

function Verdict({ result, hint }: { result: StepResult; hint: string }) {
  const tone = result.passed
    ? "border-run bg-run-soft"
    : "border-exp bg-exp-soft";

  return (
    <div className={`mt-4 rounded border-2 p-3 ${tone}`} data-testid="step-verdict">
      <p className="text-sm font-semibold">
        {result.passed ? "That worked." : "Not there yet."}
        <span
          className="ml-2 font-mono text-xs font-normal text-fg-secondary"
          data-testid="decided-by"
        >
          decided by {result.decided_by}
          {result.conclusive ? "" : " · judgement, not a check"}
        </span>
      </p>
      <p className="mt-1 text-sm">{result.reason}</p>
      {!result.passed && (hint || result.hint) && (
        <p className="mt-2 text-sm text-fg-secondary" data-testid="step-hint">
          Hint: {result.hint || hint}
        </p>
      )}
      {result.lesson_complete && (
        <p className="mt-2 text-sm font-semibold text-run-fg">
          That is the whole lesson. You have run every step against a real Cassandra.
        </p>
      )}
    </div>
  );
}

function References({ step }: { step: LessonStep }) {
  if (step.code_references.length === 0 && step.documentation_references.length === 0) {
    return null;
  }

  return (
    <div
      className="mt-4 border-t border-line pt-3"
      data-testid="step-references"
    >
      {step.code_references.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            In this repository
          </h3>
          <ul className="mt-1 flex flex-col gap-1">
            {step.code_references.map((ref, index) => (
              <li key={index} className="font-mono text-xs text-fg-secondary">
                {ref.path}
                {ref.start_line ? `:${ref.start_line}` : ""}
                {ref.excerpt && (
                  <span className="ml-2 text-fg-muted">{ref.excerpt.slice(0, 90)}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {step.documentation_references.length > 0 && (
        <div className="mt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            Official documentation
          </h3>
          <ul className="mt-1 flex flex-col gap-1">
            {step.documentation_references.map((doc, index) => (
              <li key={index} className="text-xs">
                <a
                  href={doc.url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-2"
                >
                  {doc.title ?? doc.url}
                </a>
                {doc.version && <span className="ml-2 text-fg-muted">{doc.version}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function SimulatedBanner({ reason }: { reason: string }) {
  return (
    <div
      data-testid="simulated-banner"
      className="rounded-lg border-2 border-dashed border-exp bg-exp-soft p-4"
    >
      <p className="font-mono text-sm font-bold uppercase tracking-widest text-exp-fg">
        Simulated
      </p>
      <p className="mt-1 text-sm text-exp-fg">{reason}</p>
      <p className="mt-2 text-xs text-exp-fg">
        Nothing is executed. No output is shown, because none was produced, and no timing or
        performance figure can be given. Each step names the real source behind it.
      </p>
    </div>
  );
}

export function LessonHeader({ lesson }: { lesson: Lesson }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h1 className="text-lg font-semibold tracking-tight" data-testid="lesson-title">
        {lesson.title}
      </h1>
      <Badge>{lesson.generated_by === "super" ? "written by Nemotron Super" : "by RepoLab"}</Badge>
      <Badge tone={lesson.simulated ? "bad" : "good"}>{lesson.fidelity} fidelity</Badge>
      <p className="w-full text-sm text-fg-secondary">{lesson.summary}</p>
    </div>
  );
}

export function SimulatedLessonBody({ lesson }: { lesson: SimulatedLesson }) {
  return (
    <div className="flex flex-col gap-3" data-testid="simulated-lesson">
      {lesson.steps.map((step) => (
        <div
          key={step.number}
          data-testid={`simulated-step-${step.number}`}
          className="rounded-lg border-2 border-dashed border-exp bg-exp-soft/40 p-4"
        >
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-xs font-bold uppercase tracking-widest text-exp-fg">
              {step.label}
            </span>
            <h3 className="text-sm font-semibold">
              {step.number}. {step.concept}
            </h3>
          </div>
          <p className="mt-1 text-sm text-fg">{step.context}</p>
          <pre className="mt-2 overflow-x-auto rounded border border-exp-line bg-surface/60 p-2 font-mono text-xs">
            {step.action}
          </pre>
          <p className="mt-2 text-sm text-exp-fg">{step.explanation}</p>
          {step.code_references.length > 0 && (
            <ul className="mt-2 flex flex-col gap-0.5">
              {step.code_references.map((ref, index) => (
                <li key={index} className="font-mono text-xs text-fg-secondary">
                  {ref.path}
                  {ref.start_line ? `:${ref.start_line}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

export function UngroundedNote({ lesson }: { lesson: Lesson }) {
  const ungrounded = lesson.steps.filter((step) => !step.grounded).length;
  if (ungrounded === 0) return null;

  return (
    <Note>
      {ungrounded} of {lesson.step_count} steps cite no repository file. RepoLab drops a citation
      it could not resolve against this commit rather than showing one you cannot check.
    </Note>
  );
}
