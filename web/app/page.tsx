/**
 * Import — the first screen.
 *
 * Takes a repository URL and starts investigating it. Spec: docs/design/01-import.md
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { ErrorNote } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

/** The P0 demo repository, offered because watching it is the fastest way in. */
const DEMO_REPO = "https://github.com/AlexIoannides/pyspark-example-project";

const STEPS = [
  ["Inspect the repository", "Read-only. Repository code is never executed here."],
  ["Understand how it runs", "Entry points, data flow, and the concepts behind them."],
  ["Run it for real", "The repository's own job, on real Spark, in a container."],
  ["Test a question", "Design an experiment, approve it, and measure the answer."],
];

export default function ImportPage() {
  const router = useRouter();
  const [repoUrl, setRepoUrl] = useState(DEMO_REPO);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (formEvent: React.FormEvent) => {
      formEvent.preventDefault();
      setError(null);
      setSubmitting(true);
      try {
        const created = await api.submitRepository(repoUrl);
        router.push(`/r/${created.job_id}`);
      } catch (cause) {
        // URL validation lives in the backend; its message is the real answer.
        setError(cause instanceof ApiError ? cause.message : String(cause));
        setSubmitting(false);
      }
    },
    [repoUrl, router],
  );

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col justify-center gap-10 p-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">RepoLab</h1>
        <p className="mt-2 text-neutral-600 dark:text-neutral-400">
          Turn a repository into an executable experiment. RepoLab investigates the
          code, runs it for real, and answers questions with measurements rather
          than assertions.
        </p>
      </header>

      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor="repo-url" className="text-sm font-medium">
          Repository URL
        </label>
        <div className="flex gap-2">
          <input
            id="repo-url"
            name="repo_url"
            value={repoUrl}
            onChange={(event) => setRepoUrl(event.target.value)}
            placeholder="https://github.com/owner/name"
            autoComplete="off"
            spellCheck={false}
            className="flex-1 rounded border border-neutral-300 px-3 py-2 font-mono text-sm outline-none focus:border-neutral-900 dark:border-neutral-700 dark:bg-neutral-900 dark:focus:border-neutral-300"
          />
          <button
            type="submit"
            disabled={submitting || repoUrl.trim().length === 0}
            className="rounded bg-neutral-900 px-5 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
          >
            {submitting ? "Starting…" : "Investigate"}
          </button>
        </div>

        {repoUrl !== DEMO_REPO && (
          <button
            type="button"
            onClick={() => setRepoUrl(DEMO_REPO)}
            className="self-start text-xs text-neutral-500 underline underline-offset-2 hover:text-neutral-900 dark:hover:text-neutral-200"
          >
            Use the Spark demo repository
          </button>
        )}
      </form>

      {error && <ErrorNote>{error}</ErrorNote>}

      <section>
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          What happens next
        </h2>
        <ol className="mt-3 grid gap-4 sm:grid-cols-2">
          {STEPS.map(([title, detail], index) => (
            <li key={title} className="flex gap-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full border border-neutral-300 text-xs text-neutral-500 dark:border-neutral-700">
                {index + 1}
              </span>
              <div>
                <p className="text-sm font-medium">{title}</p>
                <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">{detail}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <footer className="border-t border-neutral-200 pt-6 dark:border-neutral-800">
        <Link
          href="/metrics"
          data-testid="metrics-link"
          className="text-sm underline underline-offset-2"
        >
          Metrics
        </Link>
        <span className="ml-2 text-xs text-neutral-500">
          What RepoLab has been used for, and how it performed doing it.
        </span>
      </footer>
    </main>
  );
}
