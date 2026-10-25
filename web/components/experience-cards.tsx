/**
 * The things to do with a repository, best first: a ★ primary experience, a few
 * more, and -- when nothing real can run -- why, what RepoLab can run, and a
 * Mini Lab clearly marked as a simulation.
 *
 * Each card answers four questions: what will I do, what will I learn, is it
 * real or simulated, and can RepoLab run it -- and how long until a result.
 */

"use client";

import Link from "next/link";

import { RealityBadge } from "./experience-workspace";
import type { ExperienceDefinition, ExperienceListing, RuntimeOption } from "@/lib/types";

export function ExperienceCards({
  listing,
  jobId,
  starting,
  onStart,
  onContinue,
}: {
  listing: ExperienceListing;
  jobId: string;
  starting: string | null;
  onStart: (experience: ExperienceDefinition, option: RuntimeOption | null) => void;
  onContinue: (experience: ExperienceDefinition) => void;
}) {
  const [primary, ...more] = listing.experiences;
  return (
    <div className="flex flex-col gap-5" data-testid="experience-cards">
      {primary && (
        <Card experience={primary} jobId={jobId} primary starting={starting} onStart={onStart} onContinue={onContinue} />
      )}
      {more.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Explore more</h2>
          <ul className="mt-2 grid gap-4 lg:grid-cols-2">
            {more.map((experience) => (
              <li key={experience.id}>
                <Card experience={experience} jobId={jobId} starting={starting} onStart={onStart} onContinue={onContinue} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {listing.experiences.length === 0 && (
        <NothingRuns listing={listing} jobId={jobId} starting={starting} onStart={onStart} onContinue={onContinue} />
      )}
    </div>
  );
}

function Card({
  experience,
  jobId,
  primary = false,
  starting,
  onStart,
  onContinue,
}: {
  experience: ExperienceDefinition;
  jobId: string;
  primary?: boolean;
  starting: string | null;
  onStart: (experience: ExperienceDefinition, option: RuntimeOption | null) => void;
  onContinue: (experience: ExperienceDefinition) => void;
}) {
  const busy = starting === experience.id;
  return (
    <article
      className={`flex h-full flex-col gap-3 rounded-lg border p-4 ${primary ? "border-neutral-900 dark:border-neutral-200" : "border-neutral-200 dark:border-neutral-800"}`}
      data-testid={primary ? "primary-experience" : "experience-card"}
      data-experience-id={experience.id}
      data-state={experience.state}
    >
      <header>
        <h3 className={`${primary ? "text-lg" : "text-base"} font-semibold`}>
          {primary && <span aria-label="primary" className="mr-1">★</span>}
          {experience.title}
        </h3>
        <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">{experience.summary}</p>
      </header>

      {primary && (
        <dl className="grid gap-2 text-sm sm:grid-cols-3">
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-neutral-500">What you&apos;ll do</dt><dd>{experience.what_you_do}</dd></div>
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-neutral-500">What you&apos;ll learn</dt><dd>{experience.what_you_learn}</dd></div>
          {experience.what_to_notice && <div><dt className="text-xs font-semibold uppercase tracking-wide text-neutral-500">What to notice</dt><dd>{experience.what_to_notice}</dd></div>}
        </dl>
      )}

      <div className="mt-auto flex flex-col gap-2">
        {experience.state === "running" && experience.session_id ? (
          <button type="button" onClick={() => onContinue(experience)} data-testid="continue-experience" className="self-start rounded bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white">
            ● Continue where you left off
          </button>
        ) : experience.route && experience.route !== "#live" ? (
          <>
            {experience.options[0] && <OptionLine option={experience.options[0]} />}
            <Link href={`/r/${jobId}${experience.route}`} data-testid="open-experience" className="self-start rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white dark:bg-neutral-100 dark:text-neutral-900">
              Start →
            </Link>
          </>
        ) : experience.route === "#live" ? (
          <>
            {experience.options[0] && <OptionLine option={experience.options[0]} />}
            <button type="button" onClick={() => onStart(experience, null)} data-testid="start-experience" className="self-start rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white dark:bg-neutral-100 dark:text-neutral-900">
              Start →
            </button>
          </>
        ) : (
          experience.options.map((option) => (
            <div key={option.strategy} className="flex flex-col gap-1">
              <OptionLine option={option} />
              <button
                type="button"
                disabled={busy}
                onClick={() => onStart(experience, option)}
                data-testid="start-experience"
                data-strategy={option.strategy}
                className={`self-start rounded px-3 py-1.5 text-sm font-medium disabled:opacity-50 ${
                  option.ready ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900" : "border border-neutral-300 dark:border-neutral-700"
                }`}
              >
                {busy ? "Starting…" : option.ready ? "Start" : option.reality === "real_repository" ? "Build and start" : "Start"}
              </button>
            </div>
          ))
        )}
      </div>
    </article>
  );
}

function OptionLine({ option }: { option: RuntimeOption }) {
  return (
    <div className="text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <RealityBadge reality={option.reality} />
        <span className="text-neutral-700 dark:text-neutral-300">{option.label}</span>
        <span className="text-neutral-500">· {option.estimate}</span>
      </div>
      {option.disclosure && <p className="mt-0.5 text-neutral-500" data-testid="option-disclosure">{option.disclosure}</p>}
    </div>
  );
}

function NothingRuns({
  listing,
  jobId,
  starting,
  onStart,
  onContinue,
}: {
  listing: ExperienceListing;
  jobId: string;
  starting: string | null;
  onStart: (experience: ExperienceDefinition, option: RuntimeOption | null) => void;
  onContinue: (experience: ExperienceDefinition) => void;
}) {
  return (
    <section className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-neutral-50 p-4 dark:border-neutral-800 dark:bg-neutral-900/40" data-testid="nothing-runs">
      <div>
        <h2 className="text-sm font-semibold">This repository cannot run in RepoLab yet</h2>
        <h3 className="mt-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">Why</h3>
        <ul className="mt-1 list-disc pl-5 text-sm text-neutral-700 dark:text-neutral-300" data-testid="why-not">
          {listing.nothing_runs_because.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
        <h3 className="mt-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">RepoLab runs</h3>
        <ul className="mt-1 list-disc pl-5 text-sm text-neutral-600 dark:text-neutral-400">
          {listing.supported_kinds.map((kind) => <li key={kind}>{kind}</li>)}
        </ul>
      </div>
      {listing.fallback && (
        <div className="rounded border-2 border-dashed border-amber-400 p-3" data-testid="minilab-card">
          <Card experience={listing.fallback} jobId={jobId} starting={starting} onStart={onStart} onContinue={onContinue} />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Link href={`/r/${jobId}/architecture`} data-testid="explore-architecture" className="rounded border border-neutral-300 px-3 py-1.5 text-xs font-medium dark:border-neutral-700">
          Explore the architecture →
        </Link>
        <Link href={`/r/${jobId}/ask?q=${encodeURIComponent("How do I build and run this repository locally?")}`} data-testid="ask-run-locally" className="rounded border border-neutral-300 px-3 py-1.5 text-xs font-medium dark:border-neutral-700">
          Ask how to run it locally →
        </Link>
      </div>
    </section>
  );
}
