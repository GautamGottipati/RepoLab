/**
 * The five contextual actions, identical wherever they appear:
 *
 *   Ask / Explain     violet   AskIcon
 *   Architecture      cyan     ArchIcon
 *   Run / Experience  emerald  RunIcon
 *   Experiment        amber    ExperimentIcon
 *   Source            neutral  SourceIcon
 *
 * Icon + text + colour: never colour alone.
 */

import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

import { buttonClass, type Size, type Tone, type Variant } from "./button";
import { ArchIcon, AskIcon, ExperimentIcon, RunIcon, SourceIcon } from "./icons";

export type ActionKind = "ask" | "arch" | "run" | "experiment" | "source";

const KINDS: Record<ActionKind, { tone: Tone; icon: (size: number) => ReactNode }> = {
  ask: { tone: "ai", icon: (s) => <AskIcon size={s} /> },
  arch: { tone: "arch", icon: (s) => <ArchIcon size={s} /> },
  run: { tone: "run", icon: (s) => <RunIcon size={s} /> },
  experiment: { tone: "exp", icon: (s) => <ExperimentIcon size={s} /> },
  source: { tone: "neutral", icon: (s) => <SourceIcon size={s} /> },
};

export function actionIcon(kind: ActionKind, size = 14): ReactNode {
  return KINDS[kind].icon(size);
}

export function ActionLink({
  kind,
  variant = "secondary",
  size = "sm",
  className,
  children,
  external = false,
  ...rest
}: {
  kind: ActionKind;
  variant?: Variant;
  size?: Size;
  external?: boolean;
} & ComponentProps<typeof Link>) {
  const look = KINDS[kind];
  const classes = buttonClass({ variant, tone: look.tone, size, className });
  if (external) {
    return (
      <a
        className={classes}
        href={String(rest.href)}
        target="_blank"
        rel="noreferrer"
        data-testid={(rest as Record<string, unknown>)["data-testid"] as string | undefined}
      >
        {look.icon(14)}
        {children}
      </a>
    );
  }
  return (
    <Link className={classes} {...rest}>
      {look.icon(14)}
      {children}
    </Link>
  );
}

export function ActionButton({
  kind,
  variant = "secondary",
  size = "sm",
  className,
  children,
  type = "button",
  ...rest
}: {
  kind: ActionKind;
  variant?: Variant;
  size?: Size;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const look = KINDS[kind];
  return (
    <button type={type} className={buttonClass({ variant, tone: look.tone, size, className })} {...rest}>
      {look.icon(14)}
      {children}
    </button>
  );
}
