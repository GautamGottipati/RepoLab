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

A header naming the repository and the commit, a tab bar for the five
workspace screens, and the **agent activity rail** on the right.

The shell owns the job's SSE connection. Next.js layouts preserve state across
navigation, so switching tabs never drops the stream or re-replays history.

Tabs become available as the data behind them does, and say why they are not:
*Architecture* is disabled until the investigation finishes; *Playground* says
so if the environment is unavailable.

## Agent activity

Visible on every workspace screen. Two parts, from the same event stream:

- **Phase tracker** — the four phases, from `agent_status` events carrying
  `{phase, label, index, total}`. The labels come from the backend
  (`repolab/events/activity.py`); the browser holds no copy of that vocabulary.
- **Activity feed** — a line per event: its type, its step, and its `message`.

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

- Dark and light, following the system; tokens in `globals.css`.
- Monospace for anything that came from the machine: paths, hashes, commands,
  figures.
- One accent colour for "this is live", one for "this needs you" (approval), one
  for "this failed". Nothing else is coloured for decoration.
- Evidence is always adjacent to the claim it supports, never behind a link.

## States every screen handles

**Loading** — a skeleton, never a spinner on an empty page.
**Empty** — says what would fill it and how to get there.
**Live** — something is visibly happening; the activity rail is the proof.
**Error** — the real message from the API, not "something went wrong".
