/**
 * Landing — the first screen.
 *
 * One dominant action: paste a repository URL and investigate it. The examples
 * are repositories RepoLab has been verified to handle end to end. Navy in both
 * themes, so the product's front door reads differently from the workspace.
 *
 * Spec: docs/design/01-import.md
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { ArchIcon, ArrowRightIcon, AskIcon, ExperimentIcon, RunIcon } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

/** Repositories RepoLab has been verified to investigate and run. */
const EXAMPLES: { url: string; label: string; what: string }[] = [
  {
    url: "https://github.com/AlexIoannides/pyspark-example-project",
    label: "AlexIoannides/pyspark-example-project",
    what: "A Spark job",
  },
  { url: "https://github.com/apache/spark", label: "apache/spark", what: "Spark itself" },
  { url: "https://github.com/pallets/click", label: "pallets/click", what: "A Python library" },
  { url: "https://github.com/apache/cassandra", label: "apache/cassandra", what: "A database with a CLI" },
];

const VALUE: { title: string; detail: string; icon: React.ReactNode; tint: string }[] = [
  { title: "Understand", detail: "What a real project is, in plain words", icon: <AskIcon size={16} />, tint: "text-violet-300" },
  { title: "Explore", detail: "Its architecture, down to the source", icon: <ArchIcon size={16} />, tint: "text-cyan-300" },
  { title: "Run", detail: "Interactive experiences on the real software", icon: <RunIcon size={16} />, tint: "text-emerald-300" },
  { title: "Experiment", detail: "Test a question and measure the answer", icon: <ExperimentIcon size={16} />, tint: "text-amber-300" },
];

export default function LandingPage() {
  const router = useRouter();
  const [repoUrl, setRepoUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const investigate = useCallback(
    async (url: string) => {
      setError(null);
      setSubmitting(true);
      try {
        const created = await api.submitRepository(url);
        router.push(`/r/${created.job_id}`);
      } catch (cause) {
        // URL validation lives in the backend; its message is the real answer.
        setError(cause instanceof ApiError ? cause.message : String(cause));
        setSubmitting(false);
      }
    },
    [router],
  );

  return (
    <div className="flex min-h-screen flex-col bg-landing text-slate-100 [color-scheme:dark]">
      <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-4 py-5 sm:px-6">
        <span className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <span aria-hidden className="flex size-6 items-center justify-center rounded-md bg-indigo-500 text-xs text-white">
            R
          </span>
          RepoLab
        </span>
        <Link href="/metrics" data-testid="metrics-link" className="text-sm text-slate-400 hover:text-white">
          Metrics
        </Link>
      </header>

      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center gap-14 px-4 pb-16 pt-8 sm:px-6">
        <section className="max-w-3xl">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-white sm:text-5xl">
            Turn any GitHub repository into an interactive learning experience.
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-relaxed text-slate-300">
            Analyze, run and explore real open-source projects — with explanations grounded in the code, live
            environments, and experiments that measure the answer.
          </p>

          <form
            onSubmit={(event) => {
              event.preventDefault();
              void investigate(repoUrl);
            }}
            className="mt-10 flex flex-col gap-2"
          >
            <label htmlFor="repo-url" className="text-sm font-medium text-slate-300">
              Repository URL
            </label>
            <div className="flex flex-col gap-2 rounded-xl border border-slate-700 bg-slate-900/70 p-2 transition-colors focus-within:border-indigo-400 sm:flex-row">
              <input
                id="repo-url"
                name="repo_url"
                value={repoUrl}
                onChange={(event) => setRepoUrl(event.target.value)}
                placeholder="https://github.com/owner/name"
                autoComplete="off"
                spellCheck={false}
                className="h-11 min-w-0 flex-1 bg-transparent px-3 font-mono text-base text-white outline-none placeholder:text-slate-500"
              />
              <button
                type="submit"
                disabled={submitting || repoUrl.trim().length === 0}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-indigo-500 px-6 text-sm font-semibold text-white transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? "Starting…" : "Investigate"}
                {!submitting && <ArrowRightIcon size={14} />}
              </button>
            </div>
          </form>

          {error && (
            <p role="alert" data-testid="error-note" className="mt-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
              {error}
            </p>
          )}

          <div className="mt-6">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Try an example</p>
            <ul className="mt-3 flex flex-wrap gap-2" data-testid="examples">
              {EXAMPLES.map((example) => (
                <li key={example.url}>
                  <button
                    type="button"
                    onClick={() => setRepoUrl(example.url)}
                    title={example.what}
                    className="inline-flex h-9 items-center gap-2 rounded-full border border-slate-700 bg-slate-900/50 px-3.5 font-mono text-xs text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
                  >
                    {example.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section aria-label="What RepoLab does">
          <ul className="grid gap-6 border-t border-slate-800 pt-10 sm:grid-cols-2 lg:grid-cols-4">
            {VALUE.map((item) => (
              <li key={item.title}>
                <p className={`flex items-center gap-2 text-sm font-semibold ${item.tint}`}>
                  {item.icon}
                  {item.title}
                </p>
                <p className="mt-1.5 text-sm text-slate-400">{item.detail}</p>
              </li>
            ))}
          </ul>
          <p className="mt-8 text-xs text-slate-500">
            Investigating a repository only reads it. Its code runs only inside isolated containers, and only when you
            start an experience or approve an experiment.
          </p>
        </section>
      </main>
    </div>
  );
}
