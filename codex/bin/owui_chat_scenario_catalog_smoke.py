#!/usr/bin/env python3
"""Offline smoke for OpenWebUI chat scenario catalog policy.

This keeps the user-like scenario catalog safe by default while still exposing
mutating coding-agent scenarios for intentional live verification.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_PATH = ROOT / "codex/bin/owui_chat_scenarios.py"


def load_module():
    spec = importlib.util.spec_from_file_location("owui_chat_scenarios_catalog_smoke", SCENARIOS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scenarios helper from {SCENARIOS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    scenarios = load_module()

    bootstrap = scenarios.SCENARIOS.get("bootstrap-followthrough")
    safe_edit = scenarios.SCENARIOS.get("safe-edit-verify")
    if bootstrap is None or safe_edit is None:
        raise SystemExit("OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\nreason=missing mutating coding scenarios")
    if not bootstrap.mutating or not safe_edit.mutating:
        raise SystemExit("OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\nreason=mutating coding scenarios must be flagged mutating")
    if "workflow=bootstrap" not in bootstrap.expected_substrings:
        raise SystemExit("OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\nreason=bootstrap scenario missing workflow marker")
    if "workflow=edit" not in safe_edit.expected_substrings:
        raise SystemExit("OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\nreason=safe edit scenario missing workflow marker")

    class Args:
        list = False
        scenario = []
        include_mutating = False

    default_scenarios = scenarios.selected_scenarios(Args())
    default_names = [item.name for item in default_scenarios]
    if any(item.mutating for item in default_scenarios):
        raise SystemExit(
            "OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\n"
            f"reason=default scenario selection must stay non-mutating, got {default_names!r}"
        )
    for required in ("agent-review", "verify-project"):
        if required not in default_names:
            raise SystemExit(
                "OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\n"
                f"reason=default scenarios missing {required!r}: {default_names!r}"
            )

    class MutatingBlockedArgs:
        list = False
        scenario = ["bootstrap-followthrough"]
        include_mutating = False

    try:
        scenarios.selected_scenarios(MutatingBlockedArgs())
    except SystemExit as exc:
        text = str(exc)
        if "OWUI_CHAT_SCENARIOS_BLOCKED" not in text:
            raise SystemExit(
                "OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\n"
                f"reason=unexpected blocked text {text!r}"
            )
    else:
        raise SystemExit(
            "OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\n"
            "reason=mutating scenario should require explicit include flag"
        )

    class MutatingAllowedArgs:
        list = False
        scenario = ["bootstrap-followthrough", "safe-edit-verify"]
        include_mutating = True

    allowed = scenarios.selected_scenarios(MutatingAllowedArgs())
    allowed_names = [item.name for item in allowed]
    if allowed_names != ["bootstrap-followthrough", "safe-edit-verify"]:
        raise SystemExit(
            "OWUI_CHAT_SCENARIO_CATALOG_SMOKE_FAILED\n"
            f"reason=unexpected allowed scenario selection {allowed_names!r}"
        )

    print("OWUI_CHAT_SCENARIO_CATALOG_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
