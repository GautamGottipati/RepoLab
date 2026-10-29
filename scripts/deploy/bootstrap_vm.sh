#!/usr/bin/env bash
# Kept for VMs and notes that already use it: the Nebius bootstrap.
# New deployments: bootstrap_nebius.sh or bootstrap_gcp.sh.
exec bash "$(dirname "${BASH_SOURCE[0]}")/bootstrap_nebius.sh" "$@"
