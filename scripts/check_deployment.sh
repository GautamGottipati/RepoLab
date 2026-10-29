#!/usr/bin/env bash
# Verify a RepoLab production deployment. Read-only; prints no secret values.
#
#   bash scripts/check_deployment.sh [BASE_URL]
#
#   BASE_URL     defaults to PUBLIC_URL from .env
#   JOB_ID=...   also stream that job's events through nginx
#   CHECK_LIVE=1 also run scripts/check_services.py inside the API container,
#                which makes real (small, billed) Nemotron and Tavily calls
#
# Run on the VM from the repository directory. Exits non-zero if anything fails.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE=(docker compose -f docker-compose.prod.yml)
failures=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; failures=$((failures + 1)); }
info() { printf '  ---- %s\n' "$1"; }
check() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi; }

value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
BASE="${1:-$(value PUBLIC_URL)}"
BASE="${BASE%/}"
if [ -z "$BASE" ]; then
    echo "no BASE_URL given and PUBLIC_URL is not set in .env" >&2
    exit 2
fi
echo "RepoLab deployment check: $BASE"

echo "Containers"
for service in postgres redis api web nginx; do
    state="$("${COMPOSE[@]}" ps --format '{{.Health}}' "$service" 2>/dev/null | head -n1)"
    if [ "$state" = healthy ]; then pass "$service healthy"; else fail "$service health: ${state:-not running}"; fi
done
workers="$("${COMPOSE[@]}" ps --status running --format '{{.Name}}' worker 2>/dev/null | wc -l | tr -d ' ')"
if [ "${workers:-0}" -ge 1 ]; then pass "worker containers running: $workers"; else fail "no worker container running"; fi
migrate="$("${COMPOSE[@]}" ps -a --format '{{.State}} {{.ExitCode}}' migrate 2>/dev/null | head -n1)"
if [ "$migrate" = "exited 0" ]; then pass "migrations applied (migrate exited 0)"; else fail "migrate: ${migrate:-never ran}"; fi

echo "Through nginx"
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")"
[ "$code" = 200 ] && pass "website reachable (GET / -> 200)" || fail "website: GET / -> $code"
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/health")"
[ "$code" = 200 ] && pass "API live (/api/health -> 200)" || fail "API live: /api/health -> $code"
ready="$(curl -s -w ' %{http_code}' "$BASE/api/health/ready")"
case "$ready" in
    *" 200") pass "API ready: ${ready% *}" ;;
    *) fail "API not ready: $ready" ;;
esac
# A missing job's stream must be the API's JSON 404, not the web app's page:
# that proves the SSE route reaches FastAPI.
sse="$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "$BASE/api/jobs/no-such-job/events")"
case "$sse" in
    "404 application/json"*) pass "SSE route reaches the API" ;;
    *) fail "SSE route: $sse" ;;
esac
if [ -n "${JOB_ID:-}" ]; then
    headers="$(curl -s -N --max-time 20 -D - -o /dev/null "$BASE/api/jobs/$JOB_ID/events" 2>/dev/null | tr -d '\r')"
    if grep -qi '^content-type: text/event-stream' <<<"$headers"; then
        pass "SSE streams through nginx (job $JOB_ID)"
    else
        fail "SSE for job $JOB_ID did not return text/event-stream"
    fi
fi

echo "Data services"
check "Postgres accepts connections" "${COMPOSE[@]}" exec -T postgres pg_isready -U repolab -d repolab
pong="$("${COMPOSE[@]}" exec -T redis redis-cli ping 2>/dev/null | tr -d '\r')"
[ "$pong" = PONG ] && pass "Redis answers PING" || fail "Redis: ${pong:-no answer}"
registered="$("${COMPOSE[@]}" exec -T api python -c '
import os
from redis import Redis
from rq import Worker
print(len(Worker.all(connection=Redis.from_url(os.environ["REDIS_URL"]))))
' 2>/dev/null | tr -d '\r')"
if [ "${registered:-0}" -ge 1 ] 2>/dev/null; then
    pass "RQ workers registered: $registered"
else
    fail "no RQ worker registered in Redis"
fi

echo "Configuration (set / unset only)"
"${COMPOSE[@]}" exec -T api python -c '
from repolab.config import get_settings
s = get_settings()
rows = {
    "NEBIUS_API_KEY": s.nebius_api_key is not None,
    "NEBIUS_PROJECT_ID": bool(s.nebius_project_id),
    "MODEL_NANO": bool(s.model_nano),
    "MODEL_SUPER": bool(s.model_super),
    "MODEL_ULTRA": bool(s.model_ultra),
    "TAVILY_API_KEY": s.tavily_api_key is not None,
}
for name, present in rows.items():
    print(name, "set" if present else "unset")
print(f"RUNTIME_PROXY {s.runtime_proxy_scheme}://rt-*.{s.runtime_proxy_domain}")
' 2>/dev/null | tr -d '\r' | while read -r name state; do
    case "$name" in
        RUNTIME_PROXY) info "live apps at $state" ;;
        NEBIUS_PROJECT_ID) info "$name $state (Sandboxes only; not used by this deployment)" ;;
        *) if [ "$state" = set ]; then pass "$name set"; else info "$name unset (degrades; see docs/DEPLOY_OPERATIONS.md)"; fi ;;
    esac
done
reach="$("${COMPOSE[@]}" exec -T api python -c '
import urllib.error, urllib.request
from repolab.config import get_settings
url = get_settings().nebius_base_url.rstrip("/") + "/models"
try:
    print(urllib.request.urlopen(url, timeout=10).status)
except urllib.error.HTTPError as exc:
    print(exc.code)
except Exception:
    print(0)
' 2>/dev/null | tr -d '\r')"
case "$reach" in
    0 | "") fail "Nebius Token Factory unreachable from the API container" ;;
    *) pass "Nebius Token Factory reachable over HTTPS (unauthenticated probe: HTTP $reach)" ;;
esac
if [ "${CHECK_LIVE:-0}" = 1 ]; then
    check "live Nemotron/Tavily probes (scripts/check_services.py)" \
        "${COMPOSE[@]}" exec -T api python scripts/check_services.py
fi

echo "Execution"
version="$("${COMPOSE[@]}" exec -T worker docker info --format '{{.ServerVersion}}' 2>/dev/null | tr -d '\r')"
[ -n "$version" ] && pass "worker reaches Docker $version" || fail "worker cannot reach Docker"

# Which cloud this VM was bootstrapped for (bootstrap_<provider>.sh writes it).
PROVIDER="$(cat /etc/repolab/provider 2>/dev/null || echo unknown)"
case "$PROVIDER" in
    gcp) METADATA_NAMES="metadata.google.internal" ;;
    nebius) METADATA_NAMES="metadata.nebius.internal" ;;
    *) METADATA_NAMES="metadata.google.internal metadata.nebius.internal" ;;
esac

echo "Container firewall (provider: $PROVIDER)"
if [ "$(id -u)" -eq 0 ]; then AS_ROOT=(); else AS_ROOT=(sudo -n); fi
if rules="$("${AS_ROOT[@]}" iptables -S REPOLAB-CONTAINERS 2>/dev/null)"; then
    count="$(grep -c '^-A' <<<"$rules")"
    [ "$count" -gt 0 ] && pass "REPOLAB-CONTAINERS chain installed ($count rules)" || fail "REPOLAB-CONTAINERS chain is empty"
    if "${AS_ROOT[@]}" iptables -S DOCKER-USER 2>/dev/null | grep -q -- '-j REPOLAB-CONTAINERS'; then
        pass "DOCKER-USER jumps to REPOLAB-CONTAINERS"
    else
        fail "DOCKER-USER does not jump to REPOLAB-CONTAINERS"
    fi
else
    info "cannot read iptables without sudo; skipped (the probes below still run)"
fi

echo "Containers (control plane vs repository)"
before=$failures
while IFS='|' read -r name privileged service sock memory pids; do
    [ -n "$name" ] || continue
    name="${name#/}"
    [ "$privileged" = true ] && fail "$name runs privileged"
    if [ "$sock" = yes ]; then
        case "$service" in
            api | worker) ;;
            *) fail "$name mounts the Docker socket" ;;
        esac
    fi
    if [ -z "$service" ]; then
        # Not a compose service: a container RepoLab started for a repository.
        if [ "${memory:-0}" = 0 ]; then
            fail "repository container $name has no memory limit"
        else
            info "repository container $name: memory $((memory / 1048576)) MiB, pids ${pids:-unlimited}"
        fi
    fi
done < <(docker ps -q | xargs -r docker inspect --format \
    '{{.Name}}|{{.HostConfig.Privileged}}|{{index .Config.Labels "com.docker.compose.service"}}|{{range .Mounts}}{{if eq .Source "/var/run/docker.sock"}}yes{{end}}{{end}}|{{.HostConfig.Memory}}|{{.HostConfig.PidsLimit}}' 2>/dev/null)
if [ "$failures" -eq "$before" ]; then
    pass "only api and worker hold the Docker socket; no container is privileged"
fi

echo "Isolation (probed from a throwaway container on the default bridge)"
probe="$(METADATA_NAMES="$METADATA_NAMES" docker run --rm -i --network bridge -e METADATA_NAMES \
    repolab-backend:latest python - <<'PY' 2>&1
import os, socket, subprocess

def gateway():
    # python:slim has no iproute2; the default route is in /proc.
    for line in open("/proc/net/route").read().splitlines()[1:]:
        fields = line.split()
        if fields[1] == "00000000":
            return socket.inet_ntoa(bytes.fromhex(fields[2])[::-1])
    return ""

def connect(host, port, timeout=3):
    """'open', 'refused' (the packet arrived) or 'blocked' (no answer)."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return "open"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "blocked"

gw = gateway()
print("docker_sock", "present" if os.path.exists("/var/run/docker.sock") else "absent")
print("metadata_ip", connect("169.254.169.254", 80))
for name in os.environ.get("METADATA_NAMES", "").split():
    try:
        address = socket.gethostbyname(name)
    except OSError:
        print("metadata_name", name, "unresolved")
        continue
    print("metadata_name", name, connect(address, 80))
for port in (8000, 3000, 5432, 6379):
    print("host_port", port, connect(gw, port))
try:
    socket.gethostbyname("github.com")
    print("dns", "ok")
except OSError:
    print("dns", "failed")
print("https", connect("github.com", 443, timeout=5))
PY
)"
while read -r key a b; do
    case "$key" in
        docker_sock) [ "$a" = absent ] && pass "repository containers have no Docker socket" || fail "a repository container can see the Docker socket" ;;
        metadata_ip) [ "$a" = blocked ] && pass "metadata server (169.254.169.254:80) unreachable from containers" || fail "metadata server reachable from containers ($a)" ;;
        metadata_name)
            case "$b" in
                blocked) pass "$a unreachable from containers" ;;
                unresolved) info "$a does not resolve here (not this cloud)" ;;
                *) fail "$a reachable from containers ($b)" ;;
            esac ;;
        host_port) [ "$b" = open ] && fail "host port $a reachable from containers" || pass "host port $a unreachable from containers" ;;
        dns) [ "$a" = ok ] && pass "containers resolve DNS (github.com)" || fail "containers cannot resolve DNS: builds inside them will fail" ;;
        https) [ "$a" = open ] && pass "containers reach the internet over HTTPS" || fail "containers cannot reach github.com:443 ($a)" ;;
        *) [ -n "$key" ] && info "$key $a $b" ;;
    esac
done <<<"$probe"

echo "Host"
free_gb="$(df -BG --output=avail /var/lib/docker 2>/dev/null | tail -n1 | tr -dc '0-9')"
if [ -n "$free_gb" ]; then
    if [ "$free_gb" -ge 20 ]; then pass "disk: ${free_gb} GB free for Docker"; else fail "disk: only ${free_gb} GB free for Docker"; fi
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "All checks passed."
else
    echo "$failures check(s) failed."
fi
exit "$failures"
