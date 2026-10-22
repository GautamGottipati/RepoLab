/**
 * The evidence panel, and claims rendered by the kind of evidence behind them.
 *
 * The distinction between measured, documented and inferred is the point of the
 * whole system, so it is carried visually and not just as a word: a measurement
 * is strongest, an inference is visibly weaker, and an unsupported claim is
 * announced rather than quietly dropped.
 */

"use client";

import { useState } from "react";

import { Empty } from "./ui";
import { count } from "@/lib/format";
import type { AnswerClaimKind, ClaimKind, EvidenceBundle } from "@/lib/types";

/** An experiment's claims are measured; an answer's rest on repository files. */
type AnyClaimKind = ClaimKind | AnswerClaimKind;

type AnyClaim = { kind: AnyClaimKind; statement: string; support: string };

const KIND_STYLE: Record<AnyClaimKind, { border: string; label: string; tone: string }> = {
  repository: {
    border: "border-l-4 border-l-violet-500",
    label: "repository",
    tone: "text-violet-700 dark:text-violet-300",
  },
  measured: {
    border: "border-l-4 border-l-emerald-500",
    label: "measured",
    tone: "text-emerald-700 dark:text-emerald-300",
  },
  documented: {
    border: "border-l-4 border-l-sky-500",
    label: "documented",
    tone: "text-sky-700 dark:text-sky-300",
  },
  inferred: {
    border: "border-l-4 border-l-neutral-300 dark:border-l-neutral-700",
    label: "inferred",
    tone: "text-neutral-500 dark:text-neutral-400",
  },
};

const KIND_HINT: Record<AnyClaimKind, string> = {
  repository: "Shown by a file in this repository, at the investigated commit.",
  measured: "A number this experiment produced, traced to a metric.",
  documented: "Stated by official, version-matched documentation.",
  inferred: "A conclusion drawn from the above. Not itself evidence.",
};

export function ClaimList({ claims }: { claims: AnyClaim[] }) {
  const kinds: AnyClaimKind[] = ["measured", "repository", "documented", "inferred"];

  return (
    <div className="flex flex-col gap-5" data-testid="claims">
      {kinds.map((kind) => {
        const group = claims.filter((claim) => claim.kind === kind);
        if (group.length === 0) return null;
        const style = KIND_STYLE[kind];

        return (
          <div key={kind} data-testid={`claims-${kind}`}>
            <h4 className={`text-xs font-semibold uppercase tracking-wide ${style.tone}`}>
              {style.label}
              <span className="ml-2 font-normal normal-case text-neutral-400">
                {KIND_HINT[kind]}
              </span>
            </h4>
            <ul className="mt-2 flex flex-col gap-2">
              {group.map((claim, index) => (
                <li
                  key={`${kind}-${index}`}
                  className={`${style.border} rounded-r bg-neutral-50 py-2 pl-3 pr-3 dark:bg-neutral-900/50`}
                >
                  <p className={kind === "inferred" ? "text-sm text-neutral-600 dark:text-neutral-400" : "text-sm"}>
                    {claim.statement}
                  </p>
                  <p className="mt-1 font-mono text-xs text-neutral-400">
                    {kind === "documented" ? (
                      <a
                        href={claim.support}
                        target="_blank"
                        rel="noreferrer"
                        className="underline underline-offset-2"
                      >
                        {claim.support}
                      </a>
                    ) : (
                      <>support: {claim.support}</>
                    )}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Claims the deterministic check could not corroborate.
 *
 * Shown prominently. A claim the evidence did not support is the single most
 * important thing on the screen.
 */
export function UnsupportedClaims({
  claims,
}: {
  claims: { statement: string; kind: AnyClaimKind; problem: string }[];
}) {
  if (claims.length === 0) return null;

  return (
    <div
      data-testid="unsupported-claims"
      className="rounded border-2 border-red-400 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950/40"
    >
      <p className="text-sm font-semibold text-red-800 dark:text-red-200">
        {claims.length} claim{claims.length === 1 ? "" : "s"} could not be traced back to
        the evidence
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {claims.map((claim, index) => (
          <li key={index} className="text-xs">
            <p className="text-red-900 dark:text-red-200">{claim.statement}</p>
            <p className="mt-0.5 font-mono text-red-700 dark:text-red-400">{claim.problem}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

type Tab = "measurements" | "code" | "logs" | "docs";

export function EvidencePanel({
  evidence,
  initialTab = "measurements",
}: {
  evidence: EvidenceBundle;
  initialTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);

  const tabs: { key: Tab; label: string; badge: number }[] = [
    { key: "measurements", label: "Measurements", badge: evidence.measurements.length },
    { key: "code", label: "Code", badge: evidence.code_references.length },
    { key: "logs", label: "Logs", badge: evidence.logs.length },
    { key: "docs", label: "Official Docs", badge: evidence.documentation.length },
  ];

  return (
    <div data-testid="evidence-panel">
      <div className="flex flex-wrap gap-1 border-b border-neutral-200 dark:border-neutral-800">
        {tabs.map((entry) => (
          <button
            key={entry.key}
            type="button"
            onClick={() => setTab(entry.key)}
            data-testid={`evidence-tab-${entry.key}`}
            className={`border-b-2 px-3 py-2 text-xs ${
              tab === entry.key
                ? "border-neutral-900 font-medium dark:border-neutral-100"
                : "border-transparent text-neutral-500"
            }`}
          >
            {entry.label}
            <span className="ml-1.5 text-neutral-400">{entry.badge}</span>
          </button>
        ))}
      </div>

      <div className="mt-3 max-h-80 overflow-y-auto">
        {tab === "measurements" && <Measurements evidence={evidence} />}
        {tab === "code" && (
          <ul className="flex flex-col gap-2">
            {evidence.code_references.map((ref, index) => (
              <li key={index} className="font-mono text-xs">
                <p className="text-neutral-700 dark:text-neutral-300">
                  {ref.path}
                  {ref.start_line ? `:${ref.start_line}` : ""}
                </p>
                {ref.excerpt && (
                  <pre className="mt-1 overflow-x-auto rounded bg-neutral-100 p-2 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
                    {ref.excerpt}
                  </pre>
                )}
              </li>
            ))}
            {evidence.code_references.length === 0 && <Empty title="No code references" />}
          </ul>
        )}
        {tab === "logs" && (
          <ul className="flex flex-col gap-3">
            {evidence.logs.map((log, index) => (
              <li key={index}>
                <p className="font-mono text-xs text-neutral-500">{log.source}</p>
                <pre className="mt-1 overflow-x-auto rounded bg-neutral-100 p-2 font-mono text-[10px] leading-relaxed dark:bg-neutral-900">
                  {log.excerpt}
                </pre>
              </li>
            ))}
            {evidence.logs.length === 0 && <Empty title="No log excerpts" />}
          </ul>
        )}
        {tab === "docs" && (
          <ul className="flex flex-col gap-3">
            {evidence.documentation.map((doc, index) => (
              <li key={index}>
                <a
                  href={doc.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm underline underline-offset-2"
                >
                  {doc.title ?? doc.url}
                </a>
                {doc.version && (
                  <span className="ml-2 text-xs text-neutral-400">
                    searched for version {doc.version}
                  </span>
                )}
                <p className="mt-0.5 font-mono text-[10px] text-neutral-400">{doc.url}</p>
                {doc.excerpt && (
                  <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
                    {doc.excerpt}
                  </p>
                )}
              </li>
            ))}
            {evidence.documentation.length === 0 && <Empty title="No documentation retrieved" />}
          </ul>
        )}
      </div>

      {evidence.simulated && (
        <p className="mt-3 rounded bg-amber-100 px-3 py-2 text-xs font-semibold uppercase dark:bg-amber-950">
          Simulated — not experimental evidence
        </p>
      )}
    </div>
  );
}

function Measurements({ evidence }: { evidence: EvidenceBundle }) {
  // Grouped by metric so one name is not repeated for every variant and repeat.
  const byName = new Map<string, typeof evidence.measurements>();
  for (const measurement of evidence.measurements) {
    const existing = byName.get(measurement.name) ?? [];
    existing.push(measurement);
    byName.set(measurement.name, existing);
  }

  if (byName.size === 0) return <Empty title="No measurements" />;

  return (
    <table className="w-full border-collapse text-left text-xs">
      <thead>
        <tr className="border-b border-neutral-200 text-neutral-500 dark:border-neutral-800">
          <th className="py-1 pr-4 font-medium">metric</th>
          <th className="py-1 pr-4 font-medium">unit</th>
          <th className="py-1 pr-4 font-medium">samples</th>
          <th className="py-1 font-medium">source</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {[...byName.entries()].map(([name, group]) => (
          <tr key={name} className="border-b border-neutral-100 dark:border-neutral-900">
            <td className="py-1 pr-4">{name}</td>
            <td className="py-1 pr-4 text-neutral-500">{group[0].unit}</td>
            <td className="py-1 pr-4">{count(group.length)}</td>
            <td className="py-1 text-neutral-500">{group[0].source}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
