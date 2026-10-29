#!/usr/bin/env bash
# One-time setup of a Google Compute Engine VM (Ubuntu 24.04, amd64) for RepoLab.
#
#   sudo bash scripts/deploy/bootstrap_gcp.sh "$USER"
#
# Idempotent. Shared setup (Docker, log rotation, MTU, state directory, timers)
# is in lib/common.sh. GCP-specific here:
#   - the container firewall for Google's metadata server, which is also the
#     VM's DNS resolver (169.254.169.254): DNS allowed, everything else dropped
#   - a warning if the VM has a service account attached
# Starts nothing and reads no secrets. See docs/DEPLOY_GCP.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
REPO_DIR="$(cd "$HERE/../.." && pwd)"

METADATA=http://169.254.169.254/computeMetadata/v1

# Read the metadata server from the host (allowed; containers are not).
gcp_metadata() {
    curl -fsS -m 3 -H "Metadata-Flavor: Google" "$METADATA/$1" 2>/dev/null
}

check_gcp_identity() {
    log "Compute Engine identity"
    if ! gcp_metadata instance/id >/dev/null; then
        warn "the GCE metadata server did not answer; is this a Compute Engine VM?"
        return 0
    fi
    local accounts
    accounts="$(gcp_metadata instance/service-accounts/ | tr -d '/' | grep -v '^default$' || true)"
    if [ -z "$accounts" ]; then
        echo "    no service account attached (recommended)"
        return 0
    fi
    warn "service account attached: ${accounts}"
    warn "scopes: $(gcp_metadata instance/service-accounts/default/scopes | tr '\n' ' ')"
    warn "RepoLab needs no GCP APIs. Repository containers cannot reach the metadata"
    warn "server, but RepoLab's own services and any shell on this VM can obtain this"
    warn "account's token. Remove it from the VM (it must be stopped to change it)."
}

if [ "${REPOLAB_BOOTSTRAP_DRY_RUN:-0}" != 1 ]; then
    require_root
fi
DEPLOY_USER="$(resolve_deploy_user "${1:-}")"

bootstrap_common "$DEPLOY_USER" "$REPO_DIR"
step check_gcp_identity
step install_container_firewall gcp "$HERE/lib/common.sh"
step write_provider_marker gcp

cat <<MSG

Done. Log out and back in so '${DEPLOY_USER}' picks up the docker group.

Google Cloud firewall (VPC network -> Firewall), if not done yet:
  - allow tcp:80,443 from 0.0.0.0/0 to this VM (target tag repolab-web)
  - delete default-allow-rdp; keep default-allow-ssh (key-only login)
  - never open 3000, 8000, 5432 or 6379

Next: configure ${REPO_DIR}/.env (docs/DEPLOY_GCP.md, section 13).
MSG
