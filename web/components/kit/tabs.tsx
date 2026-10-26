/**
 * Accessible tabs: role=tablist, roving focus, arrow keys, Home/End.
 *
 * Controlled: the caller owns which tab is selected, so it can be kept in the
 * URL or derived from state.
 */

"use client";

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export type TabItem = { id: string; label: ReactNode; count?: number; disabled?: boolean };

export function Tabs({
  items,
  value,
  onChange,
  label,
  size = "md",
  children,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
  size?: "sm" | "md";
  /** The selected panel's content. */
  children?: ReactNode;
}) {
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const enabled = items.filter((item) => !item.disabled);

  const onKey = (event: KeyboardEvent) => {
    const index = enabled.findIndex((item) => item.id === value);
    let next = -1;
    if (event.key === "ArrowRight") next = (index + 1) % enabled.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + enabled.length) % enabled.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = enabled.length - 1;
    if (next < 0) return;
    event.preventDefault();
    const target = enabled[next];
    onChange(target.id);
    refs.current[items.indexOf(target)]?.focus();
  };

  return (
    <div className="min-w-0">
      <div
        role="tablist"
        aria-label={label}
        onKeyDown={onKey}
        className="flex gap-1 overflow-x-auto border-b border-line"
      >
        {items.map((item, index) => {
          const selected = item.id === value;
          return (
            <button
              key={item.id}
              ref={(node) => {
                refs.current[index] = node;
              }}
              type="button"
              role="tab"
              id={`${base}-tab-${item.id}`}
              aria-selected={selected}
              aria-controls={`${base}-panel`}
              tabIndex={selected ? 0 : -1}
              disabled={item.disabled}
              onClick={() => onChange(item.id)}
              data-testid={`tabs-${item.id}`}
              className={`-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 font-medium transition-colors duration-150 disabled:opacity-40 ${
                size === "sm" ? "px-2.5 py-1.5 text-meta" : "px-3 py-2 text-sm"
              } ${
                selected
                  ? "border-brand text-fg"
                  : "border-transparent text-fg-secondary hover:text-fg"
              }`}
            >
              {item.label}
              {item.count !== undefined && (
                <span className="rounded-full bg-surface-subtle px-1.5 text-xs text-fg-muted">{item.count}</span>
              )}
            </button>
          );
        })}
      </div>
      {children !== undefined && (
        <div
          role="tabpanel"
          id={`${base}-panel`}
          aria-labelledby={`${base}-tab-${value}`}
          className="pt-3"
        >
          {children}
        </div>
      )}
    </div>
  );
}

/** Filter chips: a single-choice group that reads as filters, not navigation. */
export function Chips({
  items,
  value,
  onChange,
  label,
}: {
  items: { id: string; label: ReactNode }[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(item.id)}
            data-testid={`chip-${item.id}`}
            className={`h-8 rounded-full border px-3 text-meta font-medium transition-colors duration-150 ${
              selected
                ? "border-brand bg-brand-soft text-brand-fg"
                : "border-line bg-surface text-fg-secondary hover:border-line-strong hover:text-fg"
            }`}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
