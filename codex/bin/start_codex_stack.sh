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

verify_gateway_contract() {
  local health_file="$STATE_ROOT/gateway-health.json"
  if ! curl -fsS http://127.0.0.1:9101/health -o "$health_file"; then
    echo "gateway_health_fetch_failed=true" >&2
    return 1
  fi
  if ! python3 - "$health_file" <<'PY'
import json, sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
issues = []
if data.get("ok") is not True:
    issues.append("health_ok_false")
if data.get("capability_mode") != "agent-first":
    issues.append("capability_mode")
if data.get("natural_codex_local_route") != "agent_loop":
    issues.append("natural_codex_local_route")
if data.get("codex_local_ready") is not True:
    issues.append("codex_local_ready")
if not str(data.get("runtime_fingerprint") or "").strip():
    issues.append("runtime_fingerprint")
model = data.get("model_runtime") or {}
if model.get("default_alias") != "codex-local":
    issues.append("model_runtime.default_alias")
if "structured_attempt_timeout" not in model:
    issues.append("model_runtime.structured_attempt_timeout")
if "structured_backend_usable" not in model:
    issues.append("model_runtime.structured_backend_usable")
if issues:
    print("CODEX_GATEWAY_CONTRACT_FAILED")
    print("issues=" + ",".join(issues))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print("CODEX_GATEWAY_CONTRACT_OK")
print("runtime_fingerprint=" + str(data.get("runtime_fingerprint") or "").strip())
PY
  then
    echo "--- gateway /health ---" >&2
    cat "$health_file" >&2 || true
    echo "--- end gateway /health ---" >&2
    return 1
  fi
}

kill_gateway_port_owner() {
  python3 - <<'PY'
import os
import signal
import time

PORT_HEX = format(9101, "04X")


def socket_inodes_for_port():
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(table, encoding="ascii").read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            try:
                _addr, port = parts[1].rsplit(":", 1)
            except ValueError:
                continue
            if port.upper() == PORT_HEX:
                inodes.add(parts[9])
    return inodes


def pids_for_inodes(inodes):
    pids = set()
    if not inodes:
        return pids
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                pids.add(int(pid))
                break
    return pids


pids = pids_for_inodes(socket_inodes_for_port())
for sig in (signal.SIGTERM, signal.SIGKILL):
    signaled = False
    for pid in sorted(pids):
        try:
            os.kill(pid, sig)
            signaled = True
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"gateway_port_kill_permission_denied pid={pid}", flush=True)
    if not signaled:
        break
    time.sleep(0.5)
PY
}

mkdir -p "$STATE_ROOT" "$AUDIT_ROOT"
chown -R "$AI_USER:$AI_USER" "$STATE_ROOT" "$AUDIT_ROOT" || true

if getent group docker >/dev/null 2>&1; then
  if ! id -nG "$AI_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    usermod -aG docker "$AI_USER" >/dev/null 2>&1 || true
  fi
fi

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
kill_gateway_port_owner || true

runuser -u "$AI_USER" -- bash -lc "PYTHONPATH='$CODEX_ROOT/bin' OPENCODE_PASS_FILE='$PASS_FILE' CODEX_WORKSPACES_FILE='$WORKSPACES_FILE' CODEX_GATEWAY_ADMIN_TOKEN_FILE='$ADMIN_TOKEN_FILE' nohup python3 '$GATEWAY' > '$AUDIT_ROOT/gateway.log' 2>&1 & echo \$! > '$STATE_ROOT/codex-gateway.pid'"

gateway_pid="$(cat "$STATE_ROOT/codex-gateway.pid" 2>/dev/null || true)"
for i in {1..30}; do
  if curl -fsS http://127.0.0.1:9101/health >/dev/null 2>&1; then
    if ! verify_gateway_contract; then
      gateway_diag
      exit 1
    fi
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
