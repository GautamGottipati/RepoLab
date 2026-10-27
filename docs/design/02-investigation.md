# Investigation — `/r/{jobId}`

## Purpose

Show the agent working. Someone who has just submitted a repository should be
able to tell what RepoLab is doing, that it is still doing it, and when it is
done — without reading tool names or seeing anything the model thought
privately.

## Reads

- `GET /jobs/{jobId}` — status, elapsed, `health` (heartbeat-based),
  `activity` (what the worker is on now), `ingestion_id`.
- `GET /jobs/{jobId}/events` (SSE) — replays history, then follows.
- `GET /jobs/{jobId}/artifacts` — every analysis artifact's status (see
  *Progressive rendering*).

## Layout

```
┌──────────────────────────────────────┬────────────────┐
│  ✓ Inspecting repository       0:04  │ Agent activity │
│  ✓ Finding entry points        0:07  │                │
│  ● Understanding data flow     ...   │ › search_code  │
│  ○ Preparing environment             │   dept         │
│                                      │ › read_file    │
│  ┌────────────────────────────────┐  │   etl_job.py   │
│  │ 23 files · Python, Shell       │  │ ✓ classified   │
│  │ commit eeee0c2b9af7            │  │                │
│  └────────────────────────────────┘  │                │
└──────────────────────────────────────┴────────────────┘
```

**Phase tracker** — the four phases, each `pending`, `active` or `done`, driven
entirely by `agent_status` events carrying `{phase, label, index, total}`. A
phase is `active` once its event arrives and `done` once a later phase starts.

**Findings strip** — facts as they are discovered, from event `data`: file
count, languages, commit, detected surfaces, the environment's readiness.

**Activity feed** — every event, newest last, auto-scrolling.

## States

- **Queued** (`health: waiting`) — "Waiting to start…", with no clock running.
- **Running** (`health: working`) — `WorkingStatus` shows the current activity
  and a quiet timer. Past 30 s on one step it says "taking a little longer";
  past 60 s it says "Still working". Long silences between milestones are
  normal (model calls) and are never called a stall.
- **Interrupted** (`health: interrupted`, only when the worker's heartbeat has
  stopped) — the amber "Processing may have been interrupted" with Retry. See
  `00-overview.md`, *Long waits*.
- **Completed** — a prominent next step to Architecture.
- **Failed** — the job's `error`, verbatim, and the feed kept visible so the
  last thing that happened before the failure is still readable.

## Refresh behaviour

Refreshing mid-run replays the stored history and continues with no gap and no
duplicate — `EventSource` resends `Last-Event-ID`, and the backend serves replay
and live from one ordered query. **This is a demo-critical property and is
exercised by the e2e spec.**

## Progressive rendering

The investigation publishes **analysis artifacts** one by one, and every
screen renders each one as soon as it is ready instead of waiting for the
whole job.

| Artifact | Priority | Made by | Feeds |
|---|---|---|---|
| `repo.facts` | P0 | deterministic | Overview header |
| `repo.manifest` | P0 | deterministic, then refined by the agent (`artifact_updated`) | every tab |
| `architecture.structure` | P0 | deterministic | Experiences, Architecture |
| `architecture.runtime` | P0 | code model, verified | Architecture → Runtime, Flows |
| `architecture.source` | P0 | cited files captured | source viewer |
| `architecture.components` | P1 | Super synthesis, verified | Architecture, Overview |
| `architecture.summary` | P1 | Super orientation | "What is this?", key components |
| `architecture.runtime_naming` | P2, on demand | Super, re-verified | Architecture → Runtime names |

Reads:

- `GET /jobs/{id}/artifacts` gives every status. It is loaded on mount, after
  each reconnect, and after each `artifact_*` event; the REST answer always
  wins, so a replayed old event can never move a section backwards.
- `artifact_ready` / `artifact_updated` refetch only the resource that
  artifact feeds (manifest, architecture or experiences).
- Once the job's stream has closed, an artifact still being generated is polled
  every 2 s until it settles (for example runtime naming after the job finished).

`ArtifactSection` (`web/components/artifact.tsx`):

- It renders children as soon as their data exists.
- Until then it reserves the content's height and shows one plain line, for
  example "Tracing runtime components…" or "Checking every reference against
  the source". It never shows a percentage.
- A failure shows the backend's message and **Retry**
  (`POST …/artifacts/{type}?retry=true`), and nothing outside that section
  changes. A blocked section says it is waiting on an earlier step.

Chrome:

- **Tabs** carry a small pulsing dot while one of their artifacts is being made.
- **Activity chip** reads "Still learning · ✓ facts ✓ manifest → runtime ○
  components ○ summary" until every eager artifact has settled.
- **Activity drawer** lists every artifact, marking reused ones and those
  generated "when opened".
- **Opening Architecture** requests `architecture.runtime_naming` once
  (idempotent on the backend). Until it is ready, components keep their real
  type names and say so.

## Must never show

- Tool arguments that might contain file contents.
- Any `data` key the screen does not explicitly name.
- A phase marked done that never emitted its event — the tracker reflects the
  stream, and never anticipates it.
