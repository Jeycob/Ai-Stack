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
OWUI_CHAT_SCENARIOS="${OWUI_CHAT_SCENARIOS:-agent-review,explicit-agent-loop,verify-project}"
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

if [ "${SKIP_OWUI_FUNCTION_RECONCILE_CHECK:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI codex functions reconcile ... SKIP (disabled)\n'
  record_summary "OpenWebUI codex functions reconcile" "SKIP"
elif ! command -v python3 >/dev/null 2>&1 || [ ! -f "$SCRIPT_DIR/reconcile_openwebui_functions.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI codex functions reconcile ... FAIL (python3 or reconciler missing)\n'
  failures=$((failures + 1))
  record_summary "OpenWebUI codex functions reconcile" "FAIL"
elif [ -z "${OWUI_API_KEY:-}" ] && [ ! -f "$OWUI_KEY_FILE" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && {
    printf '[check] OpenWebUI codex functions reconcile ... FAIL\n'
    printf 'OPENWEBUI_API_KEY_MISSING\n'
    printf 'Recovery: store the key in %s or set OWUI_API_KEY.\n' "$OWUI_KEY_FILE"
  }
  failures=$((failures + 1))
  record_summary "OpenWebUI codex functions reconcile" "FAIL"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI codex functions reconcile ...\n'
  function_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/reconcile_openwebui_functions.py" \
      --base-url "$OPENWEBUI_URL" \
      --api-key-file "$OWUI_KEY_FILE" \
      --check-only \
      --json >"$function_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$function_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI codex functions reconcile OK\n'
    record_summary "OpenWebUI codex functions reconcile" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$function_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI codex functions reconcile FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI codex functions reconcile" "FAIL"
  fi
  rm -f "$function_log"
fi

check_url 'Ollama version endpoint' "$OLLAMA_URL/api/version"
check_json_contains 'Codex gateway health' "$CODEX_GATEWAY_URL/health" '"ok": true'
check_json_contains 'Codex gateway capability mode' "$CODEX_GATEWAY_URL/health" '"capability_mode": "agent-first"'
check_json_contains 'Codex gateway natural route' "$CODEX_GATEWAY_URL/health" '"natural_codex_local_route": "agent_loop"'
check_json_contains 'Codex gateway codex-local readiness' "$CODEX_GATEWAY_URL/health" '"codex_local_ready": true'
check_json_contains 'Codex gateway model alias' "$CODEX_GATEWAY_URL/v1/models" "$MODEL"
check_json_contains 'Codex gateway workspace registry' "$CODEX_GATEWAY_URL/v1/workspaces" "$WORKSPACE"

if [ "${SKIP_FILTER_ROUTE_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex filter route smoke ... SKIP (disabled)\n'
  record_summary "Codex filter route smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/filter_route_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex filter route smoke ...\n'
  filter_route_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/filter_route_smoke.py" --json >"$filter_route_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$filter_route_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex filter route smoke OK\n'
    record_summary "Codex filter route smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$filter_route_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex filter route smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Codex filter route smoke" "FAIL"
  fi
  rm -f "$filter_route_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex filter route smoke ... SKIP (python3 or filter_route_smoke.py missing)\n'
  record_summary "Codex filter route smoke" "SKIP"
fi

if [ "${SKIP_GATEWAY_RECOVERY_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway recovery smoke ... SKIP (disabled)\n'
  record_summary "Codex gateway recovery smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/gateway_recovery_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway recovery smoke ...\n'
  gateway_recovery_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/gateway_recovery_smoke.py" >"$gateway_recovery_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_recovery_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway recovery smoke OK\n'
    record_summary "Codex gateway recovery smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_recovery_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway recovery smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Codex gateway recovery smoke" "FAIL"
  fi
  rm -f "$gateway_recovery_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway recovery smoke ... SKIP (python3 or gateway_recovery_smoke.py missing)\n'
  record_summary "Codex gateway recovery smoke" "SKIP"
fi

if [ "${SKIP_GATEWAY_ADMIN_WORKSPACE_RUN_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway admin workspace-run smoke ... SKIP (disabled)\n'
  record_summary "Codex gateway admin workspace-run smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/gateway_admin_run_workspace_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway admin workspace-run smoke ...\n'
  gateway_admin_run_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/gateway_admin_run_workspace_smoke.py" >"$gateway_admin_run_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_admin_run_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway admin workspace-run smoke OK\n'
    record_summary "Codex gateway admin workspace-run smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_admin_run_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway admin workspace-run smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Codex gateway admin workspace-run smoke" "FAIL"
  fi
  rm -f "$gateway_admin_run_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway admin workspace-run smoke ... SKIP (python3 or gateway_admin_run_workspace_smoke.py missing)\n'
  record_summary "Codex gateway admin workspace-run smoke" "SKIP"
fi

if [ "${SKIP_GATEWAY_NESTED_HELPER_RESCUE_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway nested helper rescue smoke ... SKIP (disabled)\n'
  record_summary "Codex gateway nested helper rescue smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/gateway_nested_helper_rescue_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway nested helper rescue smoke ...\n'
  gateway_nested_helper_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/gateway_nested_helper_rescue_smoke.py" >"$gateway_nested_helper_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_nested_helper_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway nested helper rescue smoke OK\n'
    record_summary "Codex gateway nested helper rescue smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_nested_helper_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway nested helper rescue smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Codex gateway nested helper rescue smoke" "FAIL"
  fi
  rm -f "$gateway_nested_helper_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway nested helper rescue smoke ... SKIP (python3 or gateway_nested_helper_rescue_smoke.py missing)\n'
  record_summary "Codex gateway nested helper rescue smoke" "SKIP"
fi

if [ "${SKIP_GATEWAY_RUNTIME_HEALTH_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime health smoke ... SKIP (disabled)\n'
  record_summary "Codex gateway runtime health smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/gateway_runtime_health_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime health smoke ...\n'
  gateway_runtime_health_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/gateway_runtime_health_smoke.py" >"$gateway_runtime_health_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_runtime_health_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime health smoke OK\n'
    record_summary "Codex gateway runtime health smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_runtime_health_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime health smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Codex gateway runtime health smoke" "FAIL"
  fi
  rm -f "$gateway_runtime_health_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime health smoke ... SKIP (python3 or gateway_runtime_health_smoke.py missing)\n'
  record_summary "Codex gateway runtime health smoke" "SKIP"
fi

if [ "${SKIP_CONTAINER_RUNNER_GUARD_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Container runner guard smoke ... SKIP (disabled)\n'
  record_summary "Container runner guard smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/container_runner_guard_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Container runner guard smoke ...\n'
  container_guard_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/container_runner_guard_smoke.py" >"$container_guard_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$container_guard_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Container runner guard smoke OK\n'
    record_summary "Container runner guard smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$container_guard_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Container runner guard smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Container runner guard smoke" "FAIL"
  fi
  rm -f "$container_guard_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Container runner guard smoke ... SKIP (python3 or container_runner_guard_smoke.py missing)\n'
  record_summary "Container runner guard smoke" "SKIP"
fi

if [ "${SKIP_RECONCILER_UNIT_TESTS:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI reconciler unit tests ... SKIP (disabled)\n'
  record_summary "OpenWebUI reconciler unit tests" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/reconcile_openwebui_functions_test.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI reconciler unit tests ...\n'
  reconciler_test_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/reconcile_openwebui_functions_test.py" >"$reconciler_test_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$reconciler_test_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI reconciler unit tests OK\n'
    record_summary "OpenWebUI reconciler unit tests" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$reconciler_test_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI reconciler unit tests FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI reconciler unit tests" "FAIL"
  fi
  rm -f "$reconciler_test_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI reconciler unit tests ... SKIP (python3 or reconcile_openwebui_functions_test.py missing)\n'
  record_summary "OpenWebUI reconciler unit tests" "SKIP"
fi

if [ "${SKIP_GATEWAY_RUNTIME_FINGERPRINT_CHECK:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime fingerprint check ... SKIP (disabled)\n'
  record_summary "Codex gateway runtime fingerprint check" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/gateway_runtime_fingerprint_check.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime fingerprint check ...\n'
  gateway_runtime_fp_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/gateway_runtime_fingerprint_check.py" \
      --base-url "$CODEX_GATEWAY_URL" >"$gateway_runtime_fp_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_runtime_fp_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime fingerprint check OK\n'
    record_summary "Codex gateway runtime fingerprint check" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$gateway_runtime_fp_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime fingerprint check FAIL\n'
    failures=$((failures + 1))
    record_summary "Codex gateway runtime fingerprint check" "FAIL"
  fi
  rm -f "$gateway_runtime_fp_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Codex gateway runtime fingerprint check ... SKIP (python3 or gateway_runtime_fingerprint_check.py missing)\n'
  record_summary "Codex gateway runtime fingerprint check" "SKIP"
fi

if [ "${SKIP_MENTOR_CAPABILITY_ROUTING_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Mentor capability routing smoke ... SKIP (disabled)\n'
  record_summary "Mentor capability routing smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/mentor_capability_routing_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Mentor capability routing smoke ...\n'
  mentor_capability_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/mentor_capability_routing_smoke.py" >"$mentor_capability_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$mentor_capability_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Mentor capability routing smoke OK\n'
    record_summary "Mentor capability routing smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$mentor_capability_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Mentor capability routing smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "Mentor capability routing smoke" "FAIL"
  fi
  rm -f "$mentor_capability_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] Mentor capability routing smoke ... SKIP (python3 or mentor_capability_routing_smoke.py missing)\n'
  record_summary "Mentor capability routing smoke" "SKIP"
fi

if [ "${SKIP_OWUI_CHAT_SCENARIO_CATALOG_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenario catalog smoke ... SKIP (disabled)\n'
  record_summary "OpenWebUI chat scenario catalog smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/owui_chat_scenario_catalog_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenario catalog smoke ...\n'
  owui_scenario_catalog_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/owui_chat_scenario_catalog_smoke.py" >"$owui_scenario_catalog_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$owui_scenario_catalog_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenario catalog smoke OK\n'
    record_summary "OpenWebUI chat scenario catalog smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$owui_scenario_catalog_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenario catalog smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI chat scenario catalog smoke" "FAIL"
  fi
  rm -f "$owui_scenario_catalog_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat scenario catalog smoke ... SKIP (python3 or owui_chat_scenario_catalog_smoke.py missing)\n'
  record_summary "OpenWebUI chat scenario catalog smoke" "SKIP"
fi

if [ "${SKIP_OWUI_CHAT_TURN_PREFLIGHT_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat turn preflight smoke ... SKIP (disabled)\n'
  record_summary "OpenWebUI chat turn preflight smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/owui_chat_turn_preflight_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat turn preflight smoke ...\n'
  owui_preflight_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/owui_chat_turn_preflight_smoke.py" >"$owui_preflight_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$owui_preflight_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat turn preflight smoke OK\n'
    record_summary "OpenWebUI chat turn preflight smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$owui_preflight_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat turn preflight smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI chat turn preflight smoke" "FAIL"
  fi
  rm -f "$owui_preflight_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI chat turn preflight smoke ... SKIP (python3 or owui_chat_turn_preflight_smoke.py missing)\n'
  record_summary "OpenWebUI chat turn preflight smoke" "SKIP"
fi

if [ "${SKIP_OWUI_VISIBLE_FALLBACK_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI visible fallback smoke ... SKIP (disabled)\n'
  record_summary "OpenWebUI visible fallback smoke" "SKIP"
elif command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/owui_chat_turn_visible_fallback_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI visible fallback smoke ...\n'
  owui_visible_fallback_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/owui_chat_turn_visible_fallback_smoke.py" >"$owui_visible_fallback_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$owui_visible_fallback_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI visible fallback smoke OK\n'
    record_summary "OpenWebUI visible fallback smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$owui_visible_fallback_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI visible fallback smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI visible fallback smoke" "FAIL"
  fi
  rm -f "$owui_visible_fallback_log"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI visible fallback smoke ... SKIP (python3 or owui_chat_turn_visible_fallback_smoke.py missing)\n'
  record_summary "OpenWebUI visible fallback smoke" "SKIP"
fi

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

if [ "${SKIP_OWUI_AGENT_LOOP_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI explicit agent-loop smoke ... SKIP (disabled)\n'
  record_summary "OpenWebUI explicit agent-loop smoke" "SKIP"
elif ! command -v python3 >/dev/null 2>&1 || [ ! -f "$SCRIPT_DIR/owui_chat_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI explicit agent-loop smoke ... SKIP (python3 or owui_chat_smoke.py missing)\n'
  record_summary "OpenWebUI explicit agent-loop smoke" "SKIP"
elif [ -z "${OWUI_API_KEY:-}" ] && [ ! -f "$OWUI_KEY_FILE" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI explicit agent-loop smoke ... SKIP (no API key)\n'
  record_summary "OpenWebUI explicit agent-loop smoke" "SKIP"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI explicit agent-loop smoke ...\n'
  explicit_smoke_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/owui_chat_turn_codex_local_route_smoke.py" \
      --base-url "$OPENWEBUI_URL" \
      --api-key-file "$OWUI_KEY_FILE" \
      --model "$MODEL" \
      --prompt "$(printf 'repo: %s\nGATEWAY_ADMIN_AGENT_LOOP %s -- Prohlédni workspace. Nic needituj. Odpověz stručně.' "$WORKSPACE" "$WORKSPACE")" \
      --expect "AGENT_LOOP_OK" \
      --expect "workflow=review" \
      --expect "read_only=True" \
      --timeout 30 \
      --attempts 4 \
      --initial-delay 1 \
      --max-delay 4 \
      --total-timeout 180 \
      --quiet >"$explicit_smoke_log" 2>&1 && grep -Fq "AGENT_LOOP" "$explicit_smoke_log"; then
      [ "$SUMMARY_ONLY" != "1" ] && cat "$explicit_smoke_log"
      [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI explicit agent-loop smoke OK\n'
      record_summary "OpenWebUI explicit agent-loop smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$explicit_smoke_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI explicit agent-loop smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI explicit agent-loop smoke" "FAIL"
  fi
  rm -f "$explicit_smoke_log"
fi

if [ "${SKIP_OWUI_STATELESS_ROUTE_SMOKE:-0}" = "1" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI stateless natural route smoke ... SKIP (disabled)\n'
  record_summary "OpenWebUI stateless natural route smoke" "SKIP"
elif ! command -v python3 >/dev/null 2>&1 || [ ! -f "$SCRIPT_DIR/owui_chat_turn_codex_local_route_smoke.py" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI stateless natural route smoke ... SKIP (python3 or smoke helper missing)\n'
  record_summary "OpenWebUI stateless natural route smoke" "SKIP"
elif [ -z "${OWUI_API_KEY:-}" ] && [ ! -f "$OWUI_KEY_FILE" ]; then
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI stateless natural route smoke ... SKIP (no API key)\n'
  record_summary "OpenWebUI stateless natural route smoke" "SKIP"
else
  [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI stateless natural route smoke ...\n'
  stateless_route_log="$(mktemp)"
  if python3 "$SCRIPT_DIR/owui_chat_turn_codex_local_route_smoke.py" \
      --base-url "$OPENWEBUI_URL" \
      --api-key-file "$OWUI_KEY_FILE" \
      --model "$MODEL" \
      --timeout 30 \
      --attempts 4 \
      --initial-delay 1 \
      --max-delay 4 \
      --total-timeout 180 \
      --quiet >"$stateless_route_log" 2>&1; then
    [ "$SUMMARY_ONLY" != "1" ] && cat "$stateless_route_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI stateless natural route smoke OK\n'
    record_summary "OpenWebUI stateless natural route smoke" "OK"
  else
    [ "$SUMMARY_ONLY" != "1" ] && cat "$stateless_route_log"
    [ "$SUMMARY_ONLY" != "1" ] && printf '[check] OpenWebUI stateless natural route smoke FAIL\n'
    failures=$((failures + 1))
    record_summary "OpenWebUI stateless natural route smoke" "FAIL"
  fi
  rm -f "$stateless_route_log"
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
