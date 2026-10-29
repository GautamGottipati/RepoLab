# Operating a RepoLab deployment

Cloud-neutral: the same for a Nebius VM ([DEPLOY_NEBIUS.md](DEPLOY_NEBIUS.md))
and a Google Compute Engine VM ([DEPLOY_GCP.md](DEPLOY_GCP.md)). Both run the
same `docker-compose.prod.yml`; only the VM setup differs.

Commands run on the VM from the repository directory (`cd /opt/repolab`).

Contents:
[Verify](#verify-the-deployment) ·
[Smoke test](#smoke-test) ·
[Logs](#logs) ·
[Restart](#restart) ·
[Update](#update-the-deployment) ·
[Backup](#backup-and-restore) ·
[Troubleshooting](#troubleshooting) ·
[Isolation model](#isolation-model) ·
[Security checklist](#security-checklist) ·
[Known limitations](#known-limitations) ·
[Future architecture](#future-architecture)

---

## Verify the deployment

```bash
bash scripts/check_deployment.sh
```

The check is read-only and never prints secret values. It checks:

- the health of every container, and that migrations were applied
- the website, `/api/health` and `/api/health/ready`, all through nginx
- that the SSE route reaches FastAPI
- that Postgres and Redis answer, and how many RQ workers are registered
- which keys are set (it prints set/unset, never the values)
- that the worker can reach Docker
- the **isolation probes**: from a throwaway container on the Docker bridge, no
  Docker socket is visible, the metadata service is unreachable, and the host's
  ports 8000, 3000, 5432 and 6379 are unreachable

Two optional extras:

```bash
JOB_ID=<JOB_ID> bash scripts/check_deployment.sh   # also stream that job's SSE through nginx
CHECK_LIVE=1 bash scripts/check_deployment.sh      # real Nemotron + Tavily calls (small, billed)
```

## Smoke test

Use a browser at `https://<DOMAIN>`. Each step lists what you should see.

| # | Do | Expect |
|---|---|---|
| 1 | Open RepoLab | The import screen, with the Spark demo URL prefilled. |
| 2 | Submit `https://github.com/AlexIoannides/pyspark-example-project` | You land on `/r/<job_id>`. |
| 3 | Watch the job | `docker compose -f docker-compose.prod.yml logs -f worker` shows the job starting. |
| 4 | Watch the screen | The phases announce themselves, with "Working on …" activity. |
| 5 | Overview | The overview loads. |
| 6 | Experiences | The Spark data playground is listed. |
| 7 | Architecture | It fills in progressively, citing repository lines. |
| 8 | A source reference | Click one: it opens the cited file and lines. |
| 9 | Ask RepoLab | Ask *"Why does this job use 200 shuffle partitions?"*: a plan with its cost, and nothing runs until you approve. |
| 10 | Approve | Variants run, the charts fill in, and the measurements come from Spark's event log. |
| 11 | Refresh mid-analysis | History replays, the stream continues, and **no new job** appears (the URL keeps the same id). |
| 12 | SSE reconnect | `docker compose -f docker-compose.prod.yml restart nginx`. The page reconnects and resumes from `Last-Event-ID`. |
| 13 | Sandbox execution | Run the playground: the metrics show tasks and stages measured from the event log. |
| 14 | No repository code on the API host | `docker ps` shows repository containers (`repolab-*`, `apache/spark…`) separate from the api/worker containers, and the isolation probes in [Verify](#verify-the-deployment) pass. |

An automated version runs the existing Playwright suite from your laptop
against the deployment:

```bash
cd web
PLAYWRIGHT_WEB_URL=https://<DOMAIN> NEXT_PUBLIC_API_URL=https://<DOMAIN>/api \
  npx playwright test e2e/p0.spec.ts e2e/metrics.spec.ts e2e/airflow.spec.ts
```

The Cassandra spec (`e2e/cassandra.spec.ts`) also works, but it needs a VM with
at least 16 GB of memory.

## Logs

The service names are `nginx`, `web`, `api`, `worker`, `migrate`, `postgres` and
`redis`.

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml logs -f nginx
docker compose -f docker-compose.prod.yml logs migrate
docker ps --filter "name=repolab-"          # repository containers
```

Each model call is logged as `llm.call`, with its model, tier, latency and
tokens. Prompts and keys are not logged.

## Restart

```bash
docker compose -f docker-compose.prod.yml restart            # everything
docker compose -f docker-compose.prod.yml restart worker     # one service
```

The services restart on their own (`unless-stopped`), and after a reboot.

- **Data survives restarts.** Completed analyses, experiments and measurements
  live in Postgres (volume `postgres_data`). Event logs live in
  `/var/lib/repolab/state`, and checkpoint images in Docker's image store.
- **Running jobs do not.** A job running at the moment of a restart is
  interrupted. The worker's startup sweep and the liveness record report it as
  interrupted; it is never shown as silently stalled. Submit it again.

## Update the deployment

### By hand

```bash
cd /opt/repolab
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
bash scripts/check_deployment.sh
```

Migrations need no separate step. The `migrate` service runs
`alembic upgrade head` before the api and workers start, on every `up`. Alembic
applies only the migrations that are missing and never drops data.

### Automatically, on push to `main` (`.github/workflows/deploy.yml`)

The workflow does two things:

1. Runs ruff, the offline pytest suite, and the web lint, tests and build.
2. SSHes to the VM and runs the manual update above, retrying the check until
   the services report healthy.

One-time setup:

1. **Make a deploy-only SSH key.** On your laptop:

   ```bash
   ssh-keygen -t ed25519 -N "" -f repolab_ci -C "github-actions"
   ```

2. **Authorize it on the VM:**

   ```bash
   cat repolab_ci.pub | ssh <USER>@<VM_PUBLIC_IP> 'cat >> ~/.ssh/authorized_keys'
   ```

3. **Open SSH to GitHub's runners.** GitHub-hosted runners have no fixed IP, so
   port 22 must accept them from anywhere, with key-only login (Ubuntu's
   default on cloud images). Your provider guide shows the firewall rule.
   Otherwise, use a self-hosted runner.

4. **Add the repository secrets** under **Settings → Secrets and variables →
   Actions**:

   | Secret | Value |
   |---|---|
   | `VM_HOST` | `<VM_PUBLIC_IP>` or `<DOMAIN>` |
   | `VM_USER` | `<USER>` |
   | `VM_SSH_KEY` | the contents of `repolab_ci` (the private key) |
   | `VM_KNOWN_HOSTS` | the output of `ssh-keyscan -t ed25519 <VM_PUBLIC_IP>` |

5. **Optional repository variable:** `VM_APP_DIR`, if you didn't clone to
   `/opt/repolab`.

Your local branch may be called `master`. The workflow fires on `main`:

```bash
git push origin master:main
```

## Backup and restore

A daily dump runs at 02:30 UTC, and the last 7 are kept in
`/var/lib/repolab/backups`. To take one now:

```bash
bash scripts/deploy/backup_postgres.sh
```

Copy dumps off the VM; a backup on the same disk doesn't survive losing that
disk:

```bash
scp <USER>@<VM_PUBLIC_IP>:/var/lib/repolab/backups/repolab-<TIMESTAMP>.dump .
```

Restore. This overwrites the current data, so take a fresh dump first:

```bash
docker compose -f docker-compose.prod.yml stop api worker
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U repolab -d repolab --clean --if-exists < repolab-<TIMESTAMP>.dump
docker compose -f docker-compose.prod.yml up -d
```

**These dumps live on the VM's own disk: they do not survive losing the VM
or its disk.** For that, take a snapshot of the boot disk in your cloud's
console, or copy the dumps off the VM as above.

## Troubleshooting

Commands assume `cd /opt/repolab`. `dc` stands for
`docker compose -f docker-compose.prod.yml`.

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| **502 Bad Gateway** | api or web not running or not healthy | `dc ps`, then `dc logs --tail 100 api web` | Fix the error the logs show, then run `dc up -d`. When the api is the problem, it is usually `migrate` failing (`dc logs migrate`) or a missing `.env` value. |
| **Frontend cannot reach the API** (requests fail; the browser console shows localhost:8000 or the wrong host) | `PUBLIC_URL` was wrong when the web image was built | `dc exec web sh -c "grep -rhoE 'https?://[^\"]+/api' .next/static \| sort -u"` | Fix `PUBLIC_URL`, then run `dc up -d --build web`. |
| **SSE disconnecting** (the activity feed stalls and reconnects) | A proxy buffering or timing out the stream | `curl -N -s -D - https://<DOMAIN>/api/jobs/<JOB_ID>/events \| head`: expect `text/event-stream` and, while idle, `: keep-alive` every 15 s | Make sure nothing in front of nginx (a CDN or another proxy) buffers. The stream resumes from `Last-Event-ID` regardless. |
| **Worker not receiving jobs** (a job stays "queued") | No worker running, or the worker can't reach Redis | `dc ps worker`, `dc logs --tail 50 worker`; the check's "RQ workers registered" line | `dc up -d worker`. If Redis auth fails, `REDIS_PASSWORD` changed since Redis started: run `dc up -d --force-recreate redis api worker`. |
| **Redis unavailable** (`/api/health/ready` shows `"redis": false`) | Redis down, or its 512 MB maxmemory reached (`noeviction`) | `dc logs redis`, then `dc exec redis redis-cli info memory` | `dc restart redis`. If memory is full, clear stale RQ job keys or raise `--maxmemory`. |
| **Postgres unavailable** (`"database": false`) | Postgres down, or the password changed after the volume was created | `dc logs postgres`, then `dc exec postgres pg_isready -U repolab` | The volume keeps its original password. Restore the old `POSTGRES_PASSWORD`, or change it with `ALTER USER` inside psql. |
| **Nemotron authentication failure** (explanations missing; the worker logs `llm.call … success=False`) | Wrong or missing `NEBIUS_API_KEY`, or an unknown model ID | `CHECK_LIVE=1 bash scripts/check_deployment.sh` | Fix `.env`, then run `dc up -d api worker`. The model IDs must match the Token Factory catalog. |
| **Sandbox failure** ("The execution environment is not available") | The worker can't use Docker | `dc exec worker docker info` | Check that `/var/run/docker.sock` exists on the host and that Docker is running (`systemctl status docker`). |
| **"Could not build a runnable environment"** | Every ladder rung failed, usually from memory (Cassandra needs 4 GB) | `free -g`; the activity feed names the failing rung | Use a bigger VM preset, or close idle terminals. |
| **GitHub clone failure** | The repository is private, the URL is wrong, or egress is blocked | `dc exec worker git ls-remote https://github.com/<OWNER>/<REPO>` | RepoLab supports public repositories only. Check that the security group's egress rule allows HTTPS. |
| **Certificate failure** (`init_tls.sh` fails) | DNS doesn't point at the VM yet, port 80 is closed, or the rate limit was hit | `dig +short <DOMAIN>`, then `curl -I http://<DOMAIN>/.well-known/acme-challenge/x` (should be 404 from nginx) | Fix DNS or the firewall, wait, and retry. Let's Encrypt rate-limits repeated failures for an hour. |
| **Disk full** | Images, build checkpoints and leftover repository containers | `df -h /`, `docker system df` | `docker container prune` and `docker builder prune`. **Not** `docker image prune -a`: it deletes Cassandra and experience checkpoints that Postgres still points to, and they'd need rebuilding (minutes for Cassandra). |
| **Container restart loop** | A crash at startup | `dc ps` (look for "Restarting"), then `dc logs --tail 100 <service>` | For nginx, a config error appears at the top of its log. For api or worker, usually a bad `.env` value or Postgres not reachable. |

## Isolation model

What RepoLab's production deployment isolates, and what it does not. This is
**container isolation on one VM, not a security sandbox.**

```
Cloud VM (one kernel)
 ├── control plane (trusted)        nginx, web, api, worker ×2, postgres, redis
 │     api, worker ──► /var/run/docker.sock   (root-equivalent on this VM)
 │
 └── repository runtime (untrusted) Spark jobs, Cassandra nodes, live apps,
       experiences, learner snippets
         ✕ no Docker socket
         ✕ no metadata service       (REPOLAB-CONTAINERS iptables chain)
         ✕ no other cloud-internal hosts (10/8, 172.16/12, 192.168/16, 100.64/10
                                         via the VM's NIC)
         ✕ no RepoLab ports          (every control-plane service binds 127.0.0.1)
         ✓ DNS to the VM's resolver, internet egress (builds need it)
```

| Path | Hardening (from `repolab/sandbox/` and `repolab/runtime/`) |
|---|---|
| Learner code (experience snippets, hardened Spark variants) | no network, `--cap-drop ALL`, `no-new-privileges`, pids 512, 4 CPUs, 4 GB |
| Live app services | internal network only, `--cap-drop ALL`, non-root user, pids 512, 2 CPUs, 3 GB |
| Spark playground and experiment runs | memory 4 GB only: network on, root inside the container |
| Build ladder (Cassandra) | memory limit only |

Every container shares the VM's kernel, so a kernel exploit in repository code
reaches the control plane. The stronger options (Nebius Sandboxes once the
account is entitled, or a dedicated execution VM) are under
[Future architecture](#future-architecture).

The firewall is installed by `scripts/deploy/bootstrap_<provider>.sh` and
rebuilt at every boot and Docker restart (`repolab-container-firewall.service`).
To inspect it: `sudo iptables -S REPOLAB-CONTAINERS`.

## Security checklist

Verify each item before calling the deployment ready.

| Item | How it's ensured | How to check |
|---|---|---|
| No secrets committed | `.env` is gitignored; `.dockerignore` keeps it out of images | `git status`; `docker run --rm repolab-backend:latest ls /app/.env` fails |
| No secrets in the frontend | Only `PUBLIC_URL/api` is inlined | `make check-secrets` locally; grep the bundle for key prefixes |
| Postgres not public | Published on `127.0.0.1` only | `ss -tlnp \| grep 5432` shows `127.0.0.1` |
| Redis not public | `127.0.0.1` only, plus a password | `ss -tlnp \| grep 6379` |
| FastAPI internal | `--host 127.0.0.1` | `ss -tlnp \| grep 8000` |
| Next.js internal | `-H 127.0.0.1` | `ss -tlnp \| grep 3000` |
| HTTPS enabled | Your provider guide's HTTPS section; HSTS on | `curl -I https://<DOMAIN>` |
| SSH restricted | Cloud firewall rule for 22; key-only login | Your provider guide's firewall section |
| Repository execution isolated | See [Isolation model](#isolation-model) | The isolation probes in `check_deployment.sh` |
| Cloud-internal hosts unreachable | `REPOLAB-CONTAINERS` drops private ranges out of the NIC | `sudo iptables -S REPOLAB-CONTAINERS` |
| Only api/worker hold the socket; nothing privileged | Compose file; checked live | `check_deployment.sh`, "Containers" section |
| No unnecessary privilege | No service is `privileged`; repository containers never get the socket | `docker inspect` the repository containers: no socket mount |
| Metadata service unreachable | `REPOLAB-CONTAINERS` chain; no service account on the VM | Isolation probes; `sudo iptables -S REPOLAB-CONTAINERS` |
| CORS correct | Same-origin; `CORS_ORIGINS` defaults to dev origins only | `curl -H 'Origin: https://evil.example' -I https://<DOMAIN>/api/health` has no `access-control-allow-origin` |
| Debug off | No `--reload`; `next start`; `NEXT_PUBLIC_REPOLAB_DEBUG` unset | `dc ps` commands |

## Known limitations

- **No separate sandbox.** Repository code runs in sibling Docker containers on
  the same VM. Nebius Sandboxes are blocked by an account entitlement, and the
  code path for them exists but is unused.
  - The Spark playground and Spark experiment containers are not fully
    hardened: they have the default network, the root user, and a memory limit
    only.
  - The learner-code paths are hardened: no network, all capabilities dropped,
    and a pids limit.
  - The api and worker hold the Docker socket, which is root-equivalent on the
    VM. Their containers therefore run as root.
- **Live web-app previews** need wildcard DNS and a wildcard certificate (see
  the HTTPS section of your provider guide).
- **One API process.** Terminal sessions live in its memory, so the API can't be
  replicated as it stands.
- **Deploys interrupt running jobs.** They are reported as interrupted; submit
  again.
- **Checkpoint images accumulate** and are never garbage-collected
  automatically. Watch the disk.

## Future architecture

None of this is implemented; it is the migration path.

```
Now                                   Later
one VM (Nebius or Compute Engine)     cloud load balancer
  └── Docker Compose                    ├── web replicas (stateless already)
      ├── nginx                         ├── api replicas  ← needs CLI sessions moved
      ├── web / api / workers           │                   out of API memory
      ├── postgres                      ├── worker pool (more RQ workers, any host)
      └── redis                         ├── managed PostgreSQL (Nebius MSP or
                                        │   Cloud SQL), TLS verified
                                        ├── Redis on its own VM, or a managed service
                                        └── execution: Nebius Sandboxes (once
                                            entitled) or a dedicated execution VM
```

Models stay on Nebius Token Factory whichever cloud hosts the application.

What already makes the move cheap:

- Postgres is the only source of truth.
- Every component reads its connection details from the environment.
- The web is stateless.
- Workers coordinate only through Redis.
- Execution already sits behind the `ExecutionEnvironment` and `SandboxProvider`
  interfaces.
