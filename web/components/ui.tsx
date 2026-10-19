/** Small shared pieces: panels, labels, states. */

import type { ReactNode } from "react";

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
    <section
      className={`rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950 ${className}`}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-tight">{title}</h2>}
            {subtitle && (
              <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">{subtitle}</p>
            )}
          </div>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
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
    <div>
      <dt className="text-xs text-neutral-500 dark:text-neutral-400">{label}</dt>
      <dd
        className={`${mono ? "font-mono" : ""} ${
          emphasis ? "text-lg font-semibold" : "text-sm"
        } mt-0.5 break-words`}
      >
        {value}
      </dd>
      {hint && <p className="mt-0.5 text-xs text-neutral-400 dark:text-neutral-500">{hint}</p>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-neutral-300 px-4 py-8 text-center dark:border-neutral-700">
      <p className="text-sm font-medium">{title}</p>
      {children && (
        <p className="mx-auto mt-1 max-w-md text-xs text-neutral-500 dark:text-neutral-400">
          {children}
        </p>
      )}
    </div>
  );
}

/**
 * A failure, as a person should read it.
 *
 * `children` is the headline — one sentence, no jargon. `nextStep` is what to do
 * about it, and `technical` is the error's own text, kept behind a disclosure so
 * it is available without being the first thing anyone sees.
 *
 * Called with a bare string everywhere it was before; the extra parts are
 * supplied where the backend sends them.
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
      className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
    >
      <p>{children}</p>
      {nextStep && (
        <p className="mt-1 text-xs text-red-700 dark:text-red-300" data-testid="error-next-step">
          {nextStep}
        </p>
      )}
      {technical && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-xs text-red-600 dark:text-red-400">
            Technical detail
          </summary>
          <pre
            data-testid="error-technical"
            className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] text-red-700 dark:text-red-400"
          >
            {technical}
          </pre>
        </details>
      )}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
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
          className="h-4 animate-pulse rounded bg-neutral-200 dark:bg-neutral-800"
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
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
        structural
          ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"
          : "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300"
      }`}
    >
      {kind}
    </span>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "live" | "good" | "bad" | "attention";
}) {
  const tones = {
    neutral: "bg-neutral-100 text-neutral-700 dark:bg-neutral-900 dark:text-neutral-300",
    live: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
    good: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
    bad: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
    attention: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>
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
        <li key={`${ref.path}:${ref.start_line}:${index}`} className="font-mono text-xs">
          <span className="text-neutral-700 dark:text-neutral-300">
            {ref.path}
            {ref.start_line ? `:${ref.start_line}` : ""}
          </span>
          {ref.excerpt && (
            <span className="ml-2 text-neutral-400 dark:text-neutral-500">
              {ref.excerpt.trim().slice(0, 70)}
            </span>
          )}
        </li>
      ))}
      {unique.length > limit && (
        <li className="text-xs text-neutral-400">+{unique.length - limit} more</li>
      )}
    </ul>
  );
}
