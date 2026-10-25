/**
 * The pieces around an answer: the question box with its context chip and
 * suggestions, the repository-context sidebar, and the compact history.
 *
 * Everything is read from the investigation's own architecture -- the selected
 * component's relationships, files and suggested questions -- so the page
 * adapts to whatever repository it is showing. Nothing is named in advance.
 */

"use client";

import Link from "next/link";

import { contextFor, componentsIn, type ContextPanel } from "@/lib/ask";
import { REPOSITORY_KIND_LABEL } from "@/lib/architecture";
import type { AnswerResult, ArchitectureView, ExperimentState } from "@/lib/types";

const INPUT =
  "w-full resize-y rounded border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900 dark:border-neutral-700 dark:bg-neutral-900 dark:focus:border-neutral-300";

export function QuestionBox({
  question,
  onChange,
  onAsk,
  busy,
  disabled,
  contextLabel,
  onClearContext,
  suggestions,
  placeholder,
  children,
}: {
  question: string;
  onChange: (question: string) => void;
  /** Ask *question*, or the one given (a suggestion). */
  onAsk: (question?: string) => void;
  busy: boolean;
  disabled: boolean;
  contextLabel: string | null;
  onClearContext: () => void;
  suggestions: string[];
  placeholder: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3" data-testid="question-box">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold tracking-tight">
          {contextLabel ? `Ask about ${contextLabel}` : "Ask about this repository"}
        </h2>
        {contextLabel && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-sky-100 py-0.5 pl-2.5 pr-1 text-xs text-sky-800 dark:bg-sky-950 dark:text-sky-300"
            data-testid="question-focus"
          >
            Context: {contextLabel}
            <button
              type="button"
              onClick={onClearContext}
              aria-label={`Ask without the ${contextLabel} context`}
              className="rounded-full px-1 leading-none hover:bg-sky-200 dark:hover:bg-sky-900"
            >
              ×
            </button>
          </span>
        )}
      </div>
      <textarea
        value={question}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !disabled) onAsk();
        }}
        rows={2}
        aria-label="Question"
        placeholder={placeholder}
        data-testid="question"
        className={INPUT}
      />
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => onAsk()}
          disabled={disabled}
          data-testid="ask"
          className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
        >
          {busy ? "Working…" : "Ask"}
        </button>
        {children}
      </div>
      {suggestions.length > 0 && (
        <div data-testid="suggestions">
          <p className="text-xs text-neutral-500">Suggested</p>
          <ul className="mt-1 flex flex-wrap gap-2">
            {suggestions.map((suggestion) => (
              <li key={suggestion}>
                <button
                  type="button"
                  onClick={() => onAsk(suggestion)}
                  disabled={busy}
                  data-testid="suggestion"
                  className="rounded-full border border-neutral-300 px-3 py-1 text-left text-xs hover:border-neutral-900 disabled:opacity-50 dark:border-neutral-700 dark:hover:border-neutral-300"
                >
                  {suggestion}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/** The right-hand panel: what the question is about, and what it relates to. */
export function AskContext({
  view,
  focusId,
  result,
}: {
  view: ArchitectureView | null;
  focusId: string | null;
  result: AnswerResult | null;
}) {
  if (!view) return null;
  const panel = contextFor(view, focusId);
  const drawn = componentsIn(result, view);
  const understanding = view.graph.understanding;

  return (
    <section className="flex flex-col gap-4 text-sm" data-testid="ask-context">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Repository context</h2>
      {panel ? (
        <Selected panel={panel} />
      ) : understanding ? (
        <div data-testid="context-repository">
          <p className="font-medium">{understanding.name}</p>
          <p className="text-xs text-neutral-500">{REPOSITORY_KIND_LABEL[understanding.repository_type]}</p>
          {understanding.one_line_description && (
            <p className="mt-1 text-neutral-600 dark:text-neutral-400">{understanding.one_line_description}</p>
          )}
        </div>
      ) : (
        <p className="text-xs text-neutral-500" data-testid="context-repository">
          {view.graph.summary ?? "Ask from a component on the Architecture page to keep it in context."}
        </p>
      )}
      {drawn.length > 0 && (
        <List title="In this answer" items={drawn} testId="context-in-answer" />
      )}
    </section>
  );
}

function Selected({ panel }: { panel: ContextPanel }) {
  return (
    <div className="flex flex-col gap-3" data-testid="context-selected">
      <div>
        <p className="font-medium">{panel.label}</p>
        <p className="text-xs capitalize text-neutral-500">{panel.kind}</p>
        {panel.description && (
          <p className="mt-1 text-neutral-600 dark:text-neutral-400">{panel.description}</p>
        )}
      </div>
      {panel.usedBy.length > 0 && <List title="Used by" items={panel.usedBy} testId="context-used-by" />}
      {panel.uses.length > 0 && <List title="Uses" items={panel.uses} testId="context-uses" />}
      {panel.implementedWith && (
        <div>
          <h3 className="text-xs text-neutral-500">Implemented with</h3>
          <p>{panel.implementedWith}</p>
        </div>
      )}
      {panel.files.length > 0 && (
        <List title="Important files" items={panel.files} mono testId="context-files" />
      )}
      {panel.related.length > 0 && panel.uses.length === 0 && panel.usedBy.length === 0 && (
        <List title="Related components" items={panel.related} testId="context-related" />
      )}
    </div>
  );
}

function List({ title, items, mono = false, testId }: { title: string; items: string[]; mono?: boolean; testId: string }) {
  return (
    <div data-testid={testId}>
      <h3 className="text-xs text-neutral-500">{title}</h3>
      <ul className={`mt-0.5 flex flex-col ${mono ? "font-mono text-xs" : ""}`}>
        {items.map((item) => (
          <li key={item} className="break-all">
            {mono ? item.split("/").pop() : `• ${item}`}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Earlier questions, compact and closed by default: the current answer is the focus. */
export function QuestionHistory({ jobId, history }: { jobId: string; history: ExperimentState[] }) {
  return (
    <details className="text-sm" data-testid="question-history">
      <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-neutral-500">
        History {history.length > 0 && <span className="font-normal">({history.length})</span>}
      </summary>
      {history.length === 0 ? (
        <p className="mt-2 text-xs text-neutral-500">No questions yet.</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1" data-testid="experiment-history">
          {history.map((entry) => (
            <li key={entry.experiment_id} className="flex min-w-0 items-baseline gap-2">
              <span aria-hidden className="text-neutral-400">›</span>
              <Link
                href={`/r/${jobId}/ask/${entry.experiment_id}`}
                className="min-w-0 flex-1 truncate underline-offset-2 hover:underline"
                title={entry.question}
              >
                {entry.question}
              </Link>
              <span className="shrink-0 text-xs text-neutral-400">{STATUS[entry.status] ?? entry.status}</span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

const STATUS: Partial<Record<ExperimentState["status"], string>> = {
  completed: "measured",
  running: "running",
  awaiting_approval: "needs approval",
  failed: "refused",
  rejected: "declined",
  answered: "answered",
};
