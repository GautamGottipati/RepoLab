/**
 * Experiment Results — measurements arriving, then the explanation.
 *
 * The screen has to be useful before the experiment finishes, so the charts are
 * built by **appending** to state as SSE events arrive rather than by refetching
 * the statistics. Once the run completes, the computed statistics replace the
 * incremental view — they are the authoritative figures, and the incremental
 * points were only ever a live approximation of them.
 *
 * Spec: docs/design/06-experiment-results.md
 */

"use client";

import { use, useEffect, useMemo, useState } from "react";

import { ActivityFeed } from "@/components/activity";
import { AnsweredQuestion } from "@/components/answer-card";
import { CliResults } from "@/components/cli-results";
import { BarChart, Sparkline, type Bar } from "@/components/charts";
import { ClaimList, EvidencePanel, UnsupportedClaims } from "@/components/evidence";
import { Badge, DeterminismTag, Empty, ErrorNote, Note, Panel, Skeleton, Stat } from "@/components/ui";
import { api, optional } from "@/lib/api";
import { field, isBoolean, isNumber, useEventStream } from "@/lib/events";
import { count, ms, percent, shortHash } from "@/lib/format";
import type {
  ExperimentState,
  ExperimentStatistics,
  ExplanationView,
  JobEvent,
  MetricComparison,
  SparkExperimentState,
} from "@/lib/types";

import { useWorkspace } from "../../workspace";

/**
 * Metrics worth a chart, in the order they answer the demo question.
 *
 * Deliberately a short list. An experiment measures a dozen metrics, and
 * charting all of them buries the finding under bars that are identical across
 * variants or zero everywhere. The rest are in the statistics table, which can
 * be expanded — nothing is hidden, it is just not all given equal weight.
 */
const CHARTED = ["empty partitions", "total runtime", "partition skew ratio", "task count"];

export default function ResultsPage({ params }: PageProps<"/r/[jobId]/ask/[experimentId]">) {
  const { experimentId } = use(params);
  const { jobId, architecture } = useWorkspace();

  const [experiment, setExperiment] = useState<ExperimentState | null>(null);
  const [explanation, setExplanation] = useState<ExplanationView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { events } = useEventStream(api.experimentEventsUrl(experimentId));
  const finished =
    experiment?.status === "completed" ||
    experiment?.status === "failed" ||
    experiment?.status === "rejected" ||
    experiment?.status === "answered";

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      const next = await optional(api.experiment(experimentId));
      if (cancelled || !next) return;
      setExperiment(next);
      if (next.error) setError(next.error);
      if (next.explanation_ready && !explanation) {
        const view = await optional(api.explanation(experimentId));
        if (!cancelled && view) setExplanation(view);
      }
    };

    void poll();
    if (finished && (explanation || experiment?.explanation_ready === false)) {
      return () => { cancelled = true; };
    }
    const timer = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [experimentId, finished, explanation, experiment?.explanation_ready]);

  // A question becomes one of three things; each has its own screen.
  if (experiment?.kind === "cli") {
    return (
      <CliResults
        experiment={experiment}
        explanation={explanation}
        events={events}
        error={error}
        finished={finished}
      />
    );
  }
  if (experiment?.kind === "answer") {
    return (
      <AnsweredQuestion experiment={experiment} jobId={jobId} architecture={architecture?.graph ?? null} />
    );
  }
  return (
    <SparkResults
      experiment={experiment}
      explanation={explanation}
      events={events}
      error={error}
      finished={finished}
    />
  );
}

function SparkResults({
  experiment,
  explanation,
  events,
  error,
  finished,
}: {
  experiment: SparkExperimentState | null;
  explanation: ExplanationView | null;
  events: JobEvent[];
  error: string | null;
  finished: boolean;
}) {
  const [showAllMetrics, setShowAllMetrics] = useState(false);

  const live = useMemo(() => liveVariants(events), [events]);
  const statistics = experiment?.statistics ?? null;
  const plan = experiment?.plan ?? null;

  const charted = useMemo(
    () => (statistics ? chartable(statistics.comparisons) : []),
    [statistics],
  );
  const tabled = useMemo(() => {
    if (!statistics) return [];
    return showAllMetrics ? orderMetrics(statistics.comparisons) : charted;
  }, [statistics, showAllMetrics, charted]);

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={experiment?.question ?? "Experiment"}
        subtitle={plan ? `Varying ${plan.variable}` : undefined}
        action={<StatusBadge status={experiment?.status} />}
      >
        {!experiment ? (
          <Skeleton rows={2} />
        ) : (
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat
              label="Runs"
              value={`${count(experiment.variant_runs)}${
                plan ? ` / ${plan.estimated_runs}` : ""
              }`}
              emphasis
            />
            <Stat label="Measurements" value={count(experiment.measurements)} />
            <Stat label="Repeats per value" value={plan ? String(plan.repeats) : "—"} />
            <Stat label="Commit" value={shortHash(experiment.commit_sha)} />
          </dl>
        )}
        {plan && (
          <p className="mt-4 text-sm text-neutral-600 dark:text-neutral-400">
            <span className="font-medium">Hypothesis:</span> {plan.hypothesis}
          </p>
        )}
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}

      {statistics && statistics.contended_runs > 0 && (
        <Note>
          {statistics.contended_runs} of {statistics.total_runs} runs executed alongside
          another variant, so their timings reflect contention and must not be read as
          clean durations.
        </Note>
      )}

      {statistics && statistics.variants.length > 0 && unstable(statistics).length > 0 && (
        <Note>
          These metrics should have been identical across repeats of the same value but
          were not: {unstable(statistics).join(", ")}. The runs were not equivalent, which
          weakens the comparison.
        </Note>
      )}

      <Panel
        title="Results"
        subtitle={
          finished
            ? "Computed by Python from each run's Spark event log"
            : "Filling in as each variant completes"
        }
      >
        {charted.length > 0 && statistics ? (
          <div className="flex flex-col gap-6" data-testid="result-charts">
            {charted.map((comparison) => (
              <BarChart
                key={comparison.metric}
                title={comparison.metric}
                unit={comparison.unit}
                determinism={comparison.determinism}
                bars={barsFromComparison(comparison, statistics)}
                format={formatterFor(comparison)}
              />
            ))}
          </div>
        ) : live.length > 0 ? (
          <div className="flex flex-col gap-6" data-testid="live-charts">
            <BarChart
              title="empty partitions"
              unit="partitions"
              determinism="structural"
              bars={live.map(toBar("empty"))}
              emptyMessage="Waiting for the first variant"
            />
            <BarChart
              title="total runtime"
              unit="ms"
              determinism="timing"
              bars={live.map(toBar("runtime"))}
              emptyMessage="Waiting for the first variant"
            />
            <p className="text-xs text-neutral-400">
              Live values from the runs completed so far. Replaced by the computed
              statistics when the experiment finishes.
            </p>
          </div>
        ) : (
          <Empty title="No variants have completed yet">
            Each variant appears here as it finishes, and the activity feed on the
            right shows what is running now.
          </Empty>
        )}

        {!finished && (
          <div className="mt-5">
            <ActivityFeed events={events} height="h-40" />
          </div>
        )}
      </Panel>

      {statistics && statistics.variants.length > 0 && (
        <Panel
          title="Statistics"
          subtitle="Median, spread and standard deviation per variant"
          action={
            <button
              type="button"
              onClick={() => setShowAllMetrics((current) => !current)}
              data-testid="toggle-all-metrics"
              className="rounded border border-neutral-300 px-3 py-1 text-xs dark:border-neutral-700"
            >
              {showAllMetrics
                ? "Show the key metrics"
                : `Show all ${statistics.comparisons.length} metrics`}
            </button>
          }
        >
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-neutral-200 text-neutral-500 dark:border-neutral-800">
                  <th className="py-1 pr-4 font-medium">metric</th>
                  <th className="py-1 pr-4 font-medium">value</th>
                  <th className="py-1 pr-4 font-medium">median</th>
                  <th className="py-1 pr-4 font-medium">spread</th>
                  <th className="py-1 pr-4 font-medium">sd</th>
                  <th className="py-1 pr-4 font-medium">vs baseline</th>
                  <th className="py-1 font-medium">samples</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {tabled.flatMap((comparison) =>
                  comparison.points.map((point) => {
                    const variant = statistics.variants.find((v) => v.value === point.value);
                    const aggregate = variant?.aggregates[comparison.metric];
                    return (
                      <tr
                        key={`${comparison.metric}-${point.value}`}
                        className="border-b border-neutral-100 dark:border-neutral-900"
                      >
                        <td className="py-1 pr-4">
                          {comparison.metric}{" "}
                          <DeterminismTag kind={comparison.determinism} />
                        </td>
                        <td className={`py-1 pr-4 ${point.is_baseline ? "font-semibold" : ""}`}>
                          {point.value}
                          {point.is_baseline && (
                            <span className="ml-1 text-[10px] uppercase text-neutral-400">
                              base
                            </span>
                          )}
                        </td>
                        <td className="py-1 pr-4">{point.median.toLocaleString()}</td>
                        <td className="py-1 pr-4 text-neutral-500">
                          {aggregate ? aggregate.spread.toLocaleString() : "—"}
                        </td>
                        <td className="py-1 pr-4 text-neutral-500">
                          {aggregate ? aggregate.stddev.toLocaleString() : "—"}
                        </td>
                        <td className="py-1 pr-4">
                          {point.is_baseline ? "—" : percent(point.relative_difference_percent)}
                        </td>
                        <td className="py-1">
                          {aggregate && <Sparkline samples={aggregate.samples} />}
                        </td>
                      </tr>
                    );
                  }),
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {explanation && (
        <Panel
          title="Explanation"
          subtitle="Produced from the computed statistics; every claim labelled by its evidence"
          action={
            explanation.explanation.grounded ? (
              <Badge tone="good">
                {explanation.explanation.checked_claims} claims verified
              </Badge>
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
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                  Caveats
                </h4>
                <ul className="mt-2 list-disc pl-5 text-sm text-neutral-600 dark:text-neutral-400">
                  {explanation.explanation.explanation.caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Panel>
      )}

      {finished && !explanation && (
        <Panel title="Explanation">
          {experiment?.explanation_error ? (
            <>
              <Empty title="The explanation could not be produced">
                The measurements above are real and complete. Only the narrative is
                missing — RepoLab reports that rather than inventing one.
              </Empty>
              <p className="mt-3 font-mono text-xs text-neutral-500">
                {experiment.explanation_error}
              </p>
            </>
          ) : (
            <Empty title="The measurements stand alone">
              No model is configured, so RepoLab reports the measurements without a
              narrative rather than inventing one.
            </Empty>
          )}
        </Panel>
      )}

      {explanation?.evidence && (
        <Panel title="Evidence" subtitle="What the explanation is grounded in">
          <EvidencePanel evidence={explanation.evidence} />
        </Panel>
      )}

      {experiment?.reproducibility && (
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
            <Stat label="Image" value={experiment.reproducibility.image} />
            <Stat
              label="Spark measured"
              value={experiment.reproducibility.application_version ?? "—"}
            />
            <Stat
              label="Dataset hash"
              value={shortHash(experiment.reproducibility.dataset.sha256, 16)}
            />
            <Stat label="Master" value={experiment.reproducibility.resources.master} />
            <Stat
              label="Driver memory"
              value={experiment.reproducibility.resources.driver_memory}
            />
            <Stat label="Repeats" value={String(experiment.reproducibility.repeats)} />
            <Stat
              label="Concurrency"
              value={String(experiment.reproducibility.resources.concurrency)}
            />
          </dl>

          <div className="mt-4 border-t border-neutral-200 pt-3 dark:border-neutral-800">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Added by RepoLab, not present in the repository
            </h4>
            <ul className="mt-2 list-disc pl-5 text-xs text-neutral-600 dark:text-neutral-400">
              {experiment.reproducibility.added_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          </div>
        </Panel>
      )}
    </div>
  );
}

type LiveVariant = {
  value: number;
  runs: number;
  empty: number | null;
  runtime: number | null;
  partitions: number | null;
  complete: boolean;
  contended: boolean;
};

/**
 * Variants assembled from the stream, appended to as events arrive.
 *
 * `experiment_variant_started` creates the column pending; `partial_result`
 * fills it in; `experiment_variant_completed` settles it. This is the
 * "incremental from SSE partial results" requirement, and it is why the charts
 * never wait for a refetch.
 */
function liveVariants(events: JobEvent[]): LiveVariant[] {
  const byValue = new Map<number, LiveVariant>();

  for (const event of events) {
    const value = field(event, "value", isNumber);
    if (value === undefined) continue;

    const current =
      byValue.get(value) ??
      {
        value,
        runs: 0,
        empty: null,
        runtime: null,
        partitions: null,
        complete: false,
        contended: false,
      };

    if (event.type === "partial_result") {
      current.runs += 1;
      const empty = field(event, "empty_partitions", isNumber);
      const runtime = field(event, "total_runtime_ms", isNumber);
      const partitions = field(event, "partitions", isNumber);
      if (empty !== undefined) current.empty = empty;
      if (runtime !== undefined) current.runtime = runtime;
      if (partitions !== undefined) current.partitions = partitions;
      if (field(event, "timing_contended", isBoolean)) current.contended = true;
    }
    if (event.type === "experiment_variant_completed") current.complete = true;

    byValue.set(value, current);
  }

  return [...byValue.values()].sort((a, b) => a.value - b.value);
}

const toBar =
  (metric: "empty" | "runtime") =>
  (variant: LiveVariant): Bar => ({
    key: variant.value,
    value: (metric === "empty" ? variant.empty : variant.runtime) ?? 0,
    pending: (metric === "empty" ? variant.empty : variant.runtime) === null,
    note: variant.complete ? `${variant.runs} runs` : "running",
  });

function barsFromComparison(
  comparison: MetricComparison,
  statistics: ExperimentStatistics,
): Bar[] {
  return comparison.points.map((point) => {
    const variant = statistics.variants.find((entry) => entry.value === point.value);
    const aggregate = variant?.aggregates[comparison.metric];
    return {
      key: point.value,
      value: point.median,
      baseline: point.is_baseline,
      spread: aggregate?.spread,
      note: point.is_baseline ? "baseline" : percent(point.relative_difference_percent),
    };
  });
}

/** The metrics that get a chart: the listed ones, in the listed order. */
function chartable(comparisons: MetricComparison[]): MetricComparison[] {
  return CHARTED.map((metric) =>
    comparisons.find((comparison) => comparison.metric === metric),
  ).filter((comparison): comparison is MetricComparison => comparison !== undefined);
}

/** Every metric, key ones first. */
function orderMetrics(comparisons: MetricComparison[]): MetricComparison[] {
  const rank = (metric: string) => {
    const index = CHARTED.indexOf(metric);
    return index === -1 ? CHARTED.length : index;
  };
  return [...comparisons].sort(
    (a, b) => rank(a.metric) - rank(b.metric) || a.metric.localeCompare(b.metric),
  );
}

function formatterFor(comparison: MetricComparison): (value: number) => string {
  if (comparison.unit === "ms") return (value) => ms(value);
  return (value) => value.toLocaleString();
}

function unstable(statistics: ExperimentStatistics): string[] {
  const found: string[] = [];
  for (const variant of statistics.variants) {
    for (const aggregate of Object.values(variant.aggregates)) {
      if (aggregate.determinism === "structural" && !aggregate.stable) {
        if (!found.includes(aggregate.metric)) found.push(aggregate.metric);
      }
    }
  }
  return found;
}

function StatusBadge({ status }: { status?: ExperimentState["status"] }) {
  if (!status) return null;
  return (
    <span data-testid="experiment-status" data-status={status}>
      {status === "completed" ? (
        <Badge tone="good">completed</Badge>
      ) : status === "running" ? (
        <Badge tone="live">running</Badge>
      ) : status === "approved" ? (
        <Badge tone="live">starting</Badge>
      ) : status === "failed" ? (
        <Badge tone="bad">failed</Badge>
      ) : status === "answered" ? (
        <Badge tone="good">answered</Badge>
      ) : (
        <Badge>{status}</Badge>
      )}
    </span>
  );
}
