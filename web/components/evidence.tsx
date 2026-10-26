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
    border: "border-l-4 border-l-ai",
    label: "static · repository",
    tone: "text-ai-fg",
  },
  measured: {
    border: "border-l-4 border-l-run",
    label: "measured",
    tone: "text-run-fg",
  },
  documented: {
    border: "border-l-4 border-l-brand",
    label: "documented",
    tone: "text-brand-fg",
  },
  inferred: {
    border: "border-l-4 border-l-line-strong",
    label: "inferred",
    tone: "text-fg-secondary",
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
              <span className="ml-2 font-normal normal-case text-fg-muted">
                {KIND_HINT[kind]}
              </span>
            </h4>
            <ul className="mt-2 flex flex-col gap-2">
              {group.map((claim, index) => (
                <li
                  key={`${kind}-${index}`}
                  className={`${style.border} rounded-r bg-surface-subtle py-2 pl-3 pr-3`}
                >
                  <p className={kind === "inferred" ? "text-sm text-fg-secondary" : "text-sm"}>
                    {claim.statement}
                  </p>
                  <p className="mt-1 font-mono text-xs text-fg-muted">
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
      className="rounded border-2 border-danger bg-danger-soft p-3"
    >
      <p className="text-sm font-semibold text-danger-fg">
        {claims.length} claim{claims.length === 1 ? "" : "s"} could not be traced back to
        the evidence
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {claims.map((claim, index) => (
          <li key={index} className="text-xs">
            <p className="text-danger-fg">{claim.statement}</p>
            <p className="mt-0.5 font-mono text-danger-fg">{claim.problem}</p>
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
      <div className="flex flex-wrap gap-1 border-b border-line">
        {tabs.map((entry) => (
          <button
            key={entry.key}
            type="button"
            onClick={() => setTab(entry.key)}
            data-testid={`evidence-tab-${entry.key}`}
            className={`border-b-2 px-3 py-2 text-xs ${
              tab === entry.key
                ? "border-brand font-medium"
                : "border-transparent text-fg-secondary"
            }`}
          >
            {entry.label}
            <span className="ml-1.5 text-fg-muted">{entry.badge}</span>
          </button>
        ))}
      </div>

      <div className="mt-3 max-h-80 overflow-y-auto">
        {tab === "measurements" && <Measurements evidence={evidence} />}
        {tab === "code" && (
          <ul className="flex flex-col gap-2">
            {evidence.code_references.map((ref, index) => (
              <li key={index} className="font-mono text-xs">
                <p className="text-fg">
                  {ref.path}
                  {ref.start_line ? `:${ref.start_line}` : ""}
                </p>
                {ref.excerpt && (
                  <pre className="mt-1 overflow-x-auto rounded bg-surface-subtle p-2 text-fg-secondary">
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
                <p className="font-mono text-xs text-fg-secondary">{log.source}</p>
                <pre className="mt-1 overflow-x-auto rounded bg-surface-subtle p-2 font-mono text-xs leading-relaxed">
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
                  <span className="ml-2 text-xs text-fg-muted">
                    searched for version {doc.version}
                  </span>
                )}
                <p className="mt-0.5 font-mono text-xs text-fg-muted">{doc.url}</p>
                {doc.excerpt && (
                  <p className="mt-1 text-xs text-fg-secondary">
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
        <p className="mt-3 rounded bg-exp-soft px-3 py-2 text-xs font-semibold uppercase">
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
        <tr className="border-b border-line text-fg-secondary">
          <th className="py-1 pr-4 font-medium">metric</th>
          <th className="py-1 pr-4 font-medium">unit</th>
          <th className="py-1 pr-4 font-medium">samples</th>
          <th className="py-1 font-medium">source</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {[...byName.entries()].map(([name, group]) => (
          <tr key={name} className="border-b border-line">
            <td className="py-1 pr-4">{name}</td>
            <td className="py-1 pr-4 text-fg-secondary">{group[0].unit}</td>
            <td className="py-1 pr-4">{count(group.length)}</td>
            <td className="py-1 text-fg-secondary">{group[0].source}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
