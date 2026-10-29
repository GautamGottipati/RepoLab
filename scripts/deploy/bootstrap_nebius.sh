#!/usr/bin/env bash
# One-time setup of a Nebius AI Cloud VM (Ubuntu 24.04) for RepoLab.
#
#   sudo bash scripts/deploy/bootstrap_nebius.sh "$USER"
#
# Idempotent. Shared setup (Docker, log rotation, MTU, state directory, timers)
# is in lib/common.sh. Nebius-specific here:
#   - the container firewall for Nebius's metadata service
#     (metadata.nebius.internal), which issues service-account tokens
#   - a warning if the VM has a service account attached
# Starts nothing and reads no secrets. See docs/DEPLOY_NEBIUS.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
REPO_DIR="$(cd "$HERE/../.." && pwd)"

check_nebius_identity() {
    log "Nebius identity"
    local code
    # The response would contain a token: only the status code is read.
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 -H "Metadata: true" \
        http://metadata.nebius.internal/v1/iam/sa 2>/dev/null || true)"
    case "$code" in
        200)
            warn "a service account is attached to this VM. RepoLab needs none."
            warn "Repository containers cannot reach the metadata service, but RepoLab's"
            warn "own services and any shell on this VM can obtain its token."
            ;;
        000 | "") warn "metadata.nebius.internal did not answer; is this a Nebius VM?" ;;
        *) echo "    no service account attached (recommended)" ;;
    esac
}

if [ "${REPOLAB_BOOTSTRAP_DRY_RUN:-0}" != 1 ]; then
    require_root
fi
DEPLOY_USER="$(resolve_deploy_user "${1:-}")"

bootstrap_common "$DEPLOY_USER" "$REPO_DIR"
step check_nebius_identity
step install_container_firewall nebius "$HERE/lib/common.sh"
step write_provider_marker nebius

echo
echo "Done. Log out and back in so '${DEPLOY_USER}' picks up the docker group."
echo "Next: configure ${REPO_DIR}/.env (docs/DEPLOY_NEBIUS.md, section 11)."
