.PHONY: install dev dev-api dev-worker dev-web stop test lint fmt up down check smoke \
	demo-check demo-prepare check-secrets \
	db-upgrade db-downgrade db-revision ingest check-repos build run-spark-demo test-docker \
	experiment e2e e2e-cassandra e2e-airflow e2e-metrics web-build web-lint build-ladder test-slow

install:
	uv python install 3.11
	uv sync
	cd web && npm install

up:
	docker compose up -d
	docker compose ps

down:
	docker compose down

# The API waits at most this long for open connections on shutdown. Without a
# limit, a browser tab's event stream (SSE) keeps it alive forever after Ctrl-C,
# still holding :8000, and the next `make dev` fails with "Address already in use".
API_SHUTDOWN_TIMEOUT := 3
UVICORN := uv run uvicorn api.main:app --reload --port 8000 --timeout-graceful-shutdown $(API_SHUTDOWN_TIMEOUT)

dev: up stop
	@echo "Starting api (:8000), two workers and web (:3000). Ctrl-C stops all."
	@trap 'trap "" INT TERM; echo; echo "Stopping..."; kill -TERM 0 2>/dev/null; sleep 4; $(MAKE) --no-print-directory stop' INT TERM; \
	$(UVICORN) & \
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run python -m worker.main & \
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run python -m worker.main & \
	(cd web && npm run dev) & \
	wait

dev-api: up
	$(UVICORN)

dev-worker: up
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run python -m worker.main

dev-web:
	cd web && npm run dev

# Stop anything a previous `make dev` left running (a closed terminal, a hung
# shutdown): the API, the workers and the web dev server. Asks politely first,
# then forces whatever is still there after three seconds.
stop:
	@sh scripts/dev-stop.sh

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

check:
	uv run python scripts/check_services.py

# Is this machine ready to demo? Read-only; exits non-zero if not.
demo-check:
	uv run python scripts/demo_check.py

# Warm everything the demo needs: images, ingestions, the Cassandra ladder and
# the lesson. Idempotent; slow the first time, which is the point.
demo-prepare:
	uv run python scripts/demo_prepare.py $(ARGS)

# Scan everything RepoLab has written for a configured credential.
check-secrets:
	uv run python scripts/check_secrets.py

smoke:
	uv run pytest -m live -v

# Network-only checks: real clones, no credentials required.
check-repos:
	uv run pytest -m network -v

# Tests that need a Docker daemon (the real Spark run, the Cassandra ladder).
test-docker:
	uv run pytest -m docker -v

# Tests that take many minutes of real compute: the real `ant jar` build of
# Cassandra. Excluded from every other target on purpose.
test-slow:
	uv run pytest -m slow -v -s

db-upgrade: up
	uv run alembic upgrade head

db-downgrade: up
	uv run alembic downgrade -1

db-revision:
	uv run alembic revision --autogenerate -m "$(M)"

# Usage: make ingest URL=https://github.com/owner/name [ARGS="--persist --llm"]
ingest:
	uv run python scripts/ingest_repo.py $(URL) $(ARGS)

# Runs the real Spark demo in a container and prints measurements taken from
# Spark's own event log. Needs Docker; no credentials.
run-spark-demo:
	uv run python scripts/run_spark_demo.py $(ARGS)

# Usage: make build URL=https://github.com/owner/name [ARGS="--no-cache"]
# Uses the Nebius sandbox when credentials exist, otherwise a fake provider --
# the output always says which.
build:
	uv run python scripts/build_repo.py $(URL) $(ARGS)

# The core RepoLab flow: a question answered by measurement, not assertion.
# Prints the plan and the estimated cost, then WAITS for approval before running
# anything. Needs Docker; works without credentials (deterministic fallback).
# Usage: make experiment Q="Why does this job use 200 shuffle partitions?"
#        make experiment Q="..." ARGS="--values 8,50,200,400 --repeats 2 --yes"
Q ?= Why does this job use 200 shuffle partitions?
experiment:
	uv run python scripts/run_experiment.py --question "$(Q)" $(ARGS)

# The browser build and linter, so the web app is checked like the backend is.
web-build:
	cd web && npm run build

web-lint:
	cd web && npm run lint

# The P0 done-when criterion, mechanically: a real browser through the whole
# experience. Needs `make dev` running (api, worker, web) and Docker.
# Uses the Chrome installed on the machine; see web/playwright.config.ts.
e2e:
	cd web && npm run e2e

# Just the Cassandra lesson, in a real browser against a real node: import,
# resolve the ladder, connect a terminal, complete all eight steps.
e2e-cassandra:
	cd web && npx playwright test e2e/cassandra.spec.ts

# Hybrid detection: import Airflow, inspect all four surfaces and their
# evidence, and confirm nothing unsupported is offered as runnable. No build.
e2e-airflow:
	cd web && npx playwright test e2e/airflow.spec.ts

# The KPI dashboard: both sections populated, and nothing unmeasured showing a
# number. Fast -- one aggregate read, nothing built.
e2e-metrics:
	cd web && npx playwright test e2e/metrics.spec.ts

# The fidelity ladder, watched: candidates race, the best passing one is served,
# and a better one that lands later is OFFERED rather than forced.
# Usage: make build-ladder [LADDER_URL=https://github.com/owner/name]
#
# A target-scoped default, not a global one: `URL` is already how `ingest` and
# `build` take their repository, and defaulting it would silently give those a
# repository nobody asked for.
build-ladder: LADDER_URL ?= https://github.com/apache/cassandra
build-ladder:
	uv run python scripts/build_ladder.py --url "$(LADDER_URL)" $(ARGS)
