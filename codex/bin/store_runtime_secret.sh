#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${CODEX_STATE_DIR:-$CODEX_ROOT/state}"

usage() {
  cat >&2 <<'EOF'
Usage: codex/bin/store_runtime_secret.sh <secret-name>

Known secret names:
  openwebui-api          -> codex/state/openwebui-api.key
  github-api             -> codex/state/github-api.token
  codex-gateway-admin    -> codex/state/codex-gateway-admin.token

The secret is read from stdin or from a hidden interactive prompt.
The value is never printed.
EOF
}

secret_name="${1:-}"
case "$secret_name" in
  openwebui-api)
    target="$STATE_DIR/openwebui-api.key"
    label="OPENWEBUI_API_KEY"
    ;;
  github-api)
    target="$STATE_DIR/github-api.token"
    label="GITHUB_API_TOKEN"
    ;;
  codex-gateway-admin)
    target="$STATE_DIR/codex-gateway-admin.token"
    label="CODEX_GATEWAY_ADMIN_TOKEN"
    ;;
  ""|-h|--help)
    usage
    exit 0
    ;;
  *)
    echo "UNKNOWN_RUNTIME_SECRET: $secret_name" >&2
    usage
    exit 2
    ;;
esac

target="${RUNTIME_SECRET_TARGET:-$target}"

if [ -t 0 ]; then
  printf 'Paste %s: ' "$label" >&2
  IFS= read -rs secret
  printf '\n' >&2
else
  IFS= read -r secret
fi

secret="${secret//$'\r'/}"
secret="${secret//$'\n'/}"
if [ -z "$secret" ]; then
  echo "RUNTIME_SECRET_NOT_STORED: empty value" >&2
  exit 2
fi

umask 077
mkdir -p "$(dirname "$target")"
printf '%s\n' "$secret" > "$target"
chmod 600 "$target" 2>/dev/null || true

echo "RUNTIME_SECRET_STORED"
echo "name=$secret_name"
echo "path=$target"
if command -v stat >/dev/null 2>&1; then
  mode="$(stat -c '%a' "$target" 2>/dev/null || true)"
  [ -n "$mode" ] && echo "mode=$mode"
fi
