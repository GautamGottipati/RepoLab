#!/usr/bin/env bash
# Dump the RepoLab database to /var/lib/repolab/backups, keeping the last 7.
#
#   bash scripts/deploy/backup_postgres.sh
#
# Restore (stops nothing; review before running):
#   docker compose -f docker-compose.prod.yml exec -T postgres \
#     pg_restore -U repolab -d repolab --clean --if-exists < <backup>.dump
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

DEST=/var/lib/repolab/backups
KEEP=7
file="${DEST}/repolab-$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_dump -U repolab -d repolab --format=custom > "${file}.partial"
mv "${file}.partial" "$file"
chmod 0600 "$file"
echo "backup: $file ($(du -h "$file" | cut -f1))"

ls -1t "$DEST"/repolab-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
