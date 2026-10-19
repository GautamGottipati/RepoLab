/**
 * Investigation — show the agent working.
 *
 * Spec: docs/design/02-investigation.md
 */

"use client";

import Link from "next/link";

import { Empty, Panel, Stat } from "@/components/ui";
import { field, isBoolean, isNumber, isString } from "@/lib/events";
import { count, shortHash } from "@/lib/format";
import type { JobEvent } from "@/lib/types";

import { useWorkspace } from "./workspace";

export default function InvestigationPage() {
  const { jobId, job, manifest, experiences, events, environmentReady } = useWorkspace();
  const findings = findingsFrom(events);
  const runnable = experiences?.experiences.find((item) => item.runnable) ?? null;

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="Investigating the repository"
        subtitle={
          job?.status === "completed"
            ? "Done. The architecture and the playground are ready."
            : job?.status === "failed"
              ? "The investigation did not finish."
              : "Reading the repository. Nothing in it is executed at this stage."
        }
      >
        {findings.length === 0 ? (
          <Empty title="Waiting for the first findings">
            The agent has started. Facts appear here as it discovers them, and the
            full activity is on the right.
          </Empty>
        ) : (
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3" data-testid="findings">
            {findings.map((finding) => (
              <Stat key={finding.label} label={finding.label} value={finding.value} />
            ))}
          </dl>
        )}
      </Panel>

      {manifest && (
        <Panel title="What it found" subtitle="From the manifest the investigation produced">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Commit" value={shortHash(manifest.commit_sha)} />
            <Stat
              label="Primary experience"
              value={
                manifest.interaction_surfaces.find((surface) => surface.primary)?.type ?? "—"
              }
              mono={false}
            />
            <Stat label="Languages" value={manifest.languages.join(", ") || "—"} mono={false} />
            <Stat
              label="Frameworks"
              value={manifest.frameworks.join(", ") || "none detected"}
              mono={false}
            />
            <Stat label="Entry points" value={count(manifest.entrypoints.length)} />
            <Stat label="Concepts" value={count(manifest.concepts.length)} />
            <Stat
              label="Surfaces"
              value={
                <Link
                  href={`/r/${jobId}/experiences`}
                  className="underline underline-offset-2"
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
            <p className="mt-4 text-xs text-amber-700 dark:text-amber-300">
              The execution environment is not available on this machine, so the
              playground cannot run. Everything else still works.
            </p>
          )}

          {/*
            What to do next depends on what this repository actually offers. The
            playground used to be offered here unconditionally, which on a
            repository RepoLab cannot build was a link to a screen that could
            not work. The catalog decides instead.
          */}
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              href={`/r/${jobId}/architecture`}
              className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
            >
              See the architecture
            </Link>
            {runnable && runnable.route && (
              <Link
                href={`/r/${jobId}${runnable.route}`}
                data-testid="open-runnable"
                className="rounded border border-neutral-300 px-4 py-2 text-sm font-medium dark:border-neutral-700"
              >
                Open the {runnable.label} experience
              </Link>
            )}
            <Link
              href={`/r/${jobId}/experiences`}
              data-testid="see-experiences"
              className="rounded border border-neutral-300 px-4 py-2 text-sm font-medium dark:border-neutral-700"
            >
              {experiences
                ? `All ${experiences.surface_count} experiences`
                : "Available experiences"}
            </Link>
          </div>
        </Panel>
      )}
    </div>
  );
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
