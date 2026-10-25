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
retrieved page), *measured* (names a recorded measurement) or *inferred*, and a
claim citing a file or URL that is not in the evidence is flagged on screen.

## An answer teaches before it proves

```
QUESTION → SIMPLE ANSWER → VISUAL → HOW IT WORKS → DETAILS → SOURCES → CONTINUE EXPLORING
```

**Shape.** The layout follows the question, read from its phrasing by rules
next to triage. When the phrasing is ambiguous, the answer's own Super call
picks; no call is spent on layout.

| Shape | Phrasing | Leads with |
|---|---|---|
| flow | how does X use / talk to Y | a small diagram (≤8 boxes), then steps |
| execution | what happens when…, follow a request | numbered steps (and a diagram if evidenced) |
| definition | what is X, what does X do | what it is · role here · how it connects · where |
| why | why does / why is | problem · design choice · why it helps · trade-offs, each *evidenced / inference / unknown* |
| comparison | X vs Y, difference between, instead of | a table; unsupported cells read "not established" |
| location | where is, which file | a code map, entry → … → persistence, each clickable |
| architecture | how is this organised, main parts | a diagram of the parts |
| debugging | why is X slow / failing | symptom · cause · how to check |

**What the answer starts from.** It starts from the investigation, and nothing
is re-analysed:

- the manifest and the README;
- the files the question's terms hit, and the local modules those files import;
- the project's official docs;
- a bounded slice of the stored architecture: the focus, the components the
  question names and their neighbours, with `edge:a->b` ids. Its cited lines
  join the evidence;
- measured values, only from recorded runs.

**Verification** (deterministic, `repolab/questions/layout.py`):

- **Arrows.** An arrow is drawn only when it cites a real file, or a slice
  relationship joining the same components. Otherwise it is listed as "RepoLab
  could not establish …" and not drawn.
  - Inferred arrows are dashed.
  - A diagram made mostly of inferences is not drawn.
- **Sources.** Step, section and code-map sources must exist.
- **Numbers.** A unit-bearing number that no evidence contains is flagged as
  an invented measurement.

## Layout (answered)

```
┌─────────────────────────────────────────┬──────────────────────┐
│ Ask about X   [Context: X ×]            │ REPOSITORY CONTEXT   │
│ [ question                          ]   │ X · role             │
│ [Ask]   Suggested: [..] [..] [..]       │ Used by / Uses       │
├─────────────────────────────────────────┤ Implemented with     │
│ HOW IT CONNECTS                         │ Important files      │
│ Simple answer, large                    │ In this answer       │
│        ┌──────┐                         │ ▸ History            │
│        │  A   │   diagram, scales to    │ ✓ Repository analyzed│
│        └──┬───┘   the column; inferred  │   [View analysis     │
│      ┌────┴────┐  arrows dashed         │    details]          │
│     B1        B2                        │                      │
│ A → B1: routes requests  📄 app.js:5    │                      │
│ HOW IT WORKS  1. … 📄  2. … 📄          │                      │
│ details · caveats                       │                      │
│ SOURCES  Code 9 · Docs 0 · Runtime 0    │                      │
│ ✓ Based on repository code              │                      │
│ ▸ reason — path:line   (expands ~11 ln) │                      │
│ [Show all evidence] → claims + tabs     │                      │
│ CONTINUE EXPLORING [q →] [q →]          │                      │
└─────────────────────────────────────────┴──────────────────────┘
```

- Clicking a source chip, a diagram box with a file or a code-map entry opens
  the **code drawer**. On desktop it is a right-hand panel; on mobile, a bottom
  sheet. It shows the cited range, "Why this matters" and a link at the commit.
- The context column stacks under the answer on small screens. Diagrams scale,
  and code and tables scroll inside their own boxes.
- Static, documented, inferred and measured labels live in **Show all
  evidence**. The answer itself is not covered in labels, but an unsupported
  claim is always shown at the top.

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
  card says the plan is a deterministic sweep rather than a model's. An answer
  shows its sources, follow-ups from the architecture, and says no narrative
  could be written.
- **Answers stored before Phase 17** — their prose is the simple answer; no
  diagram.

## Must never show

- A run that started before approval. The gate is enforced server-side; the UI
  must not imply otherwise by optimistically rendering progress.
- An approval button on an experiment that is not `awaiting_approval`.
- The model's reasoning about how it designed the plan.
- An arrow, code-map entry or source chip the evidence does not support.
- A measured value without a recorded run behind it.
