/**
 * The approval gate, as the user sees it.
 *
 * Three things on this card are not optional, and each exists because of a
 * rule in CLAUDE.md:
 *
 * - **The cost, before the decision.** Runs and estimated minutes, labelled an
 *   estimate.
 * - **What validation changed.** Dropped metrics and adjustments, so approval
 *   is given to the experiment that will actually run rather than the one that
 *   was proposed.
 * - **Both buttons, equally weighted.** Declining is a real choice.
 *
 * The gate itself is enforced server-side. This card cannot bypass it, and must
 * not imply it has by optimistically showing progress.
 */

"use client";

import { useState } from "react";

import { Badge, Stat } from "./ui";
import type { ExperimentPlan } from "@/lib/types";

export function ApprovalCard({
  plan,
  onApprove,
  onReject,
}: {
  plan: ExperimentPlan;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
}) {
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);

  const decide = async (choice: "approve" | "reject") => {
    setDeciding(choice);
    try {
      await (choice === "approve" ? onApprove() : onReject());
    } finally {
      setDeciding(null);
    }
  };

  return (
    <section
      data-testid="approval-card"
      className="rounded-lg border-2 border-violet-400 bg-violet-50/40 dark:border-violet-800 dark:bg-violet-950/20"
    >
      <header className="flex items-center gap-2 border-b border-violet-200 px-4 py-3 dark:border-violet-900">
        <h2 className="text-sm font-semibold">RepoLab wants to run an experiment</h2>
        <Badge tone="attention">needs your approval</Badge>
      </header>

      <div className="flex flex-col gap-4 p-4">
        <div>
          <p className="text-xs text-neutral-500">Question</p>
          <p className="text-sm font-medium">{plan.question}</p>
        </div>
        <div>
          <p className="text-xs text-neutral-500">Hypothesis</p>
          <p className="text-sm">{plan.hypothesis}</p>
        </div>

        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Variable" value={plan.variable} />
          <Stat label="Repeats" value={String(plan.repeats)} />
          <Stat label="Metrics" value={String(plan.metrics.length)} />
          <Stat
            label="Execution"
            value={plan.concurrency > 1 ? `${plan.concurrency} at once` : "sequential"}
            hint={
              plan.concurrency > 1
                ? "Timings will be marked contended"
                : "So timings are comparable"
            }
          />
        </dl>

        <div>
          <p className="text-xs text-neutral-500">Values</p>
          <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="plan-values">
            {plan.values.map((value) => (
              <li
                key={value}
                className={`rounded px-2 py-0.5 font-mono text-sm ${
                  value === plan.baseline_value
                    ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
                    : "bg-neutral-100 dark:bg-neutral-900"
                }`}
              >
                {value}
                {value === plan.baseline_value && (
                  <span className="ml-1 text-[10px] uppercase">baseline</span>
                )}
              </li>
            ))}
          </ul>
        </div>

        {/* The cost, stated before the decision. */}
        <p
          data-testid="plan-cost"
          className="rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
        >
          <strong className="font-mono">{plan.estimated_runs}</strong> Spark jobs ·
          about <strong className="font-mono">{plan.estimated_minutes}</strong> min
          <span className="ml-2 text-xs text-neutral-500">
            (estimated from a measured per-run baseline)
          </span>
        </p>

        <details className="text-xs">
          <summary className="cursor-pointer text-neutral-500">
            Held fixed across every variant ({Object.keys(plan.fixed_settings).length})
          </summary>
          <dl className="mt-2 grid gap-1 font-mono sm:grid-cols-2">
            {Object.entries(plan.fixed_settings).map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <dt className="text-neutral-500">{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </details>

        {plan.dropped_metrics.length > 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-300">
            <strong>Not measurable:</strong> {plan.dropped_metrics.join(", ")} — requested
            but RepoLab has no instrument for it, so it was dropped from the plan.
          </p>
        )}

        {plan.adjustments.length > 0 && (
          <div className="text-xs text-neutral-500 dark:text-neutral-400">
            <p className="font-medium">Adjusted by validation</p>
            <ul className="mt-1 list-disc pl-4">
              {plan.adjustments.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap gap-3 border-t border-violet-200 pt-4 dark:border-violet-900">
          <button
            type="button"
            onClick={() => decide("approve")}
            disabled={deciding !== null}
            data-testid="approve-experiment"
            className="rounded bg-violet-700 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {deciding === "approve" ? "Approving…" : "Approve experiment"}
          </button>
          <button
            type="button"
            onClick={() => decide("reject")}
            disabled={deciding !== null}
            data-testid="reject-experiment"
            className="rounded border border-neutral-400 px-5 py-2 text-sm font-medium dark:border-neutral-600"
          >
            {deciding === "reject" ? "Declining…" : "Decline"}
          </button>
          <p className="self-center text-xs text-neutral-500">
            Nothing runs until you choose.
          </p>
        </div>
      </div>
    </section>
  );
}
