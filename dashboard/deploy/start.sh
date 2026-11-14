#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export COMPOSE_FILE=docker-compose.yml:dashboard/deploy/compose.local.yml
export COMPOSE_PROJECT_NAME=somaa-local
make dev
export SMA_DB_HOST=127.0.0.1 SMA_DB_PORT=15432 SMA_DASHBOARD_SYNTHETIC=true
export PYTHONPATH="$PWD"
mkdir -p .local-run
setsid .venv/bin/python -m streamlit run dashboard/app.py \
  --server.address 127.0.0.1 --server.port 8501 --server.headless true \
  --theme.base light --theme.primaryColor '#1D4ED8' \
  --theme.backgroundColor '#FFFFFF' --theme.secondaryBackgroundColor '#F4F7FC' \
  --theme.textColor '#18243D' \
  --browser.gatherUsageStats false &
dashboard_pid=$!
echo "$dashboard_pid" > .local-run/dashboard.pid
trap 'kill "$dashboard_pid" 2>/dev/null || true' EXIT INT TERM
wait "$dashboard_pid"
