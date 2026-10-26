/**
 * A drawer: from the right on wide screens, a bottom sheet on narrow ones.
 *
 * Modal while open -- focus moves in, Tab stays inside, Escape closes, and
 * focus returns to whatever opened it.
 */

"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

import { CrossIcon } from "./icons";

export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  width = "md",
  ...rest
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  width?: "md" | "lg";
} & Record<`data-${string}`, string | undefined>) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    focusable()[0]?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40" {...rest}>
      <div
        className="absolute inset-0 bg-slate-950/40 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden
        data-testid="drawer-backdrop"
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`absolute inset-x-0 bottom-0 flex max-h-[85vh] flex-col rounded-t-xl border border-line bg-surface shadow-lg sm:inset-y-0 sm:right-0 sm:left-auto sm:max-h-none sm:rounded-none sm:rounded-l-xl ${
          width === "lg" ? "sm:w-[36rem]" : "sm:w-[26rem]"
        }`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="text-card font-semibold text-fg">
              {title}
            </h2>
            {description && <p className="mt-0.5 text-sm text-fg-secondary">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            data-testid="drawer-close"
            className="flex size-8 items-center justify-center rounded-md text-fg-secondary hover:bg-surface-hover hover:text-fg"
          >
            <CrossIcon />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
