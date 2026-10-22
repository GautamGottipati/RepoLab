# Ask RepoLab — `/r/{jobId}/ask`

## Purpose

The question, then an answer or an experiment. Every question is **triaged
first**, because they deserve different responses:

| Kind | Example | What RepoLab does |
|---|---|---|
| conceptual | *What is Cassandra?* | Reads the repository (README, manifest, matching code) and its official docs, then Super answers from that evidence |
| repository | *Where is the CQL parser implemented?* | The same, weighted towards the code |
| testable | *What happens if I filter on a non-key column?* | Designs an experiment **on this repository's real surface** |

A testable question becomes an experiment only when this repository has
something real to run it on, and deterministic code — not the model — decides:

- a runnable Spark playground → the Spark sweep (`spark.sql.shuffle.partitions`);
- a smoke-tested, non-simulated build driven by `cqlsh` → CQL variants on the
  built node, each run in a fresh keyspace;
- neither → it is answered instead, and the answer **says** it could have been
  tested and why it was not (no build yet, Mini Lab only, no model to design it).

Rules settle clear phrasings; Nano classifies only what the rules leave
ambiguous. Whatever the route, **nothing runs until the user approves it**.

Answers label every claim *repository* (cites a file), *documented* (cites a
retrieved page) or *inferred*, and a claim citing a file or URL that is not in
the evidence is flagged on screen.

## Reads and writes

- `POST /experiments` `{repo_url, question, rows?}` → `202 {experiment_id}`.
- `GET /experiments/{id}` — status, the plan, the rejection if refused.
- `GET /experiments/{id}/events` (SSE) — design progress, `approval_required`.
- `POST /experiments/{id}/approve` → queues the run, returns the run's `job_id`.
- `POST /experiments/{id}/reject` → nothing runs, ever.
- `GET /experiments?repo_url=` — previous experiments.

## Layout

```
┌────────────────────────────────────────────────────┐
│ Ask about this repository                          │
│ ┌────────────────────────────────────────────────┐ │
│ │ Why does this job use 200 shuffle partitions?  │ │
│ └────────────────────────────────────────────────┘ │
│                                        [ Ask ]     │
└────────────────────────────────────────────────────┘
```

The Spark demo question is offered as a suggestion only when the repository has
a runnable Spark playground; elsewhere the box is empty with a placeholder.

### While designing

The phase of the design is shown from the event stream: investigating the
repository, searching official documentation, designing the experiment. Nothing
is executed during this stage, and the screen says so.

### The approval card

When `approval_required` arrives, the plan is presented in full:

```
┌─ RepoLab wants to run an experiment ───────────────┐
│ Question    Why does this job use 200 shuffle…     │
│ Hypothesis  …                                      │
│                                                    │
│ Variable    spark.sql.shuffle.partitions           │
│ Values      1 · 50 · 200(baseline) · 500 · 1000    │
│ Repeats     3        Metrics  12                   │
│                                                    │
│ 15 Spark jobs · about 3.5 min · sequential         │
│                                                    │
│ Held fixed          image, master, AQE disabled…   │
│ Not measurable      gc_time_ms (dropped)           │
│ Adjusted            baseline added so there is an  │
│                     anchor to compare against      │
│                                                    │
│        [ Approve experiment ]   [ Decline ]        │
└────────────────────────────────────────────────────┘
```

Three things on that card are not optional:

- **The cost, before the decision.** Runs and estimated minutes, labelled an
  estimate.
- **What validation changed.** Dropped metrics and adjustments, so approval is
  given to the experiment that will actually run, not the one proposed.
- **Both buttons, equally weighted.** Declining is a real choice, not a way out
  of a dialog.

### After deciding

Approving navigates to the results screen. Declining says plainly that nothing
ran and leaves the question available to ask again.

## States

- **Refused plan** — validation can reject a proposal (a variable off the
  allowlist, an over-budget plan). Show the reason and its detail verbatim; it
  is a real answer, not an error.
- **No model configured** — the design still happens, deterministically. The
  card says the plan is a deterministic sweep rather than a model's.

## Must never show

- A run that started before approval. The gate is enforced server-side; the UI
  must not imply otherwise by optimistically rendering progress.
- An approval button on an experiment that is not `awaiting_approval`.
- The model's reasoning about how it designed the plan.
