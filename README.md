# RepoLab

Agentic experimentation and learning environment for real software.

RepoLab takes a repository URL, investigates the codebase, builds an isolated
environment, detects how the software is meant to be run, and turns it into an
interactive learning experience — then tests claims about it by running real
experiments rather than describing them.

> Models reason about measurements. Models never manufacture measurements.

See [CLAUDE.md](CLAUDE.md) for the full product and architecture definition, and
[docs/PROGRESS.md](docs/PROGRESS.md) for current status.

**Status: Phase 0 (skeleton).** Not yet demoable.

## Layout

```text
web/      Next.js + TypeScript + Tailwind
api/      FastAPI
worker/   RQ background workers
repolab/  shared package (agents, llm, sandbox, experiments, evidence, ...)
scripts/  operational scripts
docs/     progress and design
tests/    offline test suite
```

## Requirements

- [uv](https://docs.astral.sh/uv/) (provisions Python 3.11 itself)
- Node.js 20+
- Docker

## Getting started

```bash
cp .env.example .env     # optional: fill in credentials
make install             # Python 3.11 + deps + web deps
make up                  # Postgres + Redis
make db-upgrade          # apply migrations
make test                # offline test suite
make check               # probe external capabilities
make dev                 # api :8000, worker, web :3000
```

## Submitting a repository

Run `make dev`, open `http://localhost:3000`, and submit a public repository URL.
The page shows the agent's progress live; refreshing it restores the events
already recorded and continues streaming.

The same flow over HTTP:

```bash
curl -X POST localhost:8000/jobs -H 'Content-Type: application/json' \
  -d '{"repo_url":"https://github.com/AlexIoannides/pyspark-example-project"}'

curl -N localhost:8000/jobs/$JOB/events        # live, replays history first
curl -N localhost:8000/jobs/$JOB/events -H "Last-Event-ID: 12"   # resume
curl localhost:8000/jobs/$JOB                  # status, elapsed, stall state
curl localhost:8000/jobs/$JOB/manifest         # the finished RepoManifest
```

Events are persisted in Postgres and streamed over SSE with a per-job sequence
number, so a client that disconnects and reconnects sees no gap and no duplicate.

## Running the Spark demo and measuring it

```bash
make run-spark-demo                      # needs Docker; no credentials
make run-spark-demo ARGS="--persist"     # also store the run in Postgres
```

This generates a deterministic synthetic dataset with one deliberately skewed key,
runs the **repository's own transformation** on real Spark 3.5.1 inside a
container with event logging enabled, and reports measurements parsed from Spark's
own event log.

Every figure is arithmetic over what Spark recorded — no model produces a number.
The summary separates **deterministic** figures (counts, bytes, partition
distribution) from **timings**, which legitimately vary; two runs over the same
inputs agree exactly on the former. The raw event log is preserved under
`.repolab/runs/` and hashed, because it is the measurement's source.

The run also discloses what RepoLab added: the demo repository's job performs no
shuffle, so a repartition and aggregation are appended to produce measurable
shuffle and skew.

## Building a runnable environment

```bash
make build URL=https://github.com/AlexIoannides/pyspark-example-project
make build URL=... ARGS="--provider nebius"      # needs credentials
make build URL=... ARGS="--no-cache"             # force a rebuild
```

The command resolves a cached build or produces one: it uploads the repository into
an isolated environment, installs what the plan requires, runs a real smoke test,
and saves a checkpoint. **A build is ready only after its smoke test passes**, and
fidelity is derived from whether any pinned version had to be substituted — so a
build never claims to be an exact reproduction it isn't.

Without `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID` it runs against a fake provider and
labels the output `SIMULATED`, so a `READY` is never ambiguous about what produced it.

## Ingesting a repository directly

```bash
make ingest URL=https://github.com/AlexIoannides/pyspark-example-project
make ingest URL=... ARGS="--persist --llm"
```

Ingestion is **static inspection only**: the repository is shallow-cloned into an
isolated temporary directory and read. No dependency install, build tool or setup
script is ever run, and the clone is deleted afterwards. It produces a
`RepoManifest`, an architecture graph in which every node cites real files, and an
evidence bundle — all deterministic. `--llm` adds bounded Nano file summaries and
Super architecture labelling; `--persist` writes everything to Postgres.

`make check-repos` ingests the three test repositories (a small Spark ETL project,
`apache/cassandra`, `apache/airflow`) and needs network access but no credentials.

## Credentials

Everything runs without credentials — `make check` reports untested capabilities
as `SKIP` rather than guessing. To verify the external services, set
`NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID` (Sandboxes only) and `TAVILY_API_KEY` in
`.env`, then re-run `make check`.

Model IDs are never hardcoded. Set `MODEL_NANO`, `MODEL_SUPER` and `MODEL_ULTRA`
in `.env`; `make check` validates them against the live model catalog.

## Calling a model

All model access goes through the router, which takes a logical tier and a
Pydantic schema and returns a validated object. Call sites never name a model.

```python
from pydantic import BaseModel
from repolab.llm import Purpose, build_router

class Finding(BaseModel):
    framework: str
    confidence: float

router = build_router()
result = await router.super(
    purpose=Purpose.REPO_INVESTIGATION,
    schema=Finding,
    prompt="Which framework does this repository use?",
)
result.value.framework   # validated
result.record.latency_ms # per-call accounting
```

## Development

```bash
make lint         # ruff check + format check
make fmt          # apply formatting and safe fixes
make test         # pytest, fully offline
make smoke        # live model checks (skips without credentials)
make check-repos  # ingest the real test repositories (network, no credentials)
```

The test suite never touches the network: external services sit behind the
protocols in `repolab/interfaces.py` with fakes in `repolab/testing/fakes.py`.
