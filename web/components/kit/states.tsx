/**
 * Empty, error and progress states.
 *
 * Every empty state says what happened, why, and what to do next. Every error
 * says what failed, what still worked, and what the user can do. Progress shows
 * the real steps the backend reported -- never an invented one.
 */

import type { ReactNode } from "react";

import { CheckIcon, CrossIcon } from "./icons";

export function EmptyState({
  title,
  children,
  actions,
  icon,
  compact = false,
  ...rest
}: {
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  compact?: boolean;
} & Record<`data-${string}`, string | undefined>) {
  return (
    <div
      className={`rounded-lg border border-dashed border-line-strong text-center ${
        compact ? "px-4 py-6" : "px-6 py-10"
      }`}
      {...rest}
    >
      {icon && <div className="mx-auto mb-3 flex justify-center text-fg-muted">{icon}</div>}
      <p className="text-sm font-medium text-fg">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-fg-secondary">{children}</div>}
      {actions && <div className="mt-4 flex flex-wrap justify-center gap-2">{actions}</div>}
    </div>
  );
}

export type StepState = "done" | "current" | "upcoming" | "failed";

export type ProgressStep = {
  key: string;
  label: ReactNode;
  state: StepState;
  detail?: ReactNode;
};

/** A list of real steps: done ✓, current (pulsing), upcoming ○, failed ✕. */
export function ProgressSteps({
  steps,
  label,
  onSelect,
  ...rest
}: {
  steps: ProgressStep[];
  label: string;
  /** When given, steps are buttons (a guide the user can move around freely). */
  onSelect?: (key: string) => void;
} & Record<`data-${string}`, string | undefined>) {
  return (
    <ol className="flex flex-col gap-0.5" aria-label={label} {...rest}>
      {steps.map((step, position) => {
        const body = (
          <>
            <StepMarker state={step.state} number={position + 1} />
            <span className="min-w-0">
              <span
                className={`block text-sm ${
                  step.state === "current"
                    ? "font-medium text-fg"
                    : step.state === "upcoming"
                      ? "text-fg-muted"
                      : step.state === "failed"
                        ? "text-danger-fg"
                        : "text-fg-secondary"
                }`}
              >
                {step.label}
              </span>
              {step.detail && <span className="mt-0.5 block text-xs text-fg-muted">{step.detail}</span>}
            </span>
          </>
        );
        return (
          <li
            key={step.key}
            data-state={step.state}
            aria-current={step.state === "current" ? "step" : undefined}
          >
            {onSelect ? (
              <button
                type="button"
                onClick={() => onSelect(step.key)}
                className={`flex w-full items-start gap-3 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-hover ${
                  step.state === "current" ? "bg-surface-subtle" : ""
                }`}
              >
                {body}
              </button>
            ) : (
              <div className="flex items-start gap-3 px-2 py-1.5">{body}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function StepMarker({ state, number }: { state: StepState; number: number }) {
  const common = "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold";
  if (state === "done")
    return (
      <span className={`${common} bg-run-soft text-run-fg`}>
        <CheckIcon size={12} />
        <span className="sr-only">done</span>
      </span>
    );
  if (state === "failed")
    return (
      <span className={`${common} bg-danger-soft text-danger-fg`}>
        <CrossIcon size={12} />
        <span className="sr-only">failed</span>
      </span>
    );
  if (state === "current")
    return (
      <span className={`${common} bg-brand text-white`}>
        {number}
        <span className="sr-only">in progress</span>
      </span>
    );
  return <span className={`${common} border border-line-strong text-fg-muted`}>{number}</span>;
}

/**
 * A failure, explained: what failed, what still worked, why, what to do.
 */
export function ErrorState({
  title,
  reason,
  succeeded = [],
  failed,
  actions,
  technical,
}: {
  title: ReactNode;
  reason?: ReactNode;
  succeeded?: ReactNode[];
  failed?: ReactNode;
  actions?: ReactNode;
  technical?: string;
}) {
  return (
    <div
      role="alert"
      data-testid="error-state"
      className="rounded-lg border border-danger-line bg-danger-soft px-4 py-3"
    >
      <p className="text-sm font-semibold text-danger-fg">{title}</p>
      {(succeeded.length > 0 || failed) && (
        <ul className="mt-2 flex flex-col gap-1 text-sm">
          {succeeded.map((item, index) => (
            <li key={index} className="flex items-center gap-2 text-fg-secondary">
              <CheckIcon size={14} className="text-run-fg" />
              {item}
            </li>
          ))}
          {failed && (
            <li className="flex items-center gap-2 font-medium text-danger-fg">
              <CrossIcon size={14} />
              {failed}
            </li>
          )}
        </ul>
      )}
      {reason && <p className="mt-2 text-sm text-fg-secondary">{reason}</p>}
      {technical && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-danger-fg">Technical detail</summary>
          <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md bg-code p-2 font-mono text-xs text-code-fg">
            {technical}
          </pre>
        </details>
      )}
      {actions && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
