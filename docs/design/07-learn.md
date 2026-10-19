# Learn — `/r/{jobId}/learn`

## Purpose

Give a learner a real command line on the real software, and one guided lesson
to use it for. Written after the screen was built, from what it does.

The tab appears only when the manifest found a `cli` surface, the same way the
Playground appears only for a `data_playground`. The manifest decides which
experience renders; no screen knows a repository by name.

## Reads and writes

- `GET /cli/environment?repo_url=` — the surface, and what is running it.
- `POST /cli/environment` `{repo_url}` → `202 {job_id}` — resolve the ladder.
- `POST /cli/sessions` `{repo_url, job_id}` → `202 {session_id, status}`.
- `GET /cli/sessions/{id}` — status, the pending upgrade, the history.
- `WS /cli/sessions/{id}/terminal` — `{command}` in; one frame per statement out.
- `POST /cli/sessions/{id}/upgrade` — accept a better environment.
- `DELETE /cli/sessions/{id}` — stop it, releasing the container.
- `GET /lessons?repo_url=` — the lesson, and the Mini Lab rendering if that is
  all there is.
- `POST /lessons` `{repo_url}` → `202` — have Super write one.
- `POST /lessons/{id}/check` `{step, session_id, sequence}` — the verdict.
- `POST /lessons/{id}/explain` `{step, session_id, sequence}` — what happened.
- `GET /lessons/{id}/progress?session_id=` — what this learner has completed.

## Panels

**Environment** — the surface's name and why it was detected, the fidelity, the
version actually running, and two lists taken verbatim from the artifact: what
this environment can do and what it cannot. Disclosed deviations are shown as
written; an official release that is not the requested commit says so here.

**Terminal** — xterm.js, connected by WebSocket to the container. The prompt is
derived from the binary's own name. Output is written exactly as it arrived:
stdout, stderr in red, and a refusal in amber when the allowlist stopped a
statement before it ran. Line editing, history and Ctrl+C are implemented here
because xterm.js emulates a terminal, not a shell.

**Lesson** — one step at a time: concept, goal, context, the statement to run,
what to expect, how RepoLab will check it, and the repository lines and
documentation pages behind it. *Run in the terminal* types the statement into
the terminal the learner can see. *Check my work* judges the real output.

**Verdict** — pass or not, the reason, and **who decided**: a deterministic
check or a model's judgement, labelled as such. A failed step shows its own
hint, never a generated one.

**What just happened?** — offered on steps grounded in source and on the step
that provokes an error. Claims are grouped by what supports them — observed,
source, documented, inferred — and anything the backend could not corroborate is
displayed as a failure beside them, not dropped.

## States

- **Not investigated** — the tab is disabled, like every other manifest-backed
  screen.
- **No environment** — a panel explaining the ladder and a *Build an
  environment* button, with the cost stated: it compiles source, pulls a release
  and starts a 4 GB node. Nothing starts until it is clicked.
- **Building** — the button is disabled and the agent activity carries the
  ladder's progress. The terminal appears as soon as a rung passes its smoke
  test, not when the best one does.
- **Starting** — the socket holds while the checkpoint is restored and Cassandra
  replays its commit log, and says that is what it is waiting for.
- **Statement in flight** — Run and Check are disabled. The record a step is
  judged from does not exist until cqlsh has returned.
- **Upgrade available** — an offer, never an application, with the cost stated:
  a better environment starts from its own checkpoint, so anything created here
  will not be there. Refused while a statement is running.
- **Mini Lab only** — no terminal at all. A `SIMULATED` banner, dashed steps,
  each saying it requires a real build, each naming the source behind it.
- **Failed session** — the error from the bring-up, verbatim.

## Must never show

- Output that was not produced by the environment. The terminal prints what
  cqlsh printed and nothing else; a refusal is RepoLab's own sentence and is
  labelled as one.
- A step marked complete without a real command and real output behind it.
- A pass decided by a model presented as a check.
- Any timing, duration or performance figure in an explanation. A terminal
  result contains no measurements.
- A code reference that was not resolved against this commit.
- Simulated content without the `SIMULATED` label and its distinct treatment.
