/**
 * Experiences — `/r/{jobId}/experiences`
 *
 * "What can I actually do with this software?" A ★ primary experience and a few
 * more, each saying what you will do, what you will learn, whether it is the
 * real repository, an official release or a simulation, and how long until a
 * result. Starting one opens it here; its session survives a refresh
 * (``?session=``).
 *
 * Underneath, collapsed, the detected surfaces and their evidence: every way of
 * using the repository the investigation found, saying plainly whether RepoLab
 * can run it.
 *
 * The rule the screen exists to keep: **nothing unsupported is presented as
 * working.** A surface RepoLab cannot run has no link and no button -- it has
 * the sentence the specification requires and its specific reason. What *is*
 * available for every investigated repository (the architecture, Ask RepoLab)
 * is offered instead, so the screen is never a dead end.
 *
 * Spec: docs/design/08-experiences.md
 */

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { useWorkspace } from "../workspace";
import { ExperienceCards } from "@/components/experience-cards";
import { ExperienceWorkspace } from "@/components/experience-workspace";
import { LiveExperience } from "@/components/live-experience";
import { ActionLink, Badge, Empty, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { api, ApiError, optional } from "@/lib/api";
import { sourceUrl } from "@/lib/architecture";
import type {
  AvailableExperience,
  ExperienceCatalog,
  ExperienceDefinition,
  ExperienceListing,
  RuntimeOption,
} from "@/lib/types";

const STRENGTH: Record<NonNullable<AvailableExperience["strength"]>, string> = {
  strong: "Strong evidence",
  moderate: "Some evidence",
  weak: "Weak evidence",
};

export default function ExperiencesPage() {
  return (
    <Suspense fallback={<Skeleton rows={5} />}>
      <Experiences />
    </Suspense>
  );
}

function Experiences() {
  const { jobId, manifest, architecture } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const [catalog, setCatalog] = useState<ExperienceCatalog | null>(null);
  const [listing, setListing] = useState<ExperienceListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  // `?live=1` opens the live application, as the Overview's "Start" does.
  const [live, setLive] = useState(params.get("live") === "1");
  const session = params.get("session");

  useEffect(() => {
    if (!manifest) return;
    let cancelled = false;

    void (async () => {
      try {
        const [next, discovered] = await Promise.all([
          optional(api.experiences(jobId)),
          optional(api.experienceList(jobId)).catch(() => null),
        ]);
        if (!cancelled && next) setCatalog(next);
        if (!cancelled && discovered) setListing(discovered);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [jobId, manifest]);

  if (!manifest) return <Skeleton rows={5} />;

  const firstQuestion = architecture?.graph.understanding?.recommended_start ?? null;
  const openSession = (id: string | null) =>
    router.replace(id ? `/r/${jobId}/experiences?session=${id}` : `/r/${jobId}/experiences`);

  const start = async (experience: ExperienceDefinition, option: RuntimeOption | null) => {
    if (experience.adapter === "live") {
      setLive(true);
      return;
    }
    setStarting(experience.id);
    setError(null);
    try {
      const created = await api.startExperience(jobId, experience.id, option?.strategy);
      openSession(created.id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setStarting(null);
    }
  };

  const runnable = (listing?.experiences.length ?? 0) > 0;
  const liveOffered = listing?.experiences.some((e) => e.adapter === "live") ?? false;
  const liveRunning = listing?.experiences.some((e) => e.adapter === "live" && e.state === "running") ?? false;

  return (
    <div className="flex flex-col gap-8">
      {error && <ErrorNote>{error}</ErrorNote>}

      {!catalog && !error && <Skeleton rows={5} />}

      {listing && !session && (
        <div data-testid="experiences-header">
          <PageHeader
            eyebrow="Experiences"
            accent="run"
            title={runnable ? `Experience ${listing.name}` : listing.name}
            description={listing.headline}
          />
        </div>
      )}

      {session && catalog && (
        <ExperienceWorkspace
          sessionId={session}
          jobId={jobId}
          repoUrl={catalog.repo_url}
          commitSha={catalog.commit_sha}
          onClose={() => openSession(null)}
        />
      )}

      {!session && listing && (
        <ExperienceCards
          listing={listing}
          jobId={jobId}
          starting={starting}
          onStart={start}
          onContinue={(experience) => (experience.adapter === "live" ? setLive(true) : openSession(experience.session_id))}
        />
      )}

      {catalog && liveOffered && (live || liveRunning) && (
        <div id="live">
          <LiveExperience jobId={jobId} repoUrl={catalog.repo_url} commitSha={catalog.commit_sha} />
        </div>
      )}

      {catalog && (
        <details open={!runnable || undefined} className="flex flex-col gap-4 border-t border-line pt-6" data-testid="detected-surfaces">
          <summary className="cursor-pointer text-sm font-medium text-fg-secondary hover:text-fg">
            Detected surfaces &amp; evidence
            {listing && listing.also.length > 0 && (
              <span className="ml-2 text-xs font-normal">
                (also detected: {listing.also.map((e) => e.title).join(", ")})
              </span>
            )}
          </summary>
          <div className="mt-4 flex flex-col gap-5">
          <header className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h2 className="text-card font-semibold tracking-tight text-fg">Ways to use this repository</h2>
              {catalog.hybrid && (
                // Wrapped rather than passed through: `Badge` takes children and a
                // tone, and silently drops anything else.
                <span data-testid="hybrid-badge">
                  <Badge tone="attention">hybrid repository</Badge>
                </span>
              )}
            </div>
            <p className="text-sm text-fg-secondary" data-testid="experiences-summary">
              {summary(catalog)}
            </p>
            <p className="flex flex-wrap gap-x-4 text-xs text-fg-secondary">
              <span>{catalog.surface_count} detected</span>
              <span data-testid="runnable-count">{catalog.runnable_count} runnable in RepoLab</span>
            </p>
          </header>

          {!listing && catalog.experiences.length > 0 && catalog.runnable_count === 0 && (
            <section
              className="rounded-lg border border-line bg-surface-subtle p-4"
              data-testid="nothing-runnable"
            >
              <h2 className="text-sm font-semibold">RepoLab can&apos;t run this repository yet</h2>
              <p className="mt-1 text-sm text-fg-secondary">
                There is no live experience for these surfaces in this build. You can still learn
                how the code works: every component and relationship below the surface is mapped
                from the source.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <ActionLink kind="arch" href={`/r/${jobId}/architecture`} data-testid="explore-architecture">
                  Explore the architecture
                </ActionLink>
                <ActionLink
                  kind="ask"
                  href={
                    firstQuestion
                      ? `/r/${jobId}/ask?q=${encodeURIComponent(firstQuestion)}`
                      : `/r/${jobId}/ask`
                  }
                  data-testid="ask-instead"
                >
                  {firstQuestion ? `Ask: ${firstQuestion}` : "Ask RepoLab about it"}
                </ActionLink>
              </div>
            </section>
          )}

          {catalog.experiences.length === 0 && (
            <Empty title="No interaction surface was detected">
              The investigation found nothing it could justify, so nothing is offered. Its
              confidence and limits are under &ldquo;View analysis details&rdquo; on the Overview.
            </Empty>
          )}

          <ul className="grid gap-4 lg:grid-cols-2" data-testid="experience-list">
            {catalog.experiences.map((experience) => (
              <ExperienceCard
                key={experience.surface}
                jobId={jobId}
                repoUrl={catalog.repo_url}
                commitSha={catalog.commit_sha}
                experience={experience}
                expanded={selected === experience.surface}
                onSelect={() =>
                  setSelected(selected === experience.surface ? null : experience.surface)
                }
              />
            ))}
          </ul>

          {catalog.rationale && (
            <details className="text-xs text-fg-secondary" data-testid="classification-details">
              <summary className="cursor-pointer">How RepoLab decided</summary>
              <p className="mt-1 font-mono">{catalog.rationale}</p>
            </details>
          )}
          </div>
        </details>
      )}
    </div>
  );
}

/** One sentence: what was found, and which one RepoLab would lead with. */
function summary(catalog: ExperienceCatalog): string {
  const names = catalog.experiences.map((e) => inSentence(e.name));
  if (names.length === 0) return "RepoLab found no way to interact with this repository.";
  const listed =
    names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  const primary = catalog.experiences.find((e) => e.primary);
  const lead = primary && names.length > 1 ? ` The ${inSentence(primary.name)} is the main way in.` : "";
  return `RepoLab found ${names.length === 1 ? "one way" : `${names.length} ways`} to use it: ${listed}.${lead}`;
}

/** A name mid-sentence: "Web application" -> "web application", but "HTTP API" stays. */
function inSentence(name: string): string {
  return /^[A-Z][a-z]/.test(name) ? name.charAt(0).toLowerCase() + name.slice(1) : name;
}

function ExperienceCard({
  jobId,
  repoUrl,
  commitSha,
  experience,
  expanded,
  onSelect,
}: {
  jobId: string;
  repoUrl: string;
  commitSha: string;
  experience: AvailableExperience;
  expanded: boolean;
  onSelect: () => void;
}) {
  const border = experience.runnable
    ? "border-run-line"
    : "border-line";
  const strength = experience.strength ?? (experience.weak ? "weak" : "moderate");
  const signals = experience.signals?.length ? experience.signals : [experience.reason];

  return (
    <li
      data-testid={`experience-${experience.surface}`}
      data-status={experience.status}
      className={`flex flex-col rounded-lg border bg-surface ${border}`}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        <span aria-hidden className="mt-0.5 w-6 shrink-0 text-center font-mono text-lg leading-none">
          {experience.icon}
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-2">
            <span className="text-sm font-semibold">{experience.name}</span>
            {experience.primary && <Badge>primary</Badge>}
            <span
              className={`text-xs ${strength === "weak" ? "text-fg-muted" : "text-fg-secondary"}`}
              title={`confidence ${experience.confidence.toFixed(2)}`}
              data-testid={`strength-${experience.surface}`}
            >
              {STRENGTH[strength]}
            </span>
          </p>
          {experience.meaning && (
            <p className="mt-0.5 text-sm text-fg-secondary">{experience.meaning}</p>
          )}
        </div>
      </div>

      <div className="border-t border-line px-4 py-3">
        {experience.runnable && !experience.route ? (
          <p data-testid={`runnable-${experience.surface}`} className="text-sm text-run-fg">
            Runnable: RepoLab has a plan to start it from the repository&apos;s own scripts. Start it
            under &ldquo;Run it in RepoLab&rdquo;.
          </p>
        ) : experience.runnable && experience.route ? (
          <div data-testid={`runnable-${experience.surface}`}>
            <p className="text-sm text-run-fg">
              Runnable in this build, through RepoLab&apos;s {experience.adapter}.
            </p>
            <ActionLink
              kind="run"
              href={`/r/${jobId}${experience.route}`}
              data-testid={`open-${experience.surface}`}
              className="mt-2"
            >
              Open the {experience.label} experience
            </ActionLink>
          </div>
        ) : (
          <p data-testid={`unavailable-${experience.surface}`} className="text-sm text-fg-secondary">
            {experience.unavailable_reason}
          </p>
        )}
      </div>

      <div className="mt-auto border-t border-line">
        <button
          type="button"
          onClick={onSelect}
          aria-expanded={expanded}
          data-testid={`select-${experience.surface}`}
          className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs text-fg-secondary hover:text-fg"
        >
          <span aria-hidden>{expanded ? "▾" : "▸"}</span>
          Why RepoLab thinks so
        </button>
        {expanded && (
          <div data-testid={`evidence-${experience.surface}`} className="px-4 pb-3">
            <ul className="list-disc pl-5 text-sm text-fg">
              {signals.map((signal) => (
                <li key={signal}>{signal.charAt(0).toUpperCase() + signal.slice(1)}</li>
              ))}
            </ul>
            <h3 className="mt-3 text-xs font-semibold uppercase tracking-wide text-fg-secondary">
              Evidence in the repository
            </h3>
            <ul className="mt-1 flex flex-col gap-0.5">
              {experience.evidence.map((reference, index) => {
                const href = sourceUrl(repoUrl, commitSha, reference);
                const label = `${reference.path}${reference.start_line ? `:${reference.start_line}` : ""}`;
                return (
                  <li key={`${reference.path}-${index}`} className="break-all font-mono text-xs text-fg-secondary">
                    {href ? (
                      <a href={href} target="_blank" rel="noreferrer" className="underline-offset-2 hover:underline">
                        {label}
                      </a>
                    ) : (
                      label
                    )}
                  </li>
                );
              })}
              {experience.evidence.length === 0 && (
                <li className="text-xs text-fg-muted">No files cited.</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </li>
  );
}
