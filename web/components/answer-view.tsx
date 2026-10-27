/**
 * An answered question, laid out to teach: the direct answer, a small picture
 * of how the parts relate, how it works step by step, the details, and only
 * then the sources -- collapsed, a click away, opening the smallest range of
 * code that proves the point.
 *
 * Everything shown was verified by the backend. An arrow is drawn only where a
 * file or a cited relationship shows it; what could not be established is said,
 * not drawn. Inferred parts are dashed or labelled, and the full evidence --
 * with static, documented, inferred and measured claims -- stays one click away.
 */

"use client";

import { useMemo, useState } from "react";

import { ClaimList, EvidencePanel, UnsupportedClaims } from "./evidence";
import { useSourceViewer } from "./source-ref";
import { Badge, Note } from "./ui";
import {
  SHAPE_LABEL,
  answerGraph,
  citedSources,
  detailOf,
  diagramLayers,
  excerptRange,
  parseSource,
  provenanceLine,
  sourceCounts,
  summaryOf,
  type CitedSource,
} from "@/lib/ask";
import { sourceUrl } from "@/lib/architecture";
import type {
  AnswerResult,
  AnswerSection,
  ArchitectureGraph,
  GroundedAnswer,
  SectionBasis,
  VerifiedAnswer,
} from "@/lib/types";

/** Cited sources listed before "Show all evidence". */
const SOURCES_SHOWN = 6;

type Props = {
  result: AnswerResult;
  repoUrl: string;
  commitSha: string;
  architecture?: ArchitectureGraph | null;
  /** Ask a follow-up through the same Ask RepoLab flow. */
  onAsk?: (question: string) => void;
  /** Or link to it, where asking in place is not possible. */
  askHref?: (question: string) => string;
};

export function AnswerView({ result, repoUrl, commitSha, architecture = null, onAsk, askHref }: Props) {
  const [open, setOpen] = useState<CitedSource | null>(null);
  const sources = useMemo(() => citedSources(result), [result]);
  const verified = result.answer;
  const answer = verified?.answer ?? null;
  const shape = answer?.shape ?? result.shape ?? "general";

  const openSource = (cited: string, reason: string) => {
    const { path, line } = parseSource(cited);
    const known = sources.find((s) => s.path === path);
    setOpen({
      path,
      line: line ?? known?.line ?? null,
      reason,
      reference: known?.reference ?? null,
    });
  };

  const followUps = [...new Set([...(result.follow_ups ?? []), ...(answer?.follow_ups ?? [])])].slice(0, 4);

  return (
    <div className="flex min-w-0 flex-col gap-6" data-testid="answer-view" data-shape={shape}>
      <section data-testid="simple-answer">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-ai-fg">
          {SHAPE_LABEL[shape]}
        </h3>
        {verified && answer ? (
          <>
            <UnsupportedClaims claims={verified.unsupported} />
            <p className="mt-2 text-lg leading-relaxed text-fg" data-testid="answer">
              {summaryOf(answer)}
            </p>
          </>
        ) : (
          <p className="mt-2 text-sm text-fg-secondary" data-testid="answer-error">
            {result.error ?? "No answer could be produced."}
          </p>
        )}
        {result.downgraded && (
          <div className="mt-3">
            <Note>{result.downgraded}</Note>
          </div>
        )}
      </section>

      {verified && answer && (
        <>
          <Visual
            answer={answer}
            verified={verified}
            architecture={architecture}
            onOpen={openSource}
          />
          <Steps answer={answer} onOpen={openSource} />
          <Details answer={answer} onOpen={openSource} />
        </>
      )}

      <Sources
        result={result}
        sources={sources}
        repoUrl={repoUrl}
        commitSha={commitSha}
        onOpen={(source) => setOpen(source)}
      />

      {followUps.length > 0 && (onAsk || askHref) && (
        <section data-testid="continue-exploring">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            Continue exploring
          </h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {followUps.map((question) => (
              <li key={question}>
                {onAsk ? (
                  <button
                    type="button"
                    onClick={() => onAsk(question)}
                    data-testid="follow-up"
                    className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-sm text-fg-secondary transition-colors hover:border-ai-line hover:bg-ai-soft hover:text-ai-fg"
                  >
                    {question} →
                  </button>
                ) : (
                  <a
                    href={askHref!(question)}
                    data-testid="follow-up"
                    className="block rounded-full border border-line bg-surface px-3 py-1.5 text-sm text-fg-secondary transition-colors hover:border-ai-line hover:bg-ai-soft hover:text-ai-fg"
                  >
                    {question} →
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {open && (
        <CodeDrawer source={open} repoUrl={repoUrl} commitSha={commitSha} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}

// -- visual ------------------------------------------------------------------------

function Visual({
  answer,
  verified,
  architecture,
  onOpen,
}: {
  answer: GroundedAnswer;
  verified: VerifiedAnswer;
  architecture: ArchitectureGraph | null;
  onOpen: (cited: string, reason: string) => void;
}) {
  const graph = answerGraph(answer, architecture);
  const missing = verified.not_established ?? [];
  const locations = answer.locations ?? [];
  const comparison = answer.comparison;
  if (!graph && locations.length === 0 && !comparison && missing.length === 0) return null;

  return (
    <section className="flex min-w-0 flex-col gap-4">
      {graph && <AnswerDiagram answer={answer} graph={graph} onOpen={onOpen} />}
      {missing.length > 0 && (
        <p className="text-xs text-fg-secondary" data-testid="not-established">
          RepoLab could not establish from the code: {missing.join(" · ")}. These are not drawn.
        </p>
      )}
      {locations.length > 0 && (
        <div data-testid="code-map">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">Where it lives</h3>
          <ol className="mt-2 flex flex-col">
            {locations.map((location, index) => (
              <li key={`${location.path}-${index}`} className="flex flex-col" data-testid="code-map-entry">
                {index > 0 && <span aria-hidden className="ml-3 text-fg-muted">↓</span>}
                <div className="rounded border border-line px-3 py-2">
                  <p className="text-xs font-medium text-fg-secondary">{location.role}</p>
                  <button
                    type="button"
                    onClick={() => onOpen(location.line ? `${location.path}:${location.line}` : location.path, location.why || location.role)}
                    className="break-all text-left font-mono text-sm underline-offset-2 hover:underline"
                  >
                    {location.path}
                    {location.line ? `:${location.line}` : ""}
                  </button>
                  {location.why && <p className="mt-0.5 text-xs text-fg-secondary">{location.why}</p>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
      {comparison && comparison.rows.length > 0 && (
        <div className="overflow-x-auto" data-testid="comparison">
          <table className="w-full min-w-[20rem] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-fg-secondary">
                <th className="py-1.5 pr-4 font-medium" />
                {comparison.subjects.map((subject) => (
                  <th key={subject} className="py-1.5 pr-4 font-semibold text-fg">
                    {subject}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {comparison.rows.map((row) => (
                <tr
                  key={row.attribute}
                  data-provenance={row.provenance}
                  className="border-b border-line align-top"
                >
                  <th className="py-1.5 pr-4 text-xs font-medium text-fg-secondary">
                    {row.attribute}
                    {row.provenance === "inferred" && <span className="ml-1 italic">(inferred)</span>}
                  </th>
                  {row.values.map((value, i) => (
                    <td
                      key={i}
                      className={`py-1.5 pr-4 ${row.provenance === "unknown" ? "italic text-fg-muted" : ""}`}
                    >
                      {value}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const BOX_W = 150;
const BOX_H = 44;
const GAP_X = 20;
const GAP_Y = 36;

/**
 * The answer's boxes in top-to-bottom layers. Scales down to fit its column,
 * so it never pushes the page sideways. Inferred arrows are dashed.
 */
export function AnswerDiagram({
  answer,
  graph,
  onOpen,
}: {
  answer: GroundedAnswer;
  graph: ArchitectureGraph;
  onOpen: (cited: string, reason: string) => void;
}) {
  const boxes = diagramLayers(graph);
  const perLayer = new Map<number, number>();
  for (const box of boxes) perLayer.set(box.layer, Math.max(perLayer.get(box.layer) ?? 0, box.column + 1));
  const columns = Math.max(1, ...perLayer.values());
  const layers = Math.max(1, ...boxes.map((b) => b.layer + 1));
  const width = columns * BOX_W + (columns - 1) * GAP_X;
  const height = layers * BOX_H + (layers - 1) * GAP_Y;
  const at = new Map(
    boxes.map((box) => {
      const count = perLayer.get(box.layer) ?? 1;
      const rowWidth = count * BOX_W + (count - 1) * GAP_X;
      const x = (width - rowWidth) / 2 + box.column * (BOX_W + GAP_X);
      return [box.id, { x, y: box.layer * (BOX_H + GAP_Y), box }];
    }),
  );
  const visual = answer.visual!;
  const sourceOf = new Map(visual.nodes.map((node) => [node.id, node.source]));

  return (
    <figure className="min-w-0" data-testid="answer-visual">
      <svg
        viewBox={`-4 -4 ${width + 8} ${height + 8}`}
        width="100%"
        style={{ maxWidth: width + 8 }}
        role="group"
        aria-label={`${visual.nodes.length} parts and ${visual.edges.length} relationships`}
        className="mx-auto block h-auto"
      >
        <defs>
          <marker id="answer-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        {visual.edges.map((edge, index) => {
          const from = at.get(edge.source);
          const to = at.get(edge.target);
          if (!from || !to) return null;
          const down = to.y > from.y;
          const x1 = from.x + BOX_W / 2;
          const y1 = down ? from.y + BOX_H : from.y + BOX_H / 2;
          const x2 = to.x + BOX_W / 2;
          const y2 = down ? to.y : to.y + BOX_H / 2;
          const mid = (y1 + y2) / 2;
          return (
            <path
              key={`${edge.source}-${edge.target}-${index}`}
              data-testid="diagram-edge"
              data-provenance={edge.provenance}
              d={down ? `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}` : `M ${x1} ${y1} L ${x2} ${y2}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.4}
              strokeDasharray={edge.provenance === "inferred" ? "4 3" : undefined}
              markerEnd="url(#answer-arrow)"
              className="text-fg-muted"
            >
              <title>{edge.label || "connects to"}</title>
            </path>
          );
        })}
        {[...at.values()].map(({ x, y, box }) => {
          const source = sourceOf.get(box.id) ?? null;
          return (
            <g
              key={box.id}
              transform={`translate(${x} ${y})`}
              data-testid="diagram-node"
              data-node-id={box.id}
              role={source ? "button" : undefined}
              tabIndex={source ? 0 : undefined}
              aria-label={source ? `${box.label}, open ${source}` : undefined}
              onClick={source ? () => onOpen(source, box.label) : undefined}
              onKeyDown={
                source
                  ? (event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpen(source, box.label);
                      }
                    }
                  : undefined
              }
              className={source ? "cursor-pointer" : undefined}
            >
              <rect
                width={BOX_W}
                height={BOX_H}
                rx="7"
                className="fill-surface stroke-line-strong"
              />
              <text x={BOX_W / 2} y="19" textAnchor="middle" className="fill-fg text-[12px] font-medium">
                {truncate(box.label, 22)}
              </text>
              <text x={BOX_W / 2} y="34" textAnchor="middle" className="fill-fg-muted text-[10px]">
                {truncate(box.caption, 26)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-3">
        <ul className="flex flex-col gap-1 text-xs text-fg-secondary" data-testid="relationships">
          {visual.edges.map((edge, index) => {
            const from = visual.nodes.find((n) => n.id === edge.source)?.label;
            const to = visual.nodes.find((n) => n.id === edge.target)?.label;
            const file = edge.provenance !== "inferred" && !edge.support.startsWith("edge:") ? edge.support : null;
            return (
              <li key={index} className="flex flex-wrap items-center gap-x-2">
                <span>
                  {from} → {to}
                  {edge.label ? `: ${edge.label}` : ""}
                </span>
                {edge.provenance === "inferred" ? (
                  <span className="italic text-fg-muted">inferred</span>
                ) : file ? (
                  <SourceChip cited={file} onOpen={() => onOpen(file, `${from} → ${to}`)} />
                ) : (
                  <span className="text-fg-muted">from the architecture analysis</span>
                )}
              </li>
            );
          })}
        </ul>
      </figcaption>
    </figure>
  );
}

// -- how it works and the details ----------------------------------------------------

function Steps({ answer, onOpen }: { answer: GroundedAnswer; onOpen: (cited: string, reason: string) => void }) {
  const steps = answer.steps ?? [];
  if (steps.length === 0) return null;
  return (
    <section data-testid="how-it-works">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">How it works</h3>
      <ol className="mt-2 flex flex-col gap-3">
        {steps.map((step, index) => (
          <li key={index} className="flex gap-3" data-testid="answer-step">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white">
              {index + 1}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium">{step.title}</p>
              <p className="text-sm text-fg-secondary">{step.description}</p>
              {step.sources.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-2">
                  {step.sources.map((source) => (
                    <SourceChip key={source} cited={source} onOpen={() => onOpen(source, step.title)} />
                  ))}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

const BASIS: Record<SectionBasis, { label: string; tone: "good" | undefined }> = {
  evidenced: { label: "shown in the code", tone: "good" },
  inferred: { label: "inference", tone: undefined },
  unknown: { label: "not known from the evidence", tone: undefined },
};

function Details({ answer, onOpen }: { answer: GroundedAnswer; onOpen: (cited: string, reason: string) => void }) {
  const sections = answer.sections ?? [];
  const detail = detailOf(answer);
  if (sections.length === 0 && !detail && answer.caveats.length === 0) return null;
  return (
    <section data-testid="answer-details" className="flex flex-col gap-4">
      {sections.length > 0 && (
        <div className="flex flex-col gap-4" data-testid="answer-sections">
          {sections.map((section) => (
            <Section key={`${section.key}-${section.title}`} section={section} onOpen={onOpen} />
          ))}
        </div>
      )}
      {detail && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">In more detail</h3>
          <p className="mt-1 text-sm leading-relaxed text-fg" data-testid="answer-detail">
            {detail}
          </p>
        </div>
      )}
      {answer.caveats.length > 0 && (
        <div data-testid="caveats">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">What the evidence does not show</h3>
          <ul className="mt-1 list-disc pl-5 text-sm text-fg-secondary">
            {answer.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Section({ section, onOpen }: { section: AnswerSection; onOpen: (cited: string, reason: string) => void }) {
  const basis = BASIS[section.basis];
  return (
    <div data-testid="answer-section" data-basis={section.basis}>
      <h3 className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-wide text-fg-secondary">
        {section.title}
        <Badge tone={basis.tone}>{basis.label}</Badge>
      </h3>
      <p
        className={`mt-1 text-sm leading-relaxed ${
          section.basis === "evidenced" ? "" : "text-fg-secondary"
        }`}
      >
        {section.body}
      </p>
      {section.sources.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-2">
          {section.sources.map((source) => (
            <SourceChip key={source} cited={source} onOpen={() => onOpen(source, section.title)} />
          ))}
        </div>
      )}
    </div>
  );
}

function SourceChip({ cited, onOpen }: { cited: string; onOpen: () => void }) {
  if (/^https?:\/\//.test(cited)) {
    return (
      <a href={cited} target="_blank" rel="noreferrer" data-testid="source-chip" className="break-all font-mono text-xs text-brand-fg underline-offset-2 hover:underline">
        🌐 {cited.replace(/^https?:\/\//, "")}
      </a>
    );
  }
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="source-chip"
      className="break-all text-left font-mono text-xs text-fg-secondary underline-offset-2 hover:underline"
    >
      📄 {cited}
    </button>
  );
}

// -- sources -----------------------------------------------------------------------

function Sources({
  result,
  sources,
  repoUrl,
  commitSha,
  onOpen,
}: {
  result: AnswerResult;
  sources: CitedSource[];
  repoUrl: string;
  commitSha: string;
  onOpen: (source: CitedSource) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const counts = sourceCounts(result.evidence);
  const shown = all ? sources : sources.slice(0, SOURCES_SHOWN);

  return (
    <section data-testid="sources">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">Sources</h3>
      <p className="mt-1 flex flex-wrap gap-x-4 font-mono text-xs" data-testid="source-counts">
        <span>Code {counts.code}</span>
        <span>Docs {counts.docs}</span>
        <span>Runtime {counts.runtime}</span>
      </p>
      <p
        className={`mt-1 text-xs ${counts.runtime > 0 ? "text-run-fg" : "text-fg-secondary"}`}
        data-testid="provenance-line"
      >
        {counts.runtime > 0 ? "●" : "✓"} {provenanceLine(counts)}
      </p>

      <ul className="mt-3 flex flex-col divide-y divide-line rounded border border-line">
        {shown.map((source) => {
          const isOpen = expanded === source.path;
          const link = sourceUrl(repoUrl, commitSha, {
            path: source.path,
            start_line: source.line,
            end_line: null,
            commit_sha: commitSha,
            excerpt: null,
          });
          return (
            <li key={source.path} data-testid="source-item" className="min-w-0 px-3 py-2">
              <button
                type="button"
                onClick={() => setExpanded(isOpen ? null : source.path)}
                aria-expanded={isOpen}
                className="flex w-full min-w-0 items-start gap-2 text-left"
              >
                <span aria-hidden className="text-fg-muted">{isOpen ? "▾" : "▸"}</span>
                <span className="min-w-0">
                  <span className="block truncate text-sm">{source.reason}</span>
                  <span className="block break-all font-mono text-xs text-fg-secondary">
                    {source.path}
                    {source.line ? `:${source.line}` : ""}
                  </span>
                </span>
              </button>
              {isOpen && (
                <div className="mt-2 pl-5" data-testid="source-excerpt">
                  {source.reference ? (
                    <Excerpt source={source} />
                  ) : (
                    <p className="text-xs text-fg-secondary">RepoLab did not keep an excerpt of this file.</p>
                  )}
                  <div className="mt-1 flex gap-3 text-xs">
                    <button type="button" onClick={() => onOpen(source)} className="underline underline-offset-2">
                      Open beside the answer
                    </button>
                    {link && (
                      <a href={link} target="_blank" rel="noreferrer" className="underline underline-offset-2">
                        View at commit {commitSha.slice(0, 7)}
                      </a>
                    )}
                  </div>
                </div>
              )}
            </li>
          );
        })}
        {sources.length === 0 && <li className="px-3 py-2 text-sm text-fg-secondary">No repository files were cited.</li>}
      </ul>

      <button
        type="button"
        onClick={() => setAll((v) => !v)}
        aria-expanded={all}
        data-testid="show-all-evidence"
        className="mt-2 text-xs underline underline-offset-2"
      >
        {all ? "Hide evidence details" : "Show all evidence"}
      </button>
      {all && (
        <div className="mt-3 flex flex-col gap-4" data-testid="all-evidence">
          {result.answer && result.answer.answer.claims.length > 0 && <ClaimList claims={result.answer.answer.claims} />}
          {result.findings.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">What RepoLab did</h4>
              <ul className="mt-1 list-disc pl-5 text-xs text-fg-secondary">
                {result.findings.map((finding, i) => (
                  <li key={i}>{finding}</li>
                ))}
              </ul>
            </div>
          )}
          {result.answer?.dropped && Object.keys(result.answer.dropped).length > 0 && (
            <p className="text-xs text-fg-secondary" data-testid="layout-dropped">
              Removed by verification:{" "}
              {Object.entries(result.answer.dropped)
                .map(([reason, n]) => `${n} × ${reason}`)
                .join(" · ")}
            </p>
          )}
          <EvidencePanel evidence={result.evidence} initialTab={result.evidence.measurements.length ? "measurements" : "code"} />
        </div>
      )}
    </section>
  );
}

function Excerpt({ source }: { source: CitedSource }) {
  const lines = excerptRange(source.reference!, source.line);
  return (
    <pre className="max-h-64 overflow-auto rounded bg-surface-subtle py-2 font-mono text-xs leading-relaxed">
      {lines.map((line, i) => (
        <div
          key={i}
          data-cited={line.cited || undefined}
          className={`flex gap-3 px-2 ${line.cited ? "bg-exp-soft" : ""}`}
        >
          <span className="w-8 shrink-0 select-none text-right text-fg-muted">{line.number ?? ""}</span>
          <span className="whitespace-pre">{line.text}</span>
        </div>
      ))}
    </pre>
  );
}

// -- the drawer --------------------------------------------------------------------

export function CodeDrawer({
  source,
  repoUrl,
  commitSha,
  onClose,
}: {
  source: CitedSource;
  repoUrl: string;
  commitSha: string;
  onClose: () => void;
}) {
  const viewer = useSourceViewer();
  const link = sourceUrl(repoUrl, commitSha, {
    path: source.path,
    start_line: source.line,
    end_line: null,
    commit_sha: commitSha,
    excerpt: null,
  });
  return (
    <aside
      role="dialog"
      aria-label={`Source: ${source.path}`}
      data-testid="code-drawer"
      className="fixed inset-x-0 bottom-0 z-40 max-h-[70vh] overflow-y-auto border-t border-line bg-surface p-4 shadow-xl md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[32rem] md:border-l md:border-t-0"
    >
      <header className="flex items-start justify-between gap-3">
        <p className="min-w-0 break-all font-mono text-sm font-medium">
          {source.path}
          {source.line ? `:${source.line}` : ""}
        </p>
        <button type="button" onClick={onClose} aria-label="Close" className="shrink-0 text-lg leading-none text-fg-secondary">
          ×
        </button>
      </header>
      <div className="mt-3">
        {source.reference ? (
          <Excerpt source={source} />
        ) : (
          <p className="text-xs text-fg-secondary">RepoLab did not keep an excerpt of this file.</p>
        )}
      </div>
      <h4 className="mt-4 text-xs font-semibold uppercase tracking-wide text-fg-secondary">Why this matters</h4>
      <p className="mt-1 text-sm" data-testid="drawer-reason">
        {source.reason}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {viewer && (
          <button
            type="button"
            data-testid="drawer-open-viewer"
            onClick={() => {
              onClose();
              viewer.open({
                path: source.path,
                start_line: source.line,
                end_line: source.reference?.end_line ?? source.line,
                commit_sha: commitSha,
                reason: source.reason,
              });
            }}
            className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-fg hover:bg-surface-hover"
          >
            Open the whole file at {commitSha.slice(0, 7)}
          </button>
        )}
        {link && (
          <a href={link} target="_blank" rel="noreferrer" data-testid="drawer-link" className="text-xs underline underline-offset-2">
            Open at commit {commitSha.slice(0, 7)} ↗
          </a>
        )}
      </div>
    </aside>
  );
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}
