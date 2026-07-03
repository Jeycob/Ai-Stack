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
        if len(calls) == 1:
            return 0, "WORKSPACE_ACTION_OK\naction=test"
        return 0, "WORKSPACE_AUTOPILOT_OK\nchosen_action=build\nstop_reason=no_more_supported_actions"

    mentor.invoke_turn = fake_invoke_turn

    class Args:
        repo = "ai-stack"
        timeout = 2400

    rc, followup = mentor.request_recovery_followup(
        Args(),
        "Test2",
        "install,verify,smoke,test,build,lint",
        retry_action="test",
        retry_runner="container",
        retry_timeout="1800",
    )
    if rc != 0:
        raise SystemExit(f"MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=unexpected rc {rc}")
    if not calls or len(calls) != 2:
        raise SystemExit(
            "MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\n"
            f"reason=expected verify+continuation calls, got {len(calls)}"
        )
    if "WORKSPACE_ACTION_OK" not in str(followup.get("verify_output", "")):
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=missing verify output")
    if "WORKSPACE_AUTOPILOT_OK" not in str(followup.get("continuation_output", "")):
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=missing continuation output")
    if "WORKSPACE_AUTOPILOT_OK" not in str(followup.get("next_output", "")):
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=next_output did not advance to continuation")
    if not calls:
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=invoke_turn was not called")
    visible, technical = calls[0]
    if "konkrétně capability krok `test`" not in visible:
        raise SystemExit(f"MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=unexpected visible prompt {visible!r}")
    expected = "GATEWAY_ADMIN_WORKSPACE_ACTION Test2 test --runner container --timeout 1800"
    if expected not in technical:
        raise SystemExit(
            "MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\n"
            f"reason=expected technical payload to contain {expected!r}, got {technical!r}"
        )
    visible, technical = calls[1]
    expected = "GATEWAY_ADMIN_WORKSPACE_AUTOPILOT Test2 --timeout 2400 --max-steps 1 --allow-actions install,verify,smoke,test,build,lint"
    if expected not in technical:
        raise SystemExit(
            "MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\n"
            f"reason=expected continuation autopilot payload {expected!r}, got {technical!r}"
        )

    calls.clear()
    rc, followup = mentor.request_recovery_followup(
        Args(),
        "Test2",
        "install,verify,smoke,test,build,lint",
        retry_action="",
        retry_runner="",
        retry_timeout="",
    )
    if rc != 0:
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=fallback verify rc != 0")
    if len(calls) != 1:
        raise SystemExit(
            "MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\n"
            f"reason=expected single fallback verify call, got {len(calls)}"
        )
    if str(followup.get("continuation_output", "")):
        raise SystemExit("MENTOR_RECOVERY_FOLLOWUP_SMOKE_FAILED\nreason=fallback path should not add continuation output")
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
