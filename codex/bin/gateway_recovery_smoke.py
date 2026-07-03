#!/usr/bin/env python3
"""Offline smoke for gateway action-failure recovery rules.

This verifies that workspace_action_failure_recommendation() can derive
data-driven patch guidance from failure signatures declared in
docs/codex-local-capability-roadmap.json without needing a live workspace,
Docker, or OpenWebUI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.gateway import gateway


def assert_case(action: str, output: str, manifests: list[str], expected_target: str, expected_fragment: str) -> None:
    fake_scan = {
        "manifests": manifests,
        "languages": [],
        "package_scripts": [],
        "candidate_commands": [],
    }
    with patch.object(gateway, "CAPABILITY_ROADMAP_FILE", ROOT / "docs" / "codex-local-capability-roadmap.json"), patch.object(
        gateway, "load_workspace", return_value=Path("/tmp/fake-workspace")
    ), patch.object(gateway, "collect", return_value=fake_scan):
        result = gateway.workspace_action_failure_recommendation(
            "fake",
            action,
            {"output": output, "error": ""},
        )

    target = str(result.get("patch_target", ""))
    text = str(result.get("text", ""))
    if target != expected_target:
        raise SystemExit(f"expected patch_target={expected_target!r}, got {target!r} for action={action}")
    if expected_fragment not in text:
        raise SystemExit(f"expected fragment {expected_fragment!r} in text {text!r} for action={action}")
    print(f"RECOVERY_RULE_OK action={action} target={target}")


def main() -> int:
    assert_case(
        "test",
        "npm error Missing script: test",
        ["package.json"],
        "package.json",
        "nemá standardní test script",
    )
    assert_case(
        "build",
        "sh: 1: vite: not found",
        ["package.json"],
        "package.json",
        "chybějící front-end build tool",
    )
    assert_case(
        "smoke",
        "npm error Missing script: dev",
        ["package.json"],
        "package.json",
        "nemá standardní start/dev script",
    )
    assert_case(
        "install",
        "ERROR: Could not find a version that satisfies the requirement demo-pkg",
        ["pyproject.toml", "requirements.txt"],
        "requirements.txt",
        "neplatnou nebo nekompatibilní Python dependency",
    )
    print("GATEWAY_RECOVERY_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
