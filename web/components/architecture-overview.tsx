/**
 * The beginner-facing sections of the Architecture page.
 *
 * Each is small and given its data: what the repository is, the parts it is
 * made of, a path to follow, where to start, and -- collapsed -- how the
 * analysis went. They never compute claims themselves; everything shown comes
 * from the stored architecture, with its provenance.
 */

"use client";

import Link from "next/link";
import { useState } from "react";

import { EvidenceList, InferredTag } from "./architecture-inspector";
import { Badge, Panel } from "./ui";
import {
  REPOSITORY_KIND_LABEL,
  askUrl,
  formatMeasured,
  sourceUrl,
  subsystemPath,
} from "@/lib/architecture";
import type {
  ArchitectureGraph,
  ImportantFile,
  LearningStep,
  MeasuredValue,
  RepositoryUnderstanding,
  Subsystem,
  Walkthrough,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Level 0: what is this?
// ---------------------------------------------------------------------------

export function Orientation({
  jobId,
  repoUrl,
  commit,
  understanding,
  surfaces,
  partial,
  onExplore,
  onSelect,
}: {
  jobId: string;
  repoUrl: string;
  commit: string;
  understanding: RepositoryUnderstanding;
  surfaces: string[];
  partial: boolean;
  onExplore: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  const inferred = new Set(understanding.inferred_fields);
  const tag = (field: string) => (inferred.has(field) ? <InferredTag /> : null);
  const sources = [...new Set(understanding.evidence.map((e) => e.kind))];

  return (
    <section data-testid="orientation" className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="repository-name">
          {understanding.name}
        </h1>
        <p className="mt-1 text-base text-neutral-700 dark:text-neutral-300">
          <Badge tone="attention">
            <span data-testid="repository-type">{REPOSITORY_KIND_LABEL[understanding.repository_type]}</span>
          </Badge>
          {understanding.type_provenance === "inferred" && <InferredTag />}
          {understanding.languages.length > 0 && (
            <span className="ml-3 text-sm text-neutral-500">{understanding.languages.join(" • ")}</span>
          )}
        </p>
        {understanding.one_line_description && (
          <p className="mt-2 text-lg" data-testid="one-line">
            {understanding.one_line_description}
            {tag("one_line_description")}
          </p>
        )}
      </header>

      <div className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
        <h2 className="text-sm font-semibold">What is this repository?</h2>
        {understanding.beginner_explanation ? (
          <p className="mt-1 text-sm leading-relaxed" data-testid="beginner-explanation">
            {understanding.beginner_explanation}
            {tag("beginner_explanation")}
          </p>
        ) : (
          <p className="mt-1 text-sm text-neutral-500">
            The repository does not describe itself in a README or package metadata RepoLab could read.
          </p>
        )}
        <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
          {understanding.primary_purpose && (
            <div>
              <dt className="text-xs text-neutral-500">What problem it solves</dt>
              <dd>
                {understanding.primary_purpose}
                {tag("primary_purpose")}
              </dd>
            </div>
          )}
          {understanding.primary_users && (
            <div>
              <dt className="text-xs text-neutral-500">Who uses it</dt>
              <dd>
                {understanding.primary_users}
                {tag("primary_users")}
              </dd>
            </div>
          )}
          {surfaces.length > 0 && (
            <div>
              <dt className="text-xs text-neutral-500">Ways to use it</dt>
              <dd data-testid="surfaces">{surfaces.join(" · ")}</dd>
            </div>
          )}
        </dl>

        <details className="mt-3 text-xs" data-testid="how-do-you-know">
          <summary className="cursor-pointer text-neutral-500">
            Based on {sources.map((k) => k.replace("_", " ")).join(", ") || "the repository's structure"} ·
            How do you know?
          </summary>
          <div className="mt-2 flex flex-col gap-2">
            <EvidenceList
              evidence={understanding.evidence}
              repoUrl={repoUrl}
              commit={commit}
              onSelect={onSelect}
              onExplore={onExplore}
            />
            {understanding.type_reasons.length > 0 && (
              <div>
                <p className="font-medium">Why “{REPOSITORY_KIND_LABEL[understanding.repository_type]}”</p>
                <ul className="list-disc pl-5 text-neutral-600 dark:text-neutral-400">
                  {understanding.type_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </details>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {understanding.recommended_start && (
            <Link
              href={askUrl(jobId, null, understanding.recommended_start)}
              data-testid="recommended-start"
              className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
            >
              {understanding.recommended_start} →
            </Link>
          )}
          {partial && (
            <span className="text-xs text-amber-700 dark:text-amber-400" data-testid="partial-note">
              Parts of this architecture could not be determined — see Analysis details.
            </span>
          )}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Level 2: the repository map
// ---------------------------------------------------------------------------

export function SubsystemCards({
  subsystems,
  measured,
  onExplore,
}: {
  subsystems: Subsystem[];
  measured: Record<string, MeasuredValue[]>;
  onExplore: (id: string) => void;
}) {
  return (
    <Panel title="Explore the repository" subtitle="Its major parts, most useful to understand first">
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="subsystem-cards">
        {subsystems.map((subsystem) => (
          <li key={subsystem.id}>
            <button
              type="button"
              onClick={() => onExplore(subsystem.id)}
              data-testid="subsystem-card"
              data-subsystem-id={subsystem.id}
              className="flex h-full w-full flex-col rounded-lg border border-neutral-200 p-3 text-left hover:border-neutral-400 dark:border-neutral-800 dark:hover:border-neutral-600"
            >
              <span className="text-sm font-semibold">
                {subsystem.label}
                {subsystem.inferred_fields.includes("label") && <InferredTag />}
              </span>
              <span className="font-mono text-[11px] text-neutral-500">{subsystemPath(subsystem)}</span>
              {subsystem.description && (
                <span className="mt-1.5 text-xs text-neutral-700 dark:text-neutral-300">{subsystem.description}</span>
              )}
              <span className="mt-auto flex items-center justify-between pt-2 text-[11px] text-neutral-500">
                <span>
                  {subsystem.file_count} source files
                  {subsystem.languages.length > 0 && ` · ${subsystem.languages.join(", ")}`}
                </span>
                <span className="flex items-center gap-2">
                  {(measured[subsystem.id]?.length ?? 0) > 0 && <Badge tone="good">measured</Badge>}
                  <span className="font-medium text-neutral-800 dark:text-neutral-200">Explore →</span>
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Follow a request / job / command
// ---------------------------------------------------------------------------

export function Walkthroughs({
  walkthroughs,
  onSelect,
}: {
  walkthroughs: Walkthrough[];
  onSelect: (id: string) => void;
}) {
  const [index, setIndex] = useState(0);
  const walk = walkthroughs[Math.min(index, walkthroughs.length - 1)];
  if (!walk) return null;
  return (
    <Panel
      title={walk.name}
      subtitle={walk.description ?? "One concrete path through the code, along relationships RepoLab could evidence"}
      action={
        walkthroughs.length > 1 ? (
          <div className="flex gap-1">
            {walkthroughs.map((w, i) => (
              <button
                key={w.id}
                type="button"
                onClick={() => setIndex(i)}
                aria-pressed={i === index}
                className={`rounded px-2 py-0.5 text-xs ${i === index ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900" : "border border-neutral-300 dark:border-neutral-700"}`}
              >
                {w.name}
              </button>
            ))}
          </div>
        ) : undefined
      }
    >
      <ol className="flex flex-col items-start gap-1" data-testid="walkthrough" data-walkthrough-id={walk.id}>
        {walk.steps.map((step, i) => (
          <li key={`${step.target_id}-${i}`} className="flex flex-col items-start">
            {i > 0 && (
              <span aria-hidden className="ml-4 text-neutral-400">
                ↓
              </span>
            )}
            <button
              type="button"
              onClick={() => onSelect(step.target_id)}
              data-testid="walkthrough-step"
              className="rounded border border-neutral-300 px-3 py-1 text-left text-sm hover:border-neutral-500 dark:border-neutral-700"
            >
              <span className="font-medium">{step.label}</span>
              {step.detail && <span className="ml-2 text-xs text-neutral-500">{step.detail}</span>}
            </button>
          </li>
        ))}
      </ol>
      {walk.provenance === "inferred" && (
        <p className="mt-2 text-[10px] uppercase tracking-wide text-neutral-400">
          inferred by Nemotron along evidenced relationships
        </p>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// New to this project?
// ---------------------------------------------------------------------------

export function LearningPath({
  jobId,
  steps,
  experiment,
  onOpen,
}: {
  jobId: string;
  steps: LearningStep[];
  /** A question RepoLab can really test, when an experiment is available. */
  experiment: { id: string; question: string } | null;
  onOpen: (step: LearningStep) => void;
}) {
  if (steps.length === 0) return null;
  return (
    <Panel title="New to this project?" subtitle="A short path through this repository">
      <ol className="flex flex-col gap-2" data-testid="learning-path">
        {steps.map((step, i) => (
          <li key={`${step.title}-${i}`} className="flex gap-3">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-neutral-200 text-[11px] font-semibold dark:bg-neutral-800">
              {i + 1}
            </span>
            <div className="min-w-0">
              {step.target_kind === "question" ? (
                <Link href={askUrl(jobId, null, step.question ?? step.title)} className="text-sm font-medium underline underline-offset-2">
                  {step.title}
                </Link>
              ) : (
                <button type="button" onClick={() => onOpen(step)} className="text-left text-sm font-medium underline underline-offset-2">
                  {step.title}
                </button>
              )}
              {step.provenance === "inferred" && <InferredTag />}
              {step.why && <p className="text-xs text-neutral-500">{step.why}</p>}
            </div>
          </li>
        ))}
        {experiment && (
          <li className="flex gap-3">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-violet-200 text-[11px] font-semibold dark:bg-violet-900">
              {steps.length + 1}
            </span>
            <Link
              href={askUrl(jobId, experiment.id, experiment.question)}
              data-testid="learning-experiment"
              className="text-sm font-medium text-violet-700 underline underline-offset-2 dark:text-violet-300"
            >
              Run a small experiment: {experiment.question}
            </Link>
          </li>
        )}
      </ol>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Where should I start?
// ---------------------------------------------------------------------------

export function StartingPoints({
  repoUrl,
  commit,
  subsystems,
  files,
  onExplore,
  onOpenFile,
}: {
  repoUrl: string;
  commit: string;
  subsystems: Subsystem[];
  files: ImportantFile[];
  onExplore: (id: string) => void;
  onOpenFile: (file: ImportantFile) => void;
}) {
  const shown = subsystems.filter((s) => s.starting_points.length > 0).slice(0, 4);
  return (
    <Panel title="Where should I start?" subtitle="A few places to open first, and what each teaches">
      <ul className="flex flex-col gap-4" data-testid="starting-points">
        {shown.map((subsystem) => (
          <li key={subsystem.id}>
            <p className="text-sm font-semibold">{subsystem.label}</p>
            <StartingPointList repoUrl={repoUrl} commit={commit} subsystem={subsystem} />
            <button
              type="button"
              onClick={() => onExplore(subsystem.id)}
              className="mt-1 text-xs font-medium underline underline-offset-2"
            >
              Explore {subsystem.label} →
            </button>
          </li>
        ))}
      </ul>
      {files.length > 0 && (
        <details className="mt-4 border-t border-neutral-200 pt-3 dark:border-neutral-800" data-testid="advanced-files">
          <summary className="cursor-pointer text-xs font-medium text-neutral-500">
            Advanced: important implementation files
          </summary>
          <ImportantFiles files={files} onOpen={onOpenFile} />
        </details>
      )}
    </Panel>
  );
}

export function StartingPointList({
  repoUrl,
  commit,
  subsystem,
}: {
  repoUrl: string;
  commit: string;
  subsystem: Subsystem;
}) {
  return (
    <ul className="mt-1 flex flex-col gap-1.5">
      {subsystem.starting_points.map((point) => {
        const url = point.is_directory
          ? null
          : sourceUrl(repoUrl, commit, { path: point.path, start_line: null, end_line: null, commit_sha: null, excerpt: null });
        return (
          <li key={point.path} className="text-xs" data-testid="starting-point">
            <span aria-hidden>{point.is_directory ? "📁 " : "📄 "}</span>
            {url ? (
              <a href={url} target="_blank" rel="noreferrer" className="font-mono underline underline-offset-2">
                {point.path}
              </a>
            ) : (
              <span className="font-mono">{point.path}/</span>
            )}
            <span className="ml-2 text-neutral-500">{point.reason}</span>
            {point.learn_about.length > 0 && (
              <span className="block pl-5 text-neutral-500">Learn about: {point.learn_about.join(" • ")}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ImportantFiles({
  files,
  active,
  onOpen,
}: {
  files: ImportantFile[];
  active?: string | null;
  onOpen: (file: ImportantFile) => void;
}) {
  return (
    <ol className="mt-2 flex flex-col gap-1" data-testid="important-files">
      {files.map((file) => (
        <li key={file.path}>
          <button
            type="button"
            onClick={() => onOpen(file)}
            aria-pressed={active === file.path}
            data-testid="important-file"
            className={`w-full rounded px-2 py-1 text-left ${active === file.path ? "bg-sky-50 dark:bg-sky-950" : ""}`}
          >
            <span className="font-mono text-xs">{file.path}</span>
            <span className="block text-[11px] text-neutral-500">{file.role}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Analysis details, collapsed
// ---------------------------------------------------------------------------

export function AnalysisDetails({
  graph,
  overlay,
}: {
  graph: ArchitectureGraph;
  overlay: { runs: string[]; notes: string[] } | null;
}) {
  const evidence = graph.nodes.reduce((sum, n) => sum + n.source_references.length, 0);
  const dropped = Object.entries(graph.dropped);
  return (
    <details className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800" data-testid="analysis-details">
      <summary className="cursor-pointer text-sm">
        <span className="font-medium">✓ Repository analyzed</span>
        <span className="ml-2 text-xs text-neutral-500" data-testid="analysis-counts">
          {evidence} pieces of evidence · {graph.nodes.length} components · {graph.subsystems?.length ?? 0} parts ·{" "}
          {graph.primary_flows.length} flows
        </span>
      </summary>
      <div className="mt-3 flex flex-col gap-3 text-xs">
        <p>
          {graph.generated_by === "deterministic"
            ? "Built by RepoLab's deterministic analysis; no model output was used."
            : "Built by RepoLab's analysis, with names, explanations and concepts from Nemotron — each kept only where it cites evidence."}
        </p>
        {graph.completeness === "partial" && (
          <section
            data-testid="partial-architecture"
            className="rounded border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/40"
          >
            <p className="font-semibold">Architecture partially detected</p>
            <ul className="mt-1 list-disc pl-5">
              {graph.gaps.map((gap) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
          </section>
        )}
        {graph.completeness !== "partial" && graph.gaps.length > 0 && (
          <ul className="list-disc pl-5 text-neutral-600 dark:text-neutral-400">
            {graph.gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        )}
        {dropped.length > 0 && (
          <div>
            <p className="font-medium">Refused during validation</p>
            <ul className="list-disc pl-5 text-neutral-600 dark:text-neutral-400" data-testid="dropped">
              {dropped.map(([reason, count]) => (
                <li key={reason}>
                  {count} × {reason}
                </li>
              ))}
            </ul>
          </div>
        )}
        {overlay && overlay.runs.length > 0 && <p>Measured in: {overlay.runs.join(", ")}</p>}
        {overlay && overlay.notes.length > 0 && <p>{overlay.notes.join(" ")}</p>}
      </div>
    </details>
  );
}

export function MeasuredList({ values }: { values: MeasuredValue[] }) {
  return (
    <ul className="flex flex-col gap-1 text-xs" data-testid="runtime-overlay">
      {values.map((value, index) => (
        <li key={`${value.name}-${index}`} className="flex justify-between gap-3">
          <span>{value.name}</span>
          <span className="font-mono">
            {formatMeasured(value.value, value.unit)}
            <span className="ml-2 text-[10px] text-neutral-400">{value.source}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
