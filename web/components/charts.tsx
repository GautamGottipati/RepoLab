/**
 * Charts, drawn directly in SVG.
 *
 * No chart library: the shapes needed are bars and a comparison, incremental
 * updates are ordinary state appends, and a third-party React dependency would
 * be one more thing to validate against React 19 for no gain here.
 *
 * Every chart is labelled with its `determinism`, because a wall-clock bar and
 * a partition-count bar do not mean the same thing, and drawing them
 * identically would say they do.
 */

"use client";

import { DeterminismTag } from "./ui";

export type Bar = {
  /** The variant value this bar is for, e.g. a partition count. */
  key: number | string;
  value: number;
  label?: string;
  /** Rendered alongside the bar, e.g. a relative difference. */
  note?: string;
  baseline?: boolean;
  /** Still running: drawn as a placeholder rather than a zero-height bar. */
  pending?: boolean;
  /** Spread across repeats, drawn as a whisker. */
  spread?: number;
};

/**
 * A horizontal bar chart that grows as results arrive.
 *
 * Horizontal because the labels are numbers of varying width and the bars are
 * compared against each other, not read as a time series.
 */
export function BarChart({
  bars,
  unit,
  determinism,
  title,
  format = (value: number) => value.toLocaleString(),
  emptyMessage = "No results yet",
}: {
  bars: Bar[];
  unit?: string;
  determinism?: "structural" | "timing";
  title: string;
  format?: (value: number) => string;
  emptyMessage?: string;
}) {
  const max = Math.max(1, ...bars.filter((bar) => !bar.pending).map((bar) => bar.value));

  return (
    <figure data-testid={`chart-${title.replace(/\s+/g, "-").toLowerCase()}`}>
      <figcaption className="mb-2 flex items-center gap-2">
        <span className="text-xs font-semibold">{title}</span>
        {unit && <span className="text-xs text-neutral-500">({unit})</span>}
        {determinism && <DeterminismTag kind={determinism} />}
      </figcaption>

      {bars.length === 0 ? (
        <p className="py-4 text-xs text-neutral-500">{emptyMessage}</p>
      ) : (
        <div className="flex flex-col gap-1">
          {bars.map((bar) => {
            const width = bar.pending ? 0 : Math.max(1, (bar.value / max) * 100);
            return (
              <div
                key={bar.key}
                data-testid={`bar-${bar.key}`}
                data-pending={bar.pending ? "true" : "false"}
                className="flex items-center gap-2 text-xs"
              >
                <span
                  className={`w-14 shrink-0 text-right font-mono ${
                    bar.baseline ? "font-semibold" : "text-neutral-500"
                  }`}
                >
                  {bar.label ?? bar.key}
                </span>

                <div className="relative h-5 flex-1 overflow-hidden rounded bg-neutral-100 dark:bg-neutral-900">
                  {bar.pending ? (
                    <div className="h-full w-full animate-pulse bg-neutral-200 dark:bg-neutral-800" />
                  ) : (
                    <>
                      <div
                        className={`h-full rounded transition-[width] duration-500 ${
                          bar.baseline
                            ? "bg-neutral-700 dark:bg-neutral-300"
                            : "bg-sky-500/80 dark:bg-sky-400/80"
                        }`}
                        style={{ width: `${width}%` }}
                      />
                      {bar.spread != null && bar.spread > 0 && (
                        <div
                          aria-hidden
                          title={`spread ${format(bar.spread)}`}
                          className="absolute top-1/2 h-2 -translate-y-1/2 border-x border-neutral-500/70"
                          style={{
                            left: `${width}%`,
                            width: `${Math.min(12, (bar.spread / max) * 100)}%`,
                          }}
                        />
                      )}
                    </>
                  )}
                </div>

                <span className="w-24 shrink-0 text-right font-mono">
                  {bar.pending ? "…" : format(bar.value)}
                </span>
                <span
                  className={`w-16 shrink-0 text-right font-mono ${
                    bar.baseline ? "text-neutral-500" : "text-neutral-400"
                  }`}
                >
                  {bar.note ?? ""}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </figure>
  );
}

/**
 * A proportion, as a single bar.
 *
 * Used for empty partitions: 193 of 200 is the finding the whole demo turns on,
 * and a number in a table does not land the way a filled bar does.
 */
export function ProportionBar({
  part,
  whole,
  partLabel,
  wholeLabel,
  tone = "warn",
}: {
  part: number;
  whole: number;
  partLabel: string;
  wholeLabel: string;
  tone?: "warn" | "neutral";
}) {
  const fraction = whole > 0 ? part / whole : 0;
  return (
    <div data-testid="proportion-bar">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">{partLabel}</span>
        <span className="font-mono">
          {part.toLocaleString()} / {whole.toLocaleString()} ({(fraction * 100).toFixed(1)}%)
        </span>
      </div>
      <div className="mt-1 h-3 overflow-hidden rounded bg-neutral-100 dark:bg-neutral-900">
        <div
          className={`h-full rounded transition-[width] duration-500 ${
            tone === "warn" ? "bg-amber-500/80" : "bg-sky-500/80"
          }`}
          style={{ width: `${Math.min(100, fraction * 100)}%` }}
        />
      </div>
      <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">{wholeLabel}</p>
    </div>
  );
}

/** A sparkline of repeated samples, so a timing's variation is visible. */
export function Sparkline({ samples, width = 90, height = 20 }: {
  samples: number[];
  width?: number;
  height?: number;
}) {
  if (samples.length < 2) return null;

  const min = Math.min(...samples);
  const max = Math.max(...samples);
  const span = max - min || 1;
  const points = samples
    .map((sample, index) => {
      const x = (index / (samples.length - 1)) * width;
      const y = height - ((sample - min) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${samples.length} samples from ${min} to ${max}`}
      className="text-neutral-400"
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
