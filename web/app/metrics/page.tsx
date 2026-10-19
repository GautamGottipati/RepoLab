/**
 * KPI dashboard — `/metrics`
 *
 * Two sections, deliberately separated: what developers did with RepoLab, and
 * how RepoLab itself performed. Product-wide rather than per-repository,
 * because that is what a KPI is.
 *
 * The screen's rule is the same one the metric model enforces in Python: a
 * number appears only where one was measured. Anything else shows its status
 * and the reason — `not instrumented`, `no data yet` — in place of a value,
 * because on a dashboard "0%" and "we do not record this" look identical and
 * only one of them is true.
 *
 * Two things are stated rather than implied: whose activity these numbers
 * describe, and that the north-star metric is a long-term outcome RepoLab has
 * never measured.
 *
 * Spec: docs/design/09-metrics.md
 */

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, ErrorNote, Note, Panel, Skeleton } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Dashboard, Metric, MetricGroup } from "@/lib/types";

const STATUS_LABEL: Record<Metric["status"], string> = {
  measured: "measured",
  no_data: "no data yet",
  not_instrumented: "not instrumented",
  north_star: "long-term north-star KPI",
};

export default function MetricsPage() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const next = await api.metrics();
        if (!cancelled) setDashboard(next);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto max-w-[1100px] px-6 py-10">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <Link href="/" className="text-sm font-semibold tracking-tight">
          RepoLab
        </Link>
        <span aria-hidden className="text-neutral-300 dark:text-neutral-700">
          /
        </span>
        <h1 className="text-lg font-semibold tracking-tight">Metrics</h1>
        {dashboard && (
          <Badge>
            {dashboard.measured_count} of {dashboard.metric_count} measured
          </Badge>
        )}
      </header>

      {error && (
        <div className="mt-6">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {!dashboard && !error && (
        <div className="mt-6">
          <Skeleton rows={8} />
        </div>
      )}

      {dashboard && (
        <div className="mt-6 flex flex-col gap-8">
          {/* Whose activity this is. Stated first, because it changes how every
              figure below should be read. */}
          <p
            className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400"
            data-testid="provenance"
          >
            {dashboard.provenance.summary}
          </p>

          <Section
            id="learning"
            title="Developer learning activity"
            subtitle="What people did with RepoLab"
            groups={dashboard.learning}
          />

          <Section
            id="operations"
            title="RepoLab operational & agent performance"
            subtitle="How RepoLab itself behaved while they did it"
            groups={dashboard.operations}
          />

          <NorthStar metric={dashboard.north_star} />
        </div>
      )}
    </div>
  );
}

function Section({
  id,
  title,
  subtitle,
  groups,
}: {
  id: string;
  title: string;
  subtitle: string;
  groups: MetricGroup[];
}) {
  return (
    <section data-testid={`section-${id}`}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">{title}</h2>
      <p className="mt-0.5 text-sm text-neutral-500">{subtitle}</p>

      <div className="mt-4 flex flex-col gap-4">
        {groups.map((group) => (
          <Panel key={group.title} title={group.title} subtitle={group.subtitle}>
            <dl className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {group.metrics.map((metric) => (
                <MetricCell key={metric.key} metric={metric} />
              ))}
            </dl>
          </Panel>
        ))}
      </div>
    </section>
  );
}

function MetricCell({ metric }: { metric: Metric }) {
  const measured = metric.status === "measured" && metric.value !== null;

  return (
    <div data-testid={`metric-${metric.key}`} data-status={metric.status}>
      <dt className="text-xs text-neutral-500 dark:text-neutral-400">{metric.label}</dt>
      <dd className="mt-0.5">
        {measured ? (
          <span className="font-mono text-lg font-semibold" data-testid={`value-${metric.key}`}>
            {format(metric.value)}
            {metric.unit && (
              <span className="ml-1 text-xs font-normal text-neutral-400">{metric.unit}</span>
            )}
            {metric.samples != null && (
              <span className="ml-2 text-[11px] font-normal text-neutral-400">
                n={metric.samples}
              </span>
            )}
          </span>
        ) : (
          <span
            className="inline-block rounded bg-neutral-100 px-2 py-0.5 font-mono text-xs text-neutral-500 dark:bg-neutral-900 dark:text-neutral-400"
            data-testid={`status-${metric.key}`}
          >
            {STATUS_LABEL[metric.status]}
          </span>
        )}
      </dd>
      <p className="mt-1 text-[11px] leading-snug text-neutral-500 dark:text-neutral-400">
        {metric.definition}
      </p>
      {metric.detail && (
        <p className="mt-1 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
          {metric.detail}
        </p>
      )}
      <p className="mt-1 font-mono text-[10px] text-neutral-400">{metric.source}</p>
    </div>
  );
}

function NorthStar({ metric }: { metric: Metric }) {
  return (
    <section data-testid="north-star">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Long-term north-star KPI
      </h2>
      <div className="mt-3 rounded-lg border-2 border-dashed border-violet-400 bg-violet-50/50 p-4 dark:border-violet-800 dark:bg-violet-950/20">
        <div className="flex flex-wrap items-baseline gap-3">
          <h3 className="text-base font-semibold">{metric.label}</h3>
          <Badge tone="attention">{STATUS_LABEL[metric.status]}</Badge>
          <span className="font-mono text-xs text-neutral-500" data-testid="north-star-value">
            not measured
          </span>
        </div>
        <p className="mt-2 text-sm text-neutral-700 dark:text-neutral-300">{metric.definition}</p>
        <div className="mt-3">
          <Note>{metric.detail}</Note>
        </div>
      </div>
    </section>
  );
}

/** Thousands separated, decimals kept only where they carry information. */
function format(value: number | null): string {
  if (value === null) return "—";
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
