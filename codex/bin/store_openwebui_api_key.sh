#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${CODEX_STATE_DIR:-$CODEX_ROOT/state}"

if [ -n "${OWUI_API_KEY_FILE:-}" ]; then
  RUNTIME_SECRET_TARGET="$OWUI_API_KEY_FILE" exec "$SCRIPT_DIR/store_runtime_secret.sh" openwebui-api
fi

CODEX_STATE_DIR="$STATE_DIR" exec "$SCRIPT_DIR/store_runtime_secret.sh" openwebui-api
