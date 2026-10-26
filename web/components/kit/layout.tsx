/**
 * Page structure: page headers, sections and cards.
 *
 * Sections group by whitespace and a heading; a Card is only for content that
 * genuinely stands on its own (an experience, a result, a selected component).
 */

import type { ElementType, ReactNode } from "react";

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  accent,
}: {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  /** The concept the page belongs to; colours the eyebrow only. */
  accent?: "brand" | "ai" | "arch" | "run" | "exp";
}) {
  const accentText = {
    brand: "text-brand-fg",
    ai: "text-ai-fg",
    arch: "text-arch-fg",
    run: "text-run-fg",
    exp: "text-exp-fg",
  }[accent ?? "brand"];
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 max-w-3xl">
        {eyebrow && (
          <p className={`text-meta font-medium ${accentText} flex items-center gap-1.5`}>{eyebrow}</p>
        )}
        <h1 className="mt-1 text-page font-semibold tracking-tight text-fg">{title}</h1>
        {description && <p className="mt-2 text-body text-fg-secondary">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  description,
  action,
  children,
  className = "",
  id,
  as: Tag = "section",
  ...rest
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
  as?: ElementType;
} & Record<`data-${string}`, string | undefined>) {
  return (
    <Tag id={id} className={`scroll-mt-28 ${className}`} {...rest}>
      {(title || action) && (
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            {title && (
              <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">{title}</h2>
            )}
            {description && <p className="mt-1 text-sm text-fg-secondary">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </Tag>
  );
}

export function Card({
  children,
  className = "",
  as: Tag = "div",
  accent,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  as?: ElementType;
  /** A thin top rule in the colour of what the card is about. */
  accent?: "brand" | "ai" | "arch" | "run" | "exp" | "danger";
} & Record<`data-${string}` | `aria-${string}`, string | boolean | undefined>) {
  const rule = accent
    ? {
        brand: "border-t-brand",
        ai: "border-t-ai",
        arch: "border-t-arch",
        run: "border-t-run",
        exp: "border-t-exp",
        danger: "border-t-danger",
      }[accent] + " border-t-2"
    : "";
  return (
    <Tag className={`rounded-lg border border-line bg-surface ${rule} ${className}`} {...rest}>
      {children}
    </Tag>
  );
}

/** A dark surface for code, terminals and consoles: dark in both themes. */
export function CodeSurface({
  label,
  actions,
  children,
  className = "",
}: {
  label?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`min-w-0 overflow-hidden rounded-lg border border-code-line bg-code text-code-fg ${className}`}>
      {(label || actions) && (
        <div className="flex items-center justify-between gap-3 border-b border-code-line px-3 py-2">
          <span className="font-mono text-xs text-code-muted">{label}</span>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

/** A labelled, measured figure. Only real values belong here. */
export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="mt-0.5 font-mono text-card font-semibold tabular-nums text-fg">{value}</dd>
      {hint && <p className="mt-0.5 text-xs text-fg-muted">{hint}</p>}
    </div>
  );
}
