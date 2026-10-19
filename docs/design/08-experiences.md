# Available Experiences — `/r/{jobId}/experiences`

## Purpose

Show every way a repository can be interacted with, and say truthfully which of
them this build can run.

A repository is not one application type. Airflow ships a command line, a web
UI, an HTTP API and an importable library, and RepoLab detects all four. The
screen's job is to present that without either hiding the surfaces it cannot run
or implying it can run them.

## Reads

- `GET /jobs/{job_id}/experiences` — the catalog: each surface with its icon,
  label, confidence, detection reason, cited files, and whether it is runnable.

The runnability is decided in `repolab/experiences/catalog.py` by asking the code
that would actually refuse — `candidates_for` for a build strategy,
`teaches_cassandra` for the lesson — so this screen cannot offer something the
rest of the product declines.

## Cards

One per surface, ordered runnable first and then by confidence.

**Header** — the icon from CLAUDE.md's vocabulary (`🌐 >_ 🔌 📓 ⚡ ⌨`), the short
label, the surface's own name, its confidence, and two badges where they apply:
*primary* (the experience RepoLab would recommend) and *weak signal* (below the
hybrid threshold of 0.60 — real evidence, too thin to call substantial).

**Status** — either:

- *Runnable in this build, through RepoLab's `<adapter>`*, with a button that
  opens it; or
- the sentence, verbatim: **Detected, but not yet runnable in this RepoLab
  build.** followed by the specific reason — no build strategy for this
  repository, no adapter for this kind of surface, no lesson for this command
  line, or not the surface RepoLab would build.

**Evidence**, on selection — the detection reason naming the signals
responsible, and the repository files cited for it, with line numbers where the
detector recorded them.

## States

- **Not investigated** — the tab is disabled, like every manifest-backed screen.
- **Hybrid** — a badge, from the classification's own `hybrid` flag.
- **Nothing runnable** — the normal case for a hybrid repository today. The
  screen says `0 runnable here` in its header rather than leaving a learner to
  discover it card by card.
- **No surfaces at all** — an empty state pointing at the Investigation screen's
  confidence and limits, rather than an invented fallback.

## Elsewhere in the workspace

- The tab list gates *Playground* and *Learn* on this catalog reporting that
  surface **runnable**, so neither appears for a repository RepoLab cannot run.
- The Investigation screen's "Surfaces" count links here, and its next-step
  buttons offer a runnable experience only when the catalog names one.

## Must never show

- A link, button or tab for a surface that is not runnable.
- "Coming soon", a progress bar, or any other suggestion that an unsupported
  experience is nearly working.
- A surface with no reason or no cited files.
- A confidence figure the classifier did not produce, or a surface the manifest
  does not contain.
