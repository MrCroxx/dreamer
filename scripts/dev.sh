#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
backend_pid=""
frontend_pid=""
cleanup() {
  if [[ -n "$backend_pid" ]]; then kill "$backend_pid" 2>/dev/null || true; fi
  if [[ -n "$frontend_pid" ]]; then kill "$frontend_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
uv run uvicorn dreamer.main:app --app-dir backend --host 127.0.0.1 --port 8000 &
backend_pid=$!
npm --prefix frontend run dev -- --port "${DREAMER_FRONTEND_PORT:-5174}" --strictPort &
frontend_pid=$!
wait -n "$backend_pid" "$frontend_pid"
