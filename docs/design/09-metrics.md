# Metrics — `/metrics`

## Purpose

Show what RepoLab has been used for, and how RepoLab performed doing it, from
recorded history only.

Product-wide rather than per-repository: a KPI is about the product, not about
one import. Two sections, deliberately separated, because they answer different
questions — *did developers learn anything?* and *did the agent behave well?*

## Reads

- `GET /metrics` — the whole dashboard: provenance, both sections, the north
  star. One aggregate read; nothing is queued and nothing is computed in the
  browser.

Every figure is computed in `repolab/analytics/metrics.py` from the analytics
events and the domain tables. The screen renders what it is given and derives
nothing.

## The rule this screen exists to keep

A number appears **only** where one was measured. Every metric arrives with a
status:

| status | rendered as |
|---|---|
| `measured` | the value, its unit, and `n=` the observations behind it |
| `no_data` | the words *no data yet* in place of a value |
| `not_instrumented` | the words *not instrumented* in place of a value |
| `north_star` | its own block, labelled, with *not measured* |

On a dashboard, `0%` and *we do not record this* look identical and only one of
them is true. The Python model refuses to carry a value unless the status is
`measured`, so the screen cannot show one by mistake.

Each metric also shows **how it was computed** and **what it came from**, because
several of these are RepoLab's own definitions rather than industry standards —
"agent recovery rate" in particular — and a number whose definition is hidden
invites being read as something stronger.

## Sections

**Provenance**, first, before any figure: how many events, jobs and repositories
over how many days, and that this is development traffic from building RepoLab
rather than usage by learners. It changes how everything below should be read,
so it is not a footnote.

**Developer learning activity** — repositories analyzed and investigation
success; time to repo ready, to first successful run, to first experiment;
lessons started, completed and their rate, steps passed, hints shown, commands
executed; questions asked, experiments proposed, approved, declined, completed,
and the completion rate.

**RepoLab operational & agent performance** — agent runs, task success rate,
average steps overall and when it finished, recovery rate; tool calls, tool
success rate and the worst offenders; model calls, success rate, latency median
and p90, tokens, schema retries; container compute time and model cost.

**Long-term north-star KPI** — *Time to First Meaningful Contribution*, named,
defined, and never given a number. It needs longitudinal data from real teams,
which RepoLab does not have.

## Must never show

- A number for a metric that was not measured, including `0` and `0%`.
- An enterprise outcome claim of any kind: no improvement, no trend, no
  percentage change against a baseline that does not exist.
- A rate computed on an empty denominator.
- A metric without its definition and source.
- These figures as adoption or usage data. They are development traffic, and the
  screen says so.
