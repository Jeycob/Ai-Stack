#!/usr/bin/env bash
set -euo pipefail

ROOT="/mnt/c/Repositories/ai-stack"
GATEWAY="$ROOT/codex/gateway/gateway.py"
STATE_ROOT="$ROOT/codex/state"
AUDIT_ROOT="$ROOT/codex/audit"
PID_FILE="$STATE_ROOT/codex-gateway.pid"

mkdir -p "$STATE_ROOT" "$AUDIT_ROOT"

echo "[watch] gateway watcher started"

LAST=""

while true; do
  CURRENT="$(stat -c '%Y:%s' "$GATEWAY" 2>/dev/null || true)"

  if [ -n "$CURRENT" ] && [ "$CURRENT" != "$LAST" ]; then
    LAST="$CURRENT"

    echo "[$(date '+%F %T')] gateway.py changed, validating..." | tee -a "$AUDIT_ROOT/gateway-watch.log"

    if python3 -m py_compile "$GATEWAY"; then
      echo "[$(date '+%F %T')] validation OK, restarting gateway..." | tee -a "$AUDIT_ROOT/gateway-watch.log"

      if [ -f "$PID_FILE" ]; then
        kill "$(cat "$PID_FILE")" >/dev/null 2>&1 || true
      fi

      pkill -f "$GATEWAY" >/dev/null 2>&1 || true

      OPENCODE_PASS_FILE="$STATE_ROOT/opencode-smoke.pass" \
      OPENCODE_URL="http://127.0.0.1:4096" \
      nohup python3 "$GATEWAY" > "$AUDIT_ROOT/gateway.log" 2>&1 &

      echo $! > "$PID_FILE"

      sleep 1
      curl -sS http://127.0.0.1:9101/health | tee -a "$AUDIT_ROOT/gateway-watch.log" || true
      echo | tee -a "$AUDIT_ROOT/gateway-watch.log"
    else
      echo "[$(date '+%F %T')] validation FAILED, gateway not restarted" | tee -a "$AUDIT_ROOT/gateway-watch.log"
    fi
  fi

  sleep 2
done
