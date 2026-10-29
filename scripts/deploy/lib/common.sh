# shellcheck shell=bash
# Shared VM setup for RepoLab's production deployment, used by the provider
# scripts (bootstrap_nebius.sh, bootstrap_gcp.sh). Sourced, never executed.
#
# Everything here is cloud-neutral except container_firewall_rules, which takes
# the provider and returns its rules. Every function is idempotent.
#
# Test seams (environment overrides, so tests need no VM):
#   REPOLAB_BOOTSTRAP_DRY_RUN=1   step() prints instead of running
#   REPOLAB_RESOLV_CONF           upstream resolvers (default: systemd-resolved's)
#   REPOLAB_EXTERNAL_IFACE        the host's default-route interface
#   REPOLAB_EXTERNAL_MTU          that interface's MTU
#   REPOLAB_NEBIUS_METADATA_IP    instead of resolving metadata.nebius.internal
#   DOCKER_DAEMON_JSON            default /etc/docker/daemon.json

REPOLAB_STATE=/var/lib/repolab
REPOLAB_LIB_INSTALL=/usr/local/lib/repolab/common.sh
REPOLAB_PROVIDER_MARKER=/etc/repolab/provider

#: The chain all container rules live in; rebuilt on every apply.
REPOLAB_CHAIN=REPOLAB-CONTAINERS

#: Address ranges a repository container never needs to reach through the VM's
#: own network interface: other machines in the cloud network.
REPOLAB_PRIVATE_RANGES="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10"

log() { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

# Run a named step, or only announce it in dry-run mode.
step() {
    if [ "${REPOLAB_BOOTSTRAP_DRY_RUN:-0}" = 1 ]; then
        printf 'step: %s\n' "$*"
        return 0
    fi
    "$@"
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "run with sudo" >&2
        exit 1
    fi
}

# The non-root user who deploys (the one you SSH in as).
resolve_deploy_user() {
    local user="${1:-${SUDO_USER:-}}"
    if [ -z "$user" ] || [ "$user" = root ]; then
        echo "usage: sudo bash $0 <deploy-user>   (the non-root user you SSH in as)" >&2
        exit 1
    fi
    printf '%s' "$user"
}

check_os() {
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" != ubuntu ] || [ "${VERSION_ID:-}" != "24.04" ]; then
        warn "tested on Ubuntu 24.04; this is ${PRETTY_NAME:-unknown}"
    fi
    if [ "$(dpkg --print-architecture)" != amd64 ]; then
        warn "tested on amd64; this is $(dpkg --print-architecture)"
    fi
}

# Docker Engine + compose plugin from Docker's official apt repository.
install_docker() {
    log "Docker Engine, compose plugin, git"
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        echo "    already installed: $(docker --version)"
        return 0
    fi
    apt-get update
    apt-get install -y ca-certificates curl git python3
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    local codename
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
}

external_iface() {
    # Set, even to empty, wins: tests can simulate a host with no default route.
    if [ -n "${REPOLAB_EXTERNAL_IFACE+set}" ]; then
        printf '%s' "$REPOLAB_EXTERNAL_IFACE"
        return
    fi
    ip -4 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}'
}

external_mtu() {
    if [ -n "${REPOLAB_EXTERNAL_MTU:-}" ]; then
        printf '%s' "$REPOLAB_EXTERNAL_MTU"
        return
    fi
    local iface
    iface="$(external_iface)"
    [ -n "$iface" ] && cat "/sys/class/net/${iface}/mtu" 2>/dev/null
}

# Merge RepoLab's settings into daemon.json. Adds what is missing; never changes
# a key the operator already set (a differing value is reported, not replaced).
# Prints "changed" or "unchanged".
#
# Log rotation keeps container logs from filling the disk. The MTU matters on
# networks below 1500 (Google's VPC default is 1460): containers otherwise send
# packets the network drops, and large downloads inside them stall.
merge_docker_daemon_json() {
    local path="${DOCKER_DAEMON_JSON:-/etc/docker/daemon.json}" mtu
    mtu="$(external_mtu)"
    python3 - "$path" "${mtu:-}" <<'PY'
import json, os, sys

path, mtu = sys.argv[1], sys.argv[2]
current = {}
if os.path.exists(path):
    with open(path) as handle:
        text = handle.read().strip()
    current = json.loads(text) if text else {}

wanted = {"log-driver": "json-file", "log-opts": {"max-size": "20m", "max-file": "5"}}
if mtu.isdigit() and int(mtu) < 1500:
    wanted["mtu"] = int(mtu)
    wanted["default-network-opts"] = {"bridge": {"com.docker.network.driver.mtu": mtu}}

merged = dict(current)
for key, value in wanted.items():
    if key not in current:
        merged[key] = value
    elif current[key] != value:
        print(f"note: daemon.json already sets {key}; left as is", file=sys.stderr)

if merged == current:
    print("unchanged")
else:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as handle:
        json.dump(merged, handle, indent=2)
        handle.write("\n")
    print("changed")
PY
}

configure_docker_daemon() {
    log "Docker daemon: log rotation$( [ -n "$(external_mtu)" ] && printf ', MTU %s' "$(external_mtu)")"
    if [ "$(merge_docker_daemon_json)" = changed ]; then
        systemctl restart docker
        echo "    daemon.json updated; Docker restarted"
    else
        echo "    daemon.json already configured"
    fi
}

add_docker_user() {
    log "Docker group for $1"
    usermod -aG docker "$1"
}

create_state_dirs() {
    log "State directory ${REPOLAB_STATE}"
    install -d -m 0755 "$REPOLAB_STATE" "$REPOLAB_STATE/state" "$REPOLAB_STATE/letsencrypt" "$REPOLAB_STATE/acme"
    install -d -m 0700 "$REPOLAB_STATE/backups"
}

write_provider_marker() {
    install -d -m 0755 "$(dirname "$REPOLAB_PROVIDER_MARKER")"
    printf '%s\n' "$1" > "$REPOLAB_PROVIDER_MARKER"
}

# IPv4 nameservers the host forwards to (never the 127.0.0.53 stub).
upstream_resolvers() {
    local conf="${REPOLAB_RESOLV_CONF:-}"
    if [ -z "$conf" ]; then
        conf=/run/systemd/resolve/resolv.conf
        [ -f "$conf" ] || conf=/etc/resolv.conf
    fi
    awk '$1 == "nameserver" && $2 ~ /^[0-9.]+$/ && $2 !~ /^127\./ {print $2}' "$conf" 2>/dev/null
}

nebius_metadata_ip() {
    if [ -n "${REPOLAB_NEBIUS_METADATA_IP:-}" ]; then
        printf '%s' "$REPOLAB_NEBIUS_METADATA_IP"
        return
    fi
    getent ahostsv4 metadata.nebius.internal 2>/dev/null | awk 'NR == 1 {print $1}'
}

# The rules for traffic from containers on Docker bridges, one per line as
# "<iptables|ip6tables> <match...> -j <RETURN|DROP>". RETURN lines come first,
# so DNS exceptions win; RETURN (not ACCEPT) leaves Docker's own isolation
# rules in force. Host-network services are unaffected: their traffic never
# crosses the FORWARD chain these rules sit in.
container_firewall_rules() {
    local provider="$1" iface ns range meta
    iface="$(external_iface)"
    {
        # DNS to the host's own resolvers, wherever they live.
        for ns in $(upstream_resolvers); do
            echo "iptables -d ${ns}/32 -p udp --dport 53 -j RETURN"
            echo "iptables -d ${ns}/32 -p tcp --dport 53 -j RETURN"
        done
        case "$provider" in
            gcp)
                # Google's metadata server is also the VM's DNS resolver: keep
                # DNS, refuse everything else (tokens are served over HTTP).
                echo "iptables -d 169.254.169.254/32 -p udp --dport 53 -j RETURN"
                echo "iptables -d 169.254.169.254/32 -p tcp --dport 53 -j RETURN"
                ;;
        esac
    } | awk '!seen[$0]++'

    case "$provider" in
        gcp)
            echo "iptables -d 169.254.0.0/16 -j DROP"
            echo "ip6tables -d fd20:ce::254/128 -j DROP"
            ;;
        nebius)
            echo "iptables -d 169.254.0.0/16 -j DROP"
            meta="$(nebius_metadata_ip)"
            [ -n "$meta" ] && echo "iptables -d ${meta}/32 -j DROP"
            ;;
        *)
            echo "unknown provider: ${provider} (expected gcp or nebius)" >&2
            return 1
            ;;
    esac

    # Other machines in the cloud network. Matched on the outgoing interface, so
    # traffic between containers on Docker's own bridges is untouched.
    if [ -n "$iface" ]; then
        for range in $REPOLAB_PRIVATE_RANGES; do
            echo "iptables -o ${iface} -d ${range} -j DROP"
        done
    else
        echo "warning: no default-route interface found; private ranges not blocked" >&2
    fi
}

# Rebuild the RepoLab chain from container_firewall_rules. Called at boot by the
# systemd unit and whenever Docker restarts.
apply_container_firewall() {
    local provider="$1" rules tool tools=iptables
    rules="$(container_firewall_rules "$provider")" || return 1
    command -v ip6tables >/dev/null 2>&1 && tools="iptables ip6tables"

    for tool in $tools; do
        "$tool" -N DOCKER-USER 2>/dev/null || true
        "$tool" -N "$REPOLAB_CHAIN" 2>/dev/null || "$tool" -F "$REPOLAB_CHAIN"
        "$tool" -C DOCKER-USER -j "$REPOLAB_CHAIN" 2>/dev/null || "$tool" -I DOCKER-USER -j "$REPOLAB_CHAIN"
    done
    # Rules the first bootstrap_vm.sh put directly in DOCKER-USER; now in the chain.
    while iptables -D DOCKER-USER -d 169.254.0.0/16 -j DROP 2>/dev/null; do :; done

    while read -r tool match; do
        [ -n "$tool" ] || continue
        case " $tools " in *" $tool "*) ;; *) continue ;; esac
        # shellcheck disable=SC2086
        "$tool" -A "$REPOLAB_CHAIN" $match
    done <<<"$rules"
}

install_container_firewall() {
    local provider="$1" lib_source="$2"
    log "Container firewall (${provider}): no metadata, no cloud-internal hosts"
    install -D -m 0644 "$lib_source" "$REPOLAB_LIB_INSTALL"
    cat > /usr/local/sbin/repolab-container-firewall <<SH
#!/bin/bash
# Installed by scripts/deploy/bootstrap_${provider}.sh. Rebuilds the
# ${REPOLAB_CHAIN} iptables chain; see ${REPOLAB_LIB_INSTALL}.
set -euo pipefail
. ${REPOLAB_LIB_INSTALL}
apply_container_firewall ${provider}
SH
    chmod 0755 /usr/local/sbin/repolab-container-firewall
    cat > /etc/systemd/system/repolab-container-firewall.service <<'UNIT'
[Unit]
Description=RepoLab: keep repository containers away from metadata and cloud-internal hosts
After=docker.service
PartOf=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/repolab-container-firewall
RemainAfterExit=yes

[Install]
WantedBy=docker.service multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable repolab-container-firewall.service
    systemctl restart repolab-container-firewall.service
}

install_timers() {
    local repo_dir="$1"
    log "Certificate renewal and Postgres backup timers"
    cat > /etc/systemd/system/repolab-certbot-renew.service <<UNIT
[Unit]
Description=RepoLab: renew Let's Encrypt certificates and reload nginx
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=${repo_dir}
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml --profile tls run --rm certbot renew --webroot -w /var/www/acme --quiet
ExecStartPost=/usr/bin/docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
UNIT
    cat > /etc/systemd/system/repolab-certbot-renew.timer <<'UNIT'
[Unit]
Description=RepoLab: twice-daily certificate renewal check

[Timer]
OnCalendar=*-*-* 03,15:17:00
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    cat > /etc/systemd/system/repolab-backup.service <<UNIT
[Unit]
Description=RepoLab: Postgres backup
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=${repo_dir}
ExecStart=/bin/bash ${repo_dir}/scripts/deploy/backup_postgres.sh
UNIT
    cat > /etc/systemd/system/repolab-backup.timer <<'UNIT'
[Unit]
Description=RepoLab: daily Postgres backup

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now repolab-certbot-renew.timer repolab-backup.timer
}

# The steps every provider shares, in order. Provider scripts add theirs around it.
bootstrap_common() {
    local user="$1" repo_dir="$2"
    step check_os
    step install_docker
    step configure_docker_daemon
    step add_docker_user "$user"
    step create_state_dirs
    step install_timers "$repo_dir"
}
