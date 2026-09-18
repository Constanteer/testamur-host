#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data/testamur /data/mathhub

(
  cd /app/mathhub_app
  export MATHHUB_HOST=127.0.0.1
  export MATHHUB_PORT=8001
  export MATHHUB_DATA_DIR=/data/mathhub
  exec python mathhub.py
) &
MATHHUB_PID=$!

python -m apps.web.hosted_app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --data-dir /data/testamur \
  --secure-cookie &
HOST_PID=$!

cleanup() {
  kill "$MATHHUB_PID" "$HOST_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while kill -0 "$MATHHUB_PID" 2>/dev/null && kill -0 "$HOST_PID" 2>/dev/null; do
  sleep 2
done

if ! kill -0 "$MATHHUB_PID" 2>/dev/null; then
  wait "$MATHHUB_PID"
else
  wait "$HOST_PID"
fi
