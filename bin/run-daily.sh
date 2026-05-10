#!/usr/bin/env bash
# Mac-side daily runner. Invoked by launchd (see com.user.london-rentals.plist.example).
# Writes ./state.db and ./site/index.html, plus a log line per run to ./logs/runs.log.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p logs

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
