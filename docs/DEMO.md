# Running the RepoLab demo

Everything here was verified on 2026-10-05 against the real stack. Commands are
copy-pasteable; the state each one expects is written down so a deviation is
visible rather than surprising.

---

## 1. The demo repositories

Three, each demonstrating a different thing. The commits are what RepoLab was
last verified at; a repository that has moved on will be re-investigated on
import, which is the expected path, not a problem.

Airflow in particular moves daily, so its commit here will rarely match
what you ingest; the four surfaces rest on dependency manifests, not on a
particular commit.

| repository | verified commit | demonstrates |
|---|---|---|
| `https://github.com/AlexIoannides/pyspark-example-project` | `eeee0c2b9af7` | **P0** — investigation, architecture, Spark playground, an approved experiment with measured results |
| `https://github.com/apache/cassandra` | `b15526b48165` | **P1** — the fidelity ladder, a real `cqlsh` terminal, one guided lesson |
| `https://github.com/apache/airflow` | `94a677e8f877` | **P3** — four interaction surfaces, each evidenced, none runnable |

A fourth is useful for showing a *refusal* rather than a feature:

| `https://github.com/pallets/click` | any | RepoLab investigates it, finds a CLI surface, and says plainly that it cannot build it |

---

## 2. Prerequisites

| requirement | why | check |
|---|---|---|
| Docker, running, **≥ 6 GB** to containers | Cassandra needs 4 GB and dies silently in 2 GB; the ladder races two nodes | Docker Desktop → Settings → Resources |
| **~15 GB** free disk | the images and checkpoints below total ~9 GB | `df -h .` |
| Ports 3000, 5432, 6379, 8000 free | web, Postgres, Redis, API | `lsof -i :8000` |
| Python 3.11 via `uv`, Node for `web/` | | `make install` |
| `.env` with `NEBIUS_API_KEY` + the three `MODEL_*` IDs | **optional** — every deterministic path works without them | `make demo-check` |
| `TAVILY_API_KEY` | **optional** — answers then cite repository code only | `make demo-check` |

`.env.example` is a template and contains **no real credentials**. Copy it to
`.env` and fill in your own.

---

## 3. Startup

```bash
make up              # Postgres + Redis
make db-upgrade      # migrations to head (currently cfde6d1ca94a)
make demo-check      # the preflight — read this before going further
make dev             # api :8000, worker, web :3000
open http://localhost:3000
```

`make dev` runs all three in one terminal and stops them together with Ctrl-C.

---

## 4. Expected build state

`make demo-check` should print this. Anything marked `!!` is worth fixing before
presenting:

```
infrastructure
  ok Postgres reachable
  ok Migrations at head: cfde6d1ca94a
  ok Redis reachable
  ok Docker available

images
  ok Image apache/spark:3.5.1-python3
  ok Image cassandra:5.0

demo repositories
  ok P0 — Spark playground: ingested at eeee0c2b9af7; cached builds: source_build
  ok P1 — CLI lesson: ingested at b15526b48165; cached builds: official_release,
     partial_build, source_build; lesson by super
  ok P3 — hybrid detection: ingested at 94a677e8f877
```

The Cassandra checkpoints are local Docker images, about 3 GB in total:

```
repolab/apache-cassandra:<commit>-source-build        1.85 GB   exact fidelity
repolab/apache-cassandra:<commit>-official-release     568 MB   high fidelity
repolab/apache-cassandra:<commit>-partial-build        568 MB   partial fidelity
```

With them cached, a terminal session starts in about 40 seconds (restore a
checkpoint, wait for Cassandra to replay its commit log). Without them, the
ladder rebuilds: **about 6 minutes**, most of it a real `ant jar`.

---

## 5. Cache preparation

```bash
make demo-prepare          # whatever is missing
make demo-prepare ARGS=--force   # redo everything
```

Four steps, idempotent, each skipped if already warm:

1. pull `apache/spark:3.5.1-python3` and `cassandra:5.0` (~2 GB cold);
2. ingest the three demo repositories;
3. resolve the Cassandra ladder into the build cache (**the slow step**);
4. generate the Cassandra lesson.

**Cold: 12–15 minutes. Warm: a few seconds.** Run it the day before, and again
an hour before, and it will do nothing the second time.

```bash
make check-secrets   # scans the database and web/test-results for credentials
```

---

## 6. The three-minute script

Timings are from real runs with the cache warm. The repository URL is prefilled
on the Import screen.

**0:00 — Import (10s).** "RepoLab takes a repository URL and nothing else."
Click **Investigate** on the prefilled Spark repository.

**0:10 — Investigation (60s).** Four phases announce themselves; the agent's
activity runs beside them. Say what it is doing: reading files, finding entry
points, no code executed yet. **Refresh the page** — history replays and the
stream continues. That is worth doing live.

**1:10 — Architecture (20s).** Components, data flow, important files. Point at
one citation: every claim names the lines behind it.

**1:30 — Playground (30s).** The dataset and its hash. The pipeline, with the
three steps *RepoLab added* marked as RepoLab's. Say the honest part: the
repository's own job performs no shuffle, so RepoLab adds one to have something
to measure. Metrics come from Spark's own event log.

**2:00 — Ask RepoLab (45s).** *"Why does this job use 200 shuffle partitions?"*
The plan appears with its cost — 4 configurations × 3 repeats, ~4 minutes — and
**nothing runs**. Click **Decline** to show the gate holds, then **Approve**.
Charts fill in as variants complete.

**2:45 — The answer (15s).** Every claim labelled *measured*, *documented* or
*inferred*, with the evidence beside it. The finding: 193 of 200 partitions were
empty.

### If there is more time

- **Cassandra** (2 min, cache warm): import it, open **Learn**, start a
  terminal, run the lesson's steps. Step 7 provokes Cassandra's own
  `ALLOW FILTERING` refusal; step 8 explains it.
- **Airflow** (1 min): import it, open **Experiences**. Four surfaces with
  evidence, none runnable, each saying so. No Playground or Learn tab.
- **Metrics** (30s): `/metrics` — learning activity and RepoLab's own
  operational performance, with the north-star KPI labelled and unnumbered.

---

## 7. Recovery

Each row is a failure that has actually been provoked and watched.

| what you see | what it means | what to do |
|---|---|---|
| *"The model took too long to answer."* | a model call passed its deadline | Try again. The first call after a quiet period is slowest. The deterministic path still works. |
| *"The model's answer could not be used."* | the answer failed schema validation twice | Try again. Nothing was acted on, and an experiment will still be designed deterministically. |
| *"No model is configured for this step."* | `MODEL_*` unset | Everything deterministic still works. Set them in `.env` and restart the worker. |
| *"The execution environment is not available."* | Docker is not reachable | Start Docker Desktop. `make demo-check` confirms. |
| *"RepoLab could not build a runnable environment."* | every ladder rung failed its smoke test | Usually Docker memory. Raise it to 6 GB and retry. The activity feed names the rung. |
| *"RepoLab does not know how to build this repository yet."* | no build strategy covers it | Expected for anything but Spark and Cassandra. The read-only screens still work — this is a feature to show, not a fault. |
| *"The repository's own job failed when it ran."* | the application exited non-zero | The activity feed holds its own output. |
| *"The repository has moved on since it was investigated."* | HEAD changed between investigation and build | Import it again. |
| *"The job stopped because RepoLab's worker restarted."* | a worker died mid-job; it is now resolved rather than stuck | Start the action again. |
| **`reconnecting`** on the job badge | the SSE stream dropped | It reconnects on its own and replays from where it left off. If the API is down, restart it; the page recovers. |
| a variant fails mid-experiment | one Spark run did not complete | The others continue. The statistics say they rest on fewer runs. Below two surviving values, the experiment is abandoned with a clear message. |
| terminal says *starting* for a while | Cassandra is replaying its commit log | Up to 5 minutes cold, ~40s from a cached checkpoint. |

### Resets, cheapest first

```bash
# the worker is wedged or was killed
pkill -f "python -m worker.main" && make dev-worker   # abandoned jobs are resolved on start

# the API or web is wedged
pkill -f "uvicorn api.main:app"; pkill -f "next dev"; make dev

# containers left behind by an interrupted build
docker ps -q --filter "name=repolab-" | xargs -r docker rm -f

# start from a clean database (loses history, keeps images)
make down && make up && make db-upgrade && make demo-prepare
```

**Never** `docker image prune -a` before a demo: it deletes the Cassandra
checkpoints and the next lesson costs six minutes instead of forty seconds.

---

## 8. What to say if something breaks

The honest framing is also the strongest one: every failure above produces a
sentence rather than a stack trace, the degradation is designed, and the
screens that cannot run say so instead of pretending. If a build fails, the
ladder falling through to a lower fidelity **is** the demo.
