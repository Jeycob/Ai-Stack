#!/usr/bin/env python3
"""Offline smoke for improve outcome classification."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MENTOR_PATH = ROOT / "codex/bin/mentor_codex_local.py"


def load_module():
    spec = importlib.util.spec_from_file_location("mentor_codex_local_outcome_smoke", MENTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load mentor helper from {MENTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assert_case(mentor, raw_output: str, expected_status: str, expected_fragment: str) -> None:
    status, reason = mentor.classify_improve_outcome(raw_output)
    if status != expected_status:
        raise SystemExit(
            "MENTOR_IMPROVE_OUTCOME_SMOKE_FAILED\n"
            f"expected_status={expected_status!r}\nactual_status={status!r}\nraw_output={raw_output!r}"
        )
    if expected_fragment not in reason:
        raise SystemExit(
            "MENTOR_IMPROVE_OUTCOME_SMOKE_FAILED\n"
            f"expected_fragment={expected_fragment!r}\nactual_reason={reason!r}"
        )


def main() -> int:
    mentor = load_module()

    assert_case(
        mentor,
        "WORKSPACE_AUTOPILOT_OK\nstop_reason=no_more_supported_actions\nrecommendation=\npatch_target=none\nread_command=none",
        "completed",
        "without surfacing a new blocker",
    )
    assert_case(
        mentor,
        "WORKSPACE_AUTOPILOT_OK\nstop_reason=max_steps_reached\nrecommendation=\npatch_target=none\nread_command=none",
        "capability_progress",
        "step limit",
    )
    assert_case(
        mentor,
        "IMPROVE_READY_CYCLE_1\nWORKSPACE_ACTION_OK\naction=test\nWORKSPACE_AUTOPILOT_OK\nstop_reason=no_more_supported_actions\nrecommendation=\npatch_target=none\nread_command=none",
        "completed",
        "recovery cleared the known blocker",
    )
    assert_case(
        mentor,
        "IMPROVE_READY_CYCLE_1\nWORKSPACE_ACTION_OK\naction=test\nrecommendation=Inspect package.json\npatch_target=package.json\nread_command=GATEWAY_ADMIN_READ_NUMBERED package.json 1 220",
        "recovered_to_new_blocker",
        "new concrete blocker",
    )
    assert_case(
        mentor,
        "IMPROVE_BLOCKED_CYCLE_1\nreason=diff validation failed",
        "blocked",
        "remained blocked",
    )

    print("MENTOR_IMPROVE_OUTCOME_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
