# Deploying RepoLab to Google Compute Engine

One Compute Engine VM running the same Docker Compose stack as the Nebius
deployment. nginx sits in front, Let's Encrypt provides HTTPS, and GitHub
Actions redeploys on every push to `main`.

**Google Cloud only hosts the application.** Models still come from Nebius
Token Factory (Nemotron), over HTTPS from the VM. There is no Vertex AI, GKE,
Cloud Run, Cloud SQL or Memorystore.

Placeholders look like `<GCP_STATIC_IP>`, `<DOMAIN>`, `<PROJECT_ID>`, `<REGION>`,
`<ZONE>` and `<USER>`. Replace them yourself; none has a default.

Each Console step comes with the equivalent `gcloud` command. If a Console label
differs from what you see, use the command instead. You can run `gcloud` in
Cloud Shell (the `>_` icon at the top of the Console).

Operations are the same on every cloud and live in
[DEPLOY_OPERATIONS.md](DEPLOY_OPERATIONS.md): verification, smoke test, logs,
updates, CI, backup, troubleshooting and the isolation model.

---

## 0. What you are building

```
Internet ── 80/443 ──► static IP repolab-ip ──► Compute Engine VM (Ubuntu 24.04)
                                                 │
   nginx (only public listener) ── /      → web     127.0.0.1:3000
                                 └─ /api/* → api     127.0.0.1:8000  (SSE, WebSocket)
   api, worker ×2 ── postgres 127.0.0.1:5432 · redis 127.0.0.1:6379
        │
        ├── Docker socket ──► repository containers (Spark, Cassandra, experiences)
        │                       ✕ no socket  ✕ no metadata server  ✕ no VPC hosts
        └── HTTPS ──► Nebius Token Factory (Nemotron) · Tavily · GitHub
```

Why the VM is set up the way it is, and what isolation it does and doesn't
give you, is covered in
[DEPLOY_OPERATIONS.md → Isolation model](DEPLOY_OPERATIONS.md#isolation-model).

## 1. Create or select a project

In the [Google Cloud Console](https://console.cloud.google.com), use the
project picker at the top: select a project, or create one with **New project**.
Note its ID as `<PROJECT_ID>`. A dedicated project for RepoLab keeps its IAM and
network separate from your other work, which is recommended.

```bash
gcloud config set project <PROJECT_ID>
```

## 2. Enable Compute Engine

Open **Compute Engine** in the Console. The first visit asks you to enable the
API; billing must be enabled on the project.

```bash
gcloud services enable compute.googleapis.com
```

## 3. Reserve a static external IPv4 address

Go to **VPC network → IP addresses → Reserve external static IP address**:

| Field | Value |
|---|---|
| Name | `repolab-ip` |
| Network service tier | Premium |
| IP version | IPv4 |
| Type | **Regional**, in the region the VM will use (`<REGION>`, e.g. `europe-west4`) |

Click **Reserve** and note the address as `<GCP_STATIC_IP>`. An address that is
reserved but not attached to a VM is billed at a higher rate.

```bash
gcloud compute addresses create repolab-ip --region=<REGION>
gcloud compute addresses describe repolab-ip --region=<REGION> --format='value(address)'
```

## 4. Create the VM

Go to **Compute Engine → VM instances → Create instance**:

| Setting | Value | Why |
|---|---|---|
| Name | `repolab` | |
| Region / zone | `<REGION>` / `<ZONE>` | Same region as `repolab-ip`. |
| Machine type | **`e2-standard-8`** (8 vCPU, 32 GB) | Memory is the constraint: two Cassandra terminals at 4 GB each, a 6 GB build ladder, four 4 GB Spark variants, and ~5 GB of services. `n2-standard-8` is the same size and faster. |
| Boot disk | **Ubuntu 24.04 LTS, x86/64**, **Balanced persistent disk, 200 GB** | Base images (~9 GB) plus build checkpoints, which are kept. |
| Networking → network tags | `repolab-web` | Section 5's firewall rule targets this tag. |
| Networking → external IPv4 | `repolab-ip` | The reserved address. |
| **Service account** | **none** | Read the note below. |

**Service account.** RepoLab uses no Google Cloud APIs. Its credentials are
plain environment variables. The Compute Engine default service account may be
granted the **Editor** role on the project, and any process on the VM can fetch
its token from the metadata server. The repository containers are blocked from
the metadata server (section 10). RepoLab's own services and anyone with a shell
on the VM are not. So:

- In the **Security** section (*Identity and API access*), choose not to attach
  a service account, if the Console offers that option. Otherwise, attach a
  dedicated service account that has **no roles**, and set access scopes to the
  minimum.
- **Never** attach a service account with Owner or Editor.
- `bootstrap_gcp.sh` warns you if one is attached.

The same VM as one command (`--no-service-account --no-scopes` attaches none):

```bash
gcloud compute instances create repolab \
  --zone=<ZONE> \
  --machine-type=e2-standard-8 \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=200GB --boot-disk-type=pd-balanced \
  --address=repolab-ip \
  --tags=repolab-web \
  --no-service-account --no-scopes
```

If the image family is rejected, list the current ones:
`gcloud compute images list --filter="family~ubuntu-2404"`.

## 5. Configure the firewall

Go to **VPC network → Firewall**. The `default` network comes with these rules:

| Rule | Allows | Do |
|---|---|---|
| `default-allow-ssh` | tcp:22 from `0.0.0.0/0` | **Keep.** The CI deploy needs SSH from GitHub's runners, which have no fixed IP. Login is key-only. |
| `default-allow-rdp` | tcp:3389 from `0.0.0.0/0` | **Delete.** Not a Windows VM. |
| `default-allow-icmp` | icmp | Keep. Path-MTU discovery needs it. |
| `default-allow-internal` | everything from `10.128.0.0/9` | Keep, or tighten. RepoLab's services listen on `127.0.0.1` only, and repository containers can't reach VPC hosts (section 10). |

Create one rule for the web with **Create firewall rule**:

| Field | Value |
|---|---|
| Name | `repolab-web` |
| Direction / action | Ingress / Allow |
| Targets | Specified target tags: `repolab-web` |
| Source IPv4 ranges | `0.0.0.0/0` |
| Protocols and ports | TCP `80`, `443` |

```bash
gcloud compute firewall-rules create repolab-web \
  --network=default --direction=INGRESS --action=ALLOW \
  --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=repolab-web
gcloud compute firewall-rules delete default-allow-rdp
```

**Never open** 3000 (Next.js), 8000 (FastAPI), 5432 (Postgres) or 6379
(Redis). They listen on `127.0.0.1`, so an open rule would expose nothing
today, but opening them would remove a layer of protection.

**Stricter SSH, if you give up CI deploys.** Replace `default-allow-ssh` with a
rule allowing tcp:22 only from `35.235.240.0/20`, Google's IAP TCP-forwarding
range. Then connect through the browser SSH button or
`gcloud compute ssh --tunnel-through-iap`, and deploy by hand
([DEPLOY_OPERATIONS.md → Update](DEPLOY_OPERATIONS.md#update-the-deployment)).

## 6. SSH into the VM

Use any one of:

- **Browser.** On the VM instances page, click **SSH** next to `repolab`.
- **gcloud.** `gcloud compute ssh repolab --zone=<ZONE>`
- **Plain ssh.** Add your public key to the VM (or project) SSH keys, then run
  `ssh <USER>@<GCP_STATIC_IP>`.

The username you land as is `<USER>` from here on (`echo "$USER"`).

## 7. Install Git

```bash
sudo apt-get update && sudo apt-get install -y git
```

## 8. Create a read-only GitHub deploy key

This key is how **the VM clones the private RepoLab repository**. It has
nothing to do with RepoLab's product behaviour: the workers clone *public*
repositories that users submit, over HTTPS, with no credentials.

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/repolab_deploy -C "repolab-gcp"
cat ~/.ssh/repolab_deploy.pub
```

On GitHub, go to **Repository → Settings → Deploy keys → Add deploy key**,
paste the key, and leave **Allow write access off**.

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/repolab_deploy
  IdentitiesOnly yes
EOF
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
ssh -T git@github.com    # "You've successfully authenticated" (read-only)
```

## 9. Clone RepoLab

```bash
sudo install -d -o "$USER" -g "$USER" /opt/repolab
git clone git@github.com:GautamGottipati/RepoLab.git /opt/repolab
cd /opt/repolab
```

## 10. Run the GCP bootstrap

```bash
sudo bash scripts/deploy/bootstrap_gcp.sh "$USER"
```

The script is idempotent, so running it again is safe. It starts nothing and
reads no secrets.

**Shared with the Nebius bootstrap** (`scripts/deploy/lib/common.sh`):
- Docker Engine and the compose plugin, from Docker's official apt repository.
- Docker log rotation (20 MB × 5 per container).
- A Docker network MTU equal to the VM's NIC MTU. Google's VPC default is
  1460; Docker's default of 1500 makes large downloads inside containers stall.
- Adds `<USER>` to the `docker` group.
- `/var/lib/repolab`, the state directory. The worker bind-mounts directories
  under it into Spark containers, so the path must be identical inside and
  outside the containers. Do not move it.
- Timers for certificate renewal and a daily Postgres backup.

**GCP-specific** (`scripts/deploy/bootstrap_gcp.sh`):
- **The container firewall** (`REPOLAB-CONTAINERS` iptables chain), rebuilt at
  every boot. Google's metadata server, `169.254.169.254`, hands out
  service-account tokens. It is *also* the VM's DNS resolver, so the Nebius
  approach of blocking all link-local addresses would break DNS inside every
  container. On GCP:
  - DNS (port 53) to `169.254.169.254` is allowed.
  - Everything else to `169.254.0.0/16` is dropped, along with the IPv6 metadata
    address `fd20:ce::254`.
  - Other hosts in your VPC (10/8, 172.16/12, 192.168/16, 100.64/10) are dropped
    for traffic leaving through the NIC.

  This applies only to containers on Docker bridges. RepoLab's own services use
  the host network and are unaffected.
- A check that this is Compute Engine, and a **warning if a service account is
  attached**.

## 11. Log out and back in

That makes the docker group take effect:

```bash
exit
```

Then reconnect as in section 6.

## 12. Verify Docker

```bash
docker version
docker compose version
sudo iptables -S REPOLAB-CONTAINERS    # the container firewall
```

Both docker commands should work without `sudo`.

## 13. Configure `.env`

```bash
cd /opt/repolab
cp .env.example .env
chmod 600 .env
openssl rand -hex 32    # run twice: once for each password below
nano .env
```

| Variable | First deployment (by IP) | Notes |
|---|---|---|
| `NEBIUS_API_KEY` | your Token Factory key | Models stay on Nebius. |
| `NEBIUS_BASE_URL` | keep the default | |
| `MODEL_NANO`, `MODEL_SUPER`, `MODEL_ULTRA` | Token Factory model IDs | |
| `TAVILY_API_KEY` | your Tavily key | Optional. |
| `REPOLAB_DOMAIN` | `<GCP_STATIC_IP>` | |
| `PUBLIC_URL` | `http://<GCP_STATIC_IP>` | The only value the browser sees. It is built into the web app as `NEXT_PUBLIC_API_URL=PUBLIC_URL/api`. |
| `POSTGRES_PASSWORD` | generated | |
| `REDIS_PASSWORD` | generated | |
| `LETSENCRYPT_EMAIL` | your email | Needed for section 18. |

Never put a key, token or password in a `NEXT_PUBLIC_*` variable.
`DATABASE_URL` and `REDIS_URL` are built by the compose file, so leave the
local values alone.

## 14. Start production

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

The first build takes several minutes. Startup order:

1. Postgres and Redis become healthy.
2. `migrate` runs `alembic upgrade head` and exits.
3. The api and two workers start.
4. web and nginx start.

## 15. Check the containers

```bash
docker compose -f docker-compose.prod.yml ps
```

Expect `postgres`, `redis`, `api`, `web` and `nginx` **healthy**, two `worker`
containers **running**, and `migrate` **exited (0)**.

## 16. Open RepoLab

Browse to `http://<GCP_STATIC_IP>`. If it doesn't load, check the `repolab-web`
firewall rule and the VM's network tag (section 5).

## 17. Configure DNS

At your DNS provider, create the record:

```
<DOMAIN>.      A     <GCP_STATIC_IP>
```

Optional, for live web-app previews only:

```
*.<DOMAIN>.    A     <GCP_STATIC_IP>
```

Wait until `dig +short <DOMAIN>` prints `<GCP_STATIC_IP>`.

## 18. Configure HTTPS

This step is the same on every cloud: Let's Encrypt over port 80.

1. Edit `.env` and set:

   ```
   REPOLAB_DOMAIN=<DOMAIN>
   LETSENCRYPT_EMAIL=<EMAIL>
   ```

2. Get the certificate:

   ```bash
   docker compose -f docker-compose.prod.yml up -d nginx
   bash scripts/deploy/init_tls.sh
   ```

3. Edit `.env` again and set:

   ```
   PUBLIC_URL=https://<DOMAIN>
   RUNTIME_PROXY_DOMAIN=<DOMAIN>
   RUNTIME_PROXY_SCHEME=https
   RUNTIME_PROXY_ORIGIN_PORT=443
   ```

4. Rebuild, because `PUBLIC_URL` is part of the web build:

   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

Renewal is automatic: `systemctl list-timers repolab-certbot-renew.timer`.
Live web-app previews (`rt-*.<DOMAIN>`) also need a wildcard certificate (see
[Known limitations](DEPLOY_OPERATIONS.md#known-limitations)).

## 19. Run the deployment check

```bash
bash scripts/check_deployment.sh
```

It never prints secrets. On GCP it also checks:

- that the `REPOLAB-CONTAINERS` chain is installed;
- that a repository container can't reach `169.254.169.254:80` or
  `metadata.google.internal`;
- that containers can still resolve DNS and reach the internet over HTTPS;
- that Token Factory is reachable from the API container;
- that only `api` and `worker` hold the Docker socket and nothing runs
  privileged.

To also make real (small, billed) Nemotron and Tavily calls:

```bash
CHECK_LIVE=1 bash scripts/check_deployment.sh
```

Details:
[DEPLOY_OPERATIONS.md → Verify](DEPLOY_OPERATIONS.md#verify-the-deployment).

## 20. End-to-end smoke test

Follow
[DEPLOY_OPERATIONS.md → Smoke test](DEPLOY_OPERATIONS.md#smoke-test):

1. Import the Spark demo repository and watch the progressive artifacts.
2. Refresh mid-analysis. The same job is restored and history replays.
3. Run the playground in a container and check its measurements come from
   Spark's event log.
4. Ask RepoLab why the job uses 200 partitions, approve the experiment, and
   check the explanation is grounded.

## 21. Logs and troubleshooting

The commands are in
[DEPLOY_OPERATIONS.md → Logs](DEPLOY_OPERATIONS.md#logs) and the full table in
[Troubleshooting](DEPLOY_OPERATIONS.md#troubleshooting). GCP-specific problems:

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| The site times out from the internet, though it works from the VM (`curl -I http://127.0.0.1`) | No 80/443 rule, or the VM lacks the `repolab-web` tag | VPC network → Firewall; the VM's network tags | Section 5. |
| Downloads inside containers (pip, npm, git) hang partway through | MTU mismatch | `ip link show ens4` (1460) against `docker network inspect bridge -f '{{index .Options "com.docker.network.driver.mtu"}}'` | Re-run `sudo bash scripts/deploy/bootstrap_gcp.sh "$USER"`, then `docker compose -f docker-compose.prod.yml up -d`. |
| Builds inside containers fail with name-resolution errors | The container firewall lost its DNS exception, or a stale rule remains | `sudo iptables -S REPOLAB-CONTAINERS` should show `--dport 53 -j RETURN` for `169.254.169.254` before the `169.254.0.0/16` DROP | `sudo systemctl restart repolab-container-firewall`. |
| `check_deployment.sh` says the metadata server is reachable | The firewall isn't installed (fresh boot before Docker, or the bootstrap was never run) | `systemctl status repolab-container-firewall` | `sudo systemctl restart repolab-container-firewall`, then re-run the check. |
| The bootstrap warns that a service account is attached | The VM was created with the default service account | The VM's details page → API and identity management | Stop the VM, remove the service account (or switch to one with no roles), then start it. |
| GitHub Actions deploy fails at SSH | `default-allow-ssh` was removed or narrowed, or the CI key was overwritten | The workflow log; `~/.ssh/authorized_keys` on the VM | Section 5. If you use metadata-managed SSH keys or OS Login, add the CI key through them instead of editing `authorized_keys`. |
| Token Factory is unreachable from the API container | Egress blocked, or DNS failing | `docker compose -f docker-compose.prod.yml exec api python -c "import urllib.request; urllib.request.urlopen('https://api.tokenfactory.nebius.com/v1/models', timeout=10)"`. A `401` error means it is reachable. | Check the VPC's egress firewall rules; the default allows all egress. |

## 22. Updating the deployment

By hand on the VM:

```bash
cd /opt/repolab
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
bash scripts/check_deployment.sh
```

Or automatically on every push to `main`. The workflow is cloud-neutral: the
same `.github/workflows/deploy.yml` with secrets `VM_HOST=<GCP_STATIC_IP>` (or
`<DOMAIN>`), `VM_USER=<USER>`, `VM_SSH_KEY` and `VM_KNOWN_HOSTS`. Setup is in
[DEPLOY_OPERATIONS.md → Update](DEPLOY_OPERATIONS.md#update-the-deployment).
`VM_KNOWN_HOSTS` is the output of `ssh-keyscan -t ed25519 <GCP_STATIC_IP>`.

Migrations run automatically on every `up`, through the `migrate` service.

## 23. Backup

A daily `pg_dump` runs at 02:30 UTC and keeps the last 7 dumps in
`/var/lib/repolab/backups`. To take one now:

```bash
bash scripts/deploy/backup_postgres.sh
```

**Those dumps are on the VM's own disk and don't survive losing it.** For that:

- Copy them off the VM:
  `gcloud compute scp repolab:/var/lib/repolab/backups/<FILE>.dump . --zone=<ZONE>`
- Or snapshot the boot disk:
  `gcloud compute snapshots create repolab-$(date +%Y%m%d) --source-disk=repolab --source-disk-zone=<ZONE>`

Restore steps:
[DEPLOY_OPERATIONS.md → Backup and restore](DEPLOY_OPERATIONS.md#backup-and-restore).

## 24. Security limitations

**This is container isolation on one VM, not a sandbox:**

```
Compute Engine VM (one Linux kernel)
 ├── RepoLab control plane        nginx · web · api · workers · postgres · redis
 │     api, worker hold /var/run/docker.sock  → root-equivalent on this VM
 └── repository runtime containers   Spark · Cassandra · live apps · experiences
       no Docker socket · no metadata server · no VPC hosts · no RepoLab ports
```

- **A shared kernel.** A container escape or kernel exploit in repository code
  reaches the whole VM, including RepoLab's secrets in `.env`. Docker
  isolation is not a VM boundary.
- **Some repository containers have fewer restrictions.** Spark playground and
  experiment containers have internet egress and run as root inside the
  container, with a 4 GB memory limit. Learner-code paths are stricter: no
  network, all capabilities dropped, a pids limit.
- **The control plane holds the Docker socket.** api and worker need it to start
  repository containers, so their compromise is a host compromise. Repository
  containers never get it, and `check_deployment.sh` verifies that.
- **Google Cloud IAM governs the host.** Anyone who can set instance metadata
  (SSH keys, startup scripts) or holds OS Admin Login can become root on the VM.
  Keep the project's IAM tight, and use a dedicated project.
- **The service-account risk** is described in section 4.
- **Backups are local only** (section 23).
- **The long-term fix** is to move repository execution off this VM, to a
  dedicated execution VM or a real sandbox (Nebius Sandboxes once the account is
  entitled). Not implemented yet: see
  [Future architecture](DEPLOY_OPERATIONS.md#future-architecture).
