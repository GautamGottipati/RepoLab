/**
 * Overview — what this repository is, and what to do first.
 *
 * Answers, in about ten seconds: what is it, what does it do, how can I use
 * it, what should I explore first. While the investigation runs, it shows the
 * real steps the agent has announced. Every value on the screen comes from the
 * investigation (the architecture's understanding and system model, the
 * manifest, the ranked experiences); a section whose data is missing is left
 * out rather than filled in.
 *
 * The raw findings — files scanned, confidence, entry points — are kept under
 * "View analysis details".
 *
 * Spec: docs/design/02-investigation.md
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { phasesFrom } from "@/components/activity";
import { RealityBadge } from "@/components/experience-workspace";
import {
  ActionLink,
  ArrowRightIcon,
  Badge,
  Button,
  ButtonLink,
  Card,
  CrossIcon,
  EmptyState,
  ErrorNote,
  PageHeader,
  ProgressSteps,
  RunIcon,
  Section,
  Skeleton,
  Stat,
  StarIcon,
  StatusBadge,
  type ProgressStep,
} from "@/components/ui";
import { api, ApiError, optional } from "@/lib/api";
import { field, isBoolean, isNumber, isString } from "@/lib/events";
import { count, shortHash } from "@/lib/format";
import type { ExperienceDefinition, ExperienceListing, JobEvent } from "@/lib/types";

import { useWorkspace } from "./workspace";

const HINT_KEY = "repolab.hint.start-here.dismissed";

export default function OverviewPage() {
  const { jobId, job, manifest, architecture, experiences, events, environmentReady, openActivity } =
    useWorkspace();
  const [listing, setListing] = useState<ExperienceListing | null>(null);
  const [listingLoaded, setListingLoaded] = useState(false);

  useEffect(() => {
    if (!manifest) return;
    let cancelled = false;
    void (async () => {
      const next = await optional(api.experienceList(jobId)).catch(() => null);
      if (!cancelled) {
        setListing(next);
        setListingLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, manifest]);

  const understanding = architecture?.graph.understanding ?? null;
  const analyzing = !manifest && job?.status !== "failed";

  if (analyzing) return <Analyzing events={events} />;
  if (!manifest) {
    return (
      <EmptyState title="RepoLab couldn't finish reading this repository">
        The reason is above. The agent&apos;s steps are in the activity log.
        <div className="mt-4">
          <Button onClick={openActivity} size="sm">
            View agent activity
          </Button>
        </div>
      </EmptyState>
    );
  }

  const name = understanding?.name ?? listing?.name ?? null;
  const tags = unique([...(understanding?.languages ?? manifest.languages), ...(understanding?.frameworks ?? manifest.frameworks)]);
  const explanation = understanding?.beginner_explanation ?? understanding?.primary_purpose ?? null;
  const explanationInferred =
    understanding?.inferred_fields.includes(
      understanding.beginner_explanation ? "beginner_explanation" : "primary_purpose",
    ) ?? false;
  const concepts = architecture?.graph.system_model?.concepts ?? [];
  const firstQuestion = understanding?.recommended_start ?? null;
  const primary = listing?.experiences[0] ?? null;

  return (
    <div className="flex max-w-5xl flex-col gap-10" data-testid="overview">
      <div className="flex flex-col gap-4">
        <PageHeader
          title={name ?? "This repository"}
          description={understanding?.one_line_description ?? undefined}
        />
        {tags.length > 0 && (
          <ul className="flex flex-wrap gap-2" aria-label="Languages and frameworks">
            {tags.map((tag) => (
              <li key={tag}>
                <Badge>{tag}</Badge>
              </li>
            ))}
          </ul>
        )}
      </div>

      {explanation && (
        <Section title="What is this?">
          <p className="max-w-3xl text-body leading-7 text-fg" data-testid="what-is-this">
            {explanation}
          </p>
          {explanationInferred && (
            <p className="mt-2 text-xs text-ai-fg">
              Written by Nemotron from the repository&apos;s own files; the evidence is in Architecture.
            </p>
          )}
        </Section>
      )}

      <Section title="Start here" data-testid="start-here">
        {!listingLoaded ? (
          <Skeleton rows={3} />
        ) : primary ? (
          <>
            <StartHint count={listing?.experiences.length ?? 0} />
            <StartCard jobId={jobId} experience={primary} />
            {(listing?.experiences.length ?? 0) > 1 && (
              <p className="mt-3 text-sm text-fg-secondary">
                {listing!.experiences.length - 1} more way{listing!.experiences.length - 1 === 1 ? "" : "s"} to
                try it in{" "}
                <Link href={`/r/${jobId}/experiences`} className="font-medium text-run-fg hover:underline">
                  Experiences
                </Link>
                .
              </p>
            )}
          </>
        ) : (
          <EmptyState
            title="Nothing here can run in RepoLab yet"
            data-testid="overview-nothing-runs"
            actions={
              <>
                <ActionLink kind="arch" href={`/r/${jobId}/architecture`}>
                  Explore architecture
                </ActionLink>
                <ActionLink
                  kind="ask"
                  href={`/r/${jobId}/ask?q=${encodeURIComponent("How do I build and run this repository locally?")}`}
                >
                  Ask how to run it locally
                </ActionLink>
              </>
            }
          >
            {listing?.nothing_runs_because[0] ??
              "RepoLab mapped the code, but has no safe way to run this kind of repository."}{" "}
            You can still learn how it works from its architecture.
          </EmptyState>
        )}
      </Section>

      {concepts.length > 0 && (
        <Section
          title="Key components"
          description="The main parts, and what each one does. Open one in Architecture."
        >
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="key-components">
            {concepts.slice(0, 6).map((concept) => (
              <li key={concept.id}>
                <Link
                  href={`/r/${jobId}/architecture?node=${encodeURIComponent(concept.id)}`}
                  className="group flex h-full flex-col rounded-lg border border-line bg-surface p-4 transition-colors duration-150 hover:border-arch-line hover:bg-arch-soft"
                >
                  <span className="flex items-center justify-between gap-2 text-sm font-semibold text-fg">
                    {concept.label}
                    <ArrowRightIcon
                      size={14}
                      className="text-fg-muted transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-arch-fg"
                    />
                  </span>
                  <span className="mt-1 line-clamp-3 text-sm text-fg-secondary">{concept.what_it_does}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {primary && (
        <Section title="Other ways in">
          <div className="flex flex-wrap gap-2">
            <ActionLink kind="arch" size="md" href={`/r/${jobId}/architecture`}>
              Explore architecture
            </ActionLink>
            <ActionLink
              kind="ask"
              size="md"
              href={firstQuestion ? `/r/${jobId}/ask?q=${encodeURIComponent(firstQuestion)}` : `/r/${jobId}/ask`}
            >
              Ask RepoLab
            </ActionLink>
          </div>
          {firstQuestion && (
            <p className="mt-3 max-w-3xl text-sm text-fg-secondary">
              A good first question: <span className="text-fg">{firstQuestion}</span>
            </p>
          )}
        </Section>
      )}

      <AnalysisDetails
        jobId={jobId}
        events={events}
        manifest={manifest}
        catalogCount={experiences?.surface_count ?? null}
        runnable={experiences?.experiences.find((item) => item.runnable) ?? null}
        environmentReady={environmentReady}
        onOpenActivity={openActivity}
      />
    </div>
  );
}

/** The real steps the agent has announced, while it works. */
function Analyzing({ events }: { events: JobEvent[] }) {
  const phases = phasesFrom(events);
  const findings = findingsFrom(events);
  const total = phases[0]?.total ?? null;
  const steps: ProgressStep[] = phases.map((phase, index) => ({
    key: phase.key,
    label: phase.label,
    state: index === phases.length - 1 ? "current" : "done",
  }));
  return (
    <div className="flex max-w-3xl flex-col gap-8" data-testid="overview-analyzing">
      <PageHeader
        title="Understanding the repository"
        description="RepoLab is reading the code to find what it is, how it runs and what you can do with it. Nothing in the repository is executed at this stage."
      />
      <Card className="p-5">
        {steps.length === 0 ? (
          <p className="text-sm text-fg-secondary">Starting…</p>
        ) : (
          <ProgressSteps steps={steps} label="Investigation phases" data-testid="analysis-steps" />
        )}
        {total !== null && (
          <p className="mt-3 border-t border-line pt-3 text-xs text-fg-muted">
            Step {phases.length} of {total}
          </p>
        )}
      </Card>
      {findings.length > 0 && (
        <Section title="Found so far">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3" data-testid="findings">
            {findings.map((finding) => (
              <Stat key={finding.label} label={finding.label} value={finding.value} />
            ))}
          </dl>
        </Section>
      )}
    </div>
  );
}

/** The primary experience, with the one obvious next action. */
function StartCard({ jobId, experience }: { jobId: string; experience: ExperienceDefinition }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const option = experience.options[0] ?? null;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.startExperience(jobId, experience.id, option?.strategy);
      router.push(`/r/${jobId}/experiences?session=${created.id}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
      setBusy(false);
    }
  };

  const running = experience.state === "running" && experience.session_id;
  const action = running ? (
    <ButtonLink
      variant="primary"
      tone="run"
      href={
        experience.adapter === "live"
          ? `/r/${jobId}/experiences?live=1`
          : `/r/${jobId}/experiences?session=${experience.session_id}`
      }
      data-testid="overview-continue"
      icon={<RunIcon size={14} />}
    >
      Continue where you left off
    </ButtonLink>
  ) : experience.route === "#live" ? (
    <ButtonLink
      variant="primary"
      tone="run"
      href={`/r/${jobId}/experiences?live=1`}
      data-testid="overview-start"
      icon={<RunIcon size={14} />}
    >
      Start experience
    </ButtonLink>
  ) : experience.route ? (
    <ButtonLink
      variant="primary"
      tone="run"
      href={`/r/${jobId}${experience.route}`}
      data-testid="overview-start"
      icon={<RunIcon size={14} />}
    >
      Start experience
    </ButtonLink>
  ) : (
    <Button
      variant="primary"
      tone="run"
      onClick={start}
      disabled={busy}
      data-testid="overview-start"
      icon={<RunIcon size={14} />}
    >
      {busy ? "Starting…" : option && !option.ready && option.reality === "real_repository" ? "Build and start" : "Start experience"}
    </Button>
  );

  return (
    <Card accent="run" className="p-5" data-testid="overview-primary">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-run-fg">
            <StarIcon size={12} /> Recommended
          </p>
          <h3 className="mt-1 text-lg font-semibold text-fg">{experience.title}</h3>
        </div>
        <StatusBadge status={experience.state === "running" ? "live" : experience.state === "runnable" ? "ready" : "unavailable"}>
          {experience.state === "running" ? "Running" : experience.state === "runnable" ? "Ready to start" : "Not runnable"}
        </StatusBadge>
      </div>
      <p className="mt-2 max-w-2xl text-body text-fg-secondary">{experience.summary}</p>
      {experience.what_you_learn && (
        <p className="mt-3 text-sm text-fg">
          <span className="font-medium">You&apos;ll learn: </span>
          {experience.what_you_learn}
        </p>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-3">
        {action}
        {option && (
          <span className="flex flex-wrap items-center gap-2 text-meta text-fg-secondary">
            <RealityBadge reality={option.reality} />
            {option.label}
            {option.estimate && <span className="text-fg-muted">· {option.estimate}</span>}
          </span>
        )}
      </div>
      {error && (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
    </Card>
  );
}

/** A one-time hint for a first visit. Dismissal is remembered when storage allows. */
function StartHint({ count: ways }: { count: number }) {
  const [dismissed, setDismissed] = useState(true);
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- read once from storage after mount
      setDismissed(window.localStorage.getItem(HINT_KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);
  if (dismissed || ways === 0) return null;
  return (
    <div
      className="mb-3 flex items-start gap-3 rounded-lg border border-brand-line bg-brand-soft px-4 py-3"
      role="note"
      data-testid="start-hint"
    >
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white">
        1
      </span>
      <p className="flex-1 text-sm text-fg">
        RepoLab found {ways === 1 ? "one way" : `${ways} ways`} to experience this repository. Try the
        recommended one first.
      </p>
      <button
        type="button"
        aria-label="Dismiss hint"
        onClick={() => {
          setDismissed(true);
          try {
            window.localStorage.setItem(HINT_KEY, "1");
          } catch {
            // Storage is unavailable; the hint simply returns next time.
          }
        }}
        className="flex size-7 items-center justify-center rounded-md text-fg-secondary hover:bg-surface hover:text-fg"
      >
        <CrossIcon size={14} />
      </button>
    </div>
  );
}

/** Everything the investigation measured, for anyone who wants it. */
function AnalysisDetails({
  jobId,
  events,
  manifest,
  catalogCount,
  runnable,
  environmentReady,
  onOpenActivity,
}: {
  jobId: string;
  events: JobEvent[];
  manifest: NonNullable<ReturnType<typeof useWorkspace>["manifest"]>;
  catalogCount: number | null;
  runnable: { route: string | null; label: string } | null;
  environmentReady: boolean | null;
  onOpenActivity: () => void;
}) {
  const findings = findingsFrom(events);
  return (
    <details className="group border-t border-line pt-6" data-testid="analysis-details">
      <summary className="cursor-pointer text-sm font-medium text-fg-secondary hover:text-fg">
        View analysis details
      </summary>
      <div className="mt-5 flex flex-col gap-6">
        {findings.length > 0 && (
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3" data-testid="findings">
            {findings.map((finding) => (
              <Stat key={finding.label} label={finding.label} value={finding.value} />
            ))}
          </dl>
        )}
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Commit" value={shortHash(manifest.commit_sha)} />
          <Stat
            label="Primary surface"
            value={manifest.interaction_surfaces.find((surface) => surface.primary)?.type ?? "—"}
            mono={false}
          />
          <Stat label="Languages" value={manifest.languages.join(", ") || "—"} mono={false} />
          <Stat label="Frameworks" value={manifest.frameworks.join(", ") || "none detected"} mono={false} />
          <Stat label="Entry points" value={count(manifest.entrypoints.length)} />
          <Stat label="Concepts" value={count(manifest.concepts.length)} />
          <Stat
            label="Surfaces"
            value={
              <Link
                href={`/r/${jobId}/experiences`}
                className="text-brand-fg underline underline-offset-2"
                data-testid="surfaces-link"
              >
                {count(manifest.interaction_surfaces.length)}
              </Link>
            }
            hint="What each one is, and which can be run here"
          />
          <Stat
            label="Confidence"
            value={manifest.confidence.toFixed(2)}
            hint="Deterministic evidence alone never claims certainty"
          />
        </dl>
        {environmentReady === false && (
          <p className="text-sm text-exp-fg">
            The execution environment is not available on this machine, so experiences cannot run.
            Everything else still works.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onOpenActivity}>
            View agent activity
          </Button>
          <ButtonLink size="sm" href={`/r/${jobId}/experiences`} data-testid="see-experiences">
            {catalogCount !== null ? `All ${catalogCount} detected surfaces` : "Detected surfaces"}
          </ButtonLink>
          {runnable && runnable.route && (
            <ButtonLink size="sm" tone="run" href={`/r/${jobId}${runnable.route}`} data-testid="open-runnable">
              Open the {runnable.label} experience
            </ButtonLink>
          )}
        </div>
      </div>
    </details>
  );
}

function unique(values: string[]): string[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = value.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

type Finding = { label: string; value: string };

/**
 * Facts as the stream reveals them.
 *
 * Reads only named `data` keys, so nothing unexpected can reach the screen.
 */
function findingsFrom(events: JobEvent[]): Finding[] {
  const found: Finding[] = [];
  const add = (label: string, value: string | undefined) => {
    if (value !== undefined && !found.some((finding) => finding.label === label)) {
      found.push({ label, value });
    }
  };

  for (const event of events) {
    const files = field(event, "file_count", isNumber);
    if (files !== undefined) add("Files scanned", count(files));

    const languages = event.data?.["languages"];
    if (Array.isArray(languages) && languages.every((item) => typeof item === "string")) {
      add("Languages", (languages as string[]).join(", ") || "none detected");
    }

    const commit = field(event, "commit_sha", isString);
    if (commit) add("Commit", shortHash(commit));

    const branch = field(event, "branch", isString);
    if (branch) add("Branch", branch);

    const surfaces = event.data?.["surfaces"];
    if (Array.isArray(surfaces)) add("Surfaces detected", String(surfaces.length));

    const resolvedBy = field(event, "resolved_by", isString);
    if (resolvedBy) add("Classified by", resolvedBy);

    const image = field(event, "image", isString);
    const ready = field(event, "ready", isBoolean);
    if (image && ready !== undefined) add("Environment", ready ? "ready" : "unavailable");
    else if (image && event.type === "step_done") add("Environment", "ready");

    const tools = field(event, "index", isNumber);
    if (tools !== undefined) {
      const existing = found.find((finding) => finding.label === "Agent steps");
      if (existing) existing.value = String(tools);
      else found.push({ label: "Agent steps", value: String(tools) });
    }
  }
  return found;
}
