/**
 * Status and metadata.
 *
 * A status is always a dot *and* a word, so it never depends on colour alone.
 * Badges label metadata (Beginner, Public, Measured); they are not decoration.
 */

import type { ReactNode } from "react";

export type Status =
  | "live"
  | "ready"
  | "success"
  | "preparing"
  | "building"
  | "running"
  | "attention"
  | "failed"
  | "unavailable"
  | "unknown";

const STATUS: Record<Status, { dot: string; text: string; pulse?: boolean }> = {
  live: { dot: "bg-run", text: "text-run-fg", pulse: true },
  ready: { dot: "bg-run", text: "text-run-fg" },
  success: { dot: "bg-run", text: "text-run-fg" },
  preparing: { dot: "bg-exp", text: "text-exp-fg", pulse: true },
  building: { dot: "bg-exp", text: "text-exp-fg", pulse: true },
  running: { dot: "bg-exp", text: "text-exp-fg", pulse: true },
  attention: { dot: "bg-exp", text: "text-exp-fg" },
  failed: { dot: "bg-danger", text: "text-danger-fg" },
  unavailable: { dot: "bg-fg-muted", text: "text-fg-secondary" },
  unknown: { dot: "bg-fg-muted", text: "text-fg-secondary" },
};

export function StatusBadge({
  status,
  children,
  className = "",
  ...rest
}: {
  status: Status;
  children: ReactNode;
  className?: string;
} & Record<`data-${string}`, string | undefined>) {
  const look = STATUS[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-meta font-medium ${look.text} ${className}`}
      data-state={status}
      {...rest}
    >
      <span aria-hidden className="relative flex size-2">
        {look.pulse && (
          <span className={`absolute inline-flex size-full animate-ping rounded-full opacity-60 ${look.dot}`} />
        )}
        <span className={`relative inline-flex size-2 rounded-full ${look.dot}`} />
      </span>
      {children}
    </span>
  );
}

export type BadgeTone =
  | "neutral"
  | "live"
  | "good"
  | "bad"
  | "attention"
  | "brand"
  | "ai"
  | "arch"
  | "run"
  | "exp"
  | "danger";

const BADGE: Record<BadgeTone, string> = {
  neutral: "bg-surface-subtle text-fg-secondary border-line",
  brand: "bg-brand-soft text-brand-fg border-brand-line",
  ai: "bg-ai-soft text-ai-fg border-ai-line",
  arch: "bg-arch-soft text-arch-fg border-arch-line",
  run: "bg-run-soft text-run-fg border-run-line",
  exp: "bg-exp-soft text-exp-fg border-exp-line",
  danger: "bg-danger-soft text-danger-fg border-danger-line",
  // Older names, kept so existing call sites keep their meaning.
  live: "bg-run-soft text-run-fg border-run-line",
  good: "bg-run-soft text-run-fg border-run-line",
  bad: "bg-danger-soft text-danger-fg border-danger-line",
  attention: "bg-ai-soft text-ai-fg border-ai-line",
};

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium ${BADGE[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
