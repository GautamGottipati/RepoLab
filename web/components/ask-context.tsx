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

import { AskIcon, Button } from "./ui";
import { contextFor, componentsIn, type ContextPanel } from "@/lib/ask";
import { REPOSITORY_KIND_LABEL } from "@/lib/architecture";
import type { AnswerResult, ArchitectureView, ExperimentState } from "@/lib/types";

const INPUT =
  "w-full resize-y rounded-lg border border-line-strong bg-surface px-4 py-3 text-body text-fg outline-none transition-colors placeholder:text-fg-muted focus:border-ai focus:ring-2 focus:ring-ai-soft";

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
        <h2 className="text-sm font-medium text-fg-secondary">
          {contextLabel ? `Ask about ${contextLabel}` : "Ask about this repository"}
        </h2>
        {contextLabel && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-brand-soft py-0.5 pl-2.5 pr-1 text-xs text-brand-fg"
            data-testid="question-focus"
          >
            Context: {contextLabel}
            <button
              type="button"
              onClick={onClearContext}
              aria-label={`Ask without the ${contextLabel} context`}
              className="rounded-full px-1 leading-none hover:bg-brand-line"
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
        rows={3}
        aria-label="Question"
        placeholder={placeholder}
        data-testid="question"
        className={INPUT}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          tone="ai"
          onClick={() => onAsk()}
          disabled={disabled}
          data-testid="ask"
          icon={<AskIcon size={14} />}
        >
          {busy ? "Working…" : "Ask"}
        </Button>
        <span className="hidden text-xs text-fg-muted sm:inline">⌘ Enter</span>
        {children}
      </div>
      {suggestions.length > 0 && (
        <div data-testid="suggestions">
          <p className="text-xs font-semibold uppercase tracking-wider text-fg-muted">Suggested questions</p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {suggestions.map((suggestion) => (
              <li key={suggestion}>
                <button
                  type="button"
                  onClick={() => onAsk(suggestion)}
                  disabled={busy}
                  data-testid="suggestion"
                  className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-sm text-fg-secondary transition-colors hover:border-ai-line hover:bg-ai-soft hover:text-ai-fg disabled:opacity-50"
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
      <h2 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">Repository context</h2>
      {panel ? (
        <Selected panel={panel} />
      ) : understanding ? (
        <div data-testid="context-repository">
          <p className="font-medium">{understanding.name}</p>
          <p className="text-xs text-fg-secondary">{REPOSITORY_KIND_LABEL[understanding.repository_type]}</p>
          {understanding.one_line_description && (
            <p className="mt-1 text-fg-secondary">{understanding.one_line_description}</p>
          )}
        </div>
      ) : (
        <p className="text-xs text-fg-secondary" data-testid="context-repository">
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
        <p className="text-xs capitalize text-fg-secondary">{panel.kind}</p>
        {panel.description && (
          <p className="mt-1 text-fg-secondary">{panel.description}</p>
        )}
      </div>
      {panel.usedBy.length > 0 && <List title="Used by" items={panel.usedBy} testId="context-used-by" />}
      {panel.uses.length > 0 && <List title="Uses" items={panel.uses} testId="context-uses" />}
      {panel.implementedWith && (
        <div>
          <h3 className="text-xs text-fg-secondary">Implemented with</h3>
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
      <h3 className="text-xs text-fg-secondary">{title}</h3>
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
      <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-fg-secondary">
        History {history.length > 0 && <span className="font-normal">({history.length})</span>}
      </summary>
      {history.length === 0 ? (
        <p className="mt-2 text-xs text-fg-secondary">No questions yet.</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1" data-testid="experiment-history">
          {history.map((entry) => (
            <li key={entry.experiment_id} className="flex min-w-0 items-baseline gap-2">
              <span aria-hidden className="text-fg-muted">›</span>
              <Link
                href={`/r/${jobId}/ask/${entry.experiment_id}`}
                className="min-w-0 flex-1 truncate underline-offset-2 hover:underline"
                title={entry.question}
              >
                {entry.question}
              </Link>
              <span className="shrink-0 text-xs text-fg-muted">{STATUS[entry.status] ?? entry.status}</span>
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
