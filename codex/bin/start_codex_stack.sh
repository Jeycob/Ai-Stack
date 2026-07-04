#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="${AI_STACK_REPO_ROOT:-${REPO_ROOT:-$DEFAULT_REPO_ROOT}}"
CODEX_ROOT="$REPO_ROOT/codex"
WORKSPACES_FILE="$CODEX_ROOT/workspaces.json"
CONFIG_FILE="$CODEX_ROOT/opencode-default.json"
GATEWAY="$CODEX_ROOT/gateway/gateway.py"
STATE_ROOT="$CODEX_ROOT/state"
AUDIT_ROOT="$CODEX_ROOT/audit"
PASS_FILE="$STATE_ROOT/opencode-smoke.pass"
ADMIN_TOKEN_FILE="$STATE_ROOT/codex-gateway-admin.token"
IMAGE="ghcr.io/anomalyco/opencode"

resolve_ai_user() {
  local requested="${AI_USER:-}"
  local repo_owner=""
  local current_user=""
  if repo_owner="$(stat -c '%U' "$REPO_ROOT" 2>/dev/null)"; then
    :
  else
    repo_owner=""
  fi
  current_user="$(id -un 2>/dev/null || true)"

  local candidates=(
    "$requested"
    "${SUDO_USER:-}"
    "${USER:-}"
    "$repo_owner"
    "$current_user"
  )
  local candidate=""
  for candidate in "${candidates[@]}"; do
    candidate="${candidate:-}"
    [ -n "$candidate" ] || continue
    [ "$candidate" = "UNKNOWN" ] && continue
    if id -u "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

AI_USER="${AI_USER:-}"
if ! AI_USER="$(resolve_ai_user)"; then
  echo "CODEX_STACK_USER_RESOLUTION_FAILED" >&2
  echo "repo_root=$REPO_ROOT" >&2
  echo "requested_ai_user=${AI_USER:-}" >&2
  echo "sudo_user=${SUDO_USER:-}" >&2
  echo "current_user=$(id -un 2>/dev/null || true)" >&2
  exit 2
fi

if [ "${1:-}" = "--print-config" ]; then
  python3 - <<PY
import json
print(json.dumps({
    "script_path": ${SCRIPT_PATH@Q},
    "repo_root": ${REPO_ROOT@Q},
    "code_root": ${CODEX_ROOT@Q},
    "workspace_file": ${WORKSPACES_FILE@Q},
    "state_root": ${STATE_ROOT@Q},
    "audit_root": ${AUDIT_ROOT@Q},
    "ai_user": ${AI_USER@Q},
}, ensure_ascii=False, indent=2))
PY
  exit 0
fi

launch_gateway() {
  local command="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='$CODEX_ROOT/bin' OPENCODE_PASS_FILE='$PASS_FILE' CODEX_WORKSPACES_FILE='$WORKSPACES_FILE' CODEX_GATEWAY_ADMIN_TOKEN_FILE='$ADMIN_TOKEN_FILE' nohup python3 -B '$GATEWAY' > '$AUDIT_ROOT/gateway.log' 2>&1 & echo \$! > '$STATE_ROOT/codex-gateway.pid'"
  if [ "$AI_USER" = "root" ] || [ "$(id -u)" -ne 0 ]; then
    bash -lc "$command"
  else
    runuser -u "$AI_USER" -- bash -lc "$command"
  fi
}

clear_gateway_bytecode_cache() {
  find "$CODEX_ROOT" \
    \( -path "$CODEX_ROOT/gateway/__pycache__" -o -path "$CODEX_ROOT/bin/__pycache__" \) \
    -type d -prune -exec rm -rf {} + >/dev/null 2>&1 || true
}

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
  if ! PYTHONPATH="$REPO_ROOT:$CODEX_ROOT/bin" \
    CODEX_WORKSPACES_FILE="$WORKSPACES_FILE" \
    CODEX_GATEWAY_ADMIN_TOKEN_FILE="$ADMIN_TOKEN_FILE" \
    python3 - "$health_file" <<'PY'
import json, sys
from codex.gateway import gateway

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
expected_epoch = str(getattr(gateway, "GATEWAY_SOURCE_EPOCH", "") or "").strip()
expected_fingerprint = str(gateway.runtime_fingerprint() or "").strip()
issues = []
if data.get("ok") is not True:
    issues.append("health_ok_false")
if data.get("capability_mode") != "agent-first":
    issues.append("capability_mode")
if data.get("natural_codex_local_route") != "agent_loop":
    issues.append("natural_codex_local_route")
if data.get("codex_local_ready") is not True:
    issues.append("codex_local_ready")
source_epoch = str(data.get("gateway_source_epoch") or "").strip()
runtime_fingerprint = str(data.get("runtime_fingerprint") or "").strip()
if not source_epoch:
    issues.append("gateway_source_epoch")
elif source_epoch != expected_epoch:
    issues.append(f"gateway_source_epoch_mismatch:{source_epoch}!={expected_epoch}")
if not runtime_fingerprint:
    issues.append("runtime_fingerprint")
elif runtime_fingerprint != expected_fingerprint:
    issues.append(f"runtime_fingerprint_mismatch:{runtime_fingerprint}!={expected_fingerprint}")
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
    print("expected_gateway_source_epoch=" + expected_epoch)
    print("expected_runtime_fingerprint=" + expected_fingerprint)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print("CODEX_GATEWAY_CONTRACT_OK")
print("gateway_source_epoch=" + source_epoch)
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
if pids:
    print("gateway_port_owner_pids=" + ",".join(str(pid) for pid in sorted(pids)), flush=True)

for sig in (signal.SIGTERM, signal.SIGKILL):
    current = pids_for_inodes(socket_inodes_for_port())
    if not current:
        break
    signaled = False
    for pid in sorted(current):
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

remaining = pids_for_inodes(socket_inodes_for_port())
if remaining:
    print("gateway_port_still_listening=true", flush=True)
    print("gateway_port_remaining_pids=" + ",".join(str(pid) for pid in sorted(remaining)), flush=True)
    raise SystemExit(1)
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
kill_gateway_port_owner
clear_gateway_bytecode_cache

launch_gateway

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
