#!/usr/bin/env bash
set -u

REPO_ROOT="${AI_STACK_REPO_ROOT:-/mnt/c/Repositories/ai-stack}"
LOG_FILE="$REPO_ROOT/codex/audit/wsl-boot-ai-stack.log"
LOCK_DIR="/tmp/ai-stack-wsl-boot.lock"

run_boot() {
  echo
  echo "[$(date --iso-8601=seconds)] WSL AI Stack boot start"
  echo "repo=$REPO_ROOT"
  echo "euid=$EUID"

  if [ ! -d "$REPO_ROOT" ]; then
    echo "BOOT_SKIP_REPO_MISSING path=$REPO_ROOT"
    return 0
  fi

  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "BOOT_SKIP_ALREADY_RUNNING lock=$LOCK_DIR"
    return 0
  fi
  trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

  echo "Starting Docker service"
  /usr/sbin/service docker start || {
    echo "BOOT_FAIL_DOCKER_SERVICE"
    return 1
  }

  echo "Waiting for Docker socket"
  for _ in $(seq 1 60); do
    [ -S /var/run/docker.sock ] && break
    sleep 1
  done

  if [ ! -S /var/run/docker.sock ]; then
    echo "BOOT_FAIL_DOCKER_SOCKET_MISSING"
    return 1
  fi

  echo "Starting Codex/OpenCode stack"
  "$REPO_ROOT/codex/bin/start_codex_stack.sh" || {
    echo "BOOT_FAIL_CODEX_STACK"
    return 1
  }

  echo "[$(date --iso-8601=seconds)] WSL AI Stack boot OK"
}

main() {
  mkdir -p "$(dirname "$LOG_FILE")"

  if [ "${1:-}" = "--background" ]; then
    nohup "$0" --foreground >> "$LOG_FILE" 2>&1 &
    exit 0
  fi

  run_boot >> "$LOG_FILE" 2>&1
}

main "$@"
