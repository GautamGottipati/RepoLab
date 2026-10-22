# RepoLab Progress

Last updated: 2026-10-19 (Ask RepoLab triage; CLI experiments)

---

## Demo Status

> ## P0 and P1 are complete, and P3's hybrid detection is demonstrated.
>
> **The Spark experience, the Cassandra lesson and the hybrid-detection demo all
> work from the browser, with no backend script invoked.** Each is verified by a
> real browser driving the real stack — `make e2e` runs all three, and the
> observed runs are below.
>
> P3's scope is detection, not an Airflow environment: the demonstration is that
> RepoLab finds four interaction surfaces, evidences each one, and **refuses to
> pretend it can run any of them.**
>
> A KPI dashboard at `/metrics` shows both halves of the story — what developers
> did, and how RepoLab performed doing it — from recorded history only, with
> every unmeasurable metric labelled rather than filled in.

**`docs/DEMO.md` is the runbook**: exact repositories and commits,
prerequisites, the expected preflight output, cache preparation, a timed
three-minute script, and recovery steps for every failure that has been
provoked.

```
make up && make db-upgrade
make demo-check     # the preflight: is this machine ready?
make demo-prepare   # warm the images, ingestions, ladder and lesson
make dev            # api :8000, worker, web :3000
open http://localhost:3000
```

### P0 — Spark, in the browser alone

1. **Import** — paste a repository URL (the Spark demo is prefilled).
2. **Investigation** — watch four phases announce themselves, and the agent's
   activity beside them. Refresh mid-run: history replays and the stream
   continues.
3. **Architecture** — components, data flow, important files and concepts, each
   citing the repository lines that justify it.
4. **Playground** — the real dataset and its hash, the pipeline with RepoLab's
   additions disclosed, the shuffle partition count as a control, and metrics
   parsed from Spark's own event log.
5. **Ask RepoLab** — *"Why does this job use 200 shuffle partitions?"* The plan
   and its cost are presented, and **nothing runs until you approve**.
6. **Experiment Results** — charts fill in as each variant completes, then the
   statistics, the explanation with every claim labelled *measured*,
   *documented* or *inferred*, the evidence behind it, and the reproducibility
   record.

### P1 — Cassandra, in the browser alone

Import `https://github.com/apache/cassandra`. The **Learn** tab appears because
the manifest found a command-line surface — the Playground does not, because it
did not find a data playground.

1. **Build an environment** — asked for, not assumed: it compiles source, pulls
   a release and starts a 4 GB node. The ladder races all four rungs and caches
   each one that passes its smoke test.
2. **Start a terminal** — xterm.js over a WebSocket into the container restored
   from that checkpoint. Statements run through the environment's own `cqlsh`,
   and the output is what cqlsh printed.
3. **Work through the lesson** — eight steps, each with its concept, context,
   statement, expected observation, hint and the repository lines behind it.
   *Check my work* judges the real output, and says whether a check or a model
   decided.
4. **Provoke the error on purpose** — a `WHERE` on a non-key column, which
   Cassandra refuses with its own `ALLOW FILTERING` message; the next step
   explains what that refusal means.
5. **What just happened?** — Super explains from the output, the source and the
   documentation, and every claim is checked against them before it is shown.

### P3 — a hybrid repository, in the browser alone

Import `https://github.com/apache/airflow`. It has no *Playground* and no
*Learn* tab, because RepoLab cannot run either — and says so rather than
offering them.

1. **Experiences** — four surfaces, each with CLAUDE.md's own icon: `>_ CLI`
   (0.95, primary), `🌐 Web` (0.70), `🔌 API` (0.45), `📓 Code / Notebook`
   (0.35), and a *hybrid repository* badge.
2. **Select any of them** — the detection reason, naming the signals
   responsible, and the repository files cited for it.
3. **Read what RepoLab will not pretend** — every one of the four says
   *Detected, but not yet runnable in this RepoLab build.* with the specific
   reason, and none offers a link or a button.

### Metrics — `/metrics`

Product-wide, reachable from the Import screen. Two separated sections and one
labelled long-term KPI:

1. **Developer learning activity** — repositories analyzed, time to repo ready /
   first run / first experiment, lessons started and completed, hints shown,
   commands executed, experiments proposed / approved / completed and their
   rates.
2. **RepoLab operational & agent performance** — agent runs and task success,
   average steps, recovery rate, tool success rate, model calls, latency,
   tokens, schema retries, container compute time and model cost.
3. **The north star** — *Time to First Meaningful Contribution*, named, defined,
   and **never given a number**.

Each metric shows how it was computed, what it came from, and how many
observations it rests on. A metric that was not measured shows *no data yet* or
*not instrumented* in place of a value — never a zero.

Everything the CLI could do, it still can:

```
make experiment Q="Why does this job use 200 shuffle partitions?"
make run-spark-demo
make ingest URL=https://github.com/AlexIoannides/pyspark-example-project ARGS=--persist
make build URL=https://github.com/AlexIoannides/pyspark-example-project
make build-ladder LADDER_URL=https://github.com/apache/cassandra
make e2e            # all four browser walkthroughs
make e2e-cassandra  # just the lesson
make e2e-airflow    # just the hybrid detection
make e2e-metrics    # just the dashboard
make check-secrets  # scan everything RepoLab has written for a credential
```

Next: **P2 — graceful degradation on a repository the ladder was not written
for.** It does not begin until this phase is approved.

---

## Demo Ready

**Yes, with the risks below understood.** Verified on 2026-10-05 by a final
pre-demo audit: every path driven end to end in a real browser, every failure
mode provoked rather than reasoned about.

| path | state | cold | warm |
|---|---|---|---|
| **P0** Spark: import → architecture → playground → ask → approve → measured answer | works | ~8 min | ~7 min |
| **P1** Cassandra: ladder → terminal → eight-step lesson | works | ~8 min | ~2 min |
| **P3** Airflow: four surfaces, each evidenced, none runnable | works | ~2 min | ~1.5 min |
| **Metrics** `/metrics`: learning activity + operational performance | works | <1s | <1s |
| **Refusal** `pallets/click`: investigated, told plainly it cannot be built | works | ~1 min | ~1 min |

`make demo-check` is the gate: it reports READY only when Postgres, Redis,
Docker, the migrations, both images and all three ingestions are in place.
`docs/DEMO.md` is the runbook, with a timed three-minute script.

**1680 offline tests, 74 docker tests, 5 browser specs.** `make lint`,
`make test`, `npm run lint` and `npm run build` all clean. `make check-secrets`
clean across the live database and the browser artifacts.

### What the final audit fixed

Eight issues, each found by provoking the condition rather than reading for it:

1. **A pruned checkpoint was permanently fatal and misdescribed.** A cached build
   whose Docker image had been removed produced *"The execution environment is not
   available. Check that Docker is running"* — with Docker running perfectly — and
   the dead cache row was served again on every retry. Now its own
   `CheckpointMissingError`, its own sentence (*"The prepared environment is no
   longer on this machine… Build it again"*), and the stale row is forgotten so
   the retry rebuilds. This is the failure `docker image prune` causes, which
   `docs/DEMO.md` warns against.
2. **A double-clicked "Start a terminal" allocated two 4 GB containers.** On a
   machine sized for one, the second died the way Phase 9 measured: silently.
   One terminal per repository now, counting sessions from the moment a
   bring-up *begins*, plus a two-session cap with a reason. The button also
   disables itself while the request is in flight.
3. **A finished job's stream reconnected forever.** `EventSource` reconnects
   whenever the server closes, and the backend closes every stream for a
   finished job — measured at four reconnects in twelve idle seconds, each a
   request and a database query for events that will never exist. The hook now
   stops on the backend's terminal frame: one request instead of four.
4. **`SandboxError` was reported as "a statement is already running."** It
   subclasses `RuntimeError`, which the terminal's socket caught to report
   concurrent statements. The busy condition is now `SessionBusy`, caught by its
   own type.
5. **A raw exception string reached the terminal.** The one call site Phase 13
   missed; it now goes through `explain()` like every other.
6. **A reaped session left the terminal unusable with no way out.** The idle
   reaper can reclaim a session under an open socket; the client was then told
   "stopped" indefinitely. The socket now closes with *"This terminal was closed
   because it sat idle. Start a new one to carry on."*
7. **Research had no deadline.** Tavily's client is synchronous and run in a
   thread, and `asyncio.to_thread` cannot be cancelled — so a hang would have
   stalled an investigation with nothing reported. Bounded at 20s through the
   client's own `timeout`, verified against the installed signature.
8. **The model was told the planned repeat count, not the measured one.** Since
   variant failures became survivable, a model could be told "3 repeats per
   value" about a value that managed two. It now receives what ran, with the
   plan's figure alongside, and a note when they differ.

### Audited and found sound

- **Unsafe command execution** — every `subprocess` call passes argv, never a
  shell string; `shell=False` throughout; the one place a learner's text reaches
  a command line is `shlex.quote`-d, behind a verb allowlist that checks every
  statement in a batch.
- **Resource release** — the Spark runner, the build resolver and the smoke-test
  service each release their container in a `finally`; a failed variant no longer
  leaks one now that failures are survivable.
- **Cache correctness** — isolated by repository, commit *and* rung; an unready
  artifact cannot be cached; a hit is marked `cached` so nothing reports a fresh
  build; `best()` never prefers a simulated rung over a real one.
- **Mini Lab containment** — traced through all five routes it could take. The
  artifact refuses real experiments, `EvidenceBundle` cannot hold a measurement
  while simulated, the catalog will not start a session on it, every simulated
  step says it needs a real build, and an `observed` claim is refused outright
  when nothing was executed.
- **Secret leakage** — neither provider echoes a key (tested against the real
  services with a deliberately wrong one); `SecretStr` masks everywhere; the
  scanner finds nothing in 6,700 stored rows.
- **SSE replay** — `Last-Event-ID` resumes with no gap and no duplicate; a
  refresh mid-run is asserted in the browser.

---

## Known Risks

Ordered by what would actually hurt tomorrow.

1. **Docker memory is the single biggest risk.** Cassandra needs 4 GB and dies
   *silently* in 2 GB — no log, no error, a dead JVM. Set Docker to 6 GB before
   the demo and run `make demo-check`.
2. **A cold Cassandra ladder costs about six minutes**, most of it a real
   `ant jar`. `make demo-prepare` exists for exactly this. If the cache is cold
   and time is short, skip P1 and show P0 and P3.
3. **Three of the five paths depend on a model service.** Nebius being slow or
   down degrades rather than breaks — the deterministic planner designs the
   experiment and says so on screen — but the lesson will be RepoLab's own
   rather than Super's, and no explanation will be generated.
4. **The experiment is the longest live step** at ~4 minutes of real Spark for
   12 runs. It streams partial results throughout, so it is watchable, but it is
   the part most exposed to a slow machine.
5. **A queued job with no worker stays queued.** RepoLab cannot distinguish "no
   worker" from "worker busy" without a heartbeat it does not have. The UI shows
   *stalled* after 30 seconds and asks whether the worker is running, which is
   the right question — but nothing resolves it automatically.
6. **Metrics are development traffic**, stated on the screen. Several are n=1.
   Do not present them as adoption.
7. **Cost metrics read "no data"** because `MODEL_PRICING` is unset. Deliberate:
   RepoLab will not guess a rate. Tokens are measured and shown.
8. **The two credentials that were in `.env.example` should be rotated.** They
   are gone from the file and a test now guards it, but the exposure happened.
9. **HEAD moves.** Each demo repository is re-investigated on import, which is
   the designed path; if a build was cached against an older commit, the terminal
   will say no environment is ready and offer to build one.
10. **Airflow's surface detection depends on the read budget.** The manifest
    reservation makes the four surfaces stable because they rest on dependency
    manifests; a surface resting only on code could still vary between runs.

---

## Fallback Demo Path

If something breaks, in order of preference. Each of these has been run.

**If the Cassandra ladder is cold or fails** — skip P1. P0 and P3 together are
the stronger story anyway: *it investigates, it runs the real thing, it measures,
and it refuses to pretend*. Say the ladder exists and is demonstrated by
`make build-ladder`, which prints every rung.

**If Docker is unavailable** — everything read-only still works: import,
investigation, architecture, surfaces, experiences, metrics. The Playground
disables its run button and says why. This is a genuinely good demo of the
honesty principle; it is not an apology.

**If the model service is down** — the investigation, the manifest, the
architecture, the surfaces and the experiment *design* all still work
deterministically, and the screen says *"the model was unavailable; using the
deterministic sweep"*. The approval gate and the measurements are unaffected,
because no model ever produced a measurement.

**If an experiment fails mid-run** — a failed variant no longer aborts it. Show
the partial statistics with their disclosure. If too few values survive, RepoLab
abandons the experiment with a clear sentence, which is itself the point.

**If the live stack is unusable** — three scripted paths, no browser needed:

```
make build-ladder LADDER_URL=https://github.com/apache/cassandra   # all four rungs, with the upgrade offer
make run-spark-demo                                                # real Spark, real measurements
make experiment Q="Why does this job use 200 shuffle partitions?"  # the whole loop in a terminal
```

**If nothing runs at all** — `docs/PROGRESS.md` and `docs/design/` record every
phase with its observed output, and `make test` proves the logic offline in
about forty seconds.

---

## Unfinished Stretch Features

Honest list. Nothing here is claimed anywhere in the product.

**Not built**

- **Nebius Sandboxes as an execution backend.** Implemented against the
  documented API and blocked on an account entitlement (`ForbiddenError`).
  Everything runs in Docker through the same `ExecutionEnvironment` protocol, so
  no code path is bypassed and nothing simulated is substituted.
- **Experience adapters for `web`, `api`, `notebook` and `terminal`.** Four of
  the six surface types RepoLab detects have no adapter. The Experiences screen
  says so per surface rather than implying otherwise.
- **A full Airflow environment.** P3's scope is detection, and that is what is
  claimed.
- **Challenges.** CLAUDE.md lists a challenge success rate; there is no challenge
  feature, and the dashboard reports the metric as not instrumented.
- **Replaying a Mini Lab lesson against a real environment.** The simulated
  lesson keeps its checks intact so it *can* be replayed; nothing does the replay.
- **`code_modified`, `evidence_opened`, `quick_answer_generated`** — three events
  CLAUDE.md names. Two have no feature behind them.

**Built but unexercised**

- **`SuperPlanner`**, the model-driven agent planner: implemented and tested
  against fakes, never used in a real run — the experiment investigation uses a
  deterministic question-driven planner on purpose.
- **Super's classification tie-break**: unreachable for all four demo
  repositories, because each is unambiguous.
- **Mini Lab in a browser**: the rendering and its restrictions are tested, but
  the container rungs keep succeeding, so the path has never been seen live.
- **Build recovery**: the Build Agent can diagnose and retry, and no candidate
  has yet failed in a way a retry fixed — so the recovery rate reads "no data".

**Deliberately not done**

- **A time axis on the metrics dashboard.** Every figure is all-time. A trend
  needs enough traffic for a trend to mean anything.
- **Redaction of the technical detail behind a failure.** It is the exception's
  own words; rewriting them would make a real error harder to diagnose, and the
  default view carries nothing.
- **Authentication.** The dashboard and every endpoint are open. Correct for a
  hackathon on localhost, and the first thing to change beyond it.

---

## Completed

### Phase 0 — Skeleton + external service capability check

**Monorepo**

- `web/` — Next.js 16.3.6, React 19, TypeScript strict, Tailwind. Placeholder page only.
- `api/` — FastAPI, `GET /health`. Verified returning `{"status":"ok","version":"0.1.0"}`.
- `worker/` — RQ worker. Verified end to end: job enqueued, executed, `pong` returned.
- `repolab/` — shared package with every subpackage from CLAUDE.md created.
- `docker-compose.yml` — Postgres 16 + Redis 7, both reporting healthy.
- `pyproject.toml` + `uv.lock` — Python pinned to 3.11.15 via uv.
- `Makefile` — `install`, `dev`, `test`, `lint`, `fmt`, `up`, `down`, `check`.

**Implemented in `repolab/`**

- `config.py` — settings; all credentials optional and held as `SecretStr`.
- `interfaces.py` — `LLMProvider`, `ResearchProvider`, `SandboxProvider` protocols.
- `llm/response.py` — message-shape inspection (see *Reasoning-field handling* below).
- `llm/nebius.py`, `research/tavily.py`, `sandbox/nebius.py` — provider adapters.
- `capability.py` — probe implementations and the report model.
- `testing/fakes.py` — fakes for all three providers.

**Tests:** 86 passing, entirely offline, no network access required.
**Lint:** `ruff check` and `ruff format --check` clean.

### Phase 1 — Domain models + model router

**Domain models**

- `repolab/evidence/models.py` — `CodeReference`, `DocumentationReference`,
  `Measurement`, `LogReference`, `EvidenceBundle`, plus a `MeasurementSource`
  enum naming only real instruments.
- `repolab/manifest/models.py` — `RepoManifest`, `InteractionSurface`,
  `EntryPoint`, `Service`, `Dependency`, `Concept`, `BuildStrategy`, `SmokeTest`,
  and the `SurfaceType` / `EntryPointKind` / `BuildKind` / `Fidelity` enums.
- Multiple interaction surfaces are first class: verified with the hybrid case
  from CLAUDE.md (Next.js + FastAPI + CLI + Spark), which yields four surfaces
  and resolves `primary_surface` to the Spark data playground.
- Two invariants are enforced by validators rather than convention: at most one
  primary surface, and **simulated evidence may never carry measurements**
  (Mini Lab must never produce measured results).

**Model router — `repolab/llm/`**

- `router.py` — `ModelRouter` with `nano()` / `super()` / `ultra()`. Callers pass
  a tier, a purpose and a Pydantic schema; **no call site can name a model** (a
  test asserts no helper accepts a `model` argument). Returns
  `Completion[T]` carrying the validated object and the call's `CallRecord`.
- `tiers.py` — `ModelTier` (with its `MODEL_*` env var) and a `Purpose` enum
  seeded from CLAUDE.md's per-tier routing guidance; arbitrary purpose strings
  are still recordable.
- `schema.py` — strict JSON Schema preparation (see *Findings*).
- `parsing.py` — tolerant JSON extraction: code fences, prose-wrapped objects,
  braces inside strings. It never repairs malformed JSON, since that would
  invent data.
- `errors.py` — `ModelNotConfiguredError`, `LLMTimeoutError`,
  `LLMTransportError`, `EmptyResponseError`, `SchemaValidationError`, all under
  an `LLMError` base.
- `telemetry.py` — `CallRecord` and `ModelPricing`. One structured `llm.call`
  event per call.
- `build_router(settings, provider)` — DI factory; refuses to construct a real
  provider without `NEBIUS_API_KEY`, and accepts a fake provider offline.

**Verified behaviour** (all offline, against `FakeLLMProvider`):

| Requirement | Verified |
|---|---|
| Structured JSON requested, parsed, schema-validated | yes |
| Retry exactly once on schema failure | yes — second attempt only, `retries == 1` |
| Retry carries corrective feedback and falls back to `json_object` | yes |
| Empty `content` recovered from another field | yes |
| Reasoning-only response raises `EmptyResponseError` | yes |
| Reasoning text absent from records, logs and errors | yes |
| Timeout enforced, and not retried | yes |
| Provider failures normalised to `LLMTransportError` | yes |
| Cost `None` without pricing, computed with it | yes |
| Logs carry metadata only — no prompt, output or key | yes |

**Tests:** 209 offline tests passing (up from 86), re-verified under an
outbound-connection guard so no test touches the network. **Lint:** clean.

---

### Phase 2 — Repository ingestion + RepoManifest

**`repolab/ingest/`** — static inspection only, in this order: validate URL →
shallow clone → file tree → languages → deterministic detection → manifest,
architecture and evidence → optional bounded LLM enrichment.

- `url.py` — HTTPS only; rejects ssh/scp-style/`file://`/`git://`, embedded
  credentials, localhost and private, link-local, reserved and metadata
  addresses (`169.254.169.254`). URL validation is treated as a security
  boundary, not a convenience.
- `clone.py` — `git clone --depth 1 --single-branch --no-tags
  --recurse-submodules=no`, hardened with `core.hooksPath=/dev/null`,
  `protocol.ext.allow=never`, `protocol.file.allow=never`, an isolated `HOME`,
  `GIT_CONFIG_NOSYSTEM`, no credential prompting, and an explicit timeout. The
  clone lives in a temp directory removed on exit. Follows the remote's own
  default branch.
- `tree.py`, `languages.py`, `content.py`, `limits.py`, `priority.py` — bounded
  walking, byte-budgeted reading, and ranking of files by how informative they
  are likely to be.
- `detectors/` — dependency manifests (10 ecosystems), build files,
  configuration, entry points, tests, Dockerfiles/compose services, and
  Spark/HTTP/CLI surface signals. Every finding carries the `CodeReference`s that
  justify it.
- `enrich.py` — optional Nano file summaries and Super architecture labelling,
  both hard-bounded; skipped cleanly with no credentials.

**`repolab/manifest/`** — `architecture.py` (`ArchitectureNode`/`Edge`/`Graph`,
with file references on every node and a validator rejecting dangling edges) and
`builder.py` (manifest assembly, build-strategy inference, smoke-test proposals,
concepts, confidence).

**`repolab/db/` + Alembic** — five tables (`repositories`, `ingestions`,
`repo_manifests`, `architectures`, `ingestion_evidence`) with real `jsonb`
columns on Postgres, declared as `JSON().with_variant(JSONB, "postgresql")` so
the offline suite runs the same models on SQLite. Re-ingesting a commit updates
rather than duplicates. `make db-upgrade` applies the migration.

**Tooling** — `scripts/ingest_repo.py`, `make ingest URL=...`,
`make check-repos`, `make db-upgrade`, and a new `network` pytest marker
(network but no credentials) deselected by default.

#### Observed ingestion of the three test repositories

`make check-repos`, 2026-09-29. No credentials; enrichment not exercised.

| | Spark ETL | apache/cassandra | apache/airflow |
|---|---|---|---|
| commit | `eeee0c2b9af7` | `b11047097f52` | `5799660c4d41` |
| branch | `master` | **`trunk`** | `main` |
| files | 21 | 8,739 (136 MiB) | 14,404 (183 MiB) |
| files read | 15 (40 KiB) | 2,981 (23 MiB) | 3,328 (23 MiB) |
| languages | Python, Shell | Java, Python | Python, TypeScript |
| surfaces | data_playground(0.95), cli | cli(0.95) | web, data_playground, cli, api |
| primary | `data_playground` | `cli` | `web` |
| entry points | 3 | 139 | 341 |
| dependencies | 3 | 14 | 539 |
| architecture | 5 nodes / 6 edges | 185 / 6,394 | 355 / 4,774 |
| evidence refs | 17 | 200 | 200 |
| limits hit | none | scanned_files, read_bytes, total_read_bytes | scanned_files, total_read_bytes |
| duration | 0.7s | 9.9s | 13.9s |

Every architecture node in all three cites at least one real file, and no
ingestion reported a measurement.

#### No repository code was executed

Demonstrated rather than asserted. `tests/test_ingest_safety.py` ingests a
deliberately booby-trapped repository whose `setup.py`, `Makefile`,
`package.json` `postinstall`, `conftest.py` and shell script would each write a
sentinel file if they ran; the test asserts no sentinel exists afterwards. A
further test records every subprocess spawned during inspection and fails if any
is `pip`, `npm`, `yarn`, `mvn`, `gradle`, `make`, `python`, `sh` or `bash`. The
only subprocess ingestion runs is the hardened `git`. `setup.py` dependencies are
recovered by reading it as text, never importing it.

**Tests:** 373 offline (up from 209) plus 10 `network` tests, all passing;
offline suite re-verified under an outbound-connection guard. **Lint:** clean.

---

### Phase 3 — Classification + experience detection

**`repolab/classify/`** — classification moved out of the detectors, where it
never belonged, and gained the parts that make it trustworthy.

- `signals.py` — the signal vocabulary with three weight tiers (strong 0.45,
  medium 0.25, weak 0.10) and the thresholds: `STRONG_CONFIDENCE` 0.80 governs
  the model gate, `HYBRID_CONFIDENCE` 0.60 governs "worth its own experience",
  `TIE_MARGIN` 0.15 defines a tie. Also the documented precedence
  `data_playground > web > api > cli > notebook > terminal`.
- `rules.py` — scores each surface by its **distinct** signal kinds, adds
  corroboration from matching entry points and services, applies the two compound
  rules, and emits an `InteractionSurface` per candidate with confidence, ranked
  evidence and a reason. Picks the primary by score, breaking ties on precedence.
- `ambiguity.py` — the single definition of ambiguity, used both for the flag
  RepoLab reports and the gate it acts on, so they cannot drift.
- `llm.py` — Super tie-break, reachable only through the gate, and permitted only
  to *choose among candidates already detected*.

**Every classification carries surface, confidence, evidence and reason.**
`InteractionSurface` gained `evidence` and `reason`; the new `Classification`
model records `primary_type`, `rationale`, `hybrid`, `ambiguous`, `resolved_by`
and notes, and is persisted inside `RepoManifest`.

**New detector signals**: `detectors/structure.py` adds path-only signals
(shipped `bin/` executables, daemon/server names, systemd units, notebooks,
library packaging); `detectors/surfaces.py` adds interactive-shell (`cmd.Cmd`,
`readline`, `prompt_toolkit`, REPL loop) and server (`*Daemon`, bind/listen,
`serve_forever`) patterns.

**The Super gate.** A model is consulted only when the evidence is ambiguous
*and* there are at least two candidates to arbitrate between:

```
any surface >= 0.80          -> deterministic, never ask
fewer than 2 candidates      -> nothing to arbitrate, never ask
no strong + top two <= 0.15  -> ask Super, tie-break only
model names a non-candidate  -> discarded, deterministic answer stands
```

All three test repositories resolve deterministically, so none reaches a model.

#### Observed classification of the three test repositories

`make check-repos`, 2026-09-29. No credentials used.

| repository | surfaces (confidence) | primary | hybrid | resolved by |
|---|---|---|---|---|
| Spark ETL | `data_playground` 0.95, `cli` 0.2 | `data_playground` | no | deterministic |
| apache/cassandra | `cli` 0.95, `api` 0.1, `notebook` 0.1 | `cli` | no | deterministic |
| apache/airflow | `cli` 0.95, `web` 0.7, `api` 0.45, `data_playground` 0.35, `notebook` 0.35 | `cli` | **yes** | deterministic |

- **Spark ETL** — `detected from spark_session, framework:Spark, spark_submit,
  spark_import; corroborated by spark_job`.
- **Cassandra** — `detected from interactive_shell, bin_executable, argparse,
  shell_script; corroborated by cli_script, server+shell`. The spec's
  "server + interactive shell → CLI playground" rule fires, citing `bin/cqlsh`
  alongside the daemon.
- **Airflow** — five surfaces, marked hybrid, `cli` chosen with the reason
  recorded (`cli scored highest (0.95 vs 0.7 for web)`). This is the multiple-
  surface case the specification asked Airflow to demonstrate.

**Tests:** 441 offline (up from 373) plus 17 `network`, all passing; offline suite
re-verified under an outbound-connection guard. **Lint:** clean. **No migration
was required** — the manifest column is already `jsonb`, so the classification
persists inside it (verified by querying
`manifest->'classification'->>'primary_type'`).

---

### Phase 4 — Jobs, agent runtime + progress events

**`repolab/agents/`** — an explicit, bounded execution model. Agents reason and
choose actions; deterministic code validates and executes them.

- `models.py` — `AgentRun`, `AgentStep`, `ToolRequest`, `ToolResult`,
  `AgentStatus`, `AgentLimits` (iterations, tool retries, step timeout, repeat
  detection).
- `tools.py` — the `AgentTool` protocol and `ToolRegistry`. An agent can only name
  a registered tool; unknown tools, malformed arguments and unapproved gated tools
  are refused **before anything executes**.
- `repository_tools.py` — nine read-only tools wrapping the existing detectors,
  including the seven the specification names. `read_file` and `search_code`
  resolve every path against the clone root and refuse traversal, absolute paths
  and symlinks pointing outside it.
- `planner.py` — `DeterministicPlanner` (evidence-driven: it reads the config
  files actually found and searches docs only for a framework actually detected)
  and `SuperPlanner` (structured `{action, arguments, reason}`, validated like any
  other request, falling back to deterministic planning on failure or a bad name).
- `runtime.py` — the loop: request → validate → execute → record, emitting
  `tool_requested` → `tool_started` → `tool_completed` per step, with iteration
  caps, bounded retries, timeouts, and stall detection for a planner that repeats
  itself.
- `investigator.py` — `RepositoryInvestigatorAgent`, assembling tool results into
  the same `DetectionOutcome` the classifier already consumes.

**`repolab/events/`** — the twelve required event types, `JobEvent` with SSE
rendering, `DbEventEmitter` / `CollectingEmitter` / `NullEmitter`, heartbeat with
elapsed time, and `detect_stall`. **The sequence number is assigned by the
emitter, not a database serial** (see *Findings*), with `UNIQUE (job_id, seq)`
enforcing it.

**`repolab/analytics/`** — all seven required event types and a Postgres writer
that swallows its own failures, so measurement can never break the work.

**`repolab/db/`** — six new tables (`jobs`, `job_events`, `agent_runs`,
`agent_steps`, `tool_calls`, `analytics_events`) and a store covering job
lifecycle, event append/replay, and agent-run persistence. One Alembic revision
(`f4baa5fea88b`).

**`api/`** — `POST /jobs` (202, enqueues, never does the work), `GET /jobs/{id}`
(status, elapsed, attempts, stall flag), `GET /jobs/{id}/events` (SSE; replays
from `after` or `Last-Event-ID`, then follows; `follow=false` drains and closes),
`GET /jobs/{id}/manifest` (404 until ready).

**`worker/`** — `investigate_repository` runs the whole flow in a worker, bridging
RQ's synchronous world to the async pipeline with `asyncio.run`.

**`web/`** — one page: submit, live event log with elapsed time and a stall
indicator, `EventSource` so a refresh resumes via `Last-Event-ID`, and the job id
kept in the URL so the refresh finds its way back.

#### Observed end-to-end runs

Live, against real repositories, 2026-09-30:

| job | repository | events | status | elapsed |
|---|---|---|---|---|
| `834d2f4d` | pyspark-example-project | 37 | completed | 0.9s |
| `43a082aa` | apache/cassandra | 42 | completed | 10.0s |
| `720c14c3` | apache/airflow | 39 | completed | ~25s |
| `7226c658` | a nonexistent repository | 2 | failed | — |

Every job's sequence is **contiguous** from 1 with no gaps. The failing job
recorded `failed` and an error rather than vanishing.

**Refresh mid-run, the phase's central criterion.** On the Airflow job: one
connection watched 38 events and dropped; a second reconnected with
`Last-Event-ID: 2`, **replayed 36 stored events and then received 2 new live
ones**, contiguously. On the Spark job a reconnect from `id 38` produced zero
duplicates, zero gaps, and a combined sequence of exactly 1..39.

Persisted after those runs: 4 jobs, 120 job events, 3 agent runs, 24 agent steps,
24 tool calls, and all seven analytics event types (`agent_failed` included, from
the failing job). Every `agent_steps.status` is a concise one-liner such as
`read configs/etl_config.json (3 of 3 lines)` — no model text.

**Tests:** 606 offline (up from 441), re-verified under the outbound-connection
guard. **Lint:** clean.

---

### Phase 5 — Sandbox provider + minimal build resolver

**`repolab/sandbox/`** — the provider lifecycle, re-derived from the current
official documentation and cross-checked against the installed `contree-sdk`
0.3.6 source rather than carried over from Phase 0.

- `models.py` — `SandboxHandle`, `Checkpoint`, `SandboxCapabilities`.
- `provider.py` — the `SandboxProvider` protocol: `create`, `upload`, `execute`,
  `checkpoint`, `restore`, `terminate`, plus a `capabilities` property so callers
  ask what a provider supports instead of assuming.
- `nebius.py` — rewritten against the verified API (see *Findings*).
- `fake.py` — a real model, not a stub: state accumulates, a checkpoint takes a
  *snapshot*, and restoring one checkpoint twice yields two environments that
  cannot affect each other.
- `environment.py` — `ExecutionEnvironment` + `SandboxEnvironment`. No
  `VMEnvironment` stub, since a placeholder would be dead code; the protocol
  records where it will slot in.

The Phase 0 `start`/`run`/`close` sketch is gone, and `repolab/capability.py`'s
probe now exercises the real lifecycle including a checkpoint.

**`repolab/builds/`** — two strategies only, with the fidelity ladder below them
deliberately unimplemented.

- `models.py` — `BuildArtifact`, `BuildCapabilities`, `BuildAttempt`,
  `SmokeTestResult`, `BuildRequest`. Two invariants are in the types: **`ready` is
  computed** and true only when the smoke test passed *and* a checkpoint exists,
  and **`fidelity` is derived** — `EXACT` only when nothing was substituted.
- `plan.py` / `spark.py` — an inspectable plan: base image, upload, ordered steps,
  and a real Spark job as the smoke test that checks for a marker in stdout, not
  merely a zero exit code.
- `cache.py` — `BuildCache` with in-memory, Postgres and null implementations.
  Only ready artifacts are ever stored.
- `resolver.py` — cache lookup, then an exact source build: upload, run the plan,
  smoke test, checkpoint, cache. Records every attempt. Raises
  `BuildUnavailableError` naming the missing fallbacks rather than degrading
  silently.

**Repository transfer** — the local clone is tarred in memory (`.git`, symlinks
and caches excluded) and uploaded via `apply_files`, so the build needs neither git
nor network egress inside the sandbox.

**Database** — one new `builds` table keyed `(repo_url, commit_sha)` with a unique
constraint; one Alembic revision (`b76113caacbc`).

#### Observed output

`make build URL=…`, 2026-09-30, no credentials present:

```
provider       fake (no NEBIUS_API_KEY or NEBIUS_PROJECT_ID)
cache          in-memory (fake provider)
commit         eeee0c2b9af7 (master)
primary        data_playground
base image     docker://apache/spark-py:v3.4.0
  1. prepare the workspace  2. extract repository  3. check the toolchain
  4. install pyspark        5. record versions     6. write the smoke job
smoke test     a tiny Spark job completes
source         source_build
fidelity       high
deviations:
  - pyspark 2.4.0 -> 3.5.1 (the pinned version requires Python 3.6)
  - python 3.6 -> 3.11
smoke          passed (a tiny Spark job completes)
checkpoint     repolab/AlexIoannides-pyspark-example-project:eeee0c2b9af7
READY          (fake provider - SIMULATED, not a real build, checkpoint ckpt-2)
```

Exit 0. Verified alongside it:

| check | result |
|---|---|
| second run with `--cache postgres` | `source cache`, nothing re-executed |
| failing smoke test | `BuildUnavailableError`; 0 cache entries, **0 checkpoints taken** |
| unsupported repository (`psf/requests`) | `NOT SUPPORTED`, exit 3, no traceback |
| `builds` row in Postgres | `source_build \| high \| ready=true \| fake` |

**Tests:** 739 offline (up from 606) plus 7 network-marked CLI tests, all passing;
offline suite re-verified under the outbound-connection guard. **Lint:** clean.

---

### Phase 6 — Spark runtime + real measurements

The first experience backend, and the first measurements RepoLab has ever taken.

**`repolab/sandbox/docker.py`** — `DockerEnvironment`, a second real backend for
the Phase 5 `ExecutionEnvironment` protocol. Repository code therefore runs
isolated from the host, as CLAUDE.md requires, without needing Nebius
credentials. `docker commit` is a genuine checkpoint, verified by committing a
container and restarting from it. Its `capabilities` report deletion and network
as supported, unlike the sandbox's.

**`repolab/experiences/spark/`**

- `dataset.py` — a deterministic synthetic dataset from a fixed seed using only
  the standard library. Columns the repository's `transform_data` needs, plus a
  `dept` column with **one deliberately skewed key** (72% of rows). The sha256 of
  the bytes *is* the dataset hash, computed before execution.
- `driver.py` — the driver RepoLab generates: it reads the dataset, **calls the
  repository's own `transform_data()`**, then repartitions and aggregates on the
  skewed key. If the repository's function cannot be imported the run fails rather
  than silently measuring different code.
- `eventlog.py` — **pure JSON parsing, no pyspark**, against the schema read from
  the Spark 3.5.1 source. Malformed and truncated lines are skipped, not fatal.
- `measurements.py` — typed models and statistics computed by ordinary Python.
  Each metric records the event-log field it came from and whether it is
  `STRUCTURAL` (reproducible) or `TIMING` (varies).
- `runner.py` — orchestration, and preservation of the raw event log.

**Storage** — a new `spark_runs` table (one Alembic revision, `9ffff8d77a84`)
holding the commit, dataset hash, configuration, environment, parsed measurements,
evidence, and a pointer to the preserved raw event log with its size and sha256.

#### Observed real run

`make run-spark-demo`, 2026-10-01. Spark 3.5.1 in `apache/spark:3.5.1-python3`
(arm64-native), 200,000 rows, `spark.sql.shuffle.partitions=200`:

| deterministic | value |
|---|---|
| dataset | 200,000 rows, 7 keys, 72% on `engineering` (144,090 rows) |
| dataset hash | `af24ecb6da1ef2a7…` |
| stages / tasks | 6 / 408, 0 failed |
| shuffle read / write | 1.4 MiB / 1.4 MiB, 400,000 records |
| spill | 0 B memory, 0 B disk (genuinely zero at this size) |
| shuffle partitions | 200 |
| **empty partitions** | **193 (96.5%)** |
| heaviest partition | 144,090 records |
| median non-empty | 9,355 records |
| **partition skew ratio** | **15.4x** |

| timing (varies) | run A | run B |
|---|---|---|
| total runtime | 8,192 ms | 6,468 ms |
| slowest task | 520 ms | 354 ms |

**Reproducibility, checked against two real runs.** Every structural field matched
exactly — dataset hash, stage count, task count, shuffle bytes (1,512,720 both
times), shuffle records (400,000), partitions (200), empty partitions (193),
heaviest partition (144,090), median non-empty (9,355). Timings differed, as they
should.

The raw event log is preserved: `.repolab/runs/<run_id>/local-…`, 2.8 MB,
856 lines, 0 skipped, hashed and recorded in `spark_runs`.

**No model is involved anywhere in this phase.** Every figure is arithmetic over
what Spark wrote.

**Tests:** 878 offline (up from 739) plus 15 `docker`-marked tests that run real
Spark, all passing; offline suite re-verified under the outbound-connection guard.
Parser tests assert against an event log **captured from an actual run**
(`tests/fixtures/spark/`), preserving all 408 task events so the real partition
distribution survives. **Lint:** clean.

---

### Phase 7 — Agentic experiment loop

**The feature RepoLab exists for.** Everything before this produced a
description of a repository; this answers a question about it by measurement.

**`repolab/experiments/`**

- `models.py` — three types kept strictly apart: `ProposedPlan` (what a model
  suggested, untrusted), `ExperimentPlan` (what deterministic code approved and
  will run), and `VariantRun` (what actually happened, with its event log and
  hash). Plus `ReproducibilityRecord`, carrying everything CLAUDE.md requires be
  persisted, and `ExperimentStatus`, whose `may_execute` is the approval gate.
- `validation.py` — **the gate's teeth.** The variable must be on an
  **allowlist** in the file, not merely look like a Spark setting. Values are
  bounded, deduplicated and sorted, and the baseline is always included so the
  comparison has an anchor. Proposed metrics are **reconciled against what the
  event-log parser actually extracts**, and anything unmeasurable is dropped and
  reported. Cost is estimated from a measured per-run baseline, and a plan over
  budget is refused with a reason rather than trimmed.
- `statistics.py` — median, min, max, spread, population standard deviation and
  relative difference, in ordinary Python. No judgement: the lowest median is
  reported as the lowest median, not as "the best". Each metric's `determinism`
  travels with its statistics, so a wall-clock difference cannot be presented as
  being as solid as a task count.
- `executor.py` — runs approved variants, and refuses to run anything else.
  Every variant gets a fresh container from the same image with the same
  deterministic dataset; the executor then **verifies** that and refuses to
  aggregate if a dataset hash or image differed. Sequential by default;
  `concurrency > 1` tags every run `timing_contended`.
- `explanation.py` — assembles exactly the permitted payload and nothing else,
  then **checks the output against the measurements**: a `measured` claim must
  name a metric the experiment measured and must not state a number no
  measurement produced; a `documented` claim must cite a page that was actually
  retrieved. Unsupported claims are flagged and travel with the explanation.
- `investigation.py` — a question-driven read-only plan over the Phase 4 tools,
  plus a version-matched official documentation search.
- `agent.py` — `ExperimentAgent`: investigate, design, validate, **stop**, and
  (separately) execute, measure, explain, verify.
- `experiment.py` — the aggregate and its state machine. `approve` and `reject`
  are the only ways out of `AWAITING_APPROVAL`, and approving twice raises.

**Events** — `approval_required`, `approval_received`,
`experiment_variant_started` and `experiment_variant_completed`, all four named
in CLAUDE.md and previously unimplemented.

**Storage** — `experiments`, `experiment_variants` and `measurements` (one
Alembic revision, `adec7ab8f0b0`), matching CLAUDE.md's schema. Measurements are
stored individually as well as inside each variant's JSON, so a metric can be
queried across experiments. Re-saving replaces rather than appends, so one
experiment never leaves two sets of numbers that both look authoritative.

**API and worker** — `api/routes/experiments.py` enforces the gate server-side;
`worker/experiment_tasks.py` splits proposing from running into two tasks, so a
restart between them leaves an experiment waiting for a human rather than
half-run. The run task **verifies the commit** it clones is the one that was
approved, and fails if the default branch has moved.

#### Observed end-to-end run

`make experiment`, 2026-10-04. Live Nemotron Ultra designed the plan; 12 real
Spark runs in `apache/spark:3.5.1-python3`; 60,000 rows, dataset
`9f7cd3700e741319…`, `local[4]`, AQE disabled, sequential.

Ultra proposed `[50, 100, 200, 400, 800, 1600]` with 2 repeats — 12 runs,
estimated 2.8 min — and the hypothesis *"the default 200 minimises total runtime
for this job"*. **The measurements contradicted it.**

| metric | kind | 50 | 100 | **200** | 400 | 800 | 1600 |
|---|---|---|---|---|---|---|---|
| partitions | structural | 50 | 100 | **200** | 400 | 800 | 1600 |
| **empty partitions** | structural | 44 | 93 | **193** | 393 | 793 | 1593 |
| task count | structural | 104 | 204 | **404** | 804 | 1604 | 3204 |
| shuffle read (bytes) | structural | 486,310 | 453,330 | **453,330** | 453,330 | 453,330 | 453,330 |
| heaviest partition (records) | structural | 43,199 | 43,199 | **43,199** | 43,199 | 43,199 | 43,199 |
| partition skew ratio | structural | 15.22 | 15.34 | **15.34** | 15.34 | 15.34 | 15.34 |
| total runtime (ms, median) | timing | 5,066 | 5,271 | **6,011** | 8,242 | 9,163 | 12,949 |
| relative to baseline | timing | −15.7% | −12.3% | — | +37.1% | +52.4% | +115.4% |
| median task (ms) | timing | 38.0 | 25.8 | **17.3** | 15.5 | 10.5 | 9.5 |

Every structural metric was **identical across both repeats of every value**
(`unstable_metrics: []`), which is what makes the comparison sound. Timings
varied, as they should.

The finding: 200 is the default, and for this workload it is the wrong default.
The data has seven distinct keys, so 193 of 200 partitions receive nothing while
one carries 43,199 records; halving the count to 100 or 50 cuts runtime by 12–16%
without changing a single structural outcome except how many partitions sit idle.
The heaviest partition never moves, because the skew is in the data, not the
configuration.

**The explanation was grounded.** Ultra produced 8 claims — 4 `measured`,
2 `documented`, 2 `inferred` — and all 6 checkable claims traced back to the
statistics or to a retrieved page. It also stated plainly that the job uses 200
*"because that is Spark's default value, not because it minimizes runtime for
this workload"*, contradicting its own hypothesis, and raised three caveats
unprompted, including that only two repeats were performed.

Persisted: 1 experiment, 12 variant runs, **180 measurements**, 3 code references,
3 documentation citations, 3 raw event-log excerpts, and 12 preserved event logs
under `.repolab/runs/`.

**Tests:** 1076 offline (up from 878), 22 new `docker`-marked tests running real
Spark end to end, and 5 `live` tests exercising both Ultra calls against the real
model. All passing — 37 `docker` and 8 `live` in total; offline suite re-verified
under an outbound-connection guard that refuses every non-loopback socket.
**Lint:** clean.

#### Observed run through the API and worker

`POST /experiments` on the live API with a real worker, 2026-10-04. Ultra
proposed `[1, 50, 200, 500, 1000]` with 3 repeats — 15 runs, estimated 3.5 min.
The experiment reached `awaiting_approval` and stopped; `GET
/experiments/{id}/explanation` returned **404** while it waited, since there were
no measurements to explain. `POST .../approve` queued the run and returned the
plan summary; a **second approval returned 409** and queued nothing. The run
completed with 15 variant runs and **225 measurements**, and the explanation came
back grounded with 3 checkable claims.

**Capability-checker fixes.** Both defects found while starting this phase are
fixed and verified by `make check`: the stale `timeout_settings` reference that
crashed the run, and a **false pass** where sandbox creation reported `PASS` in
0 ms because `images.use()` resolves lazily and never called the API. It now uses
`strict=True` and reports the real `ForbiddenError`.

---

### Phase 8 — Spark web experience (P0 complete)

Seven phases built a pipeline that worked and could not be seen. This one is the
browser, and the claim that **the whole P0 experience runs from it without a
script** — mechanically verified rather than asserted.

**`docs/design/`** — the six screen specs the README had been promising since
Phase 0, written before the components. Each states its purpose, the endpoints it
reads, its states, and **what it must never show**: no private chain-of-thought,
no component without a file reference, no claim without its kind, no number
without a source.

**Three backend gaps, found by asking what each screen needed**

- `GET /jobs/{id}/architecture` — the `architectures` table was written on every
  ingestion since Phase 2 and never served.
- `api/routes/runs.py` and `worker/spark_tasks.py` — the playground can now run
  the pipeline. `POST /runs` queues a job; the measurements arrive at
  `GET /jobs/{id}/run`. `GET /runs/dataset` returns the **real** generated bytes,
  so the sample on screen carries the hash the run will record. One Alembic
  revision (`ed0d9e42fec2`) links a run to the job that produced it.
- `repolab/events/activity.py` — the four phases the specification names, each
  owning the steps and tools that belong to it. The reporter emits one
  `agent_status` per phase entered carrying `{phase, label, index, total}`, so
  the **vocabulary lives in the backend** and the browser renders its tracker
  from the stream rather than keeping its own copy.

**"Preparing environment" is real work.** `ensure_image` inspects the local image
store and pulls only when the image is missing; the investigation runs it for a
Spark repository and reports what it took. Failure is reported, not fatal — a
machine without Docker still gets a usable architecture screen, and the
playground says plainly that it cannot run.

**The browser** — `/` for import, then `/r/{jobId}` with a client shell owning
the SSE connection and child routes for investigation, architecture, playground,
ask and results. Next 16 layouts preserve state across navigation, so switching
tabs never drops the stream, while every screen still has a shareable URL.
Charts are hand-rolled SVG: no chart dependency, and appending a bar as an event
arrives is an ordinary state update.

#### Observed browser walkthrough

`make e2e`, 2026-10-04 — a real Chrome, a real clone, real Spark, a live
Nemotron Ultra. **7.1 minutes, start to finish, no script invoked.**

```
workspace: /r/56860895-8fda-45da-ad89-fd2abf342eb4
  phase: Inspecting repository
  phase: Finding Spark entry points
  phase: Understanding data flow
  refresh replayed the stream
  investigation complete
  architecture rendered with components, flow, files and concepts
  pipeline ran and produced metrics
  experiment proposed and awaiting approval
  charts began filling in from partial results
  experiment completed
  explanation rendered with labelled claims
  evidence panel shows measurements, code, logs and docs
```

Ultra proposed seven values at three repeats — **21 Spark runs, estimated
4.9 min** — hypothesising that *more* partitions would reduce skew and improve
runtime. The measurements said otherwise:

| metric | kind | 10 | 50 | 100 | **200** | 400 | 800 | 1600 |
|---|---|---|---|---|---|---|---|---|
| partitions | structural | 10 | 50 | 100 | **200** | 400 | 800 | 1600 |
| **empty partitions** | structural | 6 | 44 | 93 | **193** | 393 | 793 | 1593 |
| task count | structural | 28 | 108 | 208 | **408** | 808 | 1608 | 3208 |
| total runtime (ms) | timing | 5,424 | 5,734 | 6,671 | **7,772** | 9,250 | 13,997 | 24,861 |
| vs. baseline | timing | −30.2% | −26.2% | −14.2% | — | +19.0% | +80.1% | +219.9% |

Every structural metric was identical across all three repeats of every value
(`unstable_metrics: []`). **315 measurements** persisted, the explanation
`grounded` with **11 checkable claims** verified — ten measured, one documented,
three inferred — and the reproducibility record complete down to each run's
preserved event log.

**Tests:** 1137 offline (up from 1075), 41 `docker`, 8 `live`, plus the
Playwright spec: the full flow, and **a negative — declining runs nothing and
the gate stays closed**. Offline suite re-verified under the
outbound-connection guard. `npm run build` and `eslint` clean, TypeScript strict
with no `any`.

---

### Phase 9 — Cassandra build resolution pipeline

P0 shipped two build strategies, and `BuildUnavailableError` said so in its own
message: *"Fallback strategies (official release, partial build, Mini Lab) are
not implemented yet."* They are now, and Cassandra — which classified correctly
from Phase 3 and could not be built since — is the repository that demonstrates
them.

**Three things assumed one build per commit** and had to change before a ladder
could exist: the `builds` table was unique on `(repo_url, commit_sha)`; fidelity
was derived from deviations alone and could express neither `PARTIAL` nor
`CONCEPTUAL`; and `BuildSource` had two members.

**The ladder** — `repolab/builds/ladder.py`

```
exact cache -> source build -> official release -> partial build -> Mini Lab
```

Candidates race, and the **highest-fidelity one that passed its smoke test** is
served — never the first to finish. A candidate that built and then failed its
smoke test is not a candidate: not served, not cached, not offered.

**Fidelity is derived from the kind**, following CLAUDE.md's table, and `source`
is derived too — so "official release built from source" is unrepresentable
rather than merely discouraged. Every artifact declares what the specification
requires: source, version, commit if applicable, fidelity, capabilities,
limitations and its smoke result.

**Cassandra** — `repolab/builds/cassandra.py`. Four rungs sharing one smoke
test, because CLAUDE.md already fixes it: *cqlsh connects and a SELECT
executes*. The exact source build runs the repository's own `ant jar`; the
official release runs `cassandra:5.0` and discloses the version difference; the
partial build runs a reduced single node and declares what it therefore cannot
do.

**The Mini Lab** — `repolab/builds/minilab.py`. Its restrictions are enforced in
code, not requested in a prompt: an entry that cites no real file is dropped, an
unknown command returns "this requires a real environment", and `validate`
refuses anything reading as a measurement. That validation *is* its smoke test —
a Mini Lab has no environment to test, so what it checks is that the simulation
obeys the rules, and it is not ready until it does.

**Upgrades** — `repolab/builds/session.py`. `propose` never switches; `accept`
refuses while a command is in flight. The rule lives in deterministic code, like
the approval gate, rather than in whichever client calls it.

**A Docker-backed `SandboxProvider`** — `repolab/sandbox/docker_provider.py`.
The build system was written against `SandboxProvider`, whose only real
implementation was Nebius, which this account cannot execute in. The whole build
path was therefore runnable only against the fake — a poor position for a phase
about building things. Docker supports the entire lifecycle, and more honestly
than the sandbox: `docker commit` is a real checkpoint and images really can be
deleted.

#### Observed ladder run

`make build-ladder`, 2026-10-04, against `apache/cassandra` at `b15526b48165`.
All four rungs resolved, every one against real infrastructure:

| rung | fidelity | ready in | outcome |
|---|---|---|---|
| Mini Lab | `conceptual` | 0s | validated; simulated, no measurements possible |
| partial build | `partial` | 130s | reduced node, `SELECT` executed |
| official release | `high` | 135s | Cassandra 5.0.9, `SELECT` executed, version disclosed |
| **exact source build** | **`exact`** | **235s** | **`ant jar` compiled the commit; the server built from it answered CQL** |

And the sequence the phase asks to see demonstrated:

```
  = using the conceptual environment
  >> a learner can begin on the conceptual environment
  >> they are mid-command: CREATE KEYSPACE demo
  ? a partial environment is ready (currently conceptual)
  >> NOT switching: session is running 'CREATE KEYSPACE demo'
  ? a high environment is ready (currently conceptual)
  >> NOT switching: ...
  ? a exact environment is ready (currently conceptual)
  >> NOT switching: ...
  the learner finishes their command
  ^ upgraded from conceptual to exact
```

Three upgrades offered, every one refused while the learner was mid-command, and
the one applied afterwards was the **best offered** rather than the most recent.

**Tests:** 1290 offline (up from 1137), 58 `docker` (21 new: the real Cassandra
rungs, the upgrade sequence, and image readiness), and 5 `slow` tests running
the genuine `ant jar` — a new marker, excluded from every other target. All
passing; offline suite re-verified under the outbound-connection guard. The web
build and linter are still clean, and the two new event types are in the
browser's vocabulary.

### Phase 10 — Cassandra CLI experience + Learning Agent

Phase 9 could build Cassandra four ways and hand back a checkpoint with a server
in it. Nothing connected a person to that server: `repolab/experiences/` held
only `spark/`, `repolab/lessons/` was an empty package with a docstring, there
was no WebSocket anywhere in `api/`, and `web/` had no terminal.

**The terminal** — `repolab/experiences/cli/`, `api/routes/cli.py`,
`web/components/terminal.tsx`

```
ready artifact -> restore its checkpoint, running its service command
               -> wait for cqlsh to answer
               -> check the statement against an allowlist
               -> execute it inside the container
               -> return exactly what came back
```

xterm.js in the browser (`@xterm/xterm` 5.5 with `@xterm/addon-fit`, named by
CLAUDE.md and the only new frontend dependency), a WebSocket to the API, and
`docker exec` into the learner's own container. **Nothing is simulated and nothing is reformatted** —
what the terminal prints is what cqlsh printed, including the error the lesson
provokes on purpose. Repository code still never runs on the API host: this
process orchestrates and relays, and does not interpret CQL.

A session holds a 4 GB container, so it holds an idle deadline too — and the
API's lifespan owns the reaper that enforces it, because an unread deadline is
not a deadline. The upgrade state is Phase 9's `EnvironmentSession` rather than a
second `busy` flag that could drift from it. `POST /cli/sessions` returns 202 and the bring-up runs
outside the handler: restoring a checkpoint and waiting for Cassandra to replay
its commit log takes tens of seconds.

**The allowlist** — `repolab/experiences/cli/safety.py`. A terminal is the most
direct way to run arbitrary code in this product, so it gets the same treatment
as the experiment variable allowlist: permitted CQL verbs in one file, and a
refusal for everything else. **Every statement in a batch is checked**, not the
leading one — `cqlsh -e` runs `SELECT 1; DROP TABLE t` happily.

**The lesson** — `repolab/lessons/`

Eight steps, each carrying exactly the structure the specification names:
concept, goal, context, action, expected observation, validation, hint, code
references, documentation references. The flow is the one the phase describes:

```
connect -> inspect keyspaces -> create keyspace -> create table
        -> insert -> query -> provoke a safe error -> understand it
```

The error is a *chosen* one. A `WHERE` clause on a non-key column, which
Cassandra refuses with a message telling the learner to add `ALLOW FILTERING`.
Nothing is written, nothing breaks, and it teaches the single most important
thing about Cassandra data modelling. A model asked for "a safe error" could
have produced a syntax typo, which teaches nothing.

**Super writes a lesson from four inputs** — repository evidence read line by
line from a real checkout, the commit's own
`doc/modules/cassandra/pages/managing/tools/cqlsh.adoc`, version-matched
official documentation via Tavily restricted to `cassandra.apache.org`, and what
the resolved artifact declares it can do. Deterministic code then decides: a
step whose statement the allowlist would refuse, or whose expectation nothing can
check, is rejected and RepoLab's own lesson is served instead. A citation to a
file that is not in the repository at this commit is dropped rather than shown.

**Validation is deterministic first** — `repolab/lessons/validation.py`. Every
check a lesson may declare is answerable from the command and its output by
ordinary Python. Nano is consulted *only* when that cannot reach a verdict — the
learner succeeded in a way the step did not literally predict — and its answer is
recorded as the model's, marked inconclusive, and labelled in the UI as
"judgement, not a check".

**"What Just Happened?"** — `repolab/lessons/explain.py`. Super explains what the
server did from the real output, the repository lines behind it and the retrieved
documentation; then deterministic code checks every claim: an `observed` claim
must quote text that is really in the output, a `source` claim must cite a
supplied path, a `documented` claim a retrieved URL. **Any claim stating a timing
is refused outright, whatever its label** — a terminal result contains no
measurements, so one could only have been invented.

**Mini Lab** — `repolab/lessons/minilab_lesson.py`. The same eight steps with
every restriction visible: a prominent `SIMULATED` treatment, the real source
behind each concept, the "requires a real environment" sentence for anything the
lab does not cover, and nothing that reads as a measurement (checked with the
same function the Mini Lab build uses). The real lesson is kept whole inside it,
with its checks intact, because that is what replays when an environment arrives.

When a Mini Lab is the best rung, `GET /lessons` returns that rendering *instead
of* an interactive one and the screen drops the terminal entirely: dashed amber
steps, no Run or Check buttons, and each step saying it needs a real build. The
lab's own entries are not stored with its artifact, so every step says exactly
that — the truthful rendering rather than a degraded one.

**Building is asked for.** `POST /cli/environment` resolves the whole ladder in a
worker. CLAUDE.md requires explicit approval before starting expensive services,
and this compiles source, pulls a release and starts a 4 GB node — so RepoLab
does not do it because someone opened a screen. Each rung that passes its smoke
test is cached as it lands, which is how the upgrade sequence works across
processes: the worker writes the artifact, and the API — already asking the cache
for the best ready build — offers the learner an upgrade on its next look.

**Persistence** — migration `4aa878ba31da` adds `lessons` and `lesson_progress`.
The worker writes a lesson and the API serves it, so it has to be written down
somewhere both can see; progress is keyed by terminal session, so a reload
resumes rather than restarts.

#### Observed browser walkthrough

`make e2e-cassandra`, 2026-10-04, against `apache/cassandra` at `b15526b48165`.
No script invoked; everything below happened in Chrome against the real stack.

| phase | observed |
|---|---|
| import + investigation | 64–84s, primary surface `cli` at 0.95 confidence |
| tabs | **Learn** present, **Playground** absent — the manifest decides |
| ladder, from the browser | all four rungs raced; `partial` and `high` ready at ~90s, **exact source build** (a real `ant jar`) at ~6min |
| serving | `exact`, `/work/repo/bin/cqlsh` — the repository's own shell, compiled from the requested commit |
| terminal | xterm.js connected over WebSocket; first statement answered in seconds |
| lesson | **all eight steps completed**, every verdict deterministic |
| the error step | Cassandra's own `InvalidRequest … use ALLOW FILTERING` |
| warm repeat | 1.6–2.0 minutes end to end on the cached build |

Both lessons were completed this way: RepoLab's own, and the one Super wrote.

#### The lesson Super wrote

Generated from the real repository, its own cqlsh documentation, official
documentation retrieved from `cassandra.apache.org`, and the artifact's declared
capabilities — then run in the browser against the exact build:

```
1  USE system                                            SystemKeyspace/cqlsh.py:82
2  CREATE KEYSPACE IF NOT EXISTS demo WITH REPLICATION…  CreateKeyspaceStatement.java:40
3  USE demo                                              cqlsh.py:82
4  CREATE TABLE users (user_id int PRIMARY KEY, …)       CreateTableStatement.java:289
5  INSERT INTO users (user_id, name, email) VALUES …     ModificationStatement.java:148
6  SELECT * FROM users WHERE user_id = 1                 SelectStatement.java:161
7  SELECT * FROM users WHERE name = 'Alice'   → refused  StatementRestrictions.java:81
8  SELECT … ALLOW FILTERING                   → answered StatementRestrictions.java:81
```

And its "What Just Happened?" on step 7, verified rather than trusted:

```
observed    The server returned an InvalidRequest error with code 2200 …
source      The error message text is produced by the StatementRestrictions class …
inferred    The WHERE clause references a non-key column, so filtering was required …
grounded: True   unsupported: 0   measurements: 0
```

#### What running it against the real thing found

Six defects. The first two were found by reading the allowlist's own output
before anything was wired up; the rest only by running against a real node.

1. **The allowlist checked only the first statement.** `SELECT * FROM x; DROP
   TABLE y` passed as a "select", because `cqlsh -e` runs a batch and the check
   read the leading verb. And `DROP KEYSPACE system_schema` passed, because the
   system-keyspace match required a trailing dot. Both were found in the
   module's own verification output, and the docstring claimed the opposite of
   what the code did. Every statement in a batch is now checked, and system
   keyspaces are matched as bare words.

2. **`>` and `<` were refused as shell redirection.** They are CQL comparison
   operators, and the first real generated lesson lost two of its eight steps to
   it. What actually prevents a redirection is the `shlex.quote` in
   `cql_command` — no shell ever sees the statement — so the characters are now
   permitted and the quoting is the guarantee.
3. **`USE` did not persist.** Each statement is its own `cqlsh -e`, so a learner
   could run `USE demo`, see it accepted, and have the next statement fail with
   "No keyspace has been specified". The generated lesson broke at its fourth
   step. The session now carries the selection as `cqlsh -k` — verified against
   the binary's own `--help` — which is the state a persistent shell would have
   had.
4. **A correct citation was reported as unsupported.** The prompt lists files as
   `path:line`, the model cited `StatementRestrictions.java:81`, and the checker
   compared it against bare paths. A false alarm in the one place false alarms
   are most expensive.
5. **Super cited no repository files at all**, even when asked to. Steps that
   cite none now get RepoLab's own citation, derived from what the statement
   does and pointing at a file already resolved at this commit — a lookup, not a
   claim.

6. **A source hint that matched nothing.** `bin/cqlsh.py` was searched for
   `def main(`, which is in `pylib/cqlshlib/cqlshmain.py`, not the launcher. The
   citation was silently dropped — correct behaviour for an unresolvable
   reference, and exactly why a dropped one has to be visible. Checked against a
   real clone: all nine hints now resolve.

One more was caught by writing the test rather than running it:
`BuildUnavailableError` was being raised with one argument in the new worker
task, which would have produced a `TypeError` instead of a message.

#### Verification

- **Offline:** 1531 tests (up from 1290), including ten new files covering the
  allowlist, the session, the lesson models, validation, the agent, the
  explanation, the Mini Lab lesson, the API and the worker task. `make lint`
  clean; `npm run lint` and `npm run build` clean.
- **Docker:** 74 (11 new), all passing — `tests/docker/test_cassandra_lesson.py`
  runs the whole lesson against a real node and asserts the things only a real
  one can prove: the version is the running server's, state created in step 3 is
  there in step 6, and step 7's error is Cassandra's own. The run includes the
  five `slow` tests that compile Cassandra with `ant jar`.
- **Browser:** `make e2e-cassandra`, twice, on a cold ladder and a warm cache.

### Phase 11 — Hybrid repository detection demo

Detection of several surfaces has worked since Phase 3; `apache/airflow` has
been classified as hybrid, with four surfaces and their evidence, since then.
What did not exist was anything a person sees — and, worse, the product
contradicted its own detection. The workspace offered a *Learn* tab to any
repository with a CLI surface, so Airflow got one and would then have failed:
there is no Airflow rung on the build ladder and the lesson is Cassandra's. The
Investigation screen offered "Open the playground" unconditionally for the same
reason. Both were the specification's prohibition, live in the product.

**The catalog** — `repolab/experiences/catalog.py`

```
surface + manifest  ->  is there an adapter for this kind of surface?
                    ->  is there a build strategy for this repository?
                    ->  is it the surface the ladder would build?
                    ->  (cli) does the built-in lesson apply?
                    ->  runnable, or NOT_RUNNABLE with the specific reason
```

Runnability is not a list of supported repositories kept by hand. It is asked of
the code that would actually refuse: `candidates_for` from the build ladder, and
`teaches_cassandra` from the lesson. A surface therefore cannot be called
runnable on this screen and decline to run when opened, and a new rung on the
ladder makes its experience available without this file being edited.

The sentence the phase requires is one constant, `NOT_RUNNABLE`, used by the API
and asserted verbatim in the tests so it cannot soften into something vaguer. It
is always followed by the specific reason: *no build strategy for this
repository*, *no adapter for this kind of surface*, *no lesson for this command
line*, or *not the surface RepoLab would build*.

Each experience carries the icon from CLAUDE.md's own vocabulary (`🌐 >_ 🔌 📓
⚡ ⌨`), the surface's confidence, the detection reason naming the signals
responsible, and the cited files — passed through from the manifest unedited. A
surface below the hybrid threshold of 0.60 is flagged `weak` rather than hidden:
Airflow's notebook signal is a handful of `.ipynb` files and some `__init__.py`
packaging, and a learner should see that rather than have RepoLab decide for them.

**The screen** — `web/app/r/[jobId]/experiences/page.tsx`,
`docs/design/08-experiences.md`

One card per surface, runnable first. A runnable card says which adapter runs it
and offers a button; an unsupported card shows the sentence and **has no link,
no button and no "coming soon"**. Selecting any card — supported or not — expands
its reason and its evidence, because the evidence is worth reading either way.

**The two contradictions, fixed**

- The tab list gates *Playground* and *Learn* on the catalog reporting that
  surface runnable, not on the surface existing. A surface-gated tab appears only
  once the catalog has said so, rather than appearing and being withdrawn.
- The Investigation screen's next-step buttons offer a runnable experience only
  when the catalog names one, and otherwise point at the experiences screen.

`GET /jobs/{job_id}/experiences` serves the catalog beside the existing
`/manifest` and `/architecture`, reusing their readiness guard. It reads the
stored manifest and asks deterministic questions, so nothing slow happens in the
handler.

#### The demonstration failed on a fresh ingestion, and why

The four surfaces above had been detected since Phase 3 and were in the stored
manifest. Re-importing Airflow for this phase's browser walkthrough produced
**two**: `cli` and `notebook`, with `frameworks: []`.

The cause, from the ingestion record rather than from reasoning: 14,523 files,
`max_total_read_bytes` hit, 362 dependency manifests found and **none read**.
`detect_dependencies` walked the tree in filesystem order and read each manifest
through the shared budget, which the general scan had already spent — so the
evidence for the web UI and the HTTP API was never collected. Airflow's own
growth was enough to turn a four-surface repository into a two-surface one.

Two changes, both following reasoning the codebase already contained:

1. **A reservation.** `IngestLimits.manifest_reserve_bytes` (2 MB of the 32 MB
   total) may be drawn on only by manifest reads. A manifest is the only evidence
   of a framework when a bounded scan never reaches the application module —
   which is why `FRAMEWORK_SURFACES` exists — so it must not be the read that
   loses to a large source tree. It is carved out of the total, never added to
   it: `max_total_read_bytes` is still a true upper bound, which Phase 3 fixed
   once already.
2. **Prioritised order.** `detect_dependencies` now iterates `prioritized(...)`,
   the ranking Phase 3 added for the surfaces and entry-point detectors and did
   not apply here, so the reservation is spent on the root and `airflow-core`
   manifests before 200-odd provider packages.

Measured after the change, on current Airflow HEAD (`1c3f78e1d57f`, 14,523
files): twelve frameworks detected, four surfaces, 31,999,808 bytes read against
a 32,000,000 budget.

#### Observed browser walkthrough

`make e2e-airflow`, 2026-10-04, against `apache/airflow` at `1c3f78e1d57f`.
No script invoked.

| check | observed |
|---|---|
| import + investigation | ~75s, no build (a clone and a bounded read) |
| surfaces | `>_ CLI` 0.95 (primary), `🌐 Web` 0.70, `🔌 API` 0.45, `📓 Code / Notebook` 0.35 |
| hybrid | badge shown, from the classification's own flag |
| evidence | shown for all four on selection, each naming its signals and citing real files |
| runnable here | **0 of 4**, each stating the exact sentence |
| tabs | neither *Learn* nor *Playground* offered |
| investigation screen | "All 4 experiences", no runnable experience offered |

The awkward case is the real one: Airflow's **primary** surface is a command line
RepoLab cannot build, so the most prominent card is a detected-only one. The
screen is designed for that rather than around it.

#### Verification

- **Offline:** 1570 tests (up from 1531). New: the catalog's honesty properties
  (`tests/test_experience_catalog.py`), the endpoint (`tests/test_api_experiences.py`),
  and the manifest reservation (`tests/test_ingest_safety.py`). The Airflow
  fixture in `tests/support/manifests.py` is recorded from a real ingestion, so
  it is a record rather than an invention. `make lint`, `npm run lint` and
  `npm run build` clean.
- **Browser:** `make e2e` — 4 passed in 12.3 minutes: P3's hybrid detection
  (1.6m), P1's Cassandra lesson (2.5m), P0's Spark experiment (8.0m) and its
  approval gate. The tab gating changed in this phase, so P0 and P1 were the
  regression that mattered.

### Phase 12 — Analytics + KPI dashboard

Analytics had been written since Phase 4 and never read. The database held five
days of real history from building RepoLab — 489 product events, 19 agent runs,
356 tool calls, 13 experiments — and nothing could see it.

The arithmetic was the easy part. What took the work was that several of the
metrics the phase asks for **have no honest source**, and three of those traps
were visible in the data before a line was written:

- **Only successful builds are cached**, so `builds` was five rows, all
  `ready=True`. A build success rate computed from that table reads 100% however
  many candidates failed.
- **`CallRecord` was built on every model call and only logged**, so model
  latency, tokens and cost had no queryable source at all.
- **Challenges do not exist** in RepoLab, so a challenge success rate has
  neither numerator nor denominator.

**The metric model** — `repolab/analytics/metrics.py`

Every metric carries a status, the definition it was computed by, its source, and
how many observations it rests on:

```
measured           computed from recorded data
no_data            instrumented, nothing recorded yet
not_instrumented   nothing in RepoLab records this
north_star         a long-term outcome, deliberately never computed
```

**A metric that is not `measured` cannot carry a value**, enforced by a
validator rather than by discipline — the same shape as `EvidenceBundle`
refusing to hold a measurement while simulated. On a dashboard, `0%` and *we do
not record this* look identical and only one of them is true.

`samples` is on every metric for the same reason. A median over one observation
and a median over a hundred look the same otherwise, and on a five-day
development dataset several genuinely are n=1.

**Pairing, not subtracting.** Time to Repo Ready is computed per *job*;
time to first run and first experiment are computed from the **preceding**
`repo_ready` for that repository. The naive version — earliest submission to
latest ready — produced a five-day "time to repo ready" while this module was
being written, and measuring from a repository's first ever ready produced a
four-day "time to first experiment". Both were arithmetically correct and
worthless. A test now asserts the pairing directly.

**Model-call telemetry** — `repolab/db/models.py`, migration `cfde6d1ca94a`,
`repolab/analytics/model_telemetry.py`

One new table, and an optional `telemetry` sink on `ModelRouter` called beside
the existing log line. A router built without one behaves exactly as before,
which is why no existing test changed. Wired where a database is already at
hand: the three worker task modules and the API's lesson router.

**No prompt or output text is stored.** `CallRecord` carries none and the table
has no column one could go in — asserted structurally by a test, including that
there is no free-form blob to hide one in.

**The events that were missing** — all from CLAUDE.md's own list:
`lesson_started`, `lesson_completed`, `hint_shown`, `command_executed`,
`build_started`, `build_failed`, `build_recovered`. Each is one line at a call
site that already knew the fact. `hint_shown` is named for what happens: a
learner does not ask RepoLab for a hint, it arrives with a failed check, and
calling it `hint_requested` would describe an interaction that does not exist.

**The dashboard** — `api/routes/metrics.py`, `web/app/metrics/page.tsx`,
`docs/design/09-metrics.md`

Product-wide, two clearly separated sections — *Developer learning activity* and
*RepoLab operational & agent performance* — then the north star in its own
block. Each metric shows its value, unit and sample count, or its status and the
reason in place of a number, with its definition and source underneath.

**Provenance is stated first**, before any figure: how many events, jobs and
repositories over how many days, and that this is development traffic from
building RepoLab rather than usage by learners. It changes how everything below
should be read, so it is not a footnote.

**The north star** — *Time to First Meaningful Contribution* — is named,
defined, and never given a number. The screen states that it requires
longitudinal data from real teams that RepoLab does not have, and the e2e spec
asserts that block contains no percentage and no word like "improved".

#### Observed dashboard

`GET /metrics`, 2026-10-05, after a real investigation and a real ladder run to
populate the newly instrumented metrics. **38 of 45 metrics measured**, both
sections populated:

| learning | | operational | |
|---|---|---|---|
| Repositories analyzed | 3 | Agent runs | 20 |
| Investigation success rate | 90.9% (n=22) | Task success rate | 45% (n=20) |
| Time to Repo Ready (median) | 72.6s (n=20) | Average agent steps | 19 |
| Build candidates attempted | 4 | Average steps when it finished | 12.9 |
| Build success rate | 100% (n=4) | Agent recovery rate | 38.9% (n=18) |
| Lessons started / completed | 5 / 3 | Tool calls | 380 |
| Lesson completion rate | 60% (n=5) | Tool success rate | 73.4% (n=380) |
| Experiments proposed / approved | 13 / 7 | Model calls | 24 |
| Experiment completion rate | 100% (n=7) | Model latency (median / p90) | 2242ms / 3015ms |
| Variant runs measured | 126 | Prompt / completion tokens | 15,667 / 7,013 |

Five metrics state their status instead of a number: build recovery rate (no
candidate has failed since the events existed), challenge success rate (not
instrumented), and the three cost figures (`MODEL_PRICING` unset, so no call has
a price and RepoLab does not guess one).

Every figure above was checked against an independent SQL query — tool success
rate, agent recovery rate, model latency median, the paired repo-ready median
and the lesson counts all matched.

#### Two defects this phase found by running it

1. **A `succeeds` check passed for any statement the server accepted.** While
   deliberately failing a step to exercise the hint path, `DESCRIBE TABLES`
   satisfied a step that asked for `CREATE KEYSPACE`: `check_step` compared the
   *output* with the expectation and never compared the *command* with what the
   step asked for. A learner could have been told they created a keyspace for
   running `SELECT 1`. The checker now requires the leading verb to match, and
   reports a mismatch as **inconclusive** rather than failed, so a learner who
   phrased the same intent differently is judged by Nano rather than rejected.
   CQL's own `DESC` abbreviation is not treated as a mismatch.
2. **Playwright matches accessible names as substrings**, so asserting the group
   heading "Agent performance" also matched the section heading "RepoLab
   operational & agent performance" — two matches and a strict-mode failure in
   the new spec. Fixed with `exact: true`, recorded because it is the second
   selector-ambiguity failure in this repository's browser specs.

And one known issue closed: **Nano's fuzzy step check has now run against the
real model.** The mismatch above made a verdict inconclusive, Nano adjudicated
it, and its answer — *"the output shows existing keyspaces but does not indicate
that the 'demo' keyspace was created"* — was correct, labelled as the model's,
and recorded as `hint_shown`.

#### Verification

- **Offline:** 1615 tests (up from 1570). New: the metric model's refusals and
  its arithmetic (`tests/test_analytics_metrics.py`), the endpoint
  (`tests/test_api_metrics.py`), the telemetry sink including that no column
  could hold a prompt (`tests/test_model_telemetry.py`), the ladder's build
  events (`tests/test_build_ladder.py`), and the verb-match rule
  (`tests/test_lesson_validation.py`). `make lint`, `npm run lint` and
  `npm run build` clean.
- **Migration:** `cfde6d1ca94a` applied; 24 model calls round-tripped through it
  during the verification run.
- **Browser:** `make e2e` — 5 passed in 10.3 minutes: the dashboard (0.8s), P3's
  hybrid detection (1.4m), P1's Cassandra lesson (1.8m), P0's Spark experiment
  (7.0m) and its approval gate. This phase touched the model router, the build
  ladder and the lesson validator, so the other four were the regression that
  mattered.

### Phase 13 — Demo hardening

No features. Twelve phases of building, then one of making it survive being run
repeatedly in front of someone on a machine having a bad day.

#### What the audit found before anything was written

Two things were already true and are now kept true by tests:

- **No credential leaks through a provider.** A deliberately wrong key produces
  `AuthenticationError: Error code: 401 - {'detail': "Couldn't authenticate…"}`
  from Nebius and `InvalidAPIKeyError: Unauthorized` from Tavily. Neither echoes
  the key, and `SecretStr` masks it in `repr`, `model_dump` and `model_dump_json`.
- **No stack traces in the UI.** There never were.

And one thing was not: **`.env.example` contained a live Nebius API key, a live
Tavily key and a real project ID.** `.env` is gitignored; the *template* — the
file that gets copied, pasted into an issue, or pushed — held the real values.
Found by the new secrets suite on its first run, replaced with placeholders, and
now guarded by a test that reads whatever is configured in `.env` and asserts it
appears nowhere in the template. **The exposed keys should be rotated**: they sat
in a file whose entire purpose is to be shared.

#### Failures a person can act on

`repolab/failures.py` — `explain(exc)` maps every failure the demo can hit onto a
headline, why, and a next step, keeping the exception's own words as
`technical`. Before it, every worker failure reached the browser as
`f"{type(exc).__name__}: {exc}"`:

```
  was:  SandboxError: docker run failed: Cannot connect to the Docker daemon at
        unix:///var/run/docker.sock
  now:  The execution environment is not available.
        RepoLab runs repository code in a container and could not start or reach one.
        → Check that Docker is running, then try again.
```

Applied at the five worker tasks, the experiment agent, the terminal session and
the API. `ErrorNote` grew a next step and a `<details>` disclosure, so the
technical text is available without being what anyone reads first. An
unrecognised exception gets a generic headline and **no diagnosis** — a test
asserts it says "will not guess" rather than inventing a cause.

#### Three defects, each found by provoking the failure rather than reasoning

1. **One failed Spark variant aborted the whole experiment.**
   `VariantExecutor._run_variant` called `run_once` with no exception handling,
   on the demo's longest path. A failed repeat is now recorded and the rest
   continue; the statistics disclose that they rest on fewer runs, and the
   explanation is told so too. Below two surviving *values* the experiment is
   abandoned — a comparison needs something to compare.
2. **Docker being unreachable was reported as the repository's job failing.**
   Provoked with `DOCKER_HOST=unix:///nonexistent/docker.sock`: the Spark task
   raised `SparkRunFailed` when the image was unavailable, so a user was told
   *"The repository's own job failed when it ran"* about a job that never ran.
   Now an environment failure, which is what it is.
3. **"No build strategy" was reported as "every rung of the ladder failed."**
   Provoked by asking RepoLab to build `pallets/click`: the worker wrapped
   `NoCandidatesError` in `BuildUnavailableError`, and the user was told four
   rungs had been tried when none had. The wrap is gone, and `explain` now walks
   `__cause__` so a wrapper can never hide a failure it could have explained.

A fourth was found in the browser: the SSE stream's own terminal frame is also a
`failed` event, carrying no explanation, so taking the last event of that type
found the frame and lost the message. The browser now takes the last failure
that actually carries a headline.

#### Two more reliability fixes

- **A dead worker no longer leaves a job running forever.**
  `worker/main.py` resolves abandoned jobs on startup — `running` with no event
  for ten minutes — with the sentence *"The job stopped because RepoLab's worker
  restarted. Start it again."* On the first real run it resolved three jobs that
  had been stuck since earlier phases.
- **The stream says when it is reconnecting.** `useEventStream` reported `open`
  while `EventSource` was re-establishing, so the badge claimed *live* with the
  backend down. It now reads `reconnecting`.

#### The ten failure modes

`tests/test_failure_modes.py` — 33 tests, one class per mode, each asserting what
the *user* is told rather than what the code does. Every message must read as
prose: a shared helper rejects `Traceback`, `File "`, `raise `, anything starting
lowercase, and anything empty.

| mode | covered by |
|---|---|
| model timeout | a timing-out fake; the investigation still produces a plan |
| malformed model JSON | `{` then prose; the deterministic sweep takes over |
| sandbox failure | `FakeSandboxProvider(fail_on_create=…)` |
| build failure | every rung failing its smoke test, and a lower rung serving |
| Spark job failure | the message points at the application's own output |
| Tavily unavailable | a research fake that raises; the investigation completes |
| experiment variant failure | one value failing; the others still run |
| worker restart | an abandoned job resolved, a live one left alone |
| SSE reconnect | `tests/test_api_jobs.py` — `Last-Event-ID`, no gap, no duplicate |
| browser refresh | the same, plus `web/e2e/p0.spec.ts` reloading mid-run |

#### Secrets, verified

`tests/test_secrets.py` (13 tests) places canary credentials in settings and
asserts they reach no event, job error, failure message, analytics property or
log line — including a model call whose *prompt* contains one. It also scans
every `.py`, `.ts`, `.tsx`, `.md` and `.yml` file for credential-shaped literals.

`scripts/check_secrets.py` + `make check-secrets` scans what RepoLab has already
written — job errors, 3,028 event messages and payloads, analytics properties,
stored lessons, experiments and build records, plus `web/test-results/` — for
any configured credential or credential-shaped token. **Clean across 6,700
rows.** It reports locations, never values.

#### Demo preparation

- `make demo-check` — a read-only preflight: Postgres, Redis, Docker, migrations
  at head, both images, each demo repository with its commit and cached rungs,
  and whether credentials are set. Optional rows never fail it. Exits non-zero
  if anything required is missing.
- `make demo-prepare` — pulls the images, ingests the three repositories,
  resolves the Cassandra ladder into the cache and generates the lesson.
  Idempotent: cold 12–15 minutes, warm a few seconds.
- `docs/DEMO.md` — exact repositories and commits, prerequisites with the
  numbers that matter (6 GB to containers, 15 GB disk), startup, the expected
  preflight output, cache preparation, a timed three-minute script, and a
  recovery table whose every row has actually been provoked.

#### Verification

- **Offline:** 1661 tests (up from 1615), including 33 failure-mode tests and 13
  secrets tests. `make lint` clean; `npm run lint` and `npm run build` clean.
- **Secrets:** `make check-secrets` clean across 6,700 stored rows and the
  browser artifacts.
- **Preflight:** `make demo-check` reports READY — both images present, all three
  repositories ingested, three Cassandra rungs and a Super-written lesson cached.
- **Failures provoked against the running stack**, each read in the browser: an
  unreachable model service (the deterministic sweep took over and said so), an
  unreachable Docker daemon, and a repository with no build strategy. The
  disclosure stays collapsed until clicked, and the page contains no traceback.
- **Repetition:** `make e2e` three times in a row — 5 passed, 5 passed, 5 passed,
  in 551s, 556s and 640s. A demo that passes once is not hardened.

### Phase 14 — Ask RepoLab triages questions; experiments run on the repository's own surface

**The bug:** asking *"What is Cassandra?"* on the Cassandra repository produced a
Spark shuffle-partition experiment. Every question went straight to the Spark
experiment designer, which could only vary `spark.sql.shuffle.partitions`, so
Ultra invented "the Cassandra repository's Spark job" to fit.

**Now:**

- `repolab/questions/triage.py` classifies a question as *conceptual*,
  *repository* or *testable* — deterministic phrasing rules first, Nano
  (`question_triage`) only when they are ambiguous — and a deterministic
  **backend gate** decides whether a testable question can be run here.
- `repolab/questions/answer.py` answers non-testable questions: a read-only
  investigation (top-level README, manifest, question terms in the code, the
  project's own official docs domain), then Super (`question_answer`) answers
  from that evidence. Claims are labelled repository / documented / inferred
  and verified; an invented file or URL is flagged.
- `repolab/experiments/cli/` runs testable questions on a CLI repository:
  Ultra proposes CQL variants, validation applies the terminal allowlist plus
  keyspace isolation (no `USE`, no keyspace DDL, no qualified names except
  reading system tables) and a budget; after approval every run gets a fresh
  keyspace on one node restored from the build's checkpoint. Row counts and
  server error classes are parsed from cqlsh's own output by Python; a node
  that dies fails the experiment rather than recording "container not running"
  as results.
- `ExperimentLifecycle` is shared by both experiment kinds, so there is still
  exactly one approval gate. Migration `a1c3e5f7b9d2` adds `kind`, `triage`,
  `answer`, `runs` to `experiments` (additive).
- The Ask page shows the triage, an answer card with evidence, or a CLI
  approval card listing the exact statements; results render per kind.

**Verified live** against `apache/cassandra` @ `95b178a141`: *"What is
cassandra"* → conceptual (rules), answered by Super from `README.asc` and
cassandra.apache.org, every claim verified. *"What happens if I filter on a
column that is not part of the primary key?"* → testable → CLI backend; Ultra
designed four variants (partition key, clustering column, non-key column,
non-key column with an index), validated and stopped at the approval gate.

**Tests:** `tests/test_question_triage.py`, `tests/test_question_answer.py`,
`tests/test_cli_experiment.py`, plus API cases. 1,743 offline tests pass.

**Not yet run end to end in a browser**: approving the CLI experiment starts a
4 GB node. The cached Cassandra builds predate the heap cap in
`repolab/builds/cassandra.py`, so rebuild from the Learn tab before approving.

---

## Observed External Capabilities

Produced by `make check` on 2026-10-04, **with real credentials**. This replaces
the earlier all-`SKIP` table: every external capability has now actually been
exercised.

| Capability | Status | Notes |
|---|---|---|
| Postgres connection | `PASS` | 21ms |
| Redis connection | `PASS` | 20ms |
| Model catalog listing | `PASS` | 25 models available (454ms) |
| Nano model ID resolves | `PASS` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` |
| Super model ID resolves | `PASS` | `nvidia/nemotron-3-super-120b-a12b` |
| Ultra model ID resolves | `PASS` | `nvidia/Nemotron-3-Ultra-550b-a55b` |
| Nano inference | `PASS` | content populated; + reasoning field (375ms) |
| Nano structured JSON | `PASS` | parsed `status="ok"` (380ms) |
| Nano native tool calling | `PASS` | (745ms) [optional] |
| Super inference | `PASS` | content populated; + reasoning field (587ms) |
| Super structured JSON | `PASS` | parsed `status="ok"` (707ms) |
| Super native tool calling | `PASS` | (535ms) [optional] |
| Ultra inference | `PASS` | content populated; + reasoning field (589ms) |
| Ultra structured JSON | `PASS` | parsed `status="ok"` (628ms) |
| Ultra native tool calling | `PASS` | (668ms) [optional] |
| **Sandbox creation** | **`FAIL`** | `ForbiddenError: You do not have permission to perform this action` |
| Sandbox command | `SKIP` | sandbox unavailable |
| Sandbox state preserved | `SKIP` | sandbox unavailable [optional] |
| Sandbox python present | `SKIP` | sandbox unavailable [optional] |
| Tavily official search | `PASS` | 3 results with snippets (2029ms) |

**Phase 0's open question is answered.** Nemotron populates `content` *and* a
separate `reasoning_content` field. The router reads `content` and records only
the reasoning field's *name and size*, so no chain-of-thought is ever persisted.
Native tool calling turns out to be supported on all three tiers — but the
structured-action path is kept, since CLAUDE.md requires not assuming it.

**The sandbox failure is an account entitlement, not a defect.** The credentials
authenticate; this account cannot execute in Nebius Sandboxes. Experiments
therefore run in Docker, which is a real `ExecutionEnvironment` behind the same
protocol — so the code path is unchanged and nothing simulated was substituted.

### Reachability (verified with deliberately invalid credentials)

Running the checker with bogus keys confirms every live code path reaches its
service and fails cleanly, so only authentication remains untested:

| Service | Observed |
|---|---|
| Nebius inference | `401 AuthenticationError: Couldn't authenticate` — endpoint reached |
| Nebius Sandboxes | `ForbiddenError: You do not have permission to perform this action` — ConTree API reached |
| Tavily | `InvalidAPIKeyError: Unauthorized: missing or invalid API key` — endpoint reached |

Each became a `FAIL` row rather than a crash, and the run exited `1`.

---

## Findings

### Dropping every symlink quietly handed builds a different repository

The archive uploaded to a build dropped symlinks, with a reasonable comment:
*"Symlinks could point outside the clone once extracted elsewhere."* True of a
link that escapes; false of one that does not — and Cassandra's build failed on
a missing `.build/git/git-hooks/post-switch`, which the repository ships as a
symlink to a sibling directory.

The failure took a long time to find because it looked like nothing: `ant`
reported `error=2, No such file or directory` from a Java stack trace, the
archive contained the entry, and extraction worked on the host. It was a `head
-4` in my own diagnostic that hid the file, which cost another round.

Links are now kept when they stay inside the archive and dropped when they
escape, resolved textually — because the question is where a link points *once
extracted somewhere else*, not where it points now. Handing a build a quietly
different tree than the repository has is its own kind of unsafe.

### Build concurrency is bounded by memory, not by count

The ladder ran candidates concurrently under a count-based semaphore. Two 4 GB
Cassandra nodes on a 7.7 GB Docker produced the least debuggable failure
available: the official release, healthy and nearly finished, **failed at 48s
with no error of its own** because something else took its memory.

Candidates are now admitted against a memory budget they declare, and started
**cheapest first while still being selected best first**. Those are different
orders for different reasons — with memory to spare the start order is
irrelevant, and when the budget forces a queue it decides how long a learner
waits before they can type anything, which is the whole point of the ladder.

### A server cannot be started from a build step

Cassandra started from a build step exited silently every time: no log, no
error, no process. A `docker exec` kills what it started when it returns, so
nothing was listening when the smoke test ran.

A plan now declares a `service_command`, and the resolver commits the build and
starts a **fresh container with the server as its main process**. That also
fixed a second bug nobody had noticed: the partial build's reduced configuration
was being written *after* the server had already read it, so the "partial" node
was the full one with a different label.

The checkpoint therefore happens before the smoke test for a service build and
after it for every other kind — the one case is structurally forced, and
readiness is computed from the smoke result either way, so neither can serve
something that did not pass.

### Two rungs of one ladder committed to the same name

Every candidate checkpointed to `repolab/<repo>:<commit>`, so the official
release and the partial build landed on the same image tag and the last one to
finish silently replaced the other. Observed in the first real ladder run, where
both reported "ready at checkpoint" with identical references.

The kind is part of the tag now. A cache keyed by commit alone had the same
shape of bug, which is why the `builds` constraint became
`(repo_url, commit_sha, kind)`.

### An upgrade offer replaced a better one

The real ladder offered `high`, then `partial` a few seconds later, and the
learner who accepted got **`partial`** — a downgrade from what they had already
been offered. `propose` compared each new artifact against the environment in
*use*, where anything beats `conceptual`.

Offers are now measured against the best the session knows about, in use or
pending. A worse artifact arriving later is not an offer at all.

### A login shell silently replaced the image's PATH

`sh -lc` re-reads `/etc/profile` and rebuilds `PATH` from the system default,
discarding whatever an image put its own tools in. On `cassandra:5.0` that drops
`/opt/cassandra/bin`, and the failure surfaced inside the image's *own*
entrypoint script as `exec: cassandra: not found`.

The new provider uses `sh -c`. The Spark path keeps its login shell, which it
was built and tested against — the point was to stop sharing an assumption, not
to change one that works.

### Ubuntu moved past the Python cqlsh supports

The source build compiled Cassandra perfectly and then had no usable client to
prove it: `cqlsh` requires Python 3.6–3.13 and refuses to start outside that
range, and `eclipse-temurin:17-jdk` is now Ubuntu 26.04 with Python 3.14.

Pinned to the jammy variant, which ships Python 3.10. Worth recording because
the failure arrived as *"cassandra did not accept a connection within 300s"* —
a timeout that said nothing about a Python version check, several layers away.

### Building Cassandra needs its submodules, and ingestion must not fetch them

`ant jar` invokes a gradle build in `modules/accord`, a git submodule that a
shallow clone leaves empty. Cloning submodules by default was the wrong fix:
ingestion only reads files, and fetching submodules means downloading code
nothing will read and running more of a repository's own configuration.

The **plan** declares `needs_submodules`, and whoever clones reads it. The
knowledge belongs where it is known.

### The UI reported a model failure as "no model configured"

The results screen had two paths for a missing explanation, and only one
message. When Ultra failed, the browser said *"No model is configured, so
RepoLab reports the measurements without a narrative"* — which was false, and
false in the worst direction: it described a failure as a deliberate choice, on
the one screen whose entire purpose is not misdescribing things.

The experiment now records **why** the explanation is missing, and the screen
says which situation it is in. A failure shows the error; an unconfigured model
shows the original message, which is true in that case.

### A token budget tuned to an observation will fail eventually

Phase 7 raised the explanation's `max_tokens` from 2048 to 4096 against a
measured 2,848. It failed again here, on a 21-run experiment: 3,142 tokens of
reasoning and still no JSON.

The useful finding is in the spread. Three runs over the *same* statistics used
**1,030, 2,848 and more than 3,142** tokens. Reasoning length varies run to run,
not only with payload size, so a cap set just above a measured value is a cap
that fails on some future run — and the failure arrives *after* minutes of real
Spark, which is the worst moment to discover it.

The budget is now 8,192: well clear of anything observed rather than just above
it. Generation is billed by tokens produced, so an unused cap costs nothing,
which makes "tune it tightly" a false economy.

### A number on screen with no source behind it

The playground showed **"Distinct keys: 7"**, which was correct and entirely
unevidenced — a literal in the React component, because `DatasetSpec` exposes
the count as a Python property and pydantic does not serialise those.

It would have survived review by being right. That is precisely the problem: a
figure whose correctness depends on nobody changing the generator is not a
measurement. The API now computes and serves it, and a test asserts the served
value matches a real `generate_dataset` call.

### Resetting state in an effect, in a framework that now says not to

The SSE hook cleared its accumulated events in a `useEffect` when the URL
changed. React 19's lint rules reject it, and the reason is not stylistic: it
causes a cascading render, and one frame renders the *previous* stream's events
under the new URL.

Rewritten to adjust state during render, which is the documented pattern. The
dedup set went too — it lived in a ref, and refs cannot be written during
render either. It was replaced by a monotonic sequence check, which is simpler
and relies on a guarantee the backend already makes: per-job sequences are
contiguous and ascending, and a reconnect resumes *after* the last id seen.

### Charting everything buried the finding

The first results screen drew a chart per metric — twelve of them, including two
that were zero at every variant and two that were identical to each other, above
a 140-row statistics table.

Nothing was wrong with any of it, and it was useless: the one chart that answers
the question was twelfth. Four metrics are charted now, and the table shows those
by default with every metric one click away. Nothing is hidden; it is just no
longer all given equal weight.

### Every background job was silently dying on macOS

The API and worker path had never actually been run end to end. It does not work
on macOS, and did not for Phase 4 either: RQ forks a work-horse per job, every
RepoLab job then forks again through `git clone`, and macOS refuses the second
fork once Objective-C state exists —

```
objc[34492]: +[NSCharacterSet initialize] may have been in progress in another
thread when fork() was called. Crashing instead.
Work-horse terminated unexpectedly; waitpid returned 6 (signal 6)
```

The job vanished. No traceback, no error on the job row, nothing in the event
stream — just a job stuck in `running` forever. The experiment sat in `proposed`
through forty polls with no indication anything was wrong. Phase 4's recorded
end-to-end runs must have gone through a shell that happened to export the
variable.

`OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` fixes it, but **setting it from Python
does not**: the Objective-C runtime reads the variable when it initialises, which
is before any module code runs. The first attempt — `os.environ.setdefault` at
the top of `worker/main.py` — looked right and changed nothing, which is worse
than not trying. The worker now detects the missing variable and **re-execs
itself** with it set, so `python -m worker.main` works however it is launched;
the Makefile also exports it so the common path needs no re-exec.

The wider lesson is about the testing strategy, not macOS. The API layer is
thoroughly tested with a faked queue, and the agent is thoroughly tested with
fakes — and between those two sat a seam that had never once been crossed.

### Persisting after each run persisted nothing

The run task saves the experiment after every variant, so that a crash
mid-experiment leaves the measurements already taken. Watching a real fifteen-run
experiment through the API showed `variant_runs: 0` for its entire duration,
jumping to 15 at the end.

The callback was being handed each run, but the `Experiment` only received its
`runs` once `executor.execute` returned — so every mid-experiment save wrote an
experiment with no runs at all. The feature existed, ran, logged nothing unusual,
and did the opposite of its purpose. The agent now appends each run to the
experiment before invoking the caller's callback, and a test asserts the runs are
visible one at a time.

### A reasoning model can exhaust its output budget before emitting any JSON

The first full experiment run finished all 12 Spark runs and then produced **no
explanation at all**. Both router attempts failed schema validation, and the raw
response showed why: Nemotron Ultra writes its reasoning into `content` before
the JSON, and with a six-variant payload the reasoning alone consumed the entire
2048-token budget. The response was 5,836 characters of analysis that stopped
mid-sentence.

Two things were wrong. The payload was 62 KB because it carried every variant's
raw sample array — which also invited exactly the arithmetic the model must not
do. And the output budget was set by guesswork.

Both fixed, and measured rather than guessed: the model now receives a
`StatisticsSummary` (one row per metric per value, with the median, relative
difference, spread and standard deviation, but **not the samples**), which cut
the prompt to 23 KB; and `EXPLANATION_MAX_TOKENS` is 4096, against an observed
2,848 needed. The grounding check still runs against the *full* statistics, so
what the model may claim is no looser than before — it simply sees less.

The general lesson: with a reasoning model that uses `content` for its thinking,
`max_tokens` is not an output-size setting, it is a thinking-plus-output budget,
and under-setting it fails *silently at the last step* of expensive work.

### The live model cited a statistics field as if it were a metric

The first live explanation run came back `grounded=False`. The model had attached
`support: "baseline_value"` to a `measured` claim — a real field of the
statistics, but not a metric, so the check rejected it.

The temptation was to loosen the check to accept any statistics field. That would
have been the wrong fix: the model was guessing because the valid metric names
were buried inside nested comparison objects, while the prompt asked for "the
exact metric name". The payload now carries a top-level `metrics` list naming
exactly what a `measured` claim may cite. The next run cited metrics correctly
and all six checkable claims passed.

### Validation that always adds its own metrics can never reject a proposal

A test asserted that a proposal where *nothing* requested is measurable should be
refused. It failed: the plan was accepted. `_reconcile_metrics` added
`REQUIRED_METRICS` before the emptiness check, so the "no measurable metrics"
rejection was dead code and a confused proposal was silently replaced by
RepoLab's own defaults.

Measurability is now checked *before* the always-measured set is added. A model
that ignores the metric list it was given gets told so, rather than having a
plausible-looking plan built for it.

### A no-credentials fallback must respect the budget it will be validated against

The deterministic fallback proposed a fixed four-value sweep at two repeats. Run
under a six-run budget, validation correctly refused it — which meant the
no-credentials path produced *no experiment at all*, the one case where the
fallback is the only option. It now trims its sweep to fit, keeping the baseline
and the widest span.

### Equivalent state is a claim that has to be checked, not arranged

Giving each variant a fresh container from the same image with the same
deterministically generated dataset makes the runs equivalent. It does not make
them *verifiably* equivalent — a changed seed, a different image tag, or a
dataset regenerated with different parameters would all produce a comparison that
measures the inputs rather than the variable, and would look entirely normal.

`verify_equivalent_state` therefore re-reads the dataset hash and image off the
completed runs and **raises** if they differ. Annotating the result instead was
the alternative, and it is worse: a number nobody can trust is more dangerous
than no number.

### Hashing seven keys into eight buckets leaves four of them empty

A Docker test asserted that eight shuffle partitions would leave at most two
empty. The real measurement was four. With seven distinct keys, hash
partitioning into eight buckets collides, so only four buckets receive data.

The assertion was a guess about hash distribution dressed up as a property. What
*is* derivable is that at most seven partitions can ever be non-empty, whatever
the count is set to — so that is what the test asserts now, alongside the
comparative claim that fewer partitions waste a far smaller fraction. The lesson
is narrow but general: assert what follows from the inputs, not what the first
run happened to produce.

### Two concurrent Spark variants measure contention, not the variable

Docker here has 8 GiB and 8 CPUs; each variant takes 4 GiB and `local[4]`. Two at
once therefore saturate every core, and a "duration" from such a run is a
measurement of the scheduler, not of the partition count.

CLAUDE.md says to run independent variants in parallel, and the capability
exists — but sequential is the default, and `concurrency > 1` tags every run it
produces `timing_contended`, which flows into the reproducibility record and into
the notes the explanation is given. The parallelism is available for structural
metrics, which are unaffected; it just cannot quietly be used for timings.

### A test named "without credentials" tested nothing once credentials existed

`test_requesting_nebius_without_credentials_fails_clearly` had passed since
Phase 5. It began failing the moment a `.env` file appeared — and it was right to:
it had never removed the credentials, it had only ever run on a machine that
lacked them. It took `monkeypatch` as a parameter and never used it.

It now runs the command from an empty directory with the repository on
`PYTHONPATH` and the Nebius variables stripped from the environment, because
settings read `.env` from the working directory and clearing the variables alone
is not enough. A test whose premise is an absence has to create that absence.

### The explanation guard has to be a detector, not a censor

Checking that a claim's numbers appear in the measurements cannot be exact. A
model writing "1.1 seconds" for a median of 1,100 ms has fabricated nothing, and
"2,000,000 bytes" and "1.9 MiB" are the same measurement. So the matcher accepts
exact equality, a 2% rounding tolerance, and the common unit rescalings, and
ignores small integers that are almost always prose.

That makes it a detector with a real false-negative rate rather than a proof. It
is paired with a stricter structural check — a `measured` claim must *name* a
metric the experiment measured — and both findings are reported on the result
rather than used to suppress the explanation, so a reader sees what could not be
corroborated instead of an edited version of what the model said.

### The installed Sandbox SDK differs from its published documentation

Verified by reading `contree-sdk` 0.3.6 source, not by assumption. The docs at
`docs.tokenfactory.nebius.com` are out of date in four ways:

1. **Construction.** Docs show `Contree(ContreeAsyncClient(api_key))`. The
   installed signature is `Contree(config=None, *, base_url=None, token=None)`
   and needs no separate HTTP client.
2. **A project ID is required.** `IAMAuth` sends `Authorization: Bearer <token>`
   *and* a `Project: <project_id>` header, so Sandboxes need
   **`NEBIUS_PROJECT_ID`** in addition to the API key. This is not mentioned in
   CLAUDE.md and has been added to `.env.example`.
3. **Per-call timeouts exist.** `run()` accepts `timeout`, contrary to the docs,
   plus `env`, `cwd`, `files`, `stdin` and `tag`. Config-level
   `operation_timeout` / `operation_run_timeout` / `operation_import_timeout`
   also exist, as documented.
4. **`contree-client` is not needed.** The documented import
   (`from contree_client.httpx import ContreeAsyncClient`) belongs to a separate
   package that `contree-sdk` does not depend on — and the installed SDK does
   not require it at all, so it is not a dependency of RepoLab.

### The sandbox runtime reports its own measurements

`ContreeResult` carries `elapsed_time` and `cost` alongside `stdout`, `stderr`
and `exit_code`. These are real, instrument-provided measurements and are
surfaced through `CommandResult` for later use by experiments and analytics.

### Checkpointing model

There is no `checkpoint()` / `fork()` method. `run(..., disposable=False)`
preserves the resulting filesystem state; calling `run()` on that state again
branches from it, so experiment variants can start from byte-identical state.
`tag` gives a checkpoint a durable name. This fits the P0 experiment design well.

### The event log's field names are not the REST API's field names

Spark's monitoring documentation describes `executorRunTime` and
`shuffleReadMetrics.remoteBytesRead`. The event log, written by
`JsonProtocol.scala`, uses capitalised space-separated keys: `"Executor Run
Time"`, `"Shuffle Read Metrics"` → `"Remote Bytes Read"`. Parsing against the
documented names would have returned zero for every metric while appearing to
work. The keys were therefore read from the Spark 3.5.1 source, and the parser
tests assert against a log captured from a real run so a hand-written fixture
could not hide the mistake. The writer also appends `.inprogress` until the
application ends, which a naive reader would miss.

### Filtering empty partitions hid the finding

The first version of the distribution counted only tasks that read records, so it
reported "7 partitions, 0 empty" when the truth was **200 partitions, 193 of them
empty**. The empty partitions *are* the symptom of a partition count chosen
without regard to the data — exactly what the demo question asks about. The
distribution now counts every task in the shuffle stage, with the median taken over
non-empty partitions only, since a median of mostly zeroes would make the skew
ratio meaningless.

### A plain `groupBy` is combined map-side, so skew never reaches the shuffle

The first real run moved only **7 records** across the shuffle: Spark aggregated
partially on the map side, so one pre-aggregated row per key crossed it and the
72% skew was invisible. The driver now repartitions by the skewed key first, which
moves the real rows — 400,000 shuffle records, a 144,090-record partition, and a
15.4x skew ratio. The repartition is disclosed as RepoLab's addition.

### Timings are not reproducible, and the output says so

Two real runs produced 8,192 ms and 6,468 ms total runtime while agreeing exactly
on every count and byte total. Presenting both kinds of figure together as
"deterministic" would misrepresent what they support, so measurements carry a
`STRUCTURAL` or `TIMING` label, the summary prints them in separate sections, and
the reproducibility check compares only the structural fingerprint.

### Nebius Sandboxes have no deletion API, so `terminate` cannot delete

Re-reading the documentation and grepping the installed SDK for
`delete`/`remove`/`destroy`/`terminate` both come up empty. `terminate` therefore
untags the image and drops the local reference, leaving it unreferenced for the
documented 180-day retention to reclaim. `SandboxCapabilities.supports_deletion`
is `False` so callers discover this rather than assuming a guarantee that does not
exist. `supports_network` is `None` for the same reason: egress is undocumented and,
without credentials, untestable.

### `ContreeSession` mutates in place, so it cannot be forked

`_copy_self` returns `self`, meaning successive `run()` calls accumulate on one
object. That rules a session out for builds, because every experiment variant must
start from the *same* checkpoint. Builds use the immutable image-chaining style
instead, and the fake models the same semantics so a test asserting branch
independence actually proves something: two restores of one checkpoint produce
environments that cannot affect each other, and a checkpoint is unaffected by later
mutation of the live state.

### An empty container is falsy, so `cache or default` discarded it

`BuildResolver.__init__` did `self._cache = cache or NullBuildCache()`. Because
`InMemoryBuildCache` defines `__len__`, a *fresh* cache is falsy, so every resolver
silently ran with caching disabled — builds appeared to work while nothing was ever
stored. Now an explicit `is not None` check, applied to the emitter and limits in
`AgentRuntime` too. Same family as the Phase 2 bug where `models or [...]` treated
an explicitly empty list as unset.

### A tiny smoke test needs more than a zero exit code

A Spark job that silently does nothing still exits 0, so the smoke test checks for
a marker printed by the job itself. A test pins this: a zero exit without the
marker fails the build.

### A database serial is the wrong source for an event sequence

`BIGSERIAL` assigns a value at insert but publishes it at commit, so a reader
polling `seq > 41` can permanently skip an event that was assigned 40 and
committed later. Because exactly one worker owns a job, the emitter keeps a
per-job counter instead, and `UNIQUE (job_id, seq)` makes a duplicate impossible
to store. All four observed jobs produced contiguous sequences, and the
refresh-resume test asserts zero gaps and zero duplicates rather than assuming
them.

### An indefinite stream needs a bounded mode to be testable

`GET /jobs/{id}/events` correctly holds the connection open while a job runs,
which meant a non-streaming test client blocked forever. Rather than contort the
tests, the endpoint gained `follow=false`, which drains what has been recorded and
closes -- a genuinely useful mode for a client that wants current history without
holding a connection, and one that makes the replay tests deterministic. The
default remains live streaming.

### Binding a dependency at import time defeats configuration

`api/routes/jobs.py` did `from api.deps import session_factory`, which captures the
function object, so pointing the API at a different database had no effect on the
route. It now calls `deps.session_factory()` through the module, giving a single
indirection point for both configuration and tests.

### Assigning ORM children by foreign key leaves the relationship empty

`save_agent_run` created `AgentStepRecord`s with `run_id=...` and `session.add`,
so `record.steps` stayed empty for an already-loaded run -- the same class of bug
as Phase 2's manifest records. Steps are now assigned through the relationship,
letting `delete-orphan` replace a previous save cleanly.

### Saturated confidence made the primary experience arbitrary

Phase 2 scored a surface by counting signals and adding 0.1 each up to a 0.95
ceiling, so on a large repository every surface saturated. Airflow's primary was
therefore decided by an **alphabetical** tiebreak (`web` beat `cli` and
`data_playground`), and because nothing was ever below the strong threshold,
"ambiguous" was undetectable and the Super gate could not have fired. Scoring now
weights signals by kind and counts *distinct* kinds, which both separates the
candidates (Airflow: 0.95 / 0.7 / 0.45 / 0.35 / 0.35) and makes the gate
meaningful.

### Evidence was cited in arrival order, burying the best reference

Cassandra's CLI surface initially failed to cite `bin/cqlsh`: references were
appended as findings arrived and capped at six, so weaker signals scanned earlier
crowded out the one that mattered most. Evidence is now ranked by the weight of
the signal that produced it, so the strongest reference is always shown. A
regression test pins this.

### "Hybrid" and "strong enough not to ask a model" are different questions

Airflow's web UI scores 0.70 -- genuinely substantial, since it really does ship
one -- but below the 0.80 strong threshold. Using one threshold for both meant
Airflow was not marked hybrid. They are now separate: `HYBRID_CONFIDENCE` 0.60
answers "is this worth offering as its own experience?", `STRONG_CONFIDENCE` 0.80
answers "is this certain enough that a model cannot help?".

### The classifier must not import the ingestion package

`repolab.classify` needed `Finding` and `DetectorRun`, but importing them
executed `repolab/ingest/__init__.py`, which imports the pipeline, which imports
`repolab.classify` -- a cycle that only stayed hidden because pytest happened to
import in a lucky order. The detector types are needed for annotations only, so
they are imported under `TYPE_CHECKING`. The classifier consumes a structure and
now has no runtime dependency on ingestion, which is the correct direction.

### Hardened cloning blocks local paths, which is a tested property

`protocol.file.allow=never` means a repository URL can never make git read the
local filesystem. This is deliberate defence in depth behind `validate_repo_url`,
and it is asserted directly: cloning a local path fails with
`transport 'file' not allowed` unless a caller passes the explicit, test-only
`allow_local_source` flag. Tests that need a real clone use a `file://` URL with
that flag, because git also ignores `--depth` for bare local paths.

### Bounded scanning needs *ordering*, not just a cap

First run against `apache/airflow` detected only a CLI surface despite FastAPI
appearing in its dependencies: the read budget was spent walking 14,404 files in
filesystem order before reaching the application modules. Two fixes followed, and
Airflow now yields four surfaces (web, data_playground, cli, api):

1. `repolab/ingest/priority.py` ranks files by how informative they are likely to
   be (entry-point-shaped names, shallow paths, tests and vendored directories
   demoted), so a capped scan spends its budget on the files that define the
   system.
2. A framework declared in a dependency manifest is itself citable evidence of a
   surface, so a bounded code scan missing the app module no longer hides it.

### The read budget was not a true upper bound

`bytes_read` overshot `max_total_read_bytes` by ~2.7 KB on Airflow, because the
budget was checked before a read without regard to the file's size. Reads are now
refused when the file would cross the limit, so the cap is a genuine bound.

### A keyword collision produced a false claim

Cassandra was initially reported as having a "Spark configuration" concept: its
`partitioner` setting matched a keyword used to find Spark settings. Concept
labelling now requires actual Spark evidence (an import, session, config or
`spark-submit`), and generic settings become "Configuration settings" instead. A
regression test pins this, since an unsupported claim is precisely what RepoLab
exists not to produce.

### Pydantic's JSON Schema is not accepted by strict structured output

Verified locally against pydantic 2.13.5. `model_json_schema()` emits no
`additionalProperties: false` and omits optional fields from `required`, both of
which OpenAI-style `"strict": true` requires. `repolab/llm/schema.py` therefore
hardens the schema recursively, `$defs` included, before sending it. A test
records pydantic's raw output failing those conditions so the reason this module
exists stays documented.

### Strict-schema support per Nemotron model is still unknown

Not verified — no credentials. The router's single retry downgrades
`json_schema` to `json_object` while feeding back the validation error, which
covers both a model that ignores the schema and a serving stack that rejects
strict schemas outright. A first-attempt transport error is also retried for the
same reason. The first credentialed `make smoke` will show which tiers need the
fallback.

### Nemotron model IDs

Not verified — no credentials. Candidate IDs observed in Nebius documentation
are recorded in `.env.example` and marked unverified; casing is inconsistent
across Nebius sources. `make check` validates whatever is configured against the
live `/v1/models` catalog and suggests close matches on a mismatch, so IDs are
never hardcoded.

### Reasoning-field handling

Where reasoning models place their output is **still unknown** and deliberately
not guessed. `repolab/llm/response.py` discovers it at runtime: it reports every
populated field on a message by name, type and size, flags reasoning-looking
fields, and selects the answer from the largest non-reasoning field when
`content` is empty. It records **sizes only, never text**, so private
chain-of-thought cannot reach logs, events, or the UI. The `* inference` probes
fill this in on the first credentialed run.

### Documented service limits

- 50 simultaneous sandbox operations.
- Checkpoint image retention: 180 days.
- **No inbound networking or port exposure is documented.** This is evidence
  that long-running services (Cassandra in P1, Airflow in P3) may need the
  `VMEnvironment` path from CLAUDE.md. Not designed or decided yet.

---

## In Progress

Nothing. Phase 13 is complete and work is stopped pending review.

---

## Next

**Rotate the two credentials that were in `.env.example`.** They are no longer in
the file, but they were, and that file exists to be shared. Nothing in this
repository can undo that.

**P2 — graceful degradation on a repository the ladder was not written for.** The
ladder exists and all four rungs work, but `candidates_for` knows two repository
shapes. Phase 13 made the *refusal* honest — `pallets/click` is told plainly that
RepoLab cannot build it — which is most of what P2 asks for; what remains is
whether a third shape produces a sensible ladder or no candidates at all.

Smaller things this phase leaves open:

1. **The failure explainer is a mapping, not a taxonomy.** A new exception type
   gets the generic sentence until someone adds it. That is the right default and
   it does mean the mapping needs maintaining.
2. **`explain()` does not localise.** Every sentence is English, in the source.
3. **The abandoned-job sweep runs only at worker startup.** A worker that dies
   and is never restarted leaves its jobs listed as running until one is.

Per CLAUDE.md, the next milestone does not begin until this one is approved.

---

## Blocked

- **Nebius Sandbox execution** — `ForbiddenError: You do not have permission to
  perform this action`. The credentials authenticate and the API is reached; this
  **account lacks the sandbox entitlement**, so it is not a code blocker and not
  something this repository can fix. Experiments and builds run in Docker, which
  implements the same `ExecutionEnvironment` protocol, so no code path is
  bypassed and nothing simulated is substituted. If the entitlement is granted,
  `make build URL=… ARGS="--provider nebius"` is the first thing to try.
- **Sandbox network egress** — still undocumented and now untestable on this
  account, so whether `pip install pyspark` works inside a sandbox is unknown.
  `REPOLAB_SPARK_BASE_IMAGE` exists so a prepared image can sidestep it.
- **Spark on Nebius** — blocked by the same entitlement. `SparkDemoRunner` and
  `SparkVariantRunner` both take any `ExecutionEnvironment`, so this is a matter
  of passing `SandboxEnvironment` rather than a rewrite.
- **Super classification tie-break** — implemented, unit-tested against fakes,
  and now *runnable* with credentials, but still unexercised: by design it is
  unreachable for all three test repositories, since each is unambiguous. A
  genuinely ambiguous repository is needed to see it run.
- **`SuperPlanner`** — the model-driven agent planner is implemented and tested
  against fakes, and credentials now exist, but the Phase 7 experiment
  investigation deliberately uses a deterministic question-driven planner
  instead, so `SuperPlanner` has still never planned with a real model.

### Resolved since Phase 6

- ~~All external capability verification~~ — credentials obtained; `make check`
  now reports real results for every row except the sandbox.
- ~~Live model validation~~ — `make smoke` passes. All three tiers return
  validated Pydantic objects; Ultra's two experiment calls are verified against
  the real model in `tests/live/test_experiment_live.py`.
- ~~Model IDs unverified~~ — all three resolve against the live catalog.

---

## Known Issues

- `docs/design/07-learn.md` was written *after* the Learn screen, from what it
  does, rather than before it like the six P0 specs. The rules it records are
  real and enforced; the document is a description, not a brief.
- The sandbox probe uses `busybox:latest`, which has no Python; the
  `python --version` check would be recorded as a `WARN` observation about the
  image rather than a sandbox failure — unreachable on this account, which cannot
  create a sandbox at all.
- Cost estimation is inert until `MODEL_PRICING` is configured; `CallRecord`
  reports `estimated_cost_usd = None` rather than guessing a price.
- The router does not enforce that a `Purpose` matches its recommended tier (for
  example Ultra used for a Nano purpose). The pairing is recorded for analytics;
  a hard guard was judged premature.
- `CallRecord` is returned and logged but is not yet persisted; only ingestion
  has tables so far.
- Detector recall is imperfect on unfamiliar repositories. Cassandra yields no
  frameworks (its build is Ant/Maven with few recognisable packages) and Airflow
  yields no concepts, because concept proposal currently keys off Spark, HTTP
  routing and compose services only. Confidence values and `limits_hit` report
  this rather than hiding it.
- Cassandra and Airflow both pick up weak spurious surfaces (`api` 0.1,
  `notebook` 0.1, `data_playground` 0.35) from incidental ports, `__init__.py`
  packaging and vendored Spark provider code. They are correctly ranked far below
  the primary and each still cites real files, but they are noise rather than
  useful signal.
- Airflow's primary is `cli` rather than `web`. Defensible -- its CLI evidence is
  much stronger -- but a learner might expect the web UI first. The choice is now
  scored and the reason recorded, so it can be revisited with evidence rather than
  by changing a tiebreak.
- `docker-compose` files are parsed by indentation rather than with a YAML
  parser, since RepoLab has no YAML dependency. Service names, images and ports
  are extracted reliably; more exotic compose shapes are not.
- The Spark demo repository pins `pyspark==2.4.0` and Python 3.6 in its
  `Pipfile`. Irrelevant to ingestion, which installs nothing, but the Build Agent
  will have to resolve an old JVM/Python stack — which is the Java-version
  diagnosis path CLAUDE.md describes. It also declares no license, so it is read
  and analysed only.
- Ingestion stores no file contents, only excerpts inside `CodeReference`s, so
  re-reading a repository requires re-cloning it.
- The SSE stream polls Postgres every 250ms, so an event can appear up to that
  late. Chosen deliberately: one code path serves replay and live streaming, which
  is what makes refresh-and-continue gap-free.
- Job retries are recorded (`attempts`) and the emitter resumes the sequence, but
  nothing retries a failed job automatically yet — RQ's retry policy is not
  configured.
- **There is no way to list or return to previously imported repositories.** The
  workspace URL is the only handle, and losing it means importing again. The
  Import screen deliberately shows no "recent repositories" list rather than
  fabricating one, but a real listing endpoint is missing.
- Opening `/r/{jobId}` for a job that is not an *investigation* — an experiment's
  own job id, say — shows a workspace with every tab permanently disabled and no
  explanation of why.
- The browser polls `GET /jobs/{id}` every two seconds alongside the SSE stream,
  because status, elapsed time and stall detection are not carried as events.
  Harmless, but it means two mechanisms where one would do.
- The playground regenerates the dataset on every preview request to keep the
  hash honest. At the default 60,000 rows that is fast; at the 2,000,000-row cap
  it would not be.
- On macOS the worker **re-execs itself** to set
  `OBJC_DISABLE_INITIALIZE_FORK_SAFETY`, because the variable has to be present
  before the process starts. It works and logs that it happened, but a process
  that restarts itself is surprising; the Makefile exports the variable so the
  usual path does not need it.
- Only Spark and Cassandra have build candidates. Airflow classifies correctly
  but `candidates_for` refuses it with `NoCandidatesError`, which is honest but
  means P3 needs its own rungs.
- The fidelity ladder is implemented for Cassandra only. Spark still resolves to
  a source build or nothing, which is what P0 needs and all its tests assert.
- `--cache postgres` will store a *simulated* fake build if asked explicitly. The
  default keeps fakes out of the shared cache, and the label says `[SIMULATED
  build]` when overridden, but the row itself only records `provider=fake`.
- The repository archive is uploaded whole, capped at 64 MiB. Fine for the demo
  repo; Cassandra and Airflow would need chunking or a sparse transfer.
- Build attempts are stored in the `builds` row's jsonb rather than their own
  table. Adequate for one attempt; the retry/diagnosis loop will want a table.
- **Spill is zero** in the observed run, and genuinely so: 200,000 small rows fit
  in memory with a 2 GB driver. It is reported as a real zero rather than omitted,
  but a larger dataset would be needed to observe spill behaviour.
- The Spark run adds a repartition and aggregation the repository does not
  contain, because its job performs no shuffle at all. The additions are disclosed
  in `added_steps`, in the command output, and in the evidence — but any
  measurement from this run describes the repository's transform *plus* RepoLab's
  aggregation, not the repository alone.
- Two stages are named `json at <unknown>:0` because the write happens inside
  generated driver code. Harmless, but less readable than a real source location.
- The event-log fixture is slimmed to the fields the parser reads. Values are
  byte-identical to the real run, but a parser change that needed a dropped field
  would require recapturing it.
- **Only `spark.sql.shuffle.partitions` is on the experiment allowlist.** That is
  deliberate — an allowlist whose failure mode is a missing entry beats a denylist
  whose failure mode is executing something unvetted — but it means the Experiment
  Agent can answer exactly one *class* of question today. A second variable needs
  an entry, bounds, and a way to apply it to a run.
- **The per-run time estimate is a single measured constant** (14 s, from Phase 6
  observations) multiplied by the run count. It is labelled an estimate wherever
  it is shown, and it is what the user approves against, but it does not account
  for partition count — the observed 1600-partition run took more than twice as
  long as the 50-partition one.
- **The numeric grounding check has a real false-negative rate.** It accepts a 2%
  rounding tolerance and the common unit rescalings, and ignores integers at or
  below 10, so a fabricated small number or one that happens to land near a real
  measurement would pass. The structural check — a `measured` claim must name a
  metric the experiment measured — is the stronger of the two.
- **An `inferred` claim is not checked at all.** That is intentional: an inference
  legitimately restates measured figures, and it is already labelled as the weaker
  kind of claim. But it means a model can place an unsupported assertion in an
  `inferred` claim and the guard will not object.
- A `documented` claim is verified only to *cite a page that was retrieved*, not
  to be supported by that page's contents. In the observed run the model labelled
  "AQE is disabled for this job" as `documented` with a Spark URL, when it is
  actually a fixed setting from the plan. The claim was true; the label was wrong,
  and nothing catches that.
- Tavily returned Spark 3.5.6 and `latest` documentation for a search explicitly
  version-matched to 3.5.1. The `DocumentationReference.version` field records
  what RepoLab asked for, not what the page documents, so a citation can be
  version-labelled more precisely than it deserves.
- `ExperimentAgent.explain` returns `None` when no model is configured, and the
  CLI prints "no explanation: the measurements stand alone". Correct behaviour —
  better than a fabricated narrative — but it means the no-credentials demo stops
  at a statistics table.
- **Resuming a half-run experiment is not implemented.** Measurements are
  persisted after every run, so a crash loses nothing already measured, but
  nothing picks the experiment back up: it stays `running` and the remaining
  variants are never executed.
- An experiment re-clones the repository for both the design and the run. The run
  task verifies the commit matches what was approved and fails if the default
  branch has moved, which is correct but means a repository that moves between
  proposal and approval has to be re-proposed.
- `worker/experiment_tasks.py` marks the *design* job `COMPLETED` when it reaches
  the approval gate, because an experiment waiting for a human is not a job that
  is still running. The run then gets its own job and its own event stream, so an
  experiment's progress is split across two streams rather than one.
- **Playwright needs a browser on the machine.** The e2e spec uses the installed
  Chrome via `channel: "chrome"` rather than downloading one. On a machine
  without Chrome, `npx playwright install chromium` and clear
  `PLAYWRIGHT_CHANNEL`.
- The e2e spec takes about seven minutes, because it imports a real repository,
  runs real Spark and runs a real experiment. It is excluded from the default
  test run, like the `docker` and `live` markers, and is run on purpose.
- The e2e spec has no API-failure coverage: it asserts the happy path and the
  decline path. A worker that is down, a 409 on a second approval, or a failed
  Spark run are handled in the UI and tested at the API layer, but no browser
  test exercises them.
- The architecture graph lays nodes out in fixed columns by kind. It reads well
  for the repositories P0 targets and would not for a graph with many modules.
- The web app has **no component-level tests**. TypeScript strict, `eslint` and
  the Playwright spec are the whole safety net, so presentational logic — the
  phase tracker's state machine, the chart's pending bars — is only covered
  where the e2e spec happens to touch it.
- **The build ladder is reachable only from the CLI.** There is no API endpoint
  and nothing in the browser, so the candidates, the served artifact and the
  upgrade offer are invisible to the web experience P0 ships.
- **`EnvironmentSession` is not persisted.** It lives in the process that built
  the ladder, so a pending upgrade does not survive a restart and two clients
  cannot share one session. CLAUDE.md lists `sessions` as a table; this is not
  it yet.
- **Checkpoint images accumulate and nothing prunes them.** Each ready rung
  commits an image — four per commit for Cassandra, 0.4 to 1.9 GB each — and
  every test run adds more. Containers are cleaned up; images are not. A service
  build that fails its smoke test also leaves its commit behind, harmless (the
  artifact never claims it and only ready builds are cached) but equally
  unswept.
- **The Mini Lab is only as good as the manifest.** A repository where detection
  found no concepts and no evidence-backed surfaces produces a lab with nothing
  grounded, which correctly fails its own validation — and then *every* rung has
  failed. The final fallback can itself be unavailable.
- `DockerSandboxProvider` does not bind-mount, so a build's output is captured by
  the checkpoint; `DockerEnvironment` does mount, so Phase 6 can read Spark's
  event log from the host. Two providers with opposite and individually correct
  choices, which is a thing to know before using either.
- The memory budget defaults to 6 GB and is not detected from the daemon. On a
  machine with less, candidates will be admitted that cannot run; on one with
  more, the ladder will serialise unnecessarily.
- The real `ant jar` takes about four minutes here and needs the network from
  inside the container. It is `slow`-marked and excluded from every other target,
  so an ordinary test run never exercises the top rung.
- Cassandra's partial build reduces `num_tokens` and the heap, but the two
  container rungs otherwise share an image and a smoke test. The fidelity
  difference between them is real and declared, but it is narrower than the gap
  between either and a source build.

### From Phase 10

- **A terminal session is one container per learner, restored from a
  checkpoint.** Nothing created in a previous session is there, and switching to
  a better environment starts a new one — stated in the UI wherever an upgrade
  is offered, but it still means a learner loses their keyspace to an upgrade.
- **Sessions live in the API process's memory.** They hold real containers,
  which do not survive a restart, so persisting them would promise something the
  container cannot keep — but it also means a restart silently orphans whatever
  a learner was doing, and the containers are reclaimed only by the idle reaper.
- **cqlsh costs one to two seconds per statement**, because each one starts a
  Python process. Acceptable for a guided lesson and the price of cleanly
  delimited results, but it is why the eight-step walkthrough takes a minute.
- **`USE` is carried forward as `cqlsh -k`**, which is right for selecting a
  keyspace and does nothing for any other session state a real cqlsh would keep
  (`CONSISTENCY`, tracing, prepared statements).
- ~~**Nano's fuzzy step check has never run against the real model.**~~ Closed
  in Phase 12: a deliberate wrong statement made a verdict inconclusive, Nano
  adjudicated it correctly, and the answer was labelled as the model's.
- **One built-in lesson.** `cassandra_lesson` is served only for a repository
  whose evidence cites cqlsh; any other CLI repository gets a generated lesson
  or a 404. That is honest, and it is also the whole of RepoLab's lesson library.
- **The Mini Lab lesson has never been reached in a browser**, because the
  container rungs keep succeeding. The rendering and its restrictions are
  tested; the path is unobserved in practice.
- **Lesson progress is keyed by terminal session**, so a learner who starts a
  new session starts the lesson again even though the lesson itself is the same.
- **The allowlist permits more than the lesson needs** — `TRUNCATE`, `DROP`,
  `ALTER` outside system keyspaces — on the grounds that a learner exploring a
  database they own should be able to undo what they made. A hostile learner can
  therefore break their own environment, which is recoverable by restarting a
  session and is not treated as a security boundary.
- **The terminal sends one statement at a time and waits.** There is no
  streaming output and no way to interrupt a long query other than its 120s
  timeout.

### From Phase 11

- **Detection on a large repository is sensitive to how the read budget is
  spent.** The manifest reservation fixes the case that was observed — framework
  evidence starved by a 32 MB source scan — but `max_total_read_bytes` is still
  hit on Airflow, so which *source* files were scanned still varies between
  runs. The surfaces are now stable because they rest on manifests; a surface
  that depends only on code may not be.
- **Airflow's `web` evidence is dependency manifests, not application code.**
  `airflow-core/pyproject.toml` declaring Flask and React is real, citable
  evidence, and it is weaker than seeing the application constructed. The
  Phase 3 gap behind it is unchanged.
- **Two of Airflow's four surfaces are weak signals** (`api` 0.45 from FastAPI
  and Starlette in manifests; `notebook` 0.35 from a few `.ipynb` files and
  `__init__.py` packaging). Both are flagged `weak` on the screen rather than
  filtered, which is honest but means the hybrid demonstration rests on two
  strong surfaces and two thin ones.
- **"Runnable" promises an attempt, not a success.** The catalog reports that
  RepoLab has an adapter and a build strategy for a surface; a Spark repository
  that is not the demo repository can still fail at build time, which the
  Playground reports when it happens.
- **The catalog's runnability is per *primary* surface.** A repository with a
  strong data playground *and* a strong CLI would be offered only the one the
  ladder would build, with the other saying so. Correct today, because the ladder
  builds one environment per repository, and a limitation to revisit if a
  repository ever needs two.
- **Nothing replays a surface that becomes runnable later.** If a rung were added
  to the ladder for Airflow, its CLI card would become runnable on the next
  request — but a learner looking at the screen would not be told.
- **No experience exists for `web`, `api`, `notebook` or `terminal`.** Four of
  the six surface types RepoLab can detect have no adapter, which is what the
  screen now says plainly rather than what it previously implied.

### From Phase 12

- **Seven of the dashboard's 45 metrics have no number**, and that is the
  honest state rather than a gap to close: challenge success rate has no feature
  behind it, build recovery rate has had no failure to recover from since the
  events existed, and the three cost metrics need `MODEL_PRICING`.
- **The dataset is this machine's development traffic.** Stated at the top of
  the screen, because every figure would otherwise read as adoption.
- **Several metrics are n=1.** Time to first run and first experiment rest on a
  single repository each, so their medians are single observations. The sample
  count is shown beside every value for exactly this reason.
- **Build success rate counts a candidate with no recorded failure as a
  success**, so a worker killed mid-build would be counted as having succeeded.
  Stated on the metric.
- **Model-call telemetry starts from Phase 12.** Every call before it was logged
  only, so the model metrics describe recent traffic and not the project's
  history. The metric says so.
- **`agent_recovery_rate` is RepoLab's own definition** — a run that had a failed
  tool call and still completed — not an industry standard. Shown beside the
  number.
- **Questions → experiments can exceed 100%**, because an experiment can be
  requested without a question having been asked. It currently reads 118%.
- **No metric is bucketed by time.** Everything is all-time, so a regression
  would be diluted rather than visible.
- **`code_modified`, `evidence_opened` and `quick_answer_generated` are not
  instrumented.** Two of the three have no feature behind them yet.
- **The dashboard is unauthenticated and product-wide.** Fine for a hackathon on
  localhost; it would need scoping before it described more than one user.

### From Phase 13

- **The two credentials that were in `.env.example` should be rotated.** They
  have been replaced with placeholders and a test now guards the file, but the
  exposure happened and this repository cannot undo it.
- **The failure explainer is a hand-maintained mapping.** An exception type
  nobody has mapped gets a generic sentence and its own text — the right default,
  and a thing that needs adding to when a new failure appears.
- **Every user-facing sentence is English in the source.** No localisation, and
  no mechanism for one.
- **The abandoned-job sweep runs at worker startup only.** A worker that dies
  and is never restarted leaves its jobs reading as running until one starts.
  The browser shows *stalled* in the meantime, which is accurate.
- **A variant failure is tolerated, not retried.** The run is recorded as failed
  and the experiment continues with fewer measurements; nothing attempts it
  again, so a transient Docker hiccup costs a data point rather than a minute.
- **`MIN_COMPARABLE_VALUES` is 2.** An experiment that loses all but one value
  is abandoned. Two surviving values is a thin comparison, and RepoLab reports
  it as one rather than refusing it.
- **The technical detail is not redacted.** It is the exception's own words,
  behind a disclosure. A secret inside an exception message would appear there if
  one ever existed — verified against both real providers, neither of which
  echoes a key, and the default view carries nothing.
- **`make demo-prepare` cannot warm what it cannot build.** On a machine with
  less than 6 GB to containers the Cassandra rungs fail and the preflight reports
  no cached builds, which is the honest outcome but still a cold demo.
