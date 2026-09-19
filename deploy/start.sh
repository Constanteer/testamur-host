#!/usr/bin/env bash
set -u

mkdir -p /data/testamur /data/mathhub /data/mathhub/lake /data/lean/elan
MATHHUB_LOG=/data/mathhub/startup.log
BOOTSTRAP_LOG=/data/mathhub/bootstrap.log
touch "$MATHHUB_LOG" "$BOOTSTRAP_LOG"


if python - <<'PY'
import os
raise SystemExit(0 if os.path.ismount("/data") else 1)
PY
then
  printf '[testamur] persistent volume detected at /data\n'
else
  if [ "${TESTAMUR_ALLOW_EPHEMERAL_DATA:-}" = "1" ]; then
    printf >&2 '[testamur] WARNING: /data is not a mount point; ephemeral data explicitly allowed. Accounts, projects and history may be lost on redeploy.\n'
  else
    printf >&2 '[testamur] ERROR: /data is not a persistent mount. Refusing to start because account/project state would be ephemeral. Mount the Zeabur Volume at /data or set TESTAMUR_ALLOW_EPHEMERAL_DATA=1 only for disposable previews.\n'
    exit 78
  fi
fi

VOLUME_MARKER=/data/testamur/.volume-id
if [ ! -s "$VOLUME_MARKER" ]; then
  python - <<'PY'
from pathlib import Path
import secrets
path = Path("/data/testamur/.volume-id")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("vol_" + secrets.token_hex(16) + "\n", encoding="utf-8")
PY
fi
printf '[testamur] volume marker: %s\n' "$(tr -d '\r\n' < "$VOLUME_MARKER")"

# Keep Lean and Lake state on the persistent Zeabur volume instead of baking
# gigabytes of mathlib cache into every OCI image.
export ELAN_HOME=/data/lean/elan
if [ ! -e /app/mathhub_app/.lake ]; then
  ln -s /data/mathhub/lake /app/mathhub_app/.lake
fi

bootstrap_mathhub() {
  if [ -f /data/mathhub/.lean-ready ] && [ -d /data/mathhub/lake/packages/mathlib ]; then
    return 0
  fi
  {
    printf '\n[%s] hydrating pinned Lean/mathlib environment\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cd /app/mathhub_app || exit 1
    toolchain="$(tr -d '\r\n' < lean-toolchain)"
    printf '[%s] installing pinned Lean toolchain: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$toolchain"
    if elan toolchain install "$toolchain" \
      && elan default "$toolchain" \
      && lake update \
      && lake exe cache get; then
      touch /data/mathhub/.lean-ready
      printf '[%s] Lean/mathlib environment ready\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    else
      code=$?
      printf '[%s] Lean/mathlib bootstrap failed with code %s; MathHub stays online in read-only/runtime-unready mode\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$code"
    fi
  } >>"$BOOTSTRAP_LOG" 2>&1
}

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

# Start serving immediately; hydrate Lean/mathlib independently in the background.
bootstrap_mathhub &
BOOTSTRAP_PID=$!
run_mathhub &
MATHHUB_SUPERVISOR_PID=$!

cleanup() {
  kill "$BOOTSTRAP_PID" "$MATHHUB_SUPERVISOR_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

exec python -m apps.web.hosted_app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --data-dir /data/testamur \
  --secure-cookie
