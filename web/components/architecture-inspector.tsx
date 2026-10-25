/**
 * One component or concept, explained -- meaning first, evidence after.
 *
 *     What is this? / Why does it matter here? / Role in this system /
 *     How does it connect? / Where is it implemented?
 *     Why RepoLab detected it / Source evidence /
 *     Static · Inferred · Measured / Questions / actions
 *
 * Information is still split by what backs it -- **Static** (read from
 * source), **Inferred** (interpreted, by RepoLab or Nemotron) and **Measured**
 * (from a real run) -- and a value never moves between those sections.
 *
 * **Experiment** appears only when the API says RepoLab can really run
 * something that exercises this. Otherwise nothing is shown, except one quiet
 * line when the only thing missing is a built environment.
 */

"use client";

import Link from "next/link";

import { Badge } from "./ui";
import {
  CATEGORY_LABEL,
  EVIDENCE_KIND_LABEL,
  askUrl,
  conceptRelationships,
  formatMeasured,
  memberNodes,
  relationships,
  sourceUrl,
  subsystemFor,
} from "@/lib/architecture";
import type {
  ArchitectureGraph,
  ArchitectureNode,
  Evidence,
  MeasuredValue,
  NodeActions,
  PropertyValue,
  SystemConcept,
} from "@/lib/types";

type Navigate = {
  /** Select a component or concept by id. */
  onSelect: (id: string) => void;
  /** Open a subsystem's drill-down. */
  onExplore: (subsystemId: string) => void;
};

type Common = {
  jobId: string;
  repoUrl: string;
  commit: string;
  graph: ArchitectureGraph;
  actions: NodeActions | undefined;
  measured: MeasuredValue[];
} & Navigate;

// ---------------------------------------------------------------------------
// A component
// ---------------------------------------------------------------------------

export function ArchitectureInspector({
  node,
  ...common
}: Common & { node: ArchitectureNode }) {
  const { jobId, repoUrl, commit, graph, actions, measured, onSelect, onExplore } = common;
  const inferred = new Set(node.inferred_fields);
  const connections = relationships(graph, node.id);
  const firstRef = node.source_references[0];
  const openCode = firstRef ? sourceUrl(repoUrl, commit, firstRef) : null;
  const questions = actions?.suggested_questions ?? [];
  const home = subsystemFor(graph, node);
  const incoming = graph.edges.filter((e) => e.target === node.id);
  const outgoing = graph.edges.filter((e) => e.source === node.id);
  const label = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id;
  const properties = Object.entries(node.properties).filter(
    ([key]) => !["line_start", "line_end", "actor"].includes(key),
  );
  const files = [...new Set(node.source_references.map((r) => r.path))];

  return (
    <section data-testid="architecture-inspector" className="flex flex-col gap-4">
      <header>
        <h3 className="text-base font-semibold" data-testid="inspector-title">
          {node.label}
          {inferred.has("label") && <InferredTag />}
        </h3>
        <div className="mt-1 flex flex-wrap gap-1.5">
          <Badge>{CATEGORY_LABEL[node.category]}</Badge>
          {node.subtype && <Badge>{node.subtype.replace(/_/g, " ")}</Badge>}
          {node.technology && <Badge tone="neutral">{node.technology}</Badge>}
        </div>
      </header>

      <Meaning title="What is this?" testId="what-is-it">
        {node.description ? (
          <>
            {node.description}
            {inferred.has("description") && <InferredTag />}
          </>
        ) : (
          <>
            A {CATEGORY_LABEL[node.category]} RepoLab found in the code.{" "}
            <span className="text-neutral-500">No plain-language description was written for it.</span>
          </>
        )}
      </Meaning>

      {node.runtime_significance && (
        <Meaning title="Why does it matter here?">
          {node.runtime_significance}
          {inferred.has("runtime_significance") && <InferredTag />}
        </Meaning>
      )}

      {(incoming.length > 0 || outgoing.length > 0) && (
        <Meaning title="Role in this system">
          <RoleDiagram
            before={incoming.map((e) => ({ id: e.source, label: label(e.source), how: e.type }))}
            self={node.label}
            after={outgoing.map((e) => ({ id: e.target, label: label(e.target), how: e.type }))}
            onSelect={onSelect}
          />
        </Meaning>
      )}

      <Meaning title="How does it connect?">
        {connections.length === 0 ? (
          <span className="text-neutral-500">No relationship could be evidenced.</span>
        ) : (
          <ul className="flex flex-col gap-1 text-xs" data-testid="connections">
            {connections.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
      </Meaning>

      <Meaning title="Where is it implemented?">
        {home && (
          <p className="text-xs">
            Part of{" "}
            <button type="button" className="underline underline-offset-2" onClick={() => onExplore(home.id)}>
              {home.label}
            </button>
          </p>
        )}
        <ul className="mt-1 flex flex-col gap-0.5 text-xs" data-testid="implemented-in">
          {files.map((path) => (
            <li key={path}>
              <span className="font-medium">{path.split("/").pop()}</span>{" "}
              <span className="text-neutral-500">{path.split("/").slice(0, -1).join("/")}</span>
            </li>
          ))}
        </ul>
      </Meaning>

      <Evidenced>
        <p className="text-xs text-neutral-600 dark:text-neutral-400" data-testid="detected-because">
          <span className="font-medium">Why RepoLab detected it:</span> {node.detected_because}
        </p>
        <SourceEvidence repoUrl={repoUrl} commit={commit} node={node} />

        <Section title="Static" hint="read directly from the source">
          {properties.length === 0 && node.children.length === 0 ? (
            <p className="text-xs text-neutral-500">The references above.</p>
          ) : (
            <dl className="grid gap-1 text-xs">
              {properties.map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <dt className="shrink-0 text-neutral-500">{key.replace(/_/g, " ")}</dt>
                  <dd className="font-mono break-all">{render(value)}</dd>
                </div>
              ))}
              {node.children.length > 0 && (
                <div className="flex gap-2">
                  <dt className="shrink-0 text-neutral-500">groups</dt>
                  <dd>{node.children.join(", ")}</dd>
                </div>
              )}
            </dl>
          )}
        </Section>
        <Section title="Inferred" hint="interpreted from the evidence; not itself evidence">
          {node.inferred_fields.length > 0 ? (
            <p className="text-xs">Written by Nemotron: {node.inferred_fields.join(", ").replace(/_/g, " ")}.</p>
          ) : (
            <p className="text-xs text-neutral-500">Nothing inferred beyond the static facts.</p>
          )}
        </Section>
        <MeasuredSection measured={measured} />
      </Evidenced>

      <Questions jobId={jobId} id={node.id} questions={questions} />
      <Actions jobId={jobId} id={node.id} label={node.label} openCode={openCode} actions={actions} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// A concept of the system model
// ---------------------------------------------------------------------------

export function ConceptInspector({
  concept,
  ...common
}: Common & { concept: SystemConcept }) {
  const { jobId, repoUrl, commit, graph, actions, measured, onSelect, onExplore } = common;
  const inferred = concept.provenance === "inferred";
  const concepts = graph.system_model?.concepts ?? [];
  const links = graph.system_model?.links ?? [];
  const label = (id: string) => concepts.find((c) => c.id === id)?.label ?? id;
  const subsystems = (graph.subsystems ?? []).filter((s) => concept.subsystem_ids.includes(s.id));
  const members = memberNodes(graph, concept.member_ids);
  const firstPath = concept.evidence.find((e) => e.path && e.kind !== "subsystem");
  const openCode = firstPath
    ? sourceUrl(repoUrl, commit, {
        path: firstPath.path!,
        start_line: firstPath.line,
        end_line: firstPath.line,
        commit_sha: null,
        excerpt: null,
      })
    : null;

  return (
    <section data-testid="architecture-inspector" className="flex flex-col gap-4">
      <header>
        <h3 className="text-base font-semibold" data-testid="inspector-title">
          {concept.label}
          {inferred && <InferredTag />}
        </h3>
        {concept.term && (
          <p className="mt-0.5 text-xs text-neutral-500" data-testid="concept-term">
            Internal name: <span className="font-mono">{concept.term}</span>
          </p>
        )}
      </header>

      <Meaning title="What is this?" testId="what-is-it">
        {concept.what_it_does}
        {inferred && <InferredTag />}
      </Meaning>
      {concept.why_it_matters && <Meaning title="Why does it matter here?">{concept.why_it_matters}</Meaning>}
      {concept.role && <Meaning title="Role in this system">{concept.role}</Meaning>}

      <Meaning title="How does it connect?">
        {links.some((l) => l.source === concept.id || l.target === concept.id) ? (
          <RoleDiagram
            before={links.filter((l) => l.target === concept.id).map((l) => ({ id: l.source, label: label(l.source), how: l.type }))}
            self={concept.label}
            after={links.filter((l) => l.source === concept.id).map((l) => ({ id: l.target, label: label(l.target), how: l.type }))}
            onSelect={onSelect}
          />
        ) : (
          <span className="text-neutral-500">No relationship to the other concepts could be evidenced.</span>
        )}
        <ul className="sr-only" data-testid="connections">
          {conceptRelationships(graph, concept.id).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </Meaning>

      <Meaning title="Where is it implemented?">
        {subsystems.length === 0 && members.length === 0 ? (
          <span className="text-neutral-500">See the evidence below.</span>
        ) : (
          <div className="flex flex-col gap-1.5 text-xs">
            {subsystems.map((s) => (
              <button
                key={s.id}
                type="button"
                data-testid="explore-subsystem"
                onClick={() => onExplore(s.id)}
                className="text-left underline underline-offset-2"
              >
                Explore {s.label} →
              </button>
            ))}
            {members.length > 0 && (
              <p>
                Components:{" "}
                {members.map((m, i) => (
                  <span key={m.id}>
                    {i > 0 && ", "}
                    <button type="button" className="underline underline-offset-2" onClick={() => onSelect(m.id)}>
                      {m.label}
                    </button>
                  </span>
                ))}
              </p>
            )}
          </div>
        )}
      </Meaning>

      <Evidenced>
        <EvidenceList
          evidence={concept.evidence}
          repoUrl={repoUrl}
          commit={commit}
          onSelect={onSelect}
          onExplore={onExplore}
        />
        <Section title={inferred ? "Inferred" : "Static"} hint={inferred ? "interpreted by Nemotron from the evidence above" : "taken from the repository's structure"}>
          <p className="text-xs text-neutral-500">
            {inferred
              ? "This concept was proposed by Nemotron and kept only because it cites the evidence above."
              : "This concept is a part of the repository RepoLab found from its structure."}
          </p>
        </Section>
        <MeasuredSection measured={measured} />
      </Evidenced>

      <Questions jobId={jobId} id={concept.id} questions={actions?.suggested_questions ?? []} />
      <Actions jobId={jobId} id={concept.id} label={concept.label} openCode={openCode} actions={actions} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Shared parts
// ---------------------------------------------------------------------------

function Meaning({ title, testId, children }: { title: string; testId?: string; children: React.ReactNode }) {
  return (
    <div data-testid={testId}>
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">{title}</h4>
      <div className="mt-1 text-sm text-neutral-800 dark:text-neutral-200">{children}</div>
    </div>
  );
}

/** Everything that answers "how do you know?", after the meaning. */
function Evidenced({ children }: { children: React.ReactNode }) {
  return (
    <div data-testid="evidence-sections" className="flex flex-col gap-3 border-t border-neutral-200 pt-3 dark:border-neutral-800">
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">How RepoLab knows</h4>
      {children}
    </div>
  );
}

function RoleDiagram({
  before,
  self,
  after,
  onSelect,
}: {
  before: { id: string; label: string; how: string }[];
  self: string;
  after: { id: string; label: string; how: string }[];
  onSelect: (id: string) => void;
}) {
  const item = (n: { id: string; label: string; how: string }) => (
    <li key={n.id}>
      <button type="button" onClick={() => onSelect(n.id)} className="underline underline-offset-2">
        {n.label}
      </button>
      <span className="ml-1 text-[10px] text-neutral-400">{n.how.replace(/_/g, " ")}</span>
    </li>
  );
  return (
    <div className="flex flex-col items-start gap-1 text-xs" data-testid="role-diagram">
      {before.length > 0 && <ul className="flex flex-col gap-0.5">{before.map(item)}</ul>}
      {before.length > 0 && <span aria-hidden className="text-neutral-400">↓</span>}
      <span className="rounded border border-neutral-400 px-2 py-0.5 font-medium">{self}</span>
      {after.length > 0 && <span aria-hidden className="text-neutral-400">↓</span>}
      {after.length > 0 && <ul className="flex flex-col gap-0.5">{after.map(item)}</ul>}
    </div>
  );
}

function SourceEvidence({ repoUrl, commit, node }: { repoUrl: string; commit: string; node: ArchitectureNode }) {
  return (
    <div>
      <p className="text-xs font-medium">Source evidence</p>
      <ul className="mt-1 flex flex-col gap-2" data-testid="source-references">
        {node.source_references.map((ref, index) => {
          const url = sourceUrl(repoUrl, commit, ref);
          const where = `${ref.path}${ref.start_line ? `:${ref.start_line}` : ""}`;
          return (
            <li key={`${where}-${index}`} className="font-mono text-xs">
              {url ? (
                <a href={url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
                  {where}
                </a>
              ) : (
                where
              )}
              {ref.excerpt && (
                <pre className="mt-1 overflow-x-auto rounded bg-neutral-100 p-2 text-[10px] text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
                  {ref.excerpt}
                </pre>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Citations of any kind -- a README line, a file, a doc page, a part, a component. */
export function EvidenceList({
  evidence,
  repoUrl,
  commit,
  onSelect,
  onExplore,
}: {
  evidence: Evidence[];
  repoUrl: string;
  commit: string;
  onSelect?: (id: string) => void;
  onExplore?: (id: string) => void;
}) {
  if (evidence.length === 0) return <p className="text-xs text-neutral-500">No evidence recorded.</p>;
  return (
    <ul className="flex flex-col gap-1.5 text-xs" data-testid="evidence-list">
      {evidence.map((item, index) => {
        const code =
          item.path && item.kind !== "subsystem"
            ? sourceUrl(repoUrl, commit, { path: item.path, start_line: item.line, end_line: item.line, commit_sha: null, excerpt: null })
            : null;
        let body: React.ReactNode = item.label;
        if (item.url) {
          body = (
            <a href={item.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
              {item.label}
            </a>
          );
        } else if (code) {
          body = (
            <a href={code} target="_blank" rel="noreferrer" className="font-mono underline underline-offset-2">
              {item.label}
            </a>
          );
        } else if (item.kind === "component" && item.target_id && onSelect) {
          body = (
            <button type="button" className="underline underline-offset-2" onClick={() => onSelect(item.target_id!)}>
              {item.label}
            </button>
          );
        } else if (item.kind === "subsystem" && item.target_id && onExplore) {
          body = (
            <button type="button" className="underline underline-offset-2" onClick={() => onExplore(item.target_id!)}>
              {item.label}
            </button>
          );
        }
        return (
          <li key={`${item.label}-${index}`} data-evidence-kind={item.kind}>
            <span className="mr-1.5 rounded bg-neutral-100 px-1 py-0.5 text-[9px] uppercase tracking-wide text-neutral-500 dark:bg-neutral-800">
              {EVIDENCE_KIND_LABEL[item.kind]}
            </span>
            {body}
            {item.excerpt && <span className="ml-1 text-neutral-500">“{item.excerpt}”</span>}
          </li>
        );
      })}
    </ul>
  );
}

function MeasuredSection({ measured }: { measured: MeasuredValue[] }) {
  return (
    <Section title="Measured" hint="observed during a real run">
      {measured.length === 0 ? (
        <p className="text-xs text-neutral-500" data-testid="nothing-measured">
          Nothing measured yet. Values appear here only after RepoLab runs this repository.
        </p>
      ) : (
        <ul className="flex flex-col gap-1 text-xs" data-testid="measured-values">
          {measured.map((value, index) => (
            <li key={`${value.name}-${index}`} className="flex justify-between gap-3">
              <span>{value.name}</span>
              <span className="font-mono">
                {formatMeasured(value.value, value.unit)}
                <span className="ml-2 text-[10px] text-neutral-400">{value.source}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Questions({ jobId, id, questions }: { jobId: string; id: string; questions: string[] }) {
  if (questions.length === 0) return null;
  return (
    <Section title="Questions">
      <ul className="flex flex-col gap-1.5" data-testid="suggested-questions">
        {questions.map((question) => (
          <li key={question}>
            <Link href={askUrl(jobId, id, question)} className="text-sm underline underline-offset-2">
              {question}
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Actions({
  jobId,
  id,
  label,
  openCode,
  actions,
}: {
  jobId: string;
  id: string;
  label: string;
  openCode: string | null;
  actions: NodeActions | undefined;
}) {
  const experiment = actions?.experiment;
  const ask = actions?.suggested_questions[0] ?? `What does ${label} do, and why does it exist?`;
  return (
    <div className="flex flex-col gap-2 border-t border-neutral-200 pt-3 dark:border-neutral-800">
      <div className="flex flex-wrap gap-2">
        {openCode && (
          <a
            href={openCode}
            target="_blank"
            rel="noreferrer"
            data-testid="open-code"
            className="rounded border border-neutral-300 px-3 py-1.5 text-xs font-medium dark:border-neutral-700"
          >
            Open code
          </a>
        )}
        <Link
          href={askUrl(jobId, id, ask)}
          data-testid="ask-why"
          className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
        >
          Ask RepoLab
        </Link>
        {experiment?.available && experiment.question && (
          <Link
            href={askUrl(jobId, id, experiment.question)}
            data-testid="experiment"
            className="rounded bg-violet-700 px-3 py-1.5 text-xs font-medium text-white"
          >
            Run experiment
          </Link>
        )}
      </div>
      {experiment && !experiment.available && experiment.needs_environment && (
        <p className="text-[11px] text-neutral-400" data-testid="experiment-later">
          Experiments available after building the environment.
        </p>
      )}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <h5 className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
        {title}
        {hint && <span className="ml-2 font-normal normal-case text-neutral-400">{hint}</span>}
      </h5>
      <div className="mt-1">{children}</div>
    </div>
  );
}

export function InferredTag() {
  return (
    <span
      title="Written by Nemotron from the evidence; not itself evidence"
      className="ml-1.5 rounded bg-neutral-100 px-1 py-0.5 align-middle text-[9px] font-normal uppercase tracking-wide text-neutral-500 dark:bg-neutral-800"
    >
      inferred
    </span>
  );
}

function render(value: PropertyValue): string {
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}
