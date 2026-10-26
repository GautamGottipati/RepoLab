/**
 * Buttons: one primary per section, everything else secondary or ghost.
 *
 * A `tone` tints a secondary button with the colour of what it leads to
 * (run = experience, ai = Ask, arch = Architecture, exp = experiment), so the
 * same action looks the same everywhere. Tone never replaces the label.
 */

import Link from "next/link";
import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";

export type Variant = "primary" | "secondary" | "ghost";
export type Tone = "neutral" | "brand" | "run" | "ai" | "arch" | "exp" | "danger";
export type Size = "sm" | "md";

const BASE =
  "inline-flex max-w-full items-center justify-center gap-1.5 rounded-md text-left font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50";

const SIZES: Record<Size, string> = {
  sm: "min-h-8 px-3 py-1 text-meta",
  md: "min-h-9 px-4 py-1.5 text-sm",
};

/** Solid fills: the one obvious next action. */
const SOLID: Record<Tone, string> = {
  neutral: "bg-fg text-canvas hover:opacity-90",
  brand: "bg-brand text-white hover:bg-brand-hover",
  run: "bg-run-fg text-white hover:opacity-90 dark:bg-run dark:text-slate-950",
  ai: "bg-ai-fg text-white hover:opacity-90 dark:bg-ai dark:text-white",
  arch: "bg-arch-fg text-white hover:opacity-90 dark:bg-arch dark:text-slate-950",
  exp: "bg-exp-fg text-white hover:opacity-90 dark:bg-exp dark:text-slate-950",
  danger: "bg-danger-fg text-white hover:opacity-90 dark:bg-danger dark:text-white",
};

/** Outlined: secondary actions, tinted by what they lead to. */
const OUTLINE: Record<Tone, string> = {
  neutral: "border border-line-strong bg-surface text-fg hover:bg-surface-hover",
  brand: "border border-brand-line bg-surface text-brand-fg hover:bg-brand-soft",
  run: "border border-run-line bg-surface text-run-fg hover:bg-run-soft",
  ai: "border border-ai-line bg-surface text-ai-fg hover:bg-ai-soft",
  arch: "border border-arch-line bg-surface text-arch-fg hover:bg-arch-soft",
  exp: "border border-exp-line bg-surface text-exp-fg hover:bg-exp-soft",
  danger: "border border-danger-line bg-surface text-danger-fg hover:bg-danger-soft",
};

const GHOST: Record<Tone, string> = {
  neutral: "text-fg-secondary hover:bg-surface-hover hover:text-fg",
  brand: "text-brand-fg hover:bg-brand-soft",
  run: "text-run-fg hover:bg-run-soft",
  ai: "text-ai-fg hover:bg-ai-soft",
  arch: "text-arch-fg hover:bg-arch-soft",
  exp: "text-exp-fg hover:bg-exp-soft",
  danger: "text-danger-fg hover:bg-danger-soft",
};

export function buttonClass({
  variant = "secondary",
  tone,
  size = "md",
  className = "",
}: {
  variant?: Variant;
  tone?: Tone;
  size?: Size;
  className?: string;
}): string {
  const resolved = tone ?? (variant === "primary" ? "brand" : "neutral");
  const look =
    variant === "primary" ? SOLID[resolved] : variant === "ghost" ? GHOST[resolved] : OUTLINE[resolved];
  return `${BASE} ${SIZES[size]} ${look} ${className}`.trim();
}

type Common = { variant?: Variant; tone?: Tone; size?: Size; icon?: ReactNode; trailing?: ReactNode };

export function Button({
  variant,
  tone,
  size,
  icon,
  trailing,
  className,
  children,
  type = "button",
  ...rest
}: Common & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type={type} className={buttonClass({ variant, tone, size, className })} {...rest}>
      {icon}
      {children}
      {trailing}
    </button>
  );
}

export function ButtonLink({
  variant,
  tone,
  size,
  icon,
  trailing,
  className,
  children,
  ...rest
}: Common & ComponentProps<typeof Link>) {
  return (
    <Link className={buttonClass({ variant, tone, size, className })} {...rest}>
      {icon}
      {children}
      {trailing}
    </Link>
  );
}
