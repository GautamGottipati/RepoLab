/**
 * "What Just Happened?" — what the server did, and what backs each sentence.
 *
 * Four kinds of support, carried visually because the difference is the point:
 * `observed` quotes text that really is in the output, `source` cites a file in
 * the repository, `documented` cites a page that was really retrieved, and
 * `inferred` is a conclusion from those and is shown as the weaker thing it is.
 *
 * A claim the backend could not corroborate is displayed as a failure, not
 * hidden. That includes any sentence stating a timing: a terminal result has no
 * measurements in it, so one could only have been invented.
 */

"use client";

import { EvidencePanel } from "./evidence";
import { Empty, Note, Panel } from "./ui";
import type { SupportKind, WhatHappenedView } from "@/lib/types";

const KIND: Record<SupportKind, { border: string; label: string; tone: string; hint: string }> = {
  observed: {
    border: "border-l-4 border-l-run",
    label: "observed",
    tone: "text-run-fg",
    hint: "Quoted from what the server actually printed.",
  },
  source: {
    border: "border-l-4 border-l-ai",
    label: "source",
    tone: "text-ai-fg",
    hint: "Implemented in this repository, at the cited file.",
  },
  documented: {
    border: "border-l-4 border-l-brand",
    label: "documented",
    tone: "text-brand-fg",
    hint: "Stated by official, version-matched documentation.",
  },
  inferred: {
    border: "border-l-4 border-l-line-strong",
    label: "inferred",
    tone: "text-fg-secondary",
    hint: "A conclusion from the above. Not itself evidence.",
  },
};

export function WhatHappened({
  view,
  pending,
}: {
  view: WhatHappenedView | null;
  pending: boolean;
}) {
  if (pending) {
    return (
      <Panel title="What just happened?">
        <p className="text-sm text-fg-secondary" data-testid="what-happened-pending">
          Reading the output, the repository source and the documentation…
        </p>
      </Panel>
    );
  }

  if (!view) return null;

  if (!view.available || !view.explanation) {
    return (
      <Panel title="What just happened?">
        <Note>{view.reason || "No explanation is available for this step."}</Note>
      </Panel>
    );
  }

  const explanation = view.explanation;
  const kinds: SupportKind[] = ["observed", "source", "documented", "inferred"];

  return (
    <Panel title="What just happened?">
      <div data-testid="what-happened">
      <p className="text-sm leading-relaxed">{explanation.summary}</p>

      <div className="mt-4 flex flex-col gap-4">
        {kinds.map((kind) => {
          const group = explanation.claims.filter((claim) => claim.kind === kind);
          if (group.length === 0) return null;
          const style = KIND[kind];

          return (
            <div key={kind} data-testid={`what-happened-${kind}`}>
              <h4 className={`text-xs font-semibold uppercase tracking-wide ${style.tone}`}>
                {style.label}
                <span className="ml-2 font-normal normal-case text-fg-muted">
                  {style.hint}
                </span>
              </h4>
              <ul className="mt-2 flex flex-col gap-2">
                {group.map((claim, index) => (
                  <li key={index} className={`pl-3 ${style.border}`}>
                    <p className="text-sm">{claim.statement}</p>
                    <p className="mt-0.5 font-mono text-xs text-fg-muted">
                      {claim.support}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>

      {view.unsupported.length > 0 && (
        <div
          data-testid="what-happened-unsupported"
          className="mt-4 rounded border-2 border-danger bg-danger-soft p-3"
        >
          <p className="text-sm font-semibold text-danger-fg">
            {view.unsupported.length} statement
            {view.unsupported.length === 1 ? "" : "s"} could not be traced back to the
            evidence
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {view.unsupported.map((item, index) => (
              <li key={index} className="text-xs">
                <p className="text-danger-fg">{item.statement}</p>
                <p className="mt-0.5 font-mono text-danger-fg">{item.problem}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {view.evidence ? (
        <div className="mt-5 border-t border-line pt-4">
          <EvidencePanel evidence={view.evidence} />
        </div>
      ) : (
        <div className="mt-4">
          <Empty title="No evidence bundle" />
        </div>
      )}
      </div>
    </Panel>
  );
}
