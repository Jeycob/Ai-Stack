#!/usr/bin/env python3
"""Offline smoke for mentor follow-up retry-after-patch behavior."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MENTOR_PATH = ROOT / "codex/bin/mentor_codex_local.py"


def load_module():
    spec = importlib.util.spec_from_file_location("mentor_codex_local_smoke", MENTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load mentor helper from {MENTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    mentor = load_module()
    calls: list[tuple[str, str]] = []

    def fake_invoke_turn(args, visible, technical, send_history=True, capture_output=True):
        calls.append((visible, technical))
        return 0, "WORKSPACE_ACTION_OK\naction=test"

    mentor.invoke_turn = fake_invoke_turn

    class Args:
        repo = "ai-stack"
        timeout = 2400

    rc, output = mentor.request_followup_verify(
        Args(),
        "Test2",
        "install,verify,smoke,test,build,lint",
        retry_action="test",
        retry_runner="container",
        retry_timeout="1800",
    )
    if rc != 0:
        raise SystemExit(f"MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=unexpected rc {rc}")
    if "WORKSPACE_ACTION_OK" not in output:
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=missing fake success output")
    if not calls:
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=invoke_turn was not called")
    visible, technical = calls[-1]
    if "konkrétně capability krok `test`" not in visible:
        raise SystemExit(f"MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=unexpected visible prompt {visible!r}")
    expected = "GATEWAY_ADMIN_WORKSPACE_ACTION Test2 test --runner container --timeout 1800"
    if expected not in technical:
        raise SystemExit(
            "MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\n"
            f"reason=expected technical payload to contain {expected!r}, got {technical!r}"
        )

    calls.clear()
    rc, _ = mentor.request_followup_verify(
        Args(),
        "Test2",
        "install,verify,smoke,test,build,lint",
        retry_action="",
        retry_runner="",
        retry_timeout="",
    )
    if rc != 0:
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=fallback verify rc != 0")
    _, technical = calls[-1]
    fallback = "GATEWAY_ADMIN_WORKSPACE_AUTOPILOT Test2 --timeout 2400 --max-steps 1 --allow-actions install,verify,smoke,test,build,lint"
    if fallback not in technical:
        raise SystemExit(
            "MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\n"
            f"reason=expected fallback autopilot payload {fallback!r}, got {technical!r}"
        )

    print("MENTOR_RECOVERY_FOLLOWUP_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
