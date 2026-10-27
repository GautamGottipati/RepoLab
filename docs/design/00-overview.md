# Overview — shell, navigation and shared vocabulary

## The shape of the product

One repository at a time, moving through stages:

```
  /                      import a repository
  /r/{jobId}             watch it being investigated
  /r/{jobId}/architecture    what it contains
  /r/{jobId}/playground      run it, measure it
  /r/{jobId}/ask             ask a question, approve an experiment
  /r/{jobId}/ask/{id}        read the measured answer
```

The job id is the repository's identity in the browser, so every URL is
shareable and every refresh resumes exactly where it was.

## The shell

A sticky header that leads with the repository's **name** (the slug and the
commit are secondary), the job status as a dot and a word, and four tabs:

```text
RepoLab / apache/spark                                   ● Ready
Apache Spark   commit 0aa4f91
Overview   Experiences   Architecture   Ask RepoLab      ⌁ Analyzed · 51 evidence · 41 components  Details ›
```

*Overview* is the investigation's screen under the name a newcomer understands
(the route is unchanged). The Playground and Learn screens are experiences:
they are reached from Experiences, which stays selected inside them.

The shell owns the job's SSE connection. Next.js layouts preserve state across
navigation, so switching tabs never drops the stream or re-replays history.
Tabs that need a manifest are disabled, with the reason as their title, until
one exists.

## Agent activity

One line in the tab bar — what the agent is doing ("Mapping architecture · 3 of
5") or what it found ("Analyzed · 51 evidence · 41 components") — that opens a
drawer. The drawer holds the two views, both from the same event stream:

- **Phase tracker** — the phases, from `agent_status` events carrying
  `{phase, label, index, total}`. The labels come from the backend
  (`repolab/events/activity.py`); the browser holds no copy of that vocabulary.
- **Activity feed** — a line per event: its type, its step, and its `message`.

While an investigation runs, the Overview shows the same announced phases as
progress. Nothing is permanently given a column: the page's content gets the
width.

> **It must never show private chain-of-thought.** The feed renders `message`
> and named `data` keys. It never renders an unknown `data` blob, and no screen
> has a path to raw model output — the backend does not persist any.

## Vocabulary

| word | means |
|---|---|
| **measured** | a number produced by real execution, from Spark's event log |
| **documented** | behaviour stated by official, version-matched documentation |
| **inferred** | a conclusion drawn from the two above; not itself evidence |
| **structural** | reproducible across identical runs: counts, bytes, partitions |
| **timing** | varies between runs: wall clock, durations |
| **contended** | measured while another variant was running; not a clean timing |

Measured and structural figures are presented plainly. Timings always carry
their spread. An inference is always visually weaker than a measurement.

## Visual language

Tokens live in `web/app/globals.css` as CSS variables with a light and a dark
value each (both themes follow the system), exposed to Tailwind as
`bg-canvas`, `bg-surface`, `text-fg-secondary`, `border-line`, `text-ai-fg` …
Components use the token names, never palette values.

**Every colour means one thing, everywhere:**

| colour | token | means |
|---|---|---|
| indigo | `brand` | primary action, selected navigation, focus |
| violet | `ai` | Ask RepoLab, explanations, AI-written text |
| cyan | `arch` | architecture, components, relationships |
| emerald | `run` | experiences, live environments, success |
| amber | `exp` | experiments, parameter changes, in progress |
| rose | `danger` | failures and destructive actions only |

Everything else is neutral (`canvas`, `surface`, `surface-subtle`, `fg`,
`fg-secondary`, `fg-muted`, `line`, `line-strong`). Code, terminals and consoles
use the `code-*` tokens and are dark in both themes; the landing page is navy.

- **Type:** page 28, section 20, card 16, body 15, meta 13, nothing below 12.
  Monospace only for code, commands, hashes, paths and runtime values.
- **Radius:** 6 / 8 / 12px. Shadows only on drawers and popovers.
- **Motion:** 150–200ms, and none under `prefers-reduced-motion`.
- **Focus:** one 2px brand ring for keyboard focus, everywhere.
- **Status** is a dot *and* a word (`StatusBadge`), never colour alone.
- **Actions** — Ask (violet), Architecture (cyan), Run (emerald), Experiment
  (amber), Source (neutral) — always carry an icon and a label (`ActionLink`).
- One solid button per section: the next action. Everything else is outlined
  or ghost.
- Evidence is progressively disclosed: answer, then details, then evidence,
  then source.

Shared primitives are in `web/components/kit/` (Button, Badge, StatusBadge,
Card, Section, PageHeader, CodeSurface, Metric, EmptyState, ErrorState,
ProgressSteps, Tabs, Chips, Drawer, ActionLink, icons), re-exported by
`components/ui.tsx` with the older Panel, Stat, Empty, ErrorNote and Note.

## States every screen handles

**Loading** — a skeleton, never a spinner on an empty page.
**Empty** — says what happened, why, and what to do next (`EmptyState`).
**Live** — something is visibly happening; the activity chip and drawer are the proof.
**Error** — the real message from the API, not "something went wrong".

## Long waits

Every long wait renders `WorkingStatus` (`web/components/working-status.tsx`).
This covers the analysis, a section still being made, and experience
preparation.

**What it shows:**
- what RepoLab is doing, as a headline and a supporting line;
- a quiet "Working for 1m 5s".

**Where the words come from:**
- The backend reports a *current activity*: a machine key set where that work
  really begins, such as `architecture.components`, `finding_entrypoints` or
  `image`.
- `ACTIVITY_COPY` maps each key to words. The headline changes only when the
  activity does; nothing cycles on a timer.

**How time changes it:** only the tone changes, never the step.
- After 30 s on one step: "This step is taking a little longer than usual."
- After 60 s: "Still working · <step> · Large repositories can take a little
  longer."

**Heartbeat vs progress:**
- **Progress** is a milestone event, persisted and replayed.
- **Heartbeat** is the worker saying it is alive. It is ephemeral, in Redis
  (`repolab/events/liveness.py`), and refreshed every 5 s by a thread.
- A model call can run for a minute with no milestone. **Silence is never
  called a stall.**

**When it warns:** the single amber state, "⚠ Processing may have been
interrupted", requires `health: "interrupted"`. That means the heartbeat is
gone (45 s TTL) on a task running for over 90 s. It offers **Retry**, which
re-submits cache-aware or restarts the preparation. Healthy work never uses
amber or rose.

**Developer diagnostics:** heartbeat age, last event age, worker, activity key
and stream status. They're hidden unless `?debug=1` (remembered; `?debug=0`
turns it off) or `NEXT_PUBLIC_REPOLAB_DEBUG=1`.
