#!/bin/sh
# Stop RepoLab dev processes a previous `make dev` left running -- a closed
# terminal, or a shutdown that hung -- so the next start finds :8000 and :3000
# free. Asks politely (TERM), then forces (KILL) whatever is left.

self=$$
parent=$PPID

find_pids() {
  {
    pgrep -f 'uvicorn api\.main:app'
    pgrep -f 'python3? -m worker\.main'
    pgrep -f 'RepoLab/web/node_modules/\.bin/next dev'
    lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null
    lsof -ti tcp:3000 -sTCP:LISTEN 2>/dev/null
  } | grep -vx -e "$self" -e "$parent" | sort -u | tr '\n' ' '
}

pids=$(find_pids)
[ -z "$(echo "$pids" | tr -d ' ')" ] && exit 0

echo "Stopping leftover RepoLab dev processes: $pids"
# shellcheck disable=SC2086
kill -TERM $pids 2>/dev/null
for _ in 1 2 3 4 5 6; do
  sleep 0.5
  left=$(find_pids)
  [ -z "$(echo "$left" | tr -d ' ')" ] && exit 0
done
# shellcheck disable=SC2086
kill -KILL $left 2>/dev/null
exit 0
