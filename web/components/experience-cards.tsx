/**
 * The things to do with a repository, best first: a ★ recommended experience, a
 * few more, and -- when nothing real can run -- why, what RepoLab can run, and
 * a Mini Lab clearly marked as a simulation.
 *
 * Each card answers: what will I do, what will I learn, is it real or
 * simulated, can RepoLab run it, and what happens when I click. Everything on a
 * card comes from the backend's experience definition; nothing is hardcoded per
 * repository.
 */

"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { RealityBadge } from "./experience-workspace";
import {
  ActionLink,
  Badge,
  Button,
  ButtonLink,
  Chips,
  RunIcon,
  StatusBadge,
  type Status,
} from "./ui";
import type { ExperienceDefinition, ExperienceListing, ExperienceState, RuntimeOption } from "@/lib/types";

/** Plain names for the backend's experience types, used as categories. */
const CATEGORY: Record<string, string> = {
  web_app: "Web app",
  http_api: "API",
  cli: "CLI",
  code_playground: "Code",
  data_playground: "Data processing",
  query_console: "SQL",
  notebook: "Notebook",
  workflow_runner: "Workflows",
  streaming_demo: "Streaming",
  database_console: "Database",
  ml_inference: "Machine learning",
  library_playground: "Library",
  service: "Service",
  multi_service_app: "Application",
  mini_lab: "Simulation",
};

export function categoryOf(experience: ExperienceDefinition): string {
  return CATEGORY[experience.experience_type] ?? experience.experience_type.replace(/_/g, " ");
}

const STATE: Record<ExperienceState, { status: Status; label: string }> = {
  running: { status: "live", label: "Running" },
  runnable: { status: "ready", label: "Ready" },
  supported: { status: "unavailable", label: "Not runnable here" },
  detected: { status: "unknown", label: "Detected" },
};

type Handlers = {
  jobId: string;
  starting: string | null;
  onStart: (experience: ExperienceDefinition, option: RuntimeOption | null) => void;
  onContinue: (experience: ExperienceDefinition) => void;
};

export function ExperienceCards({ listing, ...handlers }: { listing: ExperienceListing } & Handlers) {
  const categories = useMemo(() => {
    const seen = new Map<string, string>();
    for (const experience of listing.experiences) seen.set(experience.experience_type, categoryOf(experience));
    return [...seen.entries()];
  }, [listing.experiences]);
  const [filter, setFilter] = useState("all");
  const shown = filter === "all" ? listing.experiences : listing.experiences.filter((e) => e.experience_type === filter);
  const [primary, ...more] = listing.experiences;
  const showPrimary = primary && shown.includes(primary);
  const rest = more.filter((experience) => shown.includes(experience));

  return (
    <div className="flex flex-col gap-6" data-testid="experience-cards">
      {categories.length >= 2 && (
        <Chips
          label="Filter experiences"
          value={filter}
          onChange={setFilter}
          items={[{ id: "all", label: "Recommended" }, ...categories.map(([id, label]) => ({ id, label }))]}
        />
      )}
      {showPrimary && <Card experience={primary} primary {...handlers} />}
      {rest.length > 0 && (
        <section>
          {filter === "all" && (
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-fg-muted">Explore more</h2>
          )}
          <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {rest.map((experience) => (
              <li key={experience.id}>
                <Card experience={experience} {...handlers} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {listing.experiences.length === 0 && <NothingRuns listing={listing} {...handlers} />}
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
}: { experience: ExperienceDefinition; primary?: boolean } & Handlers) {
  const busy = starting === experience.id;
  const state = STATE[experience.state];
  const simulated = experience.adapter === "minilab";
  return (
    <article
      className={`flex h-full flex-col gap-4 rounded-lg border bg-surface transition-colors duration-150 ${
        primary ? "border-run-line border-t-2 border-t-run p-5 sm:p-6" : "border-line p-5 hover:border-line-strong"
      }`}
      data-testid={primary ? "primary-experience" : "experience-card"}
      data-experience-id={experience.id}
      data-state={experience.state}
    >
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          {primary ? (
            <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-run-fg">
              <span aria-label="primary">★</span> Recommended
            </span>
          ) : (
            <Badge>{categoryOf(experience)}</Badge>
          )}
          <StatusBadge status={state.status}>{state.label}</StatusBadge>
        </div>
        <h3 className={`${primary ? "text-xl" : "text-card"} font-semibold tracking-tight text-fg`}>
          {experience.title}
        </h3>
        <p className="text-sm text-fg-secondary">{experience.summary}</p>
      </header>

      {primary && (
        <dl className="grid gap-4 border-y border-line py-4 text-sm sm:grid-cols-3">
          <Detail label="What you'll do">{experience.what_you_do}</Detail>
          <Detail label="What you'll learn">{experience.what_you_learn}</Detail>
          {experience.what_to_notice && <Detail label="What to notice">{experience.what_to_notice}</Detail>}
        </dl>
      )}
      {primary && <Badge className="self-start">{categoryOf(experience)}</Badge>}

      <div className="mt-auto flex flex-col gap-3">
        {experience.state === "running" && experience.session_id ? (
          <Button
            variant="primary"
            tone="run"
            onClick={() => onContinue(experience)}
            data-testid="continue-experience"
            className="self-start"
            icon={<RunIcon size={14} />}
          >
            Continue where you left off
          </Button>
        ) : experience.route && experience.route !== "#live" ? (
          <>
            {experience.options[0] && <OptionLine option={experience.options[0]} />}
            <ButtonLink
              variant={primary ? "primary" : "secondary"}
              tone="run"
              href={`/r/${jobId}${experience.route}`}
              data-testid="open-experience"
              className="self-start"
              icon={<RunIcon size={14} />}
            >
              Start
            </ButtonLink>
          </>
        ) : experience.route === "#live" ? (
          <>
            {experience.options[0] && <OptionLine option={experience.options[0]} />}
            <Button
              variant={primary ? "primary" : "secondary"}
              tone="run"
              onClick={() => onStart(experience, null)}
              data-testid="start-experience"
              className="self-start"
              icon={<RunIcon size={14} />}
            >
              Start
            </Button>
          </>
        ) : (
          experience.options.map((option, index) => (
            <div key={option.strategy} className="flex flex-col gap-2">
              <OptionLine option={option} />
              <Button
                variant={primary && index === 0 && option.ready ? "primary" : "secondary"}
                tone={simulated ? "exp" : "run"}
                disabled={busy}
                onClick={() => onStart(experience, option)}
                data-testid="start-experience"
                data-strategy={option.strategy}
                className="self-start"
              >
                {busy ? "Starting…" : option.ready ? "Start" : option.reality === "real_repository" ? "Build and start" : "Start"}
              </Button>
            </div>
          ))
        )}
      </div>
    </article>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wider text-fg-muted">{label}</dt>
      <dd className="mt-1 text-fg">{children}</dd>
    </div>
  );
}

function OptionLine({ option }: { option: RuntimeOption }) {
  return (
    <div className="text-meta">
      <div className="flex flex-wrap items-center gap-2">
        <RealityBadge reality={option.reality} />
        <span className="text-fg">{option.label}</span>
        {option.estimate && <span className="text-fg-muted">· {option.estimate}</span>}
      </div>
      {option.disclosure && (
        <p className="mt-1 text-fg-secondary" data-testid="option-disclosure">
          {option.disclosure}
        </p>
      )}
    </div>
  );
}

/** What happened, why, and what to do instead -- never a dead end. */
function NothingRuns({ listing, ...handlers }: { listing: ExperienceListing } & Handlers) {
  const { jobId } = handlers;
  return (
    <section className="flex flex-col gap-5 rounded-lg border border-line bg-surface p-5 sm:p-6" data-testid="nothing-runs">
      <div>
        <h2 className="text-card font-semibold text-fg">No runnable experiences yet</h2>
        <p className="mt-1 text-sm text-fg-secondary">
          RepoLab mapped this repository&apos;s architecture, but couldn&apos;t create a safe environment to
          run it.
        </p>
      </div>
      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">Why</h3>
          <ul className="mt-2 list-disc pl-5 text-sm text-fg" data-testid="why-not">
            {listing.nothing_runs_because.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">RepoLab runs</h3>
          <ul className="mt-2 list-disc pl-5 text-sm text-fg-secondary">
            {listing.supported_kinds.map((kind) => (
              <li key={kind}>{kind}</li>
            ))}
          </ul>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <ActionLink kind="arch" size="md" href={`/r/${jobId}/architecture`} data-testid="explore-architecture">
          Explore architecture
        </ActionLink>
        <ActionLink
          kind="ask"
          size="md"
          href={`/r/${jobId}/ask?q=${encodeURIComponent("How do I build and run this repository locally?")}`}
          data-testid="ask-run-locally"
        >
          Ask how to run it locally
        </ActionLink>
      </div>
      {listing.fallback && (
        <div className="rounded-lg border-2 border-dashed border-exp-line bg-exp-soft p-1" data-testid="minilab-card">
          <p className="px-4 pt-3 text-xs font-semibold uppercase tracking-wider text-exp-fg">
            Or try a simulation
          </p>
          <div className="p-3">
            <Card experience={listing.fallback} {...handlers} />
          </div>
        </div>
      )}
      <p className="text-xs text-fg-muted">
        Prefer to read first?{" "}
        <Link href={`/r/${jobId}`} className="text-brand-fg hover:underline">
          Back to the overview
        </Link>
      </p>
    </section>
  );
}
