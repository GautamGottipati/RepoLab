# Backlog: deferred work

Things found while building that were deliberately left for later: warnings,
known limitations, unverified assumptions, small debts. Each one carries enough
context to pick up cold.

**How to use this file**

- At the start of a development session, skim it; pull in items that fit the
  current milestone. The P0 → P3 order in `CLAUDE.md` still decides priority.
- When you finish an item, move it to [Done](#done) with the date and commit.
- When you defer something new, add it here. A warning in a CI log, a
  "not verified" in a report, or a "known limitation" in a runbook all belong.
- `docs/PROGRESS.md` records what happened. This file records what is still
  owed.

Each item has an ID, which links back to its source, and a size:
**S** is under an hour, **M** is about half a day, **L** is a day or more.

---

## Maintenance

### B-001 · Bump GitHub Actions off Node 20 · S
- **Why:** CI warns that `astral-sh/setup-uv@v6` targets Node.js 20, which
  GitHub is retiring; runners currently force it onto Node 24. It works today
  and will eventually stop.
- **Where:** `.github/workflows/deploy.yml`.
- **What:** Latest releases on 2026-10-29: `astral-sh/setup-uv` **v10.3.3**
  (we use v6), `actions/checkout` **v7.0.1** (v5), `actions/setup-node`
  **v7.1.0** (v5). Read each changelog for breaking changes before bumping;
  setup-uv has changed defaults between majors. Consider pinning by commit SHA.
- **Done when:** a CI run on `main` is green with no deprecation annotations.
- **Source:** CI run 40971615073.

### B-002 · Pin the CI runner image · S
- **Why:** CI notes that `ubuntu-latest` is moving to Ubuntu 26. The production
  VMs run Ubuntu 24.04, so CI drifting away from them hides OS-specific
  breakage.
- **Where:** `.github/workflows/deploy.yml` (`runs-on`).
- **What:** use `ubuntu-24.04` until the VMs move.
- **Done when:** both jobs run on `ubuntu-24.04`.
- **Source:** CI run 40971187064.

### B-003 · Run shellcheck in CI · S
- **Why:** `tests/test_deploy_scripts.py` runs shellcheck only when it is
  installed. It isn't installed locally or in CI, so that test always skips.
- **What:** install shellcheck in the `test` job (`sudo apt-get install -y
  shellcheck`), then fix whatever it reports at error severity.
- **Done when:** `test_shellcheck_finds_no_errors` passes in CI instead of
  skipping.

## Testing

### B-010 · Update the Playwright specs after the UI redesign · M
- **Why:** `web/e2e/p0.spec.ts` looks for the heading "RepoLab", and
  `web/e2e/airflow.spec.ts` for "Available Experiences". The Phase 20 redesign
  removed both. The app works; the specs check text that no longer exists.
  `metrics.spec.ts` passes.
- **What:** update the selectors to the current UI, preferably with
  `data-testid`s, which survive copy changes. Check `cassandra.spec.ts` too.
- **Done when:** `make e2e` passes against `make dev` and against a deployment
  (`PLAYWRIGHT_WEB_URL=…`).
- **Source:** Phase 24 in `docs/PROGRESS.md`.

## Deployment verification (needs a real VM)

### B-020 · Acceptance test on a real Compute Engine VM · M
- **Why:** GCP support was verified only in a local docker:dind VM with a
  *simulated* metadata server. Untested on real hardware:
  - the real metadata server and its DNS;
  - an MTU of 1460;
  - Google's guest agent and OS Login, and their effect on `authorized_keys`
    and the CI key;
  - Let's Encrypt over a real domain;
  - Cassandra on 32 GB.
- **What:** `docs/DEPLOY_GCP.md` end to end, then `check_deployment.sh`,
  `CHECK_LIVE=1`, and the browser smoke test.
- **Done when:** every check passes on GCE, with any GCP-specific fixes
  recorded in `docs/PROGRESS.md`.

### B-021 · Acceptance test on a real Nebius VM · M
- **Why:** same gap as B-020, for Nebius. It also includes one unverified
  assumption: **where Nebius's DNS resolver lives**. The bootstrap now exempts
  whatever resolvers the host uses, and `check_deployment.sh` fails if
  containers lose DNS, so this would show up as a failure rather than silently.
- **Done when:** every check passes on Nebius.

### B-022 · Confirm the GCP Console labels in DEPLOY_GCP.md · S
- **Why:** Google's docs didn't confirm the exact Console wording for "no
  service account" or for the HTTP/HTTPS firewall checkboxes. The guide hedges
  and gives `gcloud` equivalents (`--no-service-account --no-scopes`), which are
  confirmed.
- **Done when:** the Console steps are walked through once and the wording
  corrected.

## Security

### B-030 · Harden the Spark playground and experiment containers · M
- **Why:** these containers run with the default network, as root, with only a
  4 GB memory limit (`repolab/sandbox/docker.py`, `hardened=False` paths in
  `worker/spark_tasks.py` and `repolab/experiments/executor.py`). The
  learner-code paths already use `HARDENED_FLAGS`.
- **What:** check whether these runs need the network. The dataset is generated
  and the image is pre-pulled. If not, apply `HARDENED_FLAGS`. Re-run
  `make test-docker` and a real experiment to confirm measurements are
  unaffected.
- **Done when:** every repository container that has no stated reason for
  network access runs with `--network none` and `--cap-drop ALL`.

### B-031 · Move repository execution off the control-plane VM · L
- **Why:** sibling containers share the host kernel, and api and worker hold the
  Docker socket, which is root-equivalent. This is container isolation, not a
  sandbox (see `docs/DEPLOY_OPERATIONS.md#isolation-model`).
- **What:** either Nebius Sandboxes, once the account entitlement is granted
  (`repolab/sandbox/nebius.py` exists but is unused), or a dedicated execution
  VM. The second needs changes to the worker's bind mounts and to the API's
  `127.0.0.1` live-app proxy, both of which assume one machine.
- **Done when:** no repository code runs on the VM that holds RepoLab's
  secrets.

## Operations

### B-040 · Live web-app previews in production · M
- **Why:** `rt-<id>-<svc>.<domain>` needs wildcard DNS plus a wildcard
  certificate, and Let's Encrypt issues wildcards only via DNS-01.
  `init_tls.sh` uses HTTP-01. Until this is done, previews are unavailable over
  HTTPS.
- **What:** a DNS-01 certbot plugin for your DNS provider, and nginx using the
  wildcard certificate for the `rt-*` server block.

### B-041 · Off-site Postgres backups · S
- **Why:** `backup_postgres.sh` keeps 7 dumps on the VM's own disk. They don't
  survive losing that disk.
- **What:** copy each dump to object storage (GCS or Nebius Object Storage) from
  the backup timer, or schedule disk snapshots.

### B-042 · Garbage-collect checkpoint images · M
- **Why:** committed images (`repolab/checkpoint:*`, `repolab/experience-*`,
  `repolab/runtime-*`) accumulate and are never removed. A long-running VM will
  fill its disk. `docker image prune -a` is unsafe because Postgres cache rows
  point at these images.
- **What:** a reaper that deletes images no cache row references, plus cache
  rows whose image is gone (the `CheckpointMissingError` path already handles a
  missing image).

### B-043 · Drain workers on deploy · M
- **Why:** a deploy restarts the workers, which interrupts running jobs. They
  are reported honestly as interrupted, but the user has to resubmit.
- **What:** stop taking new jobs, wait (bounded) for running ones to finish,
  then restart. Or re-enqueue jobs that were interrupted.

### B-044 · More than one API process · L
- **Why:** cqlsh terminal sessions live in the API process's memory, so the API
  can't be replicated or load-balanced.
- **What:** move session state out of process memory (into the worker or Redis,
  with containers addressed by ID).

## Code hygiene

### B-050 · Fix a misleading comment in `api/main.py` · S
- **Why:** `api/main.py` says "The API never touches Docker itself". That's
  untrue: the cqlsh terminal and lesson checks run `DockerSandboxProvider` in
  the API process (`api/routes/cli.py`). The deployment depends on knowing
  this, because the api container mounts the Docker socket.
- **Done when:** the comment says which API paths use Docker.

---

## Done

| ID | Item | Date | Commit |
|---|---|---|---|
| — | — | — | — |
