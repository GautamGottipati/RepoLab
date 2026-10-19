/**
 * Learn — the CLI experience: a real terminal beside one guided lesson.
 *
 * The screen's whole claim is that nothing here is pretend. The terminal is
 * connected by WebSocket to a container running the build RepoLab resolved; the
 * statements are executed by that container's own cqlsh; the output is what
 * cqlsh printed; and a step is marked complete only after RepoLab has checked
 * the real output of the real command.
 *
 * Three things the screen is careful about:
 *
 *   - it starts no environment without one being ready, and says which is
 *     missing rather than failing silently;
 *   - a better environment is *offered*, never applied mid-statement, and the
 *     offer states what switching costs;
 *   - if only a Mini Lab exists there is no terminal at all, and the simulated
 *     treatment replaces it.
 *
 * Spec: docs/design/00-overview.md
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useWorkspace } from "../workspace";
import {
  LessonHeader,
  LessonProgressBar,
  LessonStepCard,
  SimulatedBanner,
  SimulatedLessonBody,
  UngroundedNote,
} from "@/components/lesson";
import { Terminal, type RunRequest, type TerminalStatus } from "@/components/terminal";
import { Badge, Empty, ErrorNote, Note, Panel, Skeleton } from "@/components/ui";
import { WhatHappened } from "@/components/what-happened";
import { api, ApiError } from "@/lib/api";
import type {
  CommandRecord,
  EnvironmentView,
  LessonView,
  SessionView,
  StepResult,
  WhatHappenedView,
} from "@/lib/types";

export default function LearnPage() {
  const { jobId, job, manifest } = useWorkspace();
  const repoUrl = job?.repo_url ?? null;

  const [environment, setEnvironment] = useState<EnvironmentView | null>(null);
  const [lessonView, setLessonView] = useState<LessonView | null>(null);
  const [session, setSession] = useState<SessionView | null>(null);
  const [status, setStatus] = useState<TerminalStatus>("connecting");
  const [error, setError] = useState<string | null>(null);

  const [current, setCurrent] = useState(1);
  const [completed, setCompleted] = useState<number[]>([]);
  const [results, setResults] = useState<Record<number, StepResult>>({});
  const [request, setRequest] = useState<RunRequest | null>(null);
  const [pending, setPending] = useState(false);
  // True from the moment a statement is sent until its result comes back. The
  // check cannot run in that window: the record a step is judged from only
  // exists once cqlsh has returned, so a check sent early is judged against the
  // *previous* statement, or against nothing at all.
  const [running, setRunning] = useState(false);
  const [explanation, setExplanation] = useState<WhatHappenedView | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [buildJob, setBuildJob] = useState<string | null>(null);
  // True while the start request is in flight. The backend now reuses a live
  // session rather than starting a second container, but a button that invites
  // a second click is still a button that looks broken.
  const [starting, setStarting] = useState(false);

  const lastRecord = useRef<CommandRecord | null>(null);

  // The session id is kept per job in session storage, so a reload reconnects to
  // the container the learner was already working in rather than starting a
  // second one beside it. The backend keeps the session alive across the socket
  // closing for exactly this reason.
  const sessionKey = `repolab.cli.session.${jobId}`;

  // The environment and the lesson, both of which exist before any session.
  useEffect(() => {
    if (!repoUrl) return;
    let cancelled = false;

    void (async () => {
      try {
        const [nextEnvironment, nextLesson] = await Promise.all([
          api.environment(repoUrl),
          api.lesson(repoUrl),
        ]);
        if (cancelled) return;
        setEnvironment(nextEnvironment);
        setLessonView(nextLesson);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : String(cause));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [repoUrl]);

  const lesson = lessonView?.lesson ?? null;
  const step = useMemo(
    () => lesson?.steps.find((item) => item.number === current) ?? null,
    [lesson, current],
  );

  // While a build is running, poll for an environment. The worker caches each
  // rung as it passes its smoke test, so the terminal becomes available the
  // moment the first one does -- without waiting for the best one.
  useEffect(() => {
    if (!repoUrl || !buildJob || environment?.ready) return;
    let cancelled = false;

    const timer = setInterval(() => {
      void (async () => {
        try {
          const next = await api.environment(repoUrl);
          if (!cancelled) setEnvironment(next);
        } catch {
          // The job's event stream is already reporting; nothing to add here.
        }
      })();
    }, 5000);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [repoUrl, buildJob, environment?.ready]);

  const build = useCallback(async () => {
    if (!repoUrl) return;
    setError(null);
    try {
      const queued = await api.resolveEnvironment(repoUrl);
      setBuildJob(queued.job_id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    }
  }, [repoUrl]);

  const start = useCallback(async () => {
    if (!repoUrl || starting) return;
    setError(null);
    setStarting(true);
    try {
      const next = await api.startSession(repoUrl, jobId);
      window.sessionStorage.setItem(sessionKey, next.session_id);
      setSession(next);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setStarting(false);
    }
  }, [repoUrl, jobId, sessionKey, starting]);

  // Adopt the session this browser was already using, if it is still alive.
  useEffect(() => {
    const known = window.sessionStorage.getItem(sessionKey);
    if (!known || session) return;
    let cancelled = false;

    void (async () => {
      try {
        const existing = await api.session(known);
        if (cancelled) return;
        if (existing.status === "ready" || existing.status === "starting") setSession(existing);
        else window.sessionStorage.removeItem(sessionKey);
      } catch {
        window.sessionStorage.removeItem(sessionKey);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sessionKey, session]);

  // What this learner has already done, from the record rather than the page.
  useEffect(() => {
    if (!lessonView?.lesson || !session) return;
    let cancelled = false;

    void (async () => {
      try {
        const progress = await api.lessonProgress(lessonView.lesson.id, session.session_id);
        if (cancelled || progress.completed.length === 0) return;
        setCompleted(progress.completed);
        const following = lessonView.lesson.steps.find(
          (item) => !progress.completed.includes(item.number),
        );
        setCurrent(following?.number ?? lessonView.lesson.step_count);
      } catch {
        // No progress recorded yet, which is the normal case on arrival.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [lessonView?.lesson, session]);

  // While a session is coming up, poll it: the bring-up restores a checkpoint
  // and waits for Cassandra, and the browser should say so rather than hang.
  useEffect(() => {
    if (!session || session.status !== "starting") return;
    let cancelled = false;

    const timer = setInterval(() => {
      void (async () => {
        try {
          const next = await api.session(session.session_id);
          if (!cancelled) setSession(next);
        } catch {
          // A session that has gone is reported by the socket; nothing to add.
        }
      })();
    }, 2000);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [session]);

  // Refresh the session view after each statement, so an upgrade offer that
  // arrived while the learner was working appears once they are idle.
  const onResult = useCallback(
    (record: CommandRecord) => {
      lastRecord.current = record;
      setRunning(false);
      if (!session) return;
      void (async () => {
        try {
          setSession(await api.session(session.session_id));
        } catch {
          // Not worth surfacing: the terminal already showed the result.
        }
      })();
    },
    [session],
  );

  const run = useCallback(() => {
    if (!step) return;
    setExplanation(null);
    setRunning(true);
    setRequest((previous) => ({
      statement: step.action,
      nonce: (previous?.nonce ?? 0) + 1,
    }));
  }, [step]);

  const check = useCallback(async () => {
    if (!lesson || !step || !session) return;

    setPending(true);
    setError(null);
    try {
      const result = await api.checkStep(
        lesson.id,
        step.number,
        session.session_id,
        lastRecord.current?.sequence,
      );
      setResults((previous) => ({ ...previous, [step.number]: result }));
      setCompleted(result.completed);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setPending(false);
    }
  }, [lesson, step, session]);

  const explain = useCallback(async () => {
    if (!lesson || !step || !session) return;
    setExplaining(true);
    setExplanation(null);
    try {
      setExplanation(
        await api.explainStep(
          lesson.id,
          step.number,
          session.session_id,
          lastRecord.current?.sequence,
        ),
      );
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setExplaining(false);
    }
  }, [lesson, step, session]);

  const upgrade = useCallback(async () => {
    if (!session) return;
    setError(null);
    try {
      setSession(await api.upgradeSession(session.session_id));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    }
  }, [session]);

  const generate = useCallback(async () => {
    if (!repoUrl) return;
    try {
      await api.generateLesson(repoUrl);
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    }
  }, [repoUrl]);

  const next = useCallback(() => {
    if (!lesson) return;
    const following = lesson.steps.find((item) => !completed.includes(item.number));
    setCurrent(following?.number ?? lesson.steps.length);
    setExplanation(null);
  }, [lesson, completed]);

  if (!manifest) return <Skeleton rows={6} />;

  const surfaces = manifest.interaction_surfaces.filter((surface) => surface.type === "cli");
  if (surfaces.length === 0) {
    return (
      <Empty title="This repository has no command-line surface">
        RepoLab renders the experiences the manifest found. This one found{" "}
        {manifest.interaction_surfaces.map((surface) => surface.type).join(", ") || "none"}.
      </Empty>
    );
  }

  const experience = environment?.experience ?? null;
  const simulatedLesson = lessonView?.simulated ?? null;
  const simulated = (experience?.simulated ?? false) || simulatedLesson !== null;

  return (
    <div className="flex flex-col gap-5">
      {error && <ErrorNote>{error}</ErrorNote>}

      {simulated && experience && <SimulatedBanner reason={experience.unavailable_reason} />}

      {experience && !simulated && (
        <Panel
          title={`${experience.name} · ${experience.fidelity} fidelity`}
          subtitle={experience.reason}
          action={
            <div className="flex items-center gap-2">
              <Badge tone={experience.available ? "good" : "bad"}>
                {experience.available ? "real environment" : "unavailable"}
              </Badge>
              {experience.version && (
                <span className="font-mono text-xs text-neutral-500">{experience.version}</span>
              )}
            </div>
          }
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                What this environment can do
              </h3>
              <ul className="mt-1 flex flex-col gap-1 text-sm">
                {experience.capabilities.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                What it cannot
              </h3>
              <ul className="mt-1 flex flex-col gap-1 text-sm text-neutral-600 dark:text-neutral-400">
                {experience.limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
                {experience.limitations.length === 0 && <li>Nothing declared.</li>}
              </ul>
            </div>
          </div>
          {experience.deviations.length > 0 && (
            <div className="mt-4" data-testid="deviations">
              <Note>{experience.deviations.join(" · ")}</Note>
            </div>
          )}
        </Panel>
      )}

      {!environment && !error && <Skeleton rows={4} />}

      {environment && !environment.ready && !simulated && (
        <Panel
          title="No environment yet"
          subtitle={environment.reason || "No environment has finished building yet."}
        >
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            RepoLab resolves one through the whole fidelity ladder: the requested commit built from
            source, the official release, a reduced single node, and a conceptual Mini Lab if none
            of those can be produced. The first rung to pass its smoke test is the one you start on;
            a better one is offered later, never forced.
          </p>
          <p className="mt-2 text-xs text-neutral-500">
            This compiles source, pulls a release and starts a node with 4 GB of memory, so it needs
            your say-so. Expect a few minutes.
          </p>
          <button
            type="button"
            onClick={build}
            disabled={buildJob !== null}
            data-testid="build-environment"
            className="mt-3 rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900"
          >
            {buildJob ? "Building — watch the agent activity" : "Build an environment"}
          </button>
          {buildJob && (
            <p className="mt-2 text-xs text-neutral-500" data-testid="build-job">
              Resolving in a worker. The terminal opens as soon as a rung passes its smoke test.
            </p>
          )}
        </Panel>
      )}

      {session?.upgrade && (
        <div
          data-testid="upgrade-offer"
          className="rounded-lg border-2 border-sky-400 bg-sky-50 p-4 dark:border-sky-800 dark:bg-sky-950/40"
        >
          <p className="text-sm font-semibold text-sky-900 dark:text-sky-200">
            {session.upgrade.describe}
          </p>
          <p className="mt-1 text-xs text-sky-800 dark:text-sky-300">
            {session.upgrade_disclosure}
          </p>
          <button
            type="button"
            onClick={upgrade}
            data-testid="accept-upgrade"
            disabled={session.busy}
            className="mt-3 rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
          >
            {session.busy
              ? "Waiting for the current statement to finish…"
              : `Switch to the ${session.upgrade.to_fidelity} environment`}
          </button>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-4">
          {!simulated && environment?.ready && !session && (
            <Panel title="Terminal">
              <p className="text-sm text-neutral-600 dark:text-neutral-400">
                A terminal runs inside its own container, restored from the checkpoint this build
                produced. Starting one takes a moment while Cassandra accepts its first connection.
              </p>
              <button
                type="button"
                onClick={start}
                disabled={starting}
                data-testid="start-session"
                className="mt-3 rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900"
              >
                {starting ? "Starting…" : "Start a terminal"}
              </button>
            </Panel>
          )}

          {session && session.status === "failed" && (
            <ErrorNote>{session.error ?? "The environment could not be started."}</ErrorNote>
          )}

          {session && session.status !== "failed" && (
            <div>
              <Terminal
                sessionId={session.session_id}
                prompt={experience?.prompt ?? "cqlsh>"}
                request={request}
                onResult={onResult}
                onStatusChange={setStatus}
              />
              <p className="mt-2 text-[11px] text-neutral-400">
                {session.cli_command} inside the {session.build_kind.replace("_", " ")} environment
                · statements are checked against an allowlist before they are run
                {status === "open" ? "" : " · waiting for the environment"}
              </p>
            </div>
          )}

          {simulated && (
            <Panel title="Terminal">
              <Empty title="There is nothing to connect to">
                No environment could be built for this repository, so there is no server and no
                terminal. The steps below describe what each statement would do, and name the source
                that implements it.
              </Empty>
            </Panel>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {!lesson && !error && <Skeleton rows={6} />}

          {lesson && (
            <>
              <LessonHeader lesson={lesson} />
              <LessonProgressBar lesson={lesson} completed={completed} />
              {lesson.generated_by !== "super" && (
                <div className="flex flex-wrap items-center gap-3">
                  <Note>
                    This is RepoLab&apos;s own lesson, and it is complete. Nemotron Super can write
                    one instead, from the repository, its own cqlsh documentation, version-matched
                    official documentation and what this environment can do.
                  </Note>
                  <button
                    type="button"
                    onClick={generate}
                    data-testid="generate-lesson"
                    className="rounded border border-neutral-300 px-3 py-1.5 text-xs dark:border-neutral-700"
                  >
                    Write one with Nemotron
                  </button>
                </div>
              )}
              <UngroundedNote lesson={lesson} />

              {simulatedLesson && (
                <>
                  <p className="text-sm text-amber-900 dark:text-amber-200">
                    {simulatedLesson.notice}
                  </p>
                  <SimulatedLessonBody lesson={simulatedLesson} />
                </>
              )}

              {!simulatedLesson && step && (
                <LessonStepCard
                  step={step}
                  total={lesson.step_count}
                  result={results[step.number] ?? null}
                  pending={pending || running}
                  canRun={status === "open"}
                  onRun={run}
                  onCheck={check}
                  onExplain={explain}
                  onNext={next}
                  explainable={lessonView?.explainable_steps.includes(step.number) ?? false}
                />
              )}

              {!simulatedLesson && (
                <nav className="flex flex-wrap gap-1.5" aria-label="Lesson steps">
                  {lesson.steps.map((item) => (
                    <button
                      key={item.number}
                      type="button"
                      onClick={() => {
                        setCurrent(item.number);
                        setExplanation(null);
                      }}
                      data-testid={`goto-step-${item.number}`}
                      className={`h-7 w-7 rounded text-xs ${
                        item.number === current
                          ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
                          : completed.includes(item.number)
                            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                            : "border border-neutral-300 text-neutral-500 dark:border-neutral-700"
                      }`}
                    >
                      {item.number}
                    </button>
                  ))}
                </nav>
              )}

              {!simulatedLesson && <WhatHappened view={explanation} pending={explaining} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
