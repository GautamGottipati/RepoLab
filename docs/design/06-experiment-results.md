# Experiment Results — `/r/{jobId}/ask/{experimentId}`

## Purpose

Show the measurements arriving, then explain them — with every claim labelled
by the kind of evidence behind it, and the evidence itself on the same screen.

## Reads

- `GET /experiments/{id}` — status, plan, statistics, reproducibility.
- `GET /experiments/{id}/events` (SSE) — `experiment_variant_started`,
  `partial_result`, `experiment_variant_completed`, `approval_received`.
- `GET /experiments/{id}/explanation` — 404 until there are measurements.

## Rendering as it happens

The screen must be useful before the experiment finishes.

- `experiment_variant_started` → a column appears, pending.
- `partial_result` → that variant's bar grows; the run count ticks up. Each
  carries `value`, `repeat`, `task_count`, `partitions`, `empty_partitions`,
  `total_runtime_ms`.
- `experiment_variant_completed` → the column settles.

**Charts are appended to, never recomputed from a refetch.** Hand-rolled SVG;
the data is a list of points in React state that grows as events arrive.

```
empty partitions                  total runtime (ms, median)
1600 ████████████████ 1593        1600 ████████████ 12,949
 800 ████████ 793                  800 ████████ 9,163
 400 ████ 393                      400 ███████ 8,242
 200 ██ 193  ← baseline            200 █████ 6,011  ← baseline
 100 █ 93                          100 ████ 5,271   −12.3%
  50 ▌ 44                           50 ████ 5,066   −15.7%
structural · repeats exactly      timing · varies between runs
```

Structural and timing charts are visually distinct and always labelled. A timing
bar carries its spread; a structural bar does not need one.

## The statistics table

One row per variant per metric: median, spread, standard deviation, and the
relative difference against the baseline. The baseline row is marked. A metric
that was unstable across repeats of the same value is flagged — it means the
runs were not equivalent and the comparison is weakened.

## The explanation

The answer, then the claims, grouped and visually distinguished by kind:

- **measured** — strongest treatment; shows the metric it cites.
- **documented** — shows the cited URL and the version it documents.
- **inferred** — visually weaker; shows what it follows from.

Then the caveats, verbatim.

If `grounded` is false, a banner lists each unsupported claim and the problem
with it. **This is shown prominently, not hidden.** A claim the evidence did not
support is the single most important thing on the screen.

## Evidence

Four tabs, all from the experiment's bundle:

| tab | content |
|---|---|
| Measurements | every measurement, with its variant, unit and source |
| Code | the repository lines the investigation cited |
| Logs | excerpts of the real Spark event logs |
| Official Docs | the version-matched pages retrieved |

## Reproducibility

A footer: repository, commit, image, the Spark version actually measured,
dataset hash, resources, repeat count, and whether any run was contended. Each
variant run lists its preserved event log and that log's `sha256` — the raw
source every figure above was derived from.

## States

- **Awaiting approval** — redirect back to Ask; results do not exist yet.
- **Running** — charts fill in; no explanation section at all, because there is
  nothing honest to put in it.
- **Completed without an explanation** — when no model is configured, the
  measurements stand alone and the screen says exactly that.
- **Failed** — the error, and whatever variants did complete, since partial
  measurements are still real.

## Must never show

- A number that is not in the statistics.
- A claim without its kind.
- An inference styled as a measurement.
- A timing from a contended run without saying it was contended.
