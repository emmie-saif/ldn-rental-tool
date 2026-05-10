#!/usr/bin/env bash
# Mac-side daily runner. Invoked by launchd (see com.user.london-rentals.plist.example).
# Writes ./state.db and ./site/index.html, plus a log line per run to ./logs/runs.log.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p logs

# Concurrency guard. macOS doesn't ship flock; use a directory-create lock
# which is atomic on POSIX filesystems. Stale locks (process gone) are cleared.
LOCK_DIR="$ROOT/.run.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [ -f "$LOCK_DIR/pid" ] && ! kill -0 "$(cat "$LOCK_DIR/pid")" 2>/dev/null; then
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" || { echo "[$(date -u +%FT%TZ)] lock unavailable; exiting" >> logs/runs.log; exit 0; }
  else
    echo "[$(date -u +%FT%TZ)] another run is in progress (pid $(cat "$LOCK_DIR/pid" 2>/dev/null)); exiting" >> logs/runs.log
    exit 0
  fi
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ ! -d .venv ]; then
  /usr/bin/python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

ts="$(date -u +%FT%TZ)"
echo "[$ts] starting" >> logs/runs.log
if ./.venv/bin/python -m london_rentals.run >> logs/runs.log 2>&1; then
  echo "[$(date -u +%FT%TZ)] ok" >> logs/runs.log
else
  echo "[$(date -u +%FT%TZ)] FAILED" >> logs/runs.log
  exit 1
fi
