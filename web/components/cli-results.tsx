/**
 * Results of an experiment run through the repository's real CLI.
 *
 * What matters here is what the server said, so each variant statement shows
 * whether it succeeded, the error class the server returned, the row count
 * cqlsh printed — and cqlsh's own output, verbatim. Durations are shown but
 * labelled: each one includes starting cqlsh itself, so they are weak evidence
 * next to an accepted-or-refused outcome.
 */

"use client";

import { ActivityFeed } from "./activity";
import { TriageNote } from "./answer-card";
import { ClaimList, EvidencePanel, UnsupportedClaims } from "./evidence";
import { Badge, Empty, ErrorNote, Panel, Stat } from "./ui";
import { count, ms, shortHash } from "@/lib/format";
import type { CliExperimentState, ExplanationView, JobEvent } from "@/lib/types";

export function CliResults({
  experiment,
  explanation,
  events,
  error,
  finished,
}: {
  experiment: CliExperimentState;
  explanation: ExplanationView | null;
  events: JobEvent[];
  error: string | null;
  finished: boolean;
}) {
  const plan = experiment.plan;
  const statistics = experiment.statistics;
  const evidence = explanation?.evidence ?? experiment.evidence;

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={experiment.question}
        subtitle={
          plan
            ? `Comparing ${plan.variants.length} variants on ${plan.environment.version ?? "the built node"}`
            : undefined
        }
        action={<Badge tone={finished ? "good" : "live"}>{experiment.status}</Badge>}
      >
        <div className="flex flex-col gap-4">
          {experiment.triage && <TriageNote triage={experiment.triage} />}
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat
              label="Runs"
              value={`${count(experiment.variant_runs)}${plan ? ` / ${plan.estimated_runs}` : ""}`}
              emphasis
            />
            <Stat label="Measurements" value={count(experiment.measurements)} />
            <Stat label="Repeats" value={plan ? String(plan.repeats) : "—"} />
            <Stat label="Commit" value={shortHash(experiment.commit_sha)} />
          </dl>
          {plan && (
            <p className="text-sm text-neutral-600 dark:text-neutral-400">
              <span className="font-medium">Hypothesis:</span> {plan.hypothesis}
            </p>
          )}
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}

      <Panel
        title="What came back"
        subtitle={
          finished
            ? "Parsed by Python from cqlsh's own output, per variant statement"
            : "Filling in as each variant completes"
        }
      >
        {statistics && statistics.variants.length > 0 ? (
          <div className="flex flex-col gap-5" data-testid="cli-results">
            {statistics.variants.map((variant) => (
              <div key={variant.variant_id}>
                <h4 className="text-sm font-semibold">
                  {variant.label}
                  <span className="ml-2 text-xs font-normal text-neutral-500">
                    {variant.runs} run{variant.runs === 1 ? "" : "s"}
                    {variant.failed_statements > 0 &&
                      ` · ${variant.failed_statements} statement failure(s)`}
                  </span>
                </h4>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full border-collapse text-left text-xs">
                    <thead>
                      <tr className="border-b border-neutral-200 text-neutral-500 dark:border-neutral-800">
                        <th className="py-1 pr-4 font-medium">statement</th>
                        <th className="py-1 pr-4 font-medium">succeeded</th>
                        <th className="py-1 pr-4 font-medium">rows</th>
                        <th className="py-1 pr-4 font-medium">server error</th>
                        <th className="py-1 font-medium" title="Includes starting cqlsh">
                          median time*
                        </th>
                      </tr>
                    </thead>
                    <tbody className="font-mono">
                      {variant.statements.map((statement) => (
                        <tr
                          key={statement.index}
                          className="border-b border-neutral-100 align-top dark:border-neutral-900"
                        >
                          <td className="max-w-md py-1 pr-4 break-words">{statement.statement}</td>
                          <td className="py-1 pr-4">
                            <Badge
                              tone={
                                statement.succeeded_runs === statement.runs
                                  ? "good"
                                  : statement.succeeded_runs === 0
                                    ? "bad"
                                    : "attention"
                              }
                            >
                              {statement.succeeded_runs}/{statement.runs}
                            </Badge>
                          </td>
                          <td className="py-1 pr-4">
                            {statement.rows_median ?? "—"}
                            {!statement.rows_stable && (
                              <span className="ml-1 text-amber-600">varied</span>
                            )}
                          </td>
                          <td className="max-w-md py-1 pr-4 break-words text-red-700 dark:text-red-400">
                            {statement.error_classes.join(", ") || "—"}
                            {statement.error_message && (
                              <p className="mt-0.5 text-[10px] text-neutral-500">
                                {statement.error_message}
                              </p>
                            )}
                          </td>
                          <td className="py-1">{ms(statement.duration_ms.median)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
            <p className="text-xs text-neutral-400">
              * Each time is the wall clock for a whole cqlsh invocation, including starting
              cqlsh, so small differences between them are not meaningful.
            </p>
          </div>
        ) : (
          <Empty title="No variants have completed yet">
            The node is restored from its checkpoint first, which takes a minute or two.
          </Empty>
        )}

        {!finished && (
          <div className="mt-5">
            <ActivityFeed events={events} height="h-40" />
          </div>
        )}
      </Panel>

      {explanation && (
        <Panel
          title="Explanation"
          subtitle="Produced from the outputs above; every claim labelled by its evidence"
          action={
            explanation.explanation.grounded ? (
              <Badge tone="good">{explanation.explanation.checked_claims} claims verified</Badge>
            ) : (
              <Badge tone="bad">unverified claims</Badge>
            )
          }
        >
          <div className="flex flex-col gap-5">
            <UnsupportedClaims claims={explanation.explanation.unsupported} />
            <p className="text-sm leading-relaxed" data-testid="answer">
              {explanation.explanation.explanation.answer}
            </p>
            <ClaimList claims={explanation.explanation.explanation.claims} />
            {explanation.explanation.explanation.caveats.length > 0 && (
              <ul className="list-disc pl-5 text-sm text-neutral-600 dark:text-neutral-400">
                {explanation.explanation.explanation.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      )}

      {finished && !explanation && experiment.status === "completed" && (
        <Panel title="Explanation">
          <Empty title="The outputs stand alone">
            {experiment.explanation_error ??
              "No model is configured, so RepoLab shows what came back without a narrative."}
          </Empty>
        </Panel>
      )}

      {evidence && (
        <Panel title="Evidence" subtitle="cqlsh's verbatim output, the code and docs consulted">
          <EvidencePanel evidence={evidence} initialTab="logs" />
        </Panel>
      )}

      {experiment.reproducibility && (
        <Panel
          title="Reproducibility"
          action={
            <Badge tone={experiment.reproducibility.reproducible ? "good" : "neutral"}>
              {experiment.reproducibility.reproducible ? "reproducible" : "not reproducible"}
            </Badge>
          }
        >
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Commit" value={shortHash(experiment.reproducibility.commit_sha)} />
            <Stat label="Build" value={experiment.reproducibility.build_kind} />
            <Stat label="Version" value={experiment.reproducibility.application_version ?? "—"} />
            <Stat label="Fidelity" value={experiment.reproducibility.fidelity} />
            <Stat label="Checkpoint" value={experiment.reproducibility.checkpoint_tag ?? "—"} />
            <Stat label="Memory" value={experiment.reproducibility.memory ?? "—"} />
            <Stat label="Repeats" value={String(experiment.reproducibility.repeats)} />
          </dl>
          <p className="mt-3 text-xs text-neutral-500">{experiment.reproducibility.isolation}</p>
        </Panel>
      )}
    </div>
  );
}
