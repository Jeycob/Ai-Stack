#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_SCRIPT="$(readlink -f "$REPO_ROOT/codex/bin/deploy_ai_stack.sh")"
TARGET="${SUDOERS_TARGET:-/etc/sudoers.d/ai-stack-deploy}"
DEPLOY_USER="${AI_STACK_DEPLOY_USER:-${SUDO_USER:-$(id -un)}}"

usage() {
  cat <<EOF
Usage:
  $SCRIPT_PATH --print
  $SCRIPT_PATH --check
  sudo $SCRIPT_PATH --install

Installs or checks a narrow sudoers rule for ai-stack deploy restarts.
The rule permits only:
  $DEPLOY_SCRIPT --restart-only
  $DEPLOY_SCRIPT --sudoers-probe
EOF
}

reject_unsafe_path() {
  case "$DEPLOY_SCRIPT" in
    *[[:space:]]*)
      echo "DEPLOY_SUDOERS_UNSAFE_PATH"
      echo "The deploy script path contains whitespace, which this sudoers helper will not encode."
      exit 2
      ;;
  esac
}

sudoers_entry() {
  reject_unsafe_path
  printf '%s ALL=(root) NOPASSWD: %s --restart-only, %s --sudoers-probe\n' \
    "$DEPLOY_USER" "$DEPLOY_SCRIPT" "$DEPLOY_SCRIPT"
}

print_entry() {
  echo "DEPLOY_SUDOERS_ENTRY"
  echo "target=$TARGET"
  echo "user=$DEPLOY_USER"
  sudoers_entry
}

check_entry() {
  if sudo -n "$DEPLOY_SCRIPT" --sudoers-probe >/dev/null 2>&1; then
    echo "DEPLOY_SUDOERS_READY"
    exit 0
  fi
  echo "DEPLOY_SUDOERS_NOT_CONFIGURED"
  echo "Recovery:"
  echo "  sudo $SCRIPT_PATH --install"
  exit 1
}

install_entry() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "DEPLOY_SUDOERS_INSTALL_REQUIRES_ROOT"
    echo "Recovery:"
    echo "  sudo $SCRIPT_PATH --install"
    exit 1
  fi
  if [ -z "$DEPLOY_USER" ] || [ "$DEPLOY_USER" = "root" ]; then
    echo "DEPLOY_SUDOERS_USER_REQUIRED"
    echo "Set AI_STACK_DEPLOY_USER to the non-root WSL user that runs the gateway."
    exit 1
  fi
  case "$TARGET" in
    /etc/sudoers.d/*)
      ;;
    *)
      echo "DEPLOY_SUDOERS_UNSAFE_TARGET"
      echo "Target must be under /etc/sudoers.d/"
      exit 2
      ;;
  esac

  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  {
    echo "# Managed by ai-stack codex/bin/install_deploy_sudoers.sh"
    sudoers_entry
  } >"$tmp"

  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "$tmp" >/dev/null
  fi
  install -o root -g root -m 0440 "$tmp" "$TARGET"
  echo "DEPLOY_SUDOERS_INSTALLED"
  echo "target=$TARGET"
  sudoers_entry
}

case "${1:-}" in
  --print)
    print_entry
    ;;
  --check)
    check_entry
    ;;
  --install)
    install_entry
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    echo "DEPLOY_SUDOERS_UNKNOWN_ARGUMENT: $1"
    usage
    exit 2
    ;;
esac
