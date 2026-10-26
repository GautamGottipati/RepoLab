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

import { Badge, Stat, buttonClass } from "./ui";
import type { CliExperimentPlan, ExperimentPlan } from "@/lib/types";

export function ApprovalCard({
  plan,
  onApprove,
  onReject,
}: {
  plan: ExperimentPlan;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
}) {
  return (
    <section
      data-testid="approval-card"
      className="rounded-lg border border-exp-line border-t-2 border-t-exp bg-surface"
    >
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="text-card font-semibold text-exp-fg">RepoLab wants to run an experiment</h2>
        <Badge tone="attention">needs your approval</Badge>
      </header>

      <div className="flex flex-col gap-4 p-4">
        <div>
          <p className="text-xs text-fg-secondary">Question</p>
          <p className="text-sm font-medium">{plan.question}</p>
        </div>
        <div>
          <p className="text-xs text-fg-secondary">Hypothesis</p>
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
          <p className="text-xs text-fg-secondary">Values</p>
          <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="plan-values">
            {plan.values.map((value) => (
              <li
                key={value}
                className={`rounded px-2 py-0.5 font-mono text-sm ${
                  value === plan.baseline_value
                    ? "bg-brand text-white"
                    : "bg-surface-subtle"
                }`}
              >
                {value}
                {value === plan.baseline_value && (
                  <span className="ml-1 text-xs uppercase">baseline</span>
                )}
              </li>
            ))}
          </ul>
        </div>

        {/* The cost, stated before the decision. */}
        <p
          data-testid="plan-cost"
          className="rounded border border-line-strong bg-surface px-3 py-2 text-sm"
        >
          <strong className="font-mono">{plan.estimated_runs}</strong> Spark jobs ·
          about <strong className="font-mono">{plan.estimated_minutes}</strong> min
          <span className="ml-2 text-xs text-fg-secondary">
            (estimated from a measured per-run baseline)
          </span>
        </p>

        <details className="text-xs">
          <summary className="cursor-pointer text-fg-secondary">
            Held fixed across every variant ({Object.keys(plan.fixed_settings).length})
          </summary>
          <dl className="mt-2 grid gap-1 font-mono sm:grid-cols-2">
            {Object.entries(plan.fixed_settings).map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <dt className="text-fg-secondary">{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </details>

        {plan.dropped_metrics.length > 0 && (
          <p className="text-xs text-exp-fg">
            <strong>Not measurable:</strong> {plan.dropped_metrics.join(", ")} — requested
            but RepoLab has no instrument for it, so it was dropped from the plan.
          </p>
        )}

        {plan.adjustments.length > 0 && (
          <div className="text-xs text-fg-secondary">
            <p className="font-medium">Adjusted by validation</p>
            <ul className="mt-1 list-disc pl-4">
              {plan.adjustments.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        )}

        <DecisionButtons onApprove={onApprove} onReject={onReject} />
      </div>
    </section>
  );
}

/**
 * The same gate for an experiment on a CLI: the statements that will run, on
 * which build, how they are isolated, and what it costs — before the decision.
 */
export function CliApprovalCard({
  plan,
  onApprove,
  onReject,
}: {
  plan: CliExperimentPlan;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
}) {
  return (
    <section
      data-testid="approval-card"
      className="rounded-lg border border-exp-line border-t-2 border-t-exp bg-surface"
    >
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="text-card font-semibold text-exp-fg">RepoLab wants to run an experiment</h2>
        <Badge tone="attention">needs your approval</Badge>
      </header>

      <div className="flex flex-col gap-4 p-4">
        <div>
          <p className="text-xs text-fg-secondary">Question</p>
          <p className="text-sm font-medium">{plan.question}</p>
        </div>
        <div>
          <p className="text-xs text-fg-secondary">Hypothesis</p>
          <p className="text-sm">{plan.hypothesis}</p>
        </div>

        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Runs on" value={plan.environment.version ?? "—"} hint={plan.environment.build} />
          <Stat label="Fidelity" value={plan.environment.fidelity ?? "—"} />
          <Stat label="Variants" value={String(plan.variants.length)} />
          <Stat label="Repeats" value={String(plan.repeats)} />
        </dl>

        {plan.setup.length > 0 && (
          <div>
            <p className="text-xs text-fg-secondary">Setup, run before every variant</p>
            <pre className="mt-1 overflow-x-auto rounded bg-surface-subtle p-2 font-mono text-xs">
              {plan.setup.join(";\n")};
            </pre>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2" data-testid="plan-variants">
          {plan.variants.map((variant) => (
            <div
              key={variant.label}
              className="rounded border border-line-strong bg-surface p-3"
            >
              <p className="text-xs font-semibold">{variant.label}</p>
              <pre className="mt-1 overflow-x-auto font-mono text-xs text-fg">
                {variant.statements.join(";\n")};
              </pre>
            </div>
          ))}
        </div>

        <p
          data-testid="plan-cost"
          className="rounded border border-line-strong bg-surface px-3 py-2 text-sm"
        >
          Starts a <strong className="font-mono">{plan.environment.memory || "4g"}</strong> node ·{" "}
          <strong className="font-mono">{plan.estimated_statements}</strong> CQL statements · about{" "}
          <strong className="font-mono">{plan.estimated_minutes}</strong> min
          <span className="ml-2 text-xs text-fg-secondary">(estimated)</span>
        </p>

        <details className="text-xs">
          <summary className="cursor-pointer text-fg-secondary">
            Held fixed across every variant ({Object.keys(plan.fixed_settings).length})
          </summary>
          <dl className="mt-2 grid gap-1 font-mono sm:grid-cols-2">
            {Object.entries(plan.fixed_settings).map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <dt className="text-fg-secondary">{key}</dt>
                <dd className="break-all">{value}</dd>
              </div>
            ))}
          </dl>
        </details>

        {plan.adjustments.length > 0 && (
          <div className="text-xs text-fg-secondary">
            <p className="font-medium">Adjusted by validation</p>
            <ul className="mt-1 list-disc pl-4">
              {plan.adjustments.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        )}

        <DecisionButtons onApprove={onApprove} onReject={onReject} />
      </div>
    </section>
  );
}

/** Both choices, equally weighted. The gate itself is enforced server-side. */
function DecisionButtons({
  onApprove,
  onReject,
}: {
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
    <div className="flex flex-wrap gap-3 border-t border-line pt-4">
      <button
        type="button"
        onClick={() => decide("approve")}
        disabled={deciding !== null}
        data-testid="approve-experiment"
        className={buttonClass({ variant: "primary", tone: "exp" })}
      >
        {deciding === "approve" ? "Approving…" : "Approve experiment"}
      </button>
      <button
        type="button"
        onClick={() => decide("reject")}
        disabled={deciding !== null}
        data-testid="reject-experiment"
        className={buttonClass({ variant: "secondary" })}
      >
        {deciding === "reject" ? "Declining…" : "Decline"}
      </button>
      <p className="self-center text-xs text-fg-secondary">Nothing runs until you choose.</p>
    </div>
  );
}
