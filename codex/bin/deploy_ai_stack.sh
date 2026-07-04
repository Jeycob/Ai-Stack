#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BRANCH="${AI_STACK_BRANCH:-main}"
REMOTE="${AI_STACK_REMOTE:-origin}"
RUNTIME_METADATA_STASH_REF=""
OPENWEBUI_URL="${OPENWEBUI_URL:-}"

cd "$REPO_ROOT"

resolve_openwebui_url() {
  if [ -n "${OPENWEBUI_URL:-}" ]; then
    printf '%s\n' "$OPENWEBUI_URL"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && [ -f "$REPO_ROOT/codex/bin/openwebui_runtime.py" ]; then
    OPENWEBUI_URL="$(python3 "$REPO_ROOT/codex/bin/openwebui_runtime.py" | head -n 1)"
  fi
  if [ -z "${OPENWEBUI_URL:-}" ]; then
    OPENWEBUI_URL="http://127.0.0.1:9090"
  fi
  printf '%s\n' "$OPENWEBUI_URL"
}

section() {
  printf '\n[%s] %s\n' "$(date -Is)" "$*"
}

tracked_clean() {
  git diff --quiet && git diff --cached --quiet
}

tracked_dirty_files() {
  {
    git diff --name-only
    git diff --cached --name-only
  } | awk 'NF {print}' | sort -u
}

is_runtime_local_metadata_path() {
  case "${1:-}" in
    codex/workspaces.json)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

prepare_runtime_local_metadata_stash() {
  local dirty
  mapfile -t dirty < <(tracked_dirty_files)
  if [ "${#dirty[@]}" -eq 0 ]; then
    return 0
  fi

  local blocked=()
  local path=""
  for path in "${dirty[@]}"; do
    if ! is_runtime_local_metadata_path "$path"; then
      blocked+=("$path")
    fi
  done

  if [ "${#blocked[@]}" -ne 0 ]; then
    echo "DEPLOY_BLOCKED_DIRTY_TRACKED_FILES"
    echo "Commit, stash, or discard tracked local changes before automated deploy."
    git status --short
    exit 20
  fi

  section "Stashing runtime-local metadata overrides"
  printf 'stashing_paths=%s\n' "${dirty[*]}"
  git stash push -m "ai-stack deploy runtime-local-metadata $(date +%s)" -- "${dirty[@]}" >/dev/null
  RUNTIME_METADATA_STASH_REF="$(git stash list -1 --format=%gd)"
  printf 'stash_ref=%s\n' "${RUNTIME_METADATA_STASH_REF:-none}"
}

restore_runtime_local_metadata_stash() {
  if [ -z "${RUNTIME_METADATA_STASH_REF:-}" ]; then
    return 0
  fi

  section "Restoring runtime-local metadata overrides"
  set +e
  git stash pop --index "$RUNTIME_METADATA_STASH_REF"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    echo "DEPLOY_BLOCKED_RUNTIME_METADATA_CONFLICT"
    echo "The repo updated, but restoring local runtime metadata caused a merge conflict."
    echo "Resolve and rerun:"
    echo "  git status --short"
    echo "  git stash show -p $RUNTIME_METADATA_STASH_REF"
    exit 21
  fi
  RUNTIME_METADATA_STASH_REF=""
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

gateway_runtime_fingerprint_gate() {
  local gate_log
  gate_log="$(mktemp)"

  if ! CODEX_GATEWAY_URL="http://127.0.0.1:9101" \
    python3 "$REPO_ROOT/codex/bin/gateway_runtime_fingerprint_check.py" \
      --base-url "http://127.0.0.1:9101" \
      --json >"$gate_log" 2>&1
  then
    echo "DEPLOY_BLOCKED_GATEWAY_RUNTIME_DRIFT"
    cat "$gate_log"
    rm -f "$gate_log"
    exit 23
  fi

  cat "$gate_log"
  rm -f "$gate_log"
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
  local openwebui_base
  openwebui_base="$(resolve_openwebui_url)"
  wait_http gateway http://127.0.0.1:9101/health 45 1
  section "Gateway runtime fingerprint gate"
  gateway_runtime_fingerprint_gate
  wait_http openwebui "${openwebui_base%/}/" 90 1
  wait_http openwebui_loader "${openwebui_base%/}/static/loader.js" 90 1
  curl -fsS http://127.0.0.1:9101/health
  echo
  tmp_loader="$(mktemp)"
  curl -fsS "${openwebui_base%/}/static/loader.js" -o "$tmp_loader"
  printf 'loader_bytes='
  wc -c < "$tmp_loader" | tr -d ' '
  rm -f "$tmp_loader"
}

full_stack_healthcheck() {
  if [ -f "$REPO_ROOT/codex/bin/check_ai_stack.sh" ]; then
    section "Full stack healthcheck"
    OPENWEBUI_URL="${OPENWEBUI_URL:-$(resolve_openwebui_url)}" \
    CODEX_GATEWAY_URL="${CODEX_GATEWAY_URL:-http://127.0.0.1:9101}" \
    OLLAMA_URL="${OLLAMA_URL:-http://192.168.0.48:11434}" \
    WORKSPACE="${WORKSPACE:-ai-stack}" \
    MODEL="${MODEL:-codex-local}" \
    TIMEOUT="${TIMEOUT:-8}" \
    SKIP_OWUI_CHAT_SMOKE="${SKIP_OWUI_CHAT_SMOKE:-1}" \
    SKIP_OWUI_CHAT_SCENARIOS="${SKIP_OWUI_CHAT_SCENARIOS:-1}" \
    bash "$REPO_ROOT/codex/bin/check_ai_stack.sh"
  fi
}

reconcile_openwebui_functions() {
  local key_file="$REPO_ROOT/codex/state/openwebui-api.key"
  if [ -z "${OWUI_API_KEY:-}" ] && [ -f "$key_file" ]; then
    OWUI_API_KEY="$(tr -d '\r\n' < "$key_file")"
    export OWUI_API_KEY
  fi

  if [ -z "${OWUI_API_KEY:-}" ]; then
    echo "OPENWEBUI_FUNCTION_RECONCILE_SKIPPED"
    echo "No OWUI_API_KEY env var or codex/state/openwebui-api.key file was found."
    return 0
  fi

  section "Reconciling OpenWebUI codex-local functions"
  python3 "$REPO_ROOT/codex/bin/reconcile_openwebui_functions.py"
}

if [ "${1:-}" = "--restart-only" ]; then
  restart_only
  reconcile_openwebui_functions
  exit 0
fi

if [ "${1:-}" = "--sudoers-probe" ]; then
  echo "DEPLOY_SUDOERS_PROBE_OK"
  echo "script=$SCRIPT_PATH"
  echo "euid=${EUID:-$(id -u)}"
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
  prepare_runtime_local_metadata_stash
fi

before="$(git rev-parse --short HEAD)"

section "Pulling latest git revision"
git fetch "$REMOTE" "$BRANCH"
git pull --ff-only "$REMOTE" "$BRANCH"
restore_runtime_local_metadata_stash
after="$(git rev-parse --short HEAD)"
echo "before=$before"
echo "after=$after"

section "Validating Python sources"
python3 -m py_compile \
  codex/gateway/gateway.py \
  codex/bin/openwebui_gateway_admin_filter.py \
  codex/bin/openwebui_codex_auto_tools_filter.py \
  codex/bin/codex_gateway_smoke.py \
  codex/bin/gateway_recovery_smoke.py \
  codex/bin/gateway_admin_run_workspace_smoke.py \
  codex/bin/gateway_nested_helper_rescue_smoke.py \
  codex/bin/gateway_runtime_health_smoke.py \
  codex/bin/gateway_runtime_fingerprint_check.py \
  codex/bin/docker_runner.py \
  codex/bin/container_runner_guard.py \
  codex/bin/container_runner_guard_smoke.py \
  codex/bin/mentor_capability_routing_smoke.py \
  codex/bin/owui_chat_scenario_catalog_smoke.py \
  codex/bin/owui_chat_turn_preflight_smoke.py \
  codex/bin/owui_chat_turn_visible_fallback_smoke.py \
  codex/bin/filter_route_smoke.py \
  codex/bin/http_retry.py \
  codex/bin/gateway_admin.py \
  codex/bin/agent_self_improve.py \
  codex/bin/agent_self_improve_smoke.py \
  codex/bin/start_codex_stack_smoke.py \
  codex/bin/owui_chat_turn.py \
  codex/bin/owui_chat_turn_codex_local_route_smoke.py \
  codex/bin/owui_chat_scenarios.py \
  codex/bin/run_check.py \
  codex/bin/add_workspace.py \
  codex/bin/workspace_context.py \
  codex/bin/workspace_context_regression_smoke.py \
  codex/bin/workspace_scan.py \
  codex/bin/workspace_action.py \
  codex/bin/mentor_codex_local.py \
  codex/bin/sync_openwebui_function.py \
  codex/bin/sync_openwebui_function_test.py \
  codex/bin/reconcile_openwebui_functions.py \
  codex/bin/reconcile_openwebui_functions_test.py \
  codex/bin/openwebui_runtime.py \
  codex/bin/openwebui_runtime_smoke.py \
  codex/bin/mentor_capability_routing_smoke.py \
  codex/bin/gateway_admin_run_workspace_smoke.py

section "Offline routing and recovery smoke"
python3 codex/bin/filter_route_smoke.py --json
python3 codex/bin/workspace_context_regression_smoke.py
python3 codex/bin/gateway_recovery_smoke.py
python3 codex/bin/gateway_admin_run_workspace_smoke.py
python3 codex/bin/gateway_nested_helper_rescue_smoke.py
python3 codex/bin/gateway_runtime_health_smoke.py
python3 codex/bin/agent_self_improve_smoke.py
python3 codex/bin/start_codex_stack_smoke.py
python3 codex/bin/openwebui_runtime_smoke.py
python3 codex/bin/container_runner_guard_smoke.py
python3 codex/bin/mentor_capability_routing_smoke.py
python3 codex/bin/sync_openwebui_function_test.py
python3 codex/bin/reconcile_openwebui_functions_test.py
python3 codex/bin/owui_chat_scenario_catalog_smoke.py
python3 codex/bin/owui_chat_turn_preflight_smoke.py
python3 codex/bin/owui_chat_turn_codex_local_route_smoke.py --help >/dev/null
python3 codex/bin/owui_chat_turn_visible_fallback_smoke.py

section "Validating shell helpers"
bash -n \
  codex/bin/check_ai_stack.sh \
  codex/bin/store_runtime_secret.sh \
  codex/bin/store_openwebui_api_key.sh \
  codex/bin/owui_request.sh \
  codex/bin/deploy_ai_stack.sh \
  codex/bin/install_deploy_sudoers.sh

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
    echo "$REPO_ROOT/codex/bin/install_deploy_sudoers.sh --print"
    echo "sudo $REPO_ROOT/codex/bin/install_deploy_sudoers.sh --install"
    exit "$rc"
  fi
fi

reconcile_openwebui_functions

full_stack_healthcheck

section "AI Stack deploy finished"
echo "DEPLOY_OK"
