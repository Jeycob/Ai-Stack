#!/usr/bin/env bash
set -euo pipefail

AI_USER="${AI_USER:-sklenik}"
REPO_ROOT="/mnt/c/Repositories/ai-stack"
CODEX_ROOT="$REPO_ROOT/codex"
WORKSPACES_FILE="$CODEX_ROOT/workspaces.json"
CONFIG_FILE="$CODEX_ROOT/opencode-default.json"
GATEWAY="$CODEX_ROOT/gateway/gateway.py"
STATE_ROOT="$CODEX_ROOT/state"
AUDIT_ROOT="$CODEX_ROOT/audit"
PASS_FILE="$STATE_ROOT/opencode-smoke.pass"
ADMIN_TOKEN_FILE="$STATE_ROOT/codex-gateway-admin.token"
IMAGE="ghcr.io/anomalyco/opencode"

gateway_diag() {
  echo "GATEWAY_START_FAILED" >&2
  echo "gateway=$GATEWAY" >&2
  echo "gateway_log=$AUDIT_ROOT/gateway.log" >&2
  if [ -f "$STATE_ROOT/codex-gateway.pid" ]; then
    pid="$(cat "$STATE_ROOT/codex-gateway.pid" 2>/dev/null || true)"
    echo "gateway_pid=${pid:-unknown}" >&2
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      echo "gateway_process=running" >&2
    else
      echo "gateway_process=not-running" >&2
    fi
  else
    echo "gateway_pid_file=missing" >&2
  fi

  if [ -f "$AUDIT_ROOT/gateway.log" ]; then
    echo "--- gateway.log tail ---" >&2
    tail -n 80 "$AUDIT_ROOT/gateway.log" >&2 || true
    echo "--- end gateway.log tail ---" >&2
  else
    echo "gateway_log_missing=true" >&2
  fi
}

mkdir -p "$STATE_ROOT" "$AUDIT_ROOT"
chown -R "$AI_USER:$AI_USER" "$STATE_ROOT" "$AUDIT_ROOT" || true

if [ ! -f "$PASS_FILE" ]; then
  openssl rand -hex 24 > "$PASS_FILE"
fi
chown "$AI_USER:$AI_USER" "$PASS_FILE" || true
chmod 600 "$PASS_FILE" || true

if [ ! -f "$ADMIN_TOKEN_FILE" ]; then
  openssl rand -hex 32 > "$ADMIN_TOKEN_FILE"
fi
chown "$AI_USER:$AI_USER" "$ADMIN_TOKEN_FILE" || true
chmod 600 "$ADMIN_TOKEN_FILE" || true

OPENCODE_PASS="$(cat "$PASS_FILE")"
AI_UID="$(id -u "$AI_USER")"
AI_GID="$(id -g "$AI_USER")"

docker pull "$IMAGE" >/dev/null

python3 - "$WORKSPACES_FILE" <<'PY'
import json, re, sys
data=json.load(open(sys.argv[1]))
ports=set()
for name,cfg in data.get("workspaces",{}).items():
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise SystemExit(f"Invalid workspace name: {name}")
    port=int(cfg["port"])
    if port in ports:
        raise SystemExit(f"Duplicate port: {port}")
    ports.add(port)
PY

while IFS=$'\t' read -r name path port cpus memory; do
  cname="codex-opencode-$name"
  home_dir="$STATE_ROOT/opencode-home-$name"

  if [ ! -d "$path" ]; then
    echo "Workspace path does not exist: $path" >&2
    exit 1
  fi

  mkdir -p "$home_dir"
  chown -R "$AI_USER:$AI_USER" "$home_dir" || true

  if docker ps --format '{{.Names}}' | grep -qx "$cname"; then
    if curl -fsS -u "opencode:$OPENCODE_PASS" "http://127.0.0.1:$port/global/health" >/dev/null 2>&1; then
      echo "$cname OK"
      continue
    fi
    echo "$cname unhealthy or password changed; recreating"
    docker rm -f "$cname" >/dev/null 2>&1 || true
  fi

  docker rm -f "$cname" >/dev/null 2>&1 || true

  docker run -d \
    --name "$cname" \
    --restart unless-stopped \
    --user "$AI_UID:$AI_GID" \
    --cpus "$cpus" \
    --memory "$memory" \
    --pids-limit 512 \
    --security-opt no-new-privileges \
    --cap-drop ALL \
    -e HOME=/home/opencode \
    -e OPENCODE_SERVER_USERNAME=opencode \
    -e OPENCODE_SERVER_PASSWORD="$OPENCODE_PASS" \
    -e OPENCODE_CONFIG=/etc/opencode/opencode.json \
    -v "$home_dir:/home/opencode:rw" \
    -v "$path:/workspace:rw" \
    -v "$CONFIG_FILE:/etc/opencode/opencode.json:ro" \
    -w /workspace \
    -p "127.0.0.1:$port:4096" \
    "$IMAGE" serve --hostname 0.0.0.0 --port 4096

  ready=0
  for i in {1..60}; do
    if curl -fsS -u "opencode:$OPENCODE_PASS" "http://127.0.0.1:$port/global/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" -ne 1 ]; then
    echo "OpenCode workspace did not become healthy: $name on port $port" >&2
    docker logs --tail=80 "$cname" >&2 || true
    exit 1
  fi
done < <(python3 - "$WORKSPACES_FILE" <<'PY'
import json, sys
data=json.load(open(sys.argv[1]))
for name,cfg in data["workspaces"].items():
    print("\t".join([
        name, cfg["path"], str(cfg.get("port",4096)),
        str(cfg.get("cpus",8)), str(cfg.get("memory","16g"))
    ]))
PY
)

if [ -f "$STATE_ROOT/codex-gateway.pid" ]; then
  kill "$(cat "$STATE_ROOT/codex-gateway.pid")" >/dev/null 2>&1 || true
fi
pkill -f "$GATEWAY" >/dev/null 2>&1 || true

runuser -u "$AI_USER" -- bash -lc "PYTHONPATH='$CODEX_ROOT/bin' OPENCODE_PASS_FILE='$PASS_FILE' CODEX_WORKSPACES_FILE='$WORKSPACES_FILE' CODEX_GATEWAY_ADMIN_TOKEN_FILE='$ADMIN_TOKEN_FILE' nohup python3 '$GATEWAY' > '$AUDIT_ROOT/gateway.log' 2>&1 & echo \$! > '$STATE_ROOT/codex-gateway.pid'"

gateway_pid="$(cat "$STATE_ROOT/codex-gateway.pid" 2>/dev/null || true)"
for i in {1..30}; do
  if curl -fsS http://127.0.0.1:9101/health >/dev/null 2>&1; then
    echo "Codex gateway OK"
    echo "Codex stack OK"
    exit 0
  fi
  if [ -n "$gateway_pid" ] && ! kill -0 "$gateway_pid" >/dev/null 2>&1; then
    gateway_diag
    exit 1
  fi
  sleep 1
done

gateway_diag
exit 1
