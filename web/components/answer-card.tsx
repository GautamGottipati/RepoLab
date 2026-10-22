/**
 * An answered question: what kind it was, the answer, and what backs it.
 *
 * Shown when triage decided a question was conceptual or about the code, or was
 * testable with nothing here to test it on. The last case says so plainly — a
 * question that could have been an experiment is never quietly presented as if
 * explaining it were the same thing.
 */

"use client";

import { ClaimList, EvidencePanel, UnsupportedClaims } from "./evidence";
import { Badge, Note, Panel } from "./ui";
import type { AnswerResult, ExperimentState, Triage } from "@/lib/types";

const KIND_LABEL: Record<Triage["kind"], string> = {
  conceptual: "Conceptual question",
  repository: "Question about this repository",
  testable: "Testable question",
};

/** How a question was classified, and what RepoLab chose to do about it. */
export function TriageNote({ triage }: { triage: Triage }) {
  const action =
    triage.route === "experiment"
      ? `designing an experiment on this repository's ${
          triage.backend === "cli" ? "built environment" : "Spark job"
        }`
      : "answering from the repository and its official documentation";

  return (
    <p className="text-xs text-neutral-500" data-testid="triage">
      <Badge>{KIND_LABEL[triage.kind]}</Badge>{" "}
      <span className="ml-1">
        {triage.reason} — {action}.
      </span>
      {triage.decided_by === "model" && (
        <span className="ml-1 text-neutral-400">(classified by Nemotron Nano)</span>
      )}
    </p>
  );
}

/** The answer, its claims, and the evidence they cite. */
export function AnswerCard({ result, triage }: { result: AnswerResult; triage: Triage | null }) {
  const verified = result.answer;

  return (
    <div className="flex flex-col gap-5" data-testid="answer-card">
      {triage && <TriageNote triage={triage} />}

      {result.downgraded && <Note>{result.downgraded}</Note>}

      {verified ? (
        <>
          <UnsupportedClaims claims={verified.unsupported} />
          <p className="text-sm leading-relaxed" data-testid="answer">
            {verified.answer.answer}
          </p>
          <ClaimList claims={verified.answer.claims} />
          {verified.answer.caveats.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                Caveats
              </h4>
              <ul className="mt-2 list-disc pl-5 text-sm text-neutral-600 dark:text-neutral-400">
                {verified.answer.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          {result.error ?? "No answer could be produced."}
        </p>
      )}

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Evidence
        </h4>
        <EvidencePanel evidence={result.evidence} initialTab="code" />
      </div>
    </div>
  );
}

/** The results screen for a question that was answered rather than tested. */
export function AnsweredQuestion({ experiment }: { experiment: ExperimentState }) {
  const result = experiment.answer;
  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={experiment.question}
        subtitle="Answered from the repository and its official documentation; nothing was run"
        action={
          result?.answer ? (
            result.answer.grounded ? (
              <Badge tone="good">{result.answer.checked_claims} claims verified</Badge>
            ) : (
              <Badge tone="bad">unverified claims</Badge>
            )
          ) : undefined
        }
      >
        {result ? (
          <AnswerCard result={result} triage={experiment.triage} />
        ) : (
          <p className="text-sm text-neutral-500">The answer has not been recorded.</p>
        )}
      </Panel>
    </div>
  );
}
