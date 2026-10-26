/**
 * Shared pieces: panels, labels, states -- on the design tokens.
 *
 * The design-system primitives live in `components/kit/`; this module keeps
 * the names every screen already imports and re-exports the kit, so a call site
 * needs one import whichever it uses.
 */

import type { ReactNode } from "react";

import { EmptyState } from "./kit/states";

export * from "./kit/actions";
export * from "./kit/button";
export * from "./kit/drawer";
export * from "./kit/icons";
export * from "./kit/layout";
export * from "./kit/states";
export * from "./kit/status";
export * from "./kit/tabs";

export function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`min-w-0 rounded-lg border border-line bg-surface ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-4">
          <div className="min-w-0">
            {title && <h2 className="text-card font-semibold tracking-tight text-fg">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-fg-secondary">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="px-5 pt-3 pb-5">{children}</div>
    </section>
  );
}

/** A labelled figure. `mono` for anything that came from the machine. */
export function Stat({
  label,
  value,
  hint,
  mono = true,
  emphasis = false,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  mono?: boolean;
  emphasis?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd
        className={`${mono ? "font-mono tabular-nums" : ""} ${
          emphasis ? "text-lg font-semibold" : "text-sm"
        } mt-0.5 break-words text-fg`}
      >
        {value}
      </dd>
      {hint && <p className="mt-0.5 text-xs text-fg-muted">{hint}</p>}
    </div>
  );
}

/** A simple empty state; `EmptyState` takes actions as well. */
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <EmptyState title={title} compact>
      {children}
    </EmptyState>
  );
}

/**
 * A failure, as a person should read it.
 *
 * `children` is the headline — one sentence, no jargon. `nextStep` is what to do
 * about it, and `technical` is the error's own text, kept behind a disclosure so
 * it is available without being the first thing anyone sees.
 */
export function ErrorNote({
  children,
  nextStep,
  technical,
}: {
  children: ReactNode;
  nextStep?: string;
  technical?: string;
}) {
  return (
    <div
      role="alert"
      data-testid="error-note"
      className="rounded-lg border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg"
    >
      <p className="font-medium">{children}</p>
      {nextStep && (
        <p className="mt-1 text-sm text-fg-secondary" data-testid="error-next-step">
          {nextStep}
        </p>
      )}
      {technical && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs">Technical detail</summary>
          <pre
            data-testid="error-technical"
            className="mt-1 max-h-60 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-code p-2 font-mono text-xs text-code-fg"
          >
            {technical}
          </pre>
        </details>
      )}
    </div>
  );
}

/** Something worth noticing that is not a failure. */
export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-exp-line bg-exp-soft px-3 py-2 text-sm text-exp-fg">
      {children}
    </p>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="h-4 animate-pulse rounded-sm bg-surface-subtle"
          style={{ width: `${100 - index * 12}%` }}
        />
      ))}
    </div>
  );
}

/**
 * Whether a figure repeats across identical runs.
 *
 * Shown next to every measurement, because presenting a wall-clock duration
 * with the same weight as a task count would misrepresent what it supports.
 */
export function DeterminismTag({ kind }: { kind: "structural" | "timing" }) {
  const structural = kind === "structural";
  return (
    <span
      title={
        structural
          ? "Structural: repeats exactly across identical runs"
          : "Timing: varies between runs"
      }
      className={`rounded-sm border px-1.5 py-0.5 text-xs font-medium ${
        structural
          ? "border-line bg-surface-subtle text-fg-secondary"
          : "border-exp-line bg-exp-soft text-exp-fg"
      }`}
    >
      {kind}
    </span>
  );
}

/** Repository lines behind a claim. Never rendered empty: a claim with no
 *  evidence should not be on screen at all. */
export function FileRefs({
  refs,
  limit = 4,
}: {
  refs: { path: string; start_line: number | null; excerpt: string | null }[];
  limit?: number;
}) {
  // Two references to the same line render identically, so showing both just
  // says the same thing twice.
  const seen = new Set<string>();
  const unique = refs.filter((ref) => {
    const key = `${ref.path}:${ref.start_line ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  if (unique.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-col gap-1">
      {unique.slice(0, limit).map((ref, index) => (
        <li key={`${ref.path}:${ref.start_line}:${index}`} className="min-w-0 truncate font-mono text-xs">
          <span className="text-fg">
            {ref.path}
            {ref.start_line ? `:${ref.start_line}` : ""}
          </span>
          {ref.excerpt && <span className="ml-2 text-fg-muted">{ref.excerpt.trim().slice(0, 70)}</span>}
        </li>
      ))}
      {unique.length > limit && <li className="text-xs text-fg-muted">+{unique.length - limit} more</li>}
    </ul>
  );
}
