/**
 * Available Experiences — `/r/{jobId}/experiences`
 *
 * A repository is never reduced to one application type, so this screen is a
 * list rather than an answer: every interaction surface the investigation
 * found, each with the evidence that justified it, and each saying plainly
 * whether RepoLab can run it.
 *
 * The rule the screen exists to keep: **nothing unsupported is presented as
 * working.** A surface RepoLab cannot run has no link, no button and no
 * suggestion that one is coming — it has the sentence the specification
 * requires, the specific reason beside it, and its evidence, which is still
 * worth reading.
 *
 * Spec: docs/design/08-experiences.md
 */

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useWorkspace } from "../workspace";
import { Badge, Empty, ErrorNote, Skeleton } from "@/components/ui";
import { api, ApiError, optional } from "@/lib/api";
import type { AvailableExperience, ExperienceCatalog } from "@/lib/types";

export default function ExperiencesPage() {
  const { jobId, manifest } = useWorkspace();
  const [catalog, setCatalog] = useState<ExperienceCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!manifest) return;
    let cancelled = false;

    void (async () => {
      try {
        const next = await optional(api.experiences(jobId));
        if (!cancelled && next) setCatalog(next);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [jobId, manifest]);

  if (!manifest) return <Skeleton rows={5} />;

  return (
    <div className="flex flex-col gap-5">
      {error && <ErrorNote>{error}</ErrorNote>}

      {!catalog && !error && <Skeleton rows={5} />}

      {catalog && (
        <>
          <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-lg font-semibold tracking-tight">Available Experiences</h1>
            <Badge>{catalog.surface_count} surfaces detected</Badge>
            {catalog.hybrid && (
              // Wrapped rather than passed through: `Badge` takes children and a
              // tone, and silently drops anything else.
              <span data-testid="hybrid-badge">
                <Badge tone="good">hybrid repository</Badge>
              </span>
            )}
            <Badge tone={catalog.runnable_count > 0 ? "good" : "bad"}>
              {catalog.runnable_count} runnable here
            </Badge>
            <p className="w-full text-sm text-neutral-500">
              RepoLab detects every way a repository can be interacted with, and does not force
              it into one. {catalog.rationale}
            </p>
          </header>

          {catalog.experiences.length === 0 && (
            <Empty title="No interaction surface was detected">
              The investigation found nothing it could justify, so nothing is offered. Its
              confidence and limits are on the Investigation screen.
            </Empty>
          )}

          <ul className="grid gap-4 lg:grid-cols-2" data-testid="experience-list">
            {catalog.experiences.map((experience) => (
              <ExperienceCard
                key={experience.surface}
                jobId={jobId}
                experience={experience}
                expanded={selected === experience.surface}
                onSelect={() =>
                  setSelected(selected === experience.surface ? null : experience.surface)
                }
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ExperienceCard({
  jobId,
  experience,
  expanded,
  onSelect,
}: {
  jobId: string;
  experience: AvailableExperience;
  expanded: boolean;
  onSelect: () => void;
}) {
  const border = experience.runnable
    ? "border-emerald-300 dark:border-emerald-800"
    : "border-neutral-200 dark:border-neutral-800";

  return (
    <li
      data-testid={`experience-${experience.surface}`}
      data-status={experience.status}
      className={`rounded-lg border bg-white dark:bg-neutral-950 ${border}`}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-expanded={expanded}
        data-testid={`select-${experience.surface}`}
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
      >
        <span aria-hidden className="mt-0.5 font-mono text-lg leading-none">
          {experience.icon}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-2">
            <span className="text-sm font-semibold">{experience.label}</span>
            {experience.primary && <Badge>primary</Badge>}
            {experience.weak && <Badge tone="bad">weak signal</Badge>}
            <span className="ml-auto font-mono text-xs text-neutral-400">
              {experience.confidence.toFixed(2)}
            </span>
          </span>
          <span className="mt-0.5 block text-xs text-neutral-500">{experience.name}</span>
        </span>
      </button>

      <div className="border-t border-neutral-200 px-4 py-3 dark:border-neutral-800">
        {experience.runnable && experience.route ? (
          <div data-testid={`runnable-${experience.surface}`}>
            <p className="text-sm text-emerald-700 dark:text-emerald-300">
              Runnable in this build, through RepoLab&apos;s {experience.adapter}.
            </p>
            <Link
              href={`/r/${jobId}${experience.route}`}
              data-testid={`open-${experience.surface}`}
              className="mt-2 inline-block rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
            >
              Open the {experience.label} experience →
            </Link>
          </div>
        ) : (
          <p
            data-testid={`unavailable-${experience.surface}`}
            className="text-sm text-amber-800 dark:text-amber-300"
          >
            {experience.unavailable_reason}
          </p>
        )}
      </div>

      {expanded && (
        <div
          data-testid={`evidence-${experience.surface}`}
          className="border-t border-neutral-200 px-4 py-3 dark:border-neutral-800"
        >
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Why this was detected
          </h3>
          <p className="mt-1 font-mono text-xs text-neutral-600 dark:text-neutral-400">
            {experience.reason}
          </p>

          <h3 className="mt-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Evidence in the repository
          </h3>
          <ul className="mt-1 flex flex-col gap-0.5">
            {experience.evidence.map((reference, index) => (
              <li
                key={`${reference.path}-${index}`}
                className="font-mono text-[11px] text-neutral-600 dark:text-neutral-400"
              >
                {reference.path}
                {reference.start_line ? `:${reference.start_line}` : ""}
              </li>
            ))}
            {experience.evidence.length === 0 && (
              <li className="text-xs text-neutral-400">No files cited.</li>
            )}
          </ul>
        </div>
      )}
    </li>
  );
}
