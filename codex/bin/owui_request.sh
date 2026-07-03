#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OWUI_BASE_URL:-http://192.168.0.48:9090}"
DEFAULT_KEY_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/state/openwebui-api.key"
KEY_FILE="${OWUI_API_KEY_FILE:-$DEFAULT_KEY_FILE}"
METHOD="${1:?usage: owui_request.sh METHOD PATH_OR_URL [http_retry args...]}"
TARGET="${2:?usage: owui_request.sh METHOD PATH_OR_URL [http_retry args...]}"
shift 2

if [[ "$TARGET" == http://* || "$TARGET" == https://* ]]; then
  URL="$TARGET"
else
  URL="${BASE_URL%/}/${TARGET#/}"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/http_retry.py" "$METHOD" "$URL" \
  --bearer-env OWUI_API_KEY \
  --bearer-file "$KEY_FILE" \
  --attempts "${OWUI_HTTP_ATTEMPTS:-12}" \
  --initial-delay "${OWUI_HTTP_INITIAL_DELAY:-0.5}" \
  --max-delay "${OWUI_HTTP_MAX_DELAY:-4}" \
  --total-timeout "${OWUI_HTTP_TOTAL_TIMEOUT:-180}" \
  "$@"
