#!/usr/bin/env python3
"""Smoke checks for start_codex_stack.sh config resolution."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "codex/bin/start_codex_stack.sh"


def run_print_config(extra_env: dict[str, str] | None = None) -> dict:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--print-config"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=True,
    )
    return json.loads(proc.stdout)


def main() -> int:
    payload = run_print_config()
    if payload.get("repo_root") != str(ROOT):
        raise SystemExit(f"expected repo_root={ROOT}, got {payload!r}")
    if payload.get("code_root") != str(ROOT / "codex"):
        raise SystemExit(f"expected code_root under current checkout, got {payload!r}")
    ai_user = str(payload.get("ai_user") or "").strip()
    if not ai_user:
        raise SystemExit(f"expected resolved ai_user, got {payload!r}")

    override_root = "/tmp/ai-stack-override"
    payload = run_print_config({"AI_STACK_REPO_ROOT": override_root})
    if payload.get("repo_root") != override_root:
        raise SystemExit(f"expected AI_STACK_REPO_ROOT override to win, got {payload!r}")

    payload = run_print_config({"AI_USER": "definitely-missing-user", "SUDO_USER": ""})
    if payload.get("ai_user") == "definitely-missing-user":
        raise SystemExit(f"expected fallback away from invalid AI_USER, got {payload!r}")

    print("START_CODEX_STACK_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
