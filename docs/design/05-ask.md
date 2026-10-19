# Ask RepoLab — `/r/{jobId}/ask`

## Purpose

The question, the plan, and the gate. This is where RepoLab stops being a
viewer: a question becomes a controlled experiment, its cost is stated, and
**nothing runs until the user approves it**.

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

The demo question is offered as a suggestion, because it is the question the
whole system was built to answer.

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
