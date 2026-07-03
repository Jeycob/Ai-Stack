#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BRANCH="${AI_STACK_BRANCH:-main}"
REMOTE="${AI_STACK_REMOTE:-origin}"

cd "$REPO_ROOT"

section() {
  printf '\n[%s] %s\n' "$(date -Is)" "$*"
}

tracked_clean() {
  git diff --quiet && git diff --cached --quiet
}

wait_http() {
  local label="$1"
  local url="$2"
  local attempts="${3:-60}"
  local delay="${4:-1}"

  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "${label}=ready attempts=${i}"
      return 0
    fi
    sleep "$delay"
  done

  echo "${label}=not_ready attempts=${attempts} url=${url}"
  return 1
}

restart_only() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "DEPLOY_BLOCKED_ROOT_REQUIRED"
    echo "Restarting Docker services requires root or passwordless sudo for this script."
    exit 126
  fi

  section "Restarting Codex/OpenCode stack"
  bash "$REPO_ROOT/codex/bin/start_codex_stack.sh"

  section "Recreating OpenWebUI container"
  docker compose up -d --force-recreate open-webui

  section "Post-restart smoke checks"
  wait_http gateway http://127.0.0.1:9101/health 45 1
  wait_http openwebui http://127.0.0.1:9090/ 90 1
  wait_http openwebui_loader http://127.0.0.1:9090/static/loader.js 90 1
  curl -fsS http://127.0.0.1:9101/health
  echo
  tmp_loader="$(mktemp)"
  curl -fsS http://127.0.0.1:9090/static/loader.js -o "$tmp_loader"
  printf 'loader_bytes='
  wc -c < "$tmp_loader" | tr -d ' '
  rm -f "$tmp_loader"

  if [ -f "$REPO_ROOT/codex/bin/check_ai_stack.sh" ]; then
    section "Full stack healthcheck"
    OPENWEBUI_URL="${OPENWEBUI_URL:-http://127.0.0.1:9090}" \
    CODEX_GATEWAY_URL="${CODEX_GATEWAY_URL:-http://127.0.0.1:9101}" \
    OLLAMA_URL="${OLLAMA_URL:-http://192.168.0.48:11434}" \
    WORKSPACE="${WORKSPACE:-ai-stack}" \
    MODEL="${MODEL:-codex-local-plan-qwen14b}" \
    TIMEOUT="${TIMEOUT:-10}" \
    bash "$REPO_ROOT/codex/bin/check_ai_stack.sh"
  fi
}

sync_openwebui_function() {
  local key_file="$REPO_ROOT/codex/state/openwebui-api.key"
  if [ -z "${OWUI_API_KEY:-}" ] && [ -f "$key_file" ]; then
    OWUI_API_KEY="$(tr -d '\r\n' < "$key_file")"
    export OWUI_API_KEY
  fi

  if [ -z "${OWUI_API_KEY:-}" ]; then
    echo "OPENWEBUI_FUNCTION_SYNC_SKIPPED"
    echo "No OWUI_API_KEY env var or codex/state/openwebui-api.key file was found."
    return 0
  fi

  section "Syncing OpenWebUI admin filter function"
  python3 "$REPO_ROOT/codex/bin/sync_openwebui_function.py" \
    --function-id codex_gateway_admin_filter \
    --source codex/bin/openwebui_gateway_admin_filter.py

  if [ -f "$REPO_ROOT/codex/bin/openwebui_codex_auto_tools_filter.py" ]; then
    section "Syncing OpenWebUI auto tools filter function"
    python3 "$REPO_ROOT/codex/bin/sync_openwebui_function.py" \
      --function-id codex_auto_tools_filter \
      --source codex/bin/openwebui_codex_auto_tools_filter.py
  fi
}

if [ "${1:-}" = "--restart-only" ]; then
  restart_only
  exit 0
fi

section "AI Stack deploy started"
echo "repo=$REPO_ROOT"
echo "remote=$REMOTE"
echo "branch=$BRANCH"
echo "user=$(id -un)"
echo "euid=${EUID:-$(id -u)}"

git config --global --add safe.directory "$REPO_ROOT" >/dev/null 2>&1 || true

section "Preflight git status"
git status --short --branch

if ! tracked_clean; then
  echo "DEPLOY_BLOCKED_DIRTY_TRACKED_FILES"
  echo "Commit, stash, or discard tracked local changes before automated deploy."
  git status --short
  exit 20
fi

before="$(git rev-parse --short HEAD)"

section "Pulling latest git revision"
git fetch "$REMOTE" "$BRANCH"
git pull --ff-only "$REMOTE" "$BRANCH"
after="$(git rev-parse --short HEAD)"
echo "before=$before"
echo "after=$after"

section "Validating Python sources"
python3 -m py_compile \
  codex/gateway/gateway.py \
  codex/bin/openwebui_gateway_admin_filter.py \
  codex/bin/openwebui_codex_auto_tools_filter.py \
  codex/bin/http_retry.py \
  codex/bin/gateway_admin.py \
  codex/bin/owui_chat_turn.py \
  codex/bin/owui_chat_scenarios.py \
  codex/bin/run_check.py \
  codex/bin/add_workspace.py \
  codex/bin/workspace_scan.py \
  codex/bin/workspace_action.py \
  codex/bin/mentor_codex_local.py \
  codex/bin/sync_openwebui_function.py

section "Validating shell helpers"
bash -n \
  codex/bin/check_ai_stack.sh \
  codex/bin/store_runtime_secret.sh \
  codex/bin/store_openwebui_api_key.sh \
  codex/bin/owui_request.sh \
  codex/bin/deploy_ai_stack.sh

section "Restart phase"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  restart_only
else
  set +e
  section "Trying passwordless sudo restart"
  sudo -n "$SCRIPT_PATH" --restart-only
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    section "Trying WSL root interop restart"
    WSL_EXE="${WSL_EXE:-/mnt/c/Windows/System32/wsl.exe}"
    DISTRO="${WSL_DEPLOY_DISTRO:-${WSL_DISTRO_NAME:-Ubuntu}}"
    if [ -x "$WSL_EXE" ]; then
      set +e
      "$WSL_EXE" -d "$DISTRO" -u root -e bash "$SCRIPT_PATH" --restart-only
      rc=$?
      set -e
    fi
  fi
  if [ "$rc" -ne 0 ]; then
    echo "DEPLOY_BLOCKED_ROOT_RESTART_REQUIRED"
    echo "The git pull and validation finished, but Docker restart needs root."
    echo "sudo is password-protected and WSL root interop did not complete the restart."
    echo "Manual fallback:"
    echo "sudo $SCRIPT_PATH --restart-only"
    echo "Optional narrow sudoers entry:"
    echo "$(id -un) ALL=(root) NOPASSWD: $SCRIPT_PATH"
    exit "$rc"
  fi
fi

sync_openwebui_function

section "AI Stack deploy finished"
echo "DEPLOY_OK"
