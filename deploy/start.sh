#!/usr/bin/env bash
set -u

mkdir -p /data/testamur /data/mathhub
MATHHUB_LOG=/data/mathhub/startup.log
touch "$MATHHUB_LOG"

run_mathhub() {
  while true; do
    {
      printf '\n[%s] starting MathHub\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      cd /app/mathhub_app || exit 1
      MATHHUB_HOST=127.0.0.1 \
      MATHHUB_PORT=8001 \
      MATHHUB_DATA_DIR=/data/mathhub \
        python mathhub.py
      code=$?
      printf '[%s] MathHub exited with code %s; retrying in 2s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$code"
    } >>"$MATHHUB_LOG" 2>&1
    sleep 2
  done
}

run_mathhub &
MATHHUB_SUPERVISOR_PID=$!

cleanup() {
  kill "$MATHHUB_SUPERVISOR_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

exec python -m apps.web.hosted_app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --data-dir /data/testamur \
  --secure-cookie
