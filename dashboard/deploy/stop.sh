#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
runtime="$PWD"
if [[ -f .local-run/dashboard.pid ]]; then
  dashboard_pid=$(cat .local-run/dashboard.pid)
  if [[ "$dashboard_pid" =~ ^[0-9]+$ ]] && [[ -d "/proc/$dashboard_pid" ]]; then
    process_cwd=$(readlink "/proc/$dashboard_pid/cwd")
    process_args=$(tr '\0' ' ' < "/proc/$dashboard_pid/cmdline")
    if [[ "$process_cwd" == "$runtime" && "$process_args" == *"streamlit run dashboard/app.py"* ]]; then
      kill "$dashboard_pid"
    fi
  fi
fi
export COMPOSE_FILE=docker-compose.yml:dashboard/deploy/compose.local.yml
export COMPOSE_PROJECT_NAME=somaa-local
docker compose --profile dev down
