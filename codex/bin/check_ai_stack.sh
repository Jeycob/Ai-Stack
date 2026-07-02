#!/usr/bin/env bash
set -u

OPENWEBUI_URL="${OPENWEBUI_URL:-http://127.0.0.1:9090}"
CODEX_GATEWAY_URL="${CODEX_GATEWAY_URL:-http://127.0.0.1:9101}"
OLLAMA_URL="${OLLAMA_URL:-http://192.168.0.48:11434}"
WORKSPACE="${WORKSPACE:-ai-stack}"
MODEL="${MODEL:-codex-local-plan-qwen14b}"
TIMEOUT="${TIMEOUT:-10}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

failures=0

check_url() {
  local label="$1"
  local url="$2"
  printf '[check] %s ... ' "$label"
  if curl -fsS --connect-timeout 5 --max-time "$TIMEOUT" "$url" >/dev/null; then
    printf 'OK\n'
  else
    printf 'FAIL (%s)\n' "$url"
    failures=$((failures + 1))
  fi
}

check_json_contains() {
  local label="$1"
  local url="$2"
  local needle="$3"
  local body
  printf '[check] %s ... ' "$label"
  if ! body="$(curl -fsS --connect-timeout 5 --max-time "$TIMEOUT" "$url")"; then
    printf 'FAIL (%s)\n' "$url"
    failures=$((failures + 1))
    return
  fi
  if printf '%s' "$body" | grep -Fq -- "$needle"; then
    printf 'OK\n'
  else
    printf 'FAIL missing %s\n' "$needle"
    failures=$((failures + 1))
  fi
}

printf 'AI stack healthcheck\n'
printf 'openwebui=%s\n' "$OPENWEBUI_URL"
printf 'gateway=%s\n' "$CODEX_GATEWAY_URL"
printf 'ollama=%s\n' "$OLLAMA_URL"
printf 'workspace=%s model=%s\n' "$WORKSPACE" "$MODEL"

check_url 'OpenWebUI config endpoint' "$OPENWEBUI_URL/api/config"
check_url 'Ollama version endpoint' "$OLLAMA_URL/api/version"
check_json_contains 'Codex gateway health' "$CODEX_GATEWAY_URL/health" '"ok": true'
check_json_contains 'Codex gateway model alias' "$CODEX_GATEWAY_URL/v1/models" "$MODEL"
check_json_contains 'Codex gateway workspace registry' "$CODEX_GATEWAY_URL/v1/workspaces" "$WORKSPACE"

if command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/codex_gateway_smoke.py" ]; then
  printf '[check] Codex gateway smoke ...\n'
  if python3 "$SCRIPT_DIR/codex_gateway_smoke.py" --base-url "$CODEX_GATEWAY_URL" --workspace "$WORKSPACE" --model "$MODEL" --timeout 60; then
    printf '[check] Codex gateway smoke OK\n'
  else
    printf '[check] Codex gateway smoke FAIL\n'
    failures=$((failures + 1))
  fi
else
  printf '[check] Codex gateway smoke SKIP (python3 or codex_gateway_smoke.py missing)\n'
fi

if [ "$failures" -eq 0 ]; then
  printf 'AI STACK OK\n'
  exit 0
fi

printf 'AI STACK FAILED checks=%s\n' "$failures" >&2
exit 1
