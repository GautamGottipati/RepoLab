# Deploying RepoLab to Nebius AI Cloud

> Hosting on Google Cloud instead? See [DEPLOY_GCP.md](DEPLOY_GCP.md). The
> application stack is identical; only the VM setup differs.

One CPU VM, Docker Compose, nginx in front, HTTPS from Let's Encrypt, and a
GitHub Actions workflow that redeploys on every push to `main`.

Placeholders look like `<VM_PUBLIC_IP>`, `<DOMAIN>` or `<PROJECT_ID>`. Replace
them yourself; none has a default.

Contents:
[1 Architecture](#1-architecture) ·
[2 Prerequisites](#2-prerequisites) ·
[3 Project](#3-create-or-select-the-nebius-project) ·
[4 Network](#4-network) ·
[5 VM](#5-create-the-vm) ·
[6 Public IP](#6-public-ip) ·
[7 Firewall](#7-firewall-security-group) ·
[8 SSH](#8-ssh-into-the-vm) ·
[9 Docker](#9-install-docker-and-prepare-the-vm) ·
[10 Clone](#10-clone-repolab) ·
[11 Environment](#11-configure-the-environment) ·
[12 Start](#12-start-the-services) ·
[13 Domain](#13-configure-the-domain) ·
[14 HTTPS](#14-configure-https) ·
[15 Verify, operate, update](#15-verify-operate-update) (→ [DEPLOY_OPERATIONS.md](DEPLOY_OPERATIONS.md))

---

## 1. Architecture

```
                 INTERNET
                    │  80 / 443 (22 from your IP only)
                    ▼
     Nebius security group  ──►  VM (cpu-d3, Ubuntu 24.04)
                    │
        nginx  (host network, the only public listener)
        ├── /                                  → web     127.0.0.1:3000  next start
        ├── /api/jobs|experiments/{id}/events  → api     127.0.0.1:8000  SSE, unbuffered
        ├── /api/cli/sessions/{id}/terminal    → api     WebSocket (cqlsh)
        ├── /api/*                             → api     prefix stripped
        └── rt-<id>-<svc>.<DOMAIN>             → api     live web apps (optional)

        api      uvicorn, 1 process        ─┐
        worker   python -m worker.main, ×2 ─┤ same image (Dockerfile)
        migrate  alembic upgrade head      ─┘ runs before api/worker start
        postgres 127.0.0.1:5432, volume postgres_data   (source of truth)
        redis    127.0.0.1:6379, AOF, password          (queues, liveness)

        repository containers: started by api/worker on the host Docker daemon
                               (Spark, Cassandra, live apps, experiences)
                    │
                    └── outbound: Nebius Token Factory (Nemotron), Tavily, GitHub
```

Why it is shaped like this:

- **Same origin.** The page is `https://<DOMAIN>` and the API is
  `https://<DOMAIN>/api`. nginx strips `/api`, so no backend route changes and
  the browser needs no CORS.
- **Host networking for RepoLab's own services.** A live app's sidecar publishes
  on the host's `127.0.0.1`, and both the API proxy and the worker dial it there.
  To compensate, every RepoLab service binds `127.0.0.1` only.
- **The Docker socket for api and worker.** Nebius Sandboxes are not enabled on
  the account (see `docs/PROGRESS.md`), so repository code runs in local Docker
  containers, exactly as in development. The API needs the socket too, because
  the cqlsh terminal runs inside the API process.
- **`/var/lib/repolab` mounted at the same path inside and outside.** The worker
  bind-mounts work directories it creates there into Spark containers. The
  Docker daemon resolves that path on the host, so it must be identical in both
  places.
- **One API process.** Terminal sessions live in its memory.

## 2. Prerequisites

- A Nebius AI Cloud account with access to the project, and permission to create
  VMs, networks and security groups.
- A Nebius Token Factory API key, and the model IDs for `MODEL_NANO`,
  `MODEL_SUPER` and `MODEL_ULTRA`. The IDs come from Token Factory, and
  `make check` validates them.
- A Tavily API key. Optional: research degrades without it.
- An SSH key pair on your laptop (`~/.ssh/id_ed25519.pub`).
- A domain whose DNS you control. Optional at first, because the site works by
  IP over HTTP.
- Admin rights on the GitHub repository, for the deploy key and Actions secrets.

## 3. Create or select the Nebius project

Open the [Nebius Console](https://console.nebius.com). Select the project
(`<PROJECT_ID>`, e.g. `project-…`) and the region you will use, e.g.
`eu-north1`. Everything below is created inside that project.

## 4. Network

A project normally has a default network and subnet; RepoLab needs nothing
more. To check, go to **Networking → Virtual Networks**. Create a network and
subnet there only if none exists.

## 5. Create the VM

Go to **Compute → Virtual machines → Create**.

| Setting | Value | Why |
|---|---|---|
| Platform | `cpu-d3` (AMD EPYC Genoa, no GPU) | Nemotron runs remotely in Token Factory, so RepoLab needs no GPU. |
| Preset | **`8vcpu-32gb`** | See the sizing note below. |
| Boot disk | `network_ssd`, **200 GiB** | Base images (~9 GB) plus build checkpoints, which are kept. |
| Image | Ubuntu 24.04 (`ubuntu24.04-driverless`) | The non-GPU public image. |
| Public IP | Yes, **static** | See section 6. |
| Access | Username plus your SSH public key | Don't use `root` or `admin` as the username; Nebius disallows them. |
| Service account | **None** | The metadata service hands a service account's token to anything on the VM. |

Sizing: memory is the constraint, not CPU. The worst case that can happen at
the same moment:

| Load | Memory |
|---|---|
| Two cqlsh terminals | 2 × 4 GB |
| One Cassandra build ladder | up to 6 GB |
| Four parallel Spark variants | 4 × 4 GB |
| Postgres, Redis, API, two workers, web, nginx | ~5 GB |

On a smaller VM, Cassandra in 2 GB dies without any log (`docs/PROGRESS.md`).
`4vcpu-16gb` is enough for a Spark-only demo if nobody opens a Cassandra
terminal during it.

## 6. Public IP

Choose a **static** public IP when you create the VM; Nebius dynamic addresses
are not persistent. Note it as `<VM_PUBLIC_IP>`.

## 7. Firewall (security group)

The network's **default security group allows all traffic, and it cannot be
changed.** Create your own group:

1. Go to **Networking → Virtual Networks → (your network) → Create → Security group**.
   Name it `repolab-web`.
2. Open it and click **Create security rule** for each rule:

   | Direction | Action | Protocol | Ports | Source |
   |---|---|---|---|---|
   | Ingress | Allow | TCP | 22 | `<YOUR_IP>/32` |
   | Ingress | Allow | TCP | 80, 443 | `0.0.0.0/0` |
   | Egress | Allow | Any | any | `0.0.0.0/0` |

3. Attach it: **Compute → Virtual machines → (VM) → Actions → Add to security groups**.

Postgres (5432), Redis (6379), the API (8000) and Next.js (3000) are **not**
opened here. They listen on `127.0.0.1` only, so they would be unreachable even
with an open rule.

## 8. SSH into the VM

```bash
ssh <USER>@<VM_PUBLIC_IP>
```

## 9. Install Docker and prepare the VM

`bootstrap_nebius.sh` is in the repository, so the repository is cloned first
(section 10) and the script is run afterwards. Install git if it is missing:

```bash
sudo apt-get update && sudo apt-get install -y git
```

Continue with section 10, then come back:

```bash
cd /opt/repolab
sudo bash scripts/deploy/bootstrap_nebius.sh "$USER"
exit                       # log out and back in to pick up the docker group
ssh <USER>@<VM_PUBLIC_IP>
docker version             # works without sudo now
```

The script is idempotent and does the following:

- Installs Docker Engine and the compose plugin from Docker's official apt
  repository.
- Sets Docker log rotation.
- Creates `/var/lib/repolab`.
- Installs a firewall rule that stops containers reaching link-local addresses,
  which include the Nebius metadata service.
- Enables two timers: certificate renewal, and a daily Postgres backup.

## 10. Clone RepoLab

The repository is private, so the VM gets a **read-only deploy key**:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/repolab_deploy -C "repolab-vm"
cat ~/.ssh/repolab_deploy.pub
```

Add the public key on GitHub under **Repository → Settings → Deploy keys → Add
deploy key**, and leave "Allow write access" **off**. Then clone:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/repolab_deploy
  IdentitiesOnly yes
EOF
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts

sudo install -d -o "$USER" -g "$USER" /opt/repolab
git clone git@github.com:GautamGottipati/RepoLab.git /opt/repolab
cd /opt/repolab
```

Now run section 9's bootstrap.

## 11. Configure the environment

```bash
cd /opt/repolab
cp .env.example .env
chmod 600 .env
openssl rand -hex 32    # run twice: once for each password below
nano .env
```

Set these in `.env`:

| Variable | Value | Notes |
|---|---|---|
| `NEBIUS_API_KEY` | your Token Factory key | Server-side only. |
| `NEBIUS_BASE_URL` | keep the default | |
| `MODEL_NANO`, `MODEL_SUPER`, `MODEL_ULTRA` | Token Factory model IDs | Never hardcoded in the code. |
| `TAVILY_API_KEY` | your Tavily key | Optional. |
| `NEBIUS_PROJECT_ID` | optional | Used only by Sandboxes, which this deployment doesn't use. |
| `REPOLAB_DOMAIN` | `<VM_PUBLIC_IP>` now, `<DOMAIN>` later | |
| `PUBLIC_URL` | `http://<VM_PUBLIC_IP>` now, `https://<DOMAIN>` later | The browser calls `PUBLIC_URL/api`. |
| `POSTGRES_PASSWORD` | a generated hex value | |
| `REDIS_PASSWORD` | a generated hex value | |
| `LETSENCRYPT_EMAIL` | your email | Needed for section 14. |

You can leave the local-infrastructure lines (`DATABASE_URL`, `REDIS_URL`) as
they are: `docker-compose.prod.yml` builds both URLs from the passwords and
overrides them.

**Browser-visible configuration.** Only `PUBLIC_URL` reaches the browser. It is
baked into the web build as `NEXT_PUBLIC_API_URL`. Never put a key, token or
password in any `NEXT_PUBLIC_*` variable, and leave `NEXT_PUBLIC_REPOLAB_DEBUG`
unset. RepoLab uses no GitHub token: it clones public repositories only.

## 12. Start the services

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

The first build takes several minutes. What happens:

1. Postgres and Redis start and report healthy.
2. `migrate` applies the Alembic migrations and exits 0.
3. The api and the two workers start.
4. web and nginx start.

Open `http://<VM_PUBLIC_IP>`.

To warm the large images before a demo (the Spark image is pulled on first use
anyway):

```bash
docker pull apache/spark:3.5.1-python3
docker pull cassandra:5.0
docker pull eclipse-temurin:17-jdk-jammy
```

## 13. Configure the domain

At your DNS provider, create the record:

```
<DOMAIN>.      A     <VM_PUBLIC_IP>
```

Optional, for live web-app previews only:

```
*.<DOMAIN>.    A     <VM_PUBLIC_IP>
```

Wait until `dig +short <DOMAIN>` prints `<VM_PUBLIC_IP>`.

## 14. Configure HTTPS

Edit `.env` and set:

```
REPOLAB_DOMAIN=<DOMAIN>
LETSENCRYPT_EMAIL=<EMAIL>
```

Then run:

```bash
docker compose -f docker-compose.prod.yml up -d nginx   # picks up the domain
bash scripts/deploy/init_tls.sh
```

When it finishes, edit `.env` again and set:

```
PUBLIC_URL=https://<DOMAIN>
RUNTIME_PROXY_DOMAIN=<DOMAIN>
RUNTIME_PROXY_SCHEME=https
RUNTIME_PROXY_ORIGIN_PORT=443
```

Rebuild, because `PUBLIC_URL` is part of the web build:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

How the pieces fit:

- nginx chooses HTTPS automatically once
  `/var/lib/repolab/letsencrypt/live/<DOMAIN>/fullchain.pem` exists.
- HTTP then redirects to HTTPS, except for the ACME challenge.
- Renewal runs twice a day: `systemctl list-timers repolab-certbot-renew.timer`.

**Live web-app previews** (`rt-*.<DOMAIN>`) need a *wildcard* certificate, which
Let's Encrypt issues only through a DNS-01 challenge. That challenge requires
your DNS provider's certbot plugin, and the script does not set it up. Without
it, every other surface works, and a live app's preview shows a certificate
warning.

## 15. Verify, operate, update

Everything from here on is the same on every cloud, and lives in
[DEPLOY_OPERATIONS.md](DEPLOY_OPERATIONS.md):

- [Verify the deployment](DEPLOY_OPERATIONS.md#verify-the-deployment): `bash scripts/check_deployment.sh`
- [Smoke test](DEPLOY_OPERATIONS.md#smoke-test)
- [Logs](DEPLOY_OPERATIONS.md#logs) and [Restart](DEPLOY_OPERATIONS.md#restart)
- [Update the deployment](DEPLOY_OPERATIONS.md#update-the-deployment), including CI on push to `main`
- [Backup and restore](DEPLOY_OPERATIONS.md#backup-and-restore)
- [Troubleshooting](DEPLOY_OPERATIONS.md#troubleshooting)
- [Isolation model](DEPLOY_OPERATIONS.md#isolation-model), [Security checklist](DEPLOY_OPERATIONS.md#security-checklist), [Known limitations](DEPLOY_OPERATIONS.md#known-limitations), [Future architecture](DEPLOY_OPERATIONS.md#future-architecture)

Nebius specifics for those pages:

- **CI SSH.** GitHub-hosted runners need port 22: widen rule 22 in section 7 to
  `0.0.0.0/0` (key-only login).
- **Disk snapshots** for whole-machine backup: snapshot the boot disk in the Nebius Console.
- **Bootstrap.** `scripts/deploy/bootstrap_vm.sh` still works; it now runs
  `bootstrap_nebius.sh`. Re-running either upgrades the container firewall to
  the `REPOLAB-CONTAINERS` chain, which also blocks other hosts in the Nebius
  network.
