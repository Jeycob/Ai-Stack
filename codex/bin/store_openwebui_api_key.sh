#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_KEY_FILE="$(cd "$SCRIPT_DIR/.." && pwd)/state/openwebui-api.key"
KEY_FILE="${OWUI_API_KEY_FILE:-$DEFAULT_KEY_FILE}"

if [ -t 0 ]; then
  printf 'Paste OpenWebUI API key: ' >&2
  IFS= read -rs KEY
  printf '\n' >&2
else
  IFS= read -r KEY
fi

KEY="${KEY//$'\r'/}"
KEY="${KEY//$'\n'/}"
if [ -z "$KEY" ]; then
  echo "OPENWEBUI_API_KEY_NOT_STORED: empty key" >&2
  exit 2
fi

umask 077
mkdir -p "$(dirname "$KEY_FILE")"
printf '%s\n' "$KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE" 2>/dev/null || true

echo "OPENWEBUI_API_KEY_STORED"
echo "path=$KEY_FILE"
if command -v stat >/dev/null 2>&1; then
  mode="$(stat -c '%a' "$KEY_FILE" 2>/dev/null || true)"
  [ -n "$mode" ] && echo "mode=$mode"
fi
