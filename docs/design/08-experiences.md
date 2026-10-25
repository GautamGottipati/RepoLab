# Experiences — `/r/{jobId}/experiences`

## Purpose

Answer **"What can I actually do with this software?"** — not "what interfaces were
detected". A ★ primary experience (*if I have five minutes, what should I try?*)
and up to three more, each a real action: *Transform a Dataset*, *Use the
Application*, *Try the API*, *Try the CLI*, *Try the Library*. When nothing real
can run: why, what RepoLab runs, a **Mini Lab** clearly marked as a simulation, and
the Architecture and Ask RepoLab as next steps — never a dead end.

## Four states, three realities

| State | Means |
|---|---|
| detected | the repository has the capability (evidence) |
| supported | RepoLab has an adapter for it |
| runnable | a validated plan exists; RepoLab can start it (possibly after a build) |
| running | a session is up and its readiness check passed |

| Reality | Badge | Runs |
|---|---|---|
| `real_repository` | REAL REPOSITORY | code built from the investigated commit |
| `official_runtime` | OFFICIAL RUNTIME | an official release of the project; the version gap is disclosed on the card and in the session |
| `mini_lab` | SIMULATED / EDUCATIONAL | nothing from the repository; never measurements |

Every runtime option also records its **strategy** (the fallback rung):
cached build → repository container → source build → official release → partial →
Mini Lab. Switching rungs is a choice the learner makes on the card, never silent.

## Discovery and ranking (`repolab/experiences/discovery.py`)

Adapters (`repolab/experiences/adapters/`) propose experiences from evidence —
surfaces, frameworks, `RepoManifest.facts` (a shipped Spark runtime, a Python
package and its README/`examples/` code, read with their lines at investigation
time), the Phase 18 runtime plan. Ranking is deterministic and its signals are
kept on each experience: representative (adapter claim + surface confidence),
runnable now, quick (a ready runtime), recommended (README material). At most four
runnable experiences; the rest are listed as "also detected" with their reason.

| Adapter | Experiences | Runs |
|---|---|---|
| `spark` | Transform a Dataset ★, Try Spark SQL, Explore Partitions & Shuffles (a repository that ships Spark); Run the Repository's Pipeline (a Spark job repository) | a learner's PySpark on a seeded sample dataset, `spark-submit` in a fresh hardened container, measured from Spark's event log |
| `live` | Use the Application, Try the API | the Phase 18 runtime (no-egress network, sidecar, Host-routed proxy) |
| `cli` | Try the CLI | the existing terminal + lesson (Learn tab) |
| `library` | Try the Library | `pip install .` of the commit into an image (cached), snippets run in a network-less container |
| Mini Lab (fallback) | Guided Mini Lab | the existing generator; answers from a validated, SIMULATED table |

## Reads and writes

- `GET /jobs/{id}/experience-list` — ranked experiences, `also`, `fallback`, reasons.
- `POST /jobs/{id}/experiences/{experience}/sessions` `{strategy}` — prepare (202; SSE on the returned job).
- `GET /experience-sessions/{id}` — reconnect (refresh lands on the same session, `?session=`).
- `POST /experience-sessions/{id}/runs` `{code, shuffle_partitions}` — run (202).
- `POST /experience-sessions/{id}/runs/{run}/explain` — explain a run (202).
- `POST /experience-sessions/{id}/minilab` `{command}` — a simulated response.
- `POST /experiments` `{…, experience_session_id}` — an experiment on the learner's last good run.

## The workspace

```
Transform a Dataset   [OFFICIAL RUNTIME]  Official Apache Spark 3.5.1
  This is an official Spark 3.5.1 release, not this commit (5.0.0-SNAPSHOT).
What you'll do · What you'll learn · What to notice
Input data · sales.csv (2000 rows, seeded, sha256 …)     [preview]
[Transform a Dataset] [The README's first example (README:149)]
PySpark                                    Shuffle partitions [4]
┌ result = df.filter(...).groupBy("country")... ┐            [Run ▶]
RESULT            table of real rows
RUNTIME · measured from Spark's event log   Jobs 1 · Stages 2 · Tasks 5 · 5.58 s …
WHAT JUST HAPPENED?   Your code ↓ DataFrame / SQL API ↓ Query planning (Exchange
  is a shuffle) ↓ Scheduler (1 job, 2 stages) ↓ Executors (5 tasks) ↓ Result
  each with "… in Architecture →" and 📁 source directories at the commit
[Explain this run]   [Run an experiment: fewer or more partitions? →]
▸ Execution plan, logs and raw measurements
```

Mapping steps appear only when the run shows them *and* the architecture graph has
a matching part; an official-runtime run says the links are the investigated
source, not the version that ran. Explanations are verified like Ask RepoLab
(measured claims need recorded measurements). The experiment reuses the existing
flow — approval, repeats, deterministic statistics — on the learner's snippet and
runtime.

## Must never show

- LIVE / running before the readiness check passed.
- A measurement that did not come from the run (event log or process timing).
- An official release or a Mini Lab without its badge and disclosure.
- A silent switch between runtime rungs.
- A dead end: when nothing runs, reasons + Mini Lab + Architecture + Ask.

## Detected surfaces & evidence (collapsed below the experiences)

## Reads

- `GET /jobs/{job_id}/experiences` — the catalog: each surface with its icon,
  label, confidence, detection reason, cited files, and whether it is runnable.

The runnability is decided in `repolab/experiences/catalog.py` by asking the code
that would actually refuse — `candidates_for` for a build strategy,
`teaches_cassandra` for the lesson — so this screen cannot offer something the
rest of the product declines.

## Cards

One per surface, ordered runnable first and then by confidence.

**Header** — the icon from CLAUDE.md's vocabulary (`🌐 >_ 🔌 📓 ⚡ ⌨`), the
surface's name, *primary* where it applies, and the evidence strength in words:

- *Strong evidence* means a confidence ≥ 0.80.
- *Some evidence* means at least the hybrid threshold of 0.60.
- *Weak evidence* means below that: real evidence, but too thin to call the
  surface substantial.

The figure itself is in a tooltip. Under the name, one plain sentence says what
the surface is ("A user interface that runs in the browser.").

**Status** — either:

- *Runnable in this build, through RepoLab's `<adapter>`*, with a button that
  opens it; or
- the sentence, verbatim: **Detected, but not yet runnable in this RepoLab
  build.** followed by the specific reason — no build strategy for this
  repository, no adapter for this kind of surface, no lesson for this command
  line, or not the surface RepoLab would build.

**Why RepoLab thinks so**, on selection:

- the signals as readable phrases ("Creates an Express app", "Depends on
  Angular", "A script starts a server"). Unknown kinds are shown as-is, never
  dropped;
- the cited files, each linked at the investigated commit.

**Above the cards**, one sentence says what was found and which surface is the
main way in. The classifier's raw rationale is in a collapsed "How RepoLab
decided".

## States

- **Not investigated** — the tab is disabled, like every manifest-backed screen.
- **Hybrid** — a badge, from the classification's own `hybrid` flag.
- **Nothing runnable** — the normal case for a hybrid repository today. The
  screen says so once, calmly, and points at what *is* available for every
  investigated repository: **Explore the architecture** and **Ask RepoLab**,
  with the repository's recommended first question. These are not experiences
  for the undetected surfaces, so the rule below still holds.
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

## Live experiences: web applications and HTTP APIs (Phase 18)

Three states, kept apart in code and on screen:

| State | Means | Decided by |
|---|---|---|
| **Detected** | evidence of a web app or API | the classifier (`RepoManifest.interaction_surfaces`) |
| **Runnable** | a `RuntimePlan` service exists for the surface, from repository evidence, and the `LiveAppExperience` adapter can attempt it | `repolab/experiences/catalog.py` |
| **Running / LIVE** | built, started, listening on a reachable address, and answered HTTP through RepoLab's sidecar | the launcher (`repolab/runtime/launcher.py`) |

### Reads and writes

- `GET /jobs/{id}/runtime` — the plan, and the latest launch.
- `POST /jobs/{id}/runtime` — start. This is the approval gate for starting
  services, and the launch runs as a worker job.
- `GET /runtimes/{id}` — one launch.
- `DELETE /runtimes/{id}` — stop.
- `POST /runtimes/{id}/request` — the API explorer. It runs server-side,
  method- and size-bounded, to the running service only.
- `http://rt-<runtime>-<service>.localhost:8000/` — the live app on its own
  origin, through the Host-routed proxy. It supports HTTP and WebSocket.

### Layout

```
Run it in RepoLab                              [Start application]
  Needs: MongoDB — external, not reachable from the sandbox (app.js:19)
  🌐 Web application  ./        Runtime node:14 — lockfile v1 (inferred)
                                Install npm ci · Start npm run start -- --host 0.0.0.0 …
                                Port 4200 — the Angular dev server's default (inferred)
  🔌 HTTP API  backend/         Port 3000 — PORT || 3000 in backend/bin/www:15
```

While it starts, each service shows ✓ / → / ○ / ✗ for:

1. preparing
2. installing
3. building
4. starting
5. waiting for it to answer

The latest event is on one line, and the full logs are collapsed.

**Live states:**

- **Web, ● LIVE:** an embedded preview, plus Open application, Reload and Stop.
- **API, ● LIVE:** the base URL, the declared routes (method, path, file:line,
  with mount prefixes resolved), and a request form showing the real response.

**On failure,** the card shows:

- what succeeded;
- the evidenced reason (exit code, nothing listening, bound to localhost, no
  HTTP answer);
- the file:line evidence;
- the next step;
- the logs.

### Must never show

- LIVE before a service answered HTTP through the sidecar.
- A request schema the source does not declare.
- A credential found in source, anywhere.
- A failure reason not taken from what actually happened.
