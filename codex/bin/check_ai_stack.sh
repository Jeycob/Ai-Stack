#!/usr/bin/env bash
set -u

OPENWEBUI_URL="${OPENWEBUI_URL:-http://127.0.0.1:9090}"
CODEX_GATEWAY_URL="${CODEX_GATEWAY_URL:-http://127.0.0.1:9101}"
OLLAMA_URL="${OLLAMA_URL:-http://192.168.0.48:11434}"
WORKSPACE="${WORKSPACE:-ai-stack}"
MODEL="${MODEL:-codex-local-plan-qwen14b}"
TIMEOUT="${TIMEOUT:-10}"
OWUI_CHAT_SMOKE_EXPECTED="${OWUI_CHAT_SMOKE_EXPECTED:-smoke}"
OWUI_CHAT_SMOKE_VISIBLE="${OWUI_CHAT_SMOKE_VISIBLE:-repo: ${WORKSPACE}\nOdpovez jednim slovem: smoke}"
OWUI_CHAT_SMOKE_PROMPT="${OWUI_CHAT_SMOKE_PROMPT:-repo: ${WORKSPACE}\nOdpovez jednim slovem: smoke}"
OWUI_CHAT_SCENARIOS="${OWUI_CHAT_SCENARIOS:-git-status,next-step}"
SUMMARY_ONLY="${CHECK_AI_STACK_SUMMARY_ONLY:-0}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_KEY_FILE="$(cd "$SCRIPT_DIR/.." && pwd)/state/openwebui-api.key"
OWUI_KEY_FILE="${OWUI_API_KEY_FILE:-$DEFAULT_KEY_FILE}"
WSL_BOOT_WRAPPER="${WSL_BOOT_WRAPPER:-$REPO_ROOT/codex/bin/wsl_boot_ai_stack.sh}"
WSL_CONF="${WSL_CONF:-/etc/wsl.conf}"

failures=0
total_checks=0
passed_checks=0
skipped_checks=0
summary_lines=""

record_summary() {
  local label="$1"
  local status="$2"
  summary_lines+="${label}=${status}"$'\n'
  case "$status" in
    OK)
      total_checks=$((total_checks + 1))
      passed_checks=$((passed_checks + 1))
      ;;
    FAIL)
      total_checks=$((total_checks + 1))
      ;;
    SKIP)
      skipped_checks=$((skipped_checks + 1))
      ;;
  esac
}

check_url() {
  local label="$1"
  local url="$2"
  if [ "$SUMMARY_ONLY" != "1" ]; then
    printf '[check] %s ... ' "$label"
  fi
  if curl -fsS --connect-timeout 5 --max-time "$TIMEOUT" "$url" >/dev/null 2>/dev/null; then
    [ "$SUMMARY_ONLY" != "1" ] && printf 'OK\n'
    record_summary "$label" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && printf 'FAIL (%s)\n' "$url"
    failures=$((failures + 1))
    record_summary "$label" "FAIL"
  fi
}

check_json_contains() {
  local label="$1"
  local url="$2"
  local needle="$3"
  local body
  if [ "$SUMMARY_ONLY" != "1" ]; then
    printf '[check] %s ... ' "$label"
  fi
  if ! body="$(curl -fsS --connect-timeout 5 --max-time "$TIMEOUT" "$url" 2>/dev/null)"; then
    [ "$SUMMARY_ONLY" != "1" ] && printf 'FAIL (%s)\n' "$url"
    failures=$((failures + 1))
    record_summary "$label" "FAIL"
    return
  fi
  if printf '%s' "$body" | grep -Fq -- "$needle"; then
    [ "$SUMMARY_ONLY" != "1" ] && printf 'OK\n'
    record_summary "$label" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && printf 'FAIL missing %s\n' "$needle"
    failures=$((failures + 1))
    record_summary "$label" "FAIL"
  fi
}

is_wsl() {
  grep -qi microsoft /proc/version 2>/dev/null
}

check_file_exists() {
  local label="$1"
  local path="$2"
  if [ "$SUMMARY_ONLY" != "1" ]; then
    printf '[check] %s ... ' "$label"
  fi
  if [ -f "$path" ]; then
    [ "$SUMMARY_ONLY" != "1" ] && printf 'OK\n'
    record_summary "$label" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && printf 'FAIL (%s missing)\n' "$path"
    failures=$((failures + 1))
    record_summary "$label" "FAIL"
  fi
}

check_wsl_boot_config() {
  local label="WSL boot config"
  if ! is_wsl; then
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] %s ... SKIP (not WSL)\n' "$label"
    record_summary "$label" "SKIP"
    return
  fi
  if [ ! -f "$WSL_CONF" ]; then
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] %s ... SKIP (%s missing)\n' "$label" "$WSL_CONF"
    record_summary "$label" "SKIP"
    return
  fi
  if grep -Fq "wsl_boot_ai_stack.sh" "$WSL_CONF"; then
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] %s ... OK\n' "$label"
    record_summary "$label" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] %s ... SKIP (wrapper not configured)\n' "$label"
    record_summary "$label" "SKIP"
  fi
}

emit_summary() {
  local verdict="OK"
  if [ "$failures" -ne 0 ]; then
    verdict="FAILED"
  fi
  printf 'AI_STACK_VERDICT\n'
  printf 'status=%s\n' "$verdict"
  printf 'workspace=%s\n' "$WORKSPACE"
  printf 'model=%s\n' "$MODEL"
  printf 'checks_total=%s\n' "$total_checks"
  printf 'checks_passed=%s\n' "$passed_checks"
  printf 'checks_failed=%s\n' "$failures"
  printf 'checks_skipped=%s\n' "$skipped_checks"
  printf 'summary:\n'
  printf '%s' "$summary_lines"
}

if [ "$SUMMARY_ONLY" != "1" ]; then
  printf 'AI stack healthcheck\n'
  printf 'openwebui=%s\n' "$OPENWEBUI_URL"
  printf 'gateway=%s\n' "$CODEX_GATEWAY_URL"
  printf 'ollama=%s\n' "$OLLAMA_URL"
  printf 'workspace=%s model=%s\n' "$WORKSPACE" "$MODEL"
fi

if [ "${SKIP_WSL_BOOT_CHECK:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] WSL boot wrapper ... SKIP (disabled)\n'
  record_summary "WSL boot wrapper" "SKIP"
  record_summary "WSL boot config" "SKIP"
else
  check_file_exists "WSL boot wrapper" "$WSL_BOOT_WRAPPER"
  check_wsl_boot_config
fi

if [ "${SKIP_OPENWEBUI:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI config endpoint ... SKIP (self-check disabled)\n'
  record_summary "OpenWebUI config endpoint" "SKIP"
else
  check_url 'OpenWebUI config endpoint' "$OPENWEBUI_URL/api/config"
fi
check_url 'Ollama version endpoint' "$OLLAMA_URL/api/version"
check_json_contains 'Codex gateway health' "$CODEX_GATEWAY_URL/health" '"ok": true'
check_json_contains 'Codex gateway model alias' "$CODEX_GATEWAY_URL/v1/models" "$MODEL"
check_json_contains 'Codex gateway workspace registry' "$CODEX_GATEWAY_URL/v1/workspaces" "$WORKSPACE"

if [ "${SKIP_GATEWAY_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway smoke ... SKIP (disabled)\n'
  record_summary "Codex gateway smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/codex_gateway_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway smoke ...\n'
  gateway_smoke_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/codex_gateway_smoke.py" --base-url "$CODEX_GATEWAY_URL" --workspace "$WORKSPACE" --model "$MODEL" --timeout 60 >"$gateway_smoke_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_smoke_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway smoke OK\n'
    record_summary "Codex gateway smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_smoke_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Codex gateway smoke" "FAIL"
  fi
  rm -f "$gateway_smoke_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway smoke SKIP (python3 or codex_gateway_smoke.py missing)\n'
  record_summary "Codex gateway smoke" "SKIP"
fi

if [ "${SKIP_OWUI_CHAT_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI audit chat smoke ... SKIP (disabled)\n'
  record_summary "OpenWebUI audit chat smoke" "SKIP"
elif ! command -v python3 >/dev/null 2>&1 || [ ! -f "$SCRIPT_DIR/owui_chat_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI audit chat smoke ... SKIP (python3 or owui_chat_smoke.py missing)\n'
  record_summary "OpenWebUI audit chat smoke" "SKIP"
elif [ -z "${OWUI_API_KEY:-}" ] && [ ! -f "$OWUI_KEY_FILE" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI audit chat smoke ... SKIP (no API key)\n'
  record_summary "OpenWebUI audit chat smoke" "SKIP"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI audit chat smoke ...\n'
  visible_file="$(mktemp)"
  prompt_file="$(mktemp)"
  chat_smoke_log="$(mktemp)"
  printf '%b\n' "$OWUI_CHAT_SMOKE_VISIBLE" > "$visible_file"
  printf '%b\n' "$OWUI_CHAT_SMOKE_PROMPT" > "$prompt_file"
  if python3 "$SCRIPT_DIR/owui_chat_smoke.py" \
      --base-url "$OPENWEBUI_URL" \
      --chat-id "${OWUI_AUDIT_CHAT_ID:-57529037-84b9-42e1-8bae-9eab35b601bd}" \
      --model "$MODEL" \
      --visible-prompt-file "$visible_file" \
      --prompt-file "$prompt_file" \
      --expected-substring "$OWUI_CHAT_SMOKE_EXPECTED" \
      --status-interval 2 \
      --quiet >"$chat_smoke_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$chat_smoke_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI audit chat smoke OK\n'
    record_summary "OpenWebUI audit chat smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$chat_smoke_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI audit chat smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI audit chat smoke" "FAIL"
  fi
  rm -f "$visible_file" "$prompt_file" "$chat_smoke_log"
fi

if [ "${SKIP_OWUI_CHAT_SCENARIOS:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenarios ... SKIP (disabled)\n'
  record_summary "OpenWebUI chat scenarios" "SKIP"
elif ! command -v python3 >/dev/null 2>&1 || [ ! -f "$SCRIPT_DIR/owui_chat_scenarios.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenarios ... SKIP (python3 or owui_chat_scenarios.py missing)\n'
  record_summary "OpenWebUI chat scenarios" "SKIP"
elif [ -z "${OWUI_API_KEY:-}" ] && [ ! -f "$OWUI_KEY_FILE" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenarios ... SKIP (no API key)\n'
  record_summary "OpenWebUI chat scenarios" "SKIP"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenarios ...\n'
  scenario_log="$(mktemp)"
  scenario_args=()
  IFS=',' read -r -a scenario_names <<< "$OWUI_CHAT_SCENARIOS"
  for scenario_name in "${scenario_names[@]}"; do
    scenario_name="${scenario_name#"${scenario_name%%[![:space:]]*}"}"
    scenario_name="${scenario_name%"${scenario_name##*[![:space:]]}"}"
    [ -n "$scenario_name" ] || continue
    scenario_args+=(--scenario "$scenario_name")
  done
  if python3 "$SCRIPT_DIR/owui_chat_scenarios.py" \
      --base-url "$OPENWEBUI_URL" \
      --chat-id "${OWUI_AUDIT_CHAT_ID:-57529037-84b9-42e1-8bae-9eab35b601bd}" \
      --model "$MODEL" \
      --workspace "$WORKSPACE" \
      --status-interval 2 \
      --quiet \
      "${scenario_args[@]}" >"$scenario_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$scenario_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenarios OK\n'
    record_summary "OpenWebUI chat scenarios" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$scenario_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenarios FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI chat scenarios" "FAIL"
  fi
  rm -f "$scenario_log"
fi

if [ "$failures" -eq 0 ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf 'AI STACK OK\n'
  emit_summary
  exit 0
fi

if [ "$SUMMARY_ONLY" != "1" ]; then
  printf 'AI STACK FAILED checks=%s\n' "$failures" >&2
fi
emit_summary
exit 1
