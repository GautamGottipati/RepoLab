# Investigation — `/r/{jobId}`

## Purpose

Show the agent working. Someone who has just submitted a repository should be
able to tell what RepoLab is doing, that it is still doing it, and when it is
done — without reading tool names or seeing anything the model thought
privately.

## Reads

- `GET /jobs/{jobId}` — status, elapsed, stall detection, `ingestion_id`.
- `GET /jobs/{jobId}/events` (SSE) — replays history, then follows.

## Layout

```
┌──────────────────────────────────────┬────────────────┐
│  ✓ Inspecting repository       0:04  │ Agent activity │
│  ✓ Finding Spark entry points  0:07  │                │
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

- **Queued** — "waiting for a worker". If this persists, the worker is not
  running, and the stall banner says so after the threshold.
- **Running** — phases advance; the feed moves.
- **Stalled** — `GET /jobs/{id}` reports `stalled`; show its `stall_summary`.
- **Completed** — a prominent next step to Architecture.
- **Failed** — the job's `error`, verbatim, and the feed kept visible so the
  last thing that happened before the failure is still readable.

## Refresh behaviour

Refreshing mid-run replays the stored history and continues with no gap and no
duplicate — `EventSource` resends `Last-Event-ID`, and the backend serves replay
and live from one ordered query. **This is a demo-critical property and is
exercised by the e2e spec.**

## Must never show

- Tool arguments that might contain file contents.
- Any `data` key the screen does not explicitly name.
- A phase marked done that never emitted its event — the tracker reflects the
  stream, and never anticipates it.
