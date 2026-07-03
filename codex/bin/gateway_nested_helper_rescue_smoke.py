#!/usr/bin/env python3
"""Offline regression smoke for nested helper rescue in gateway admin_run_workspace.

This guards the backend authority layer itself. Even if an upstream filter or
helper emits a nested mentor/OpenWebUI command, gateway must not blindly run a
recursive helper inside workspace-run. It should rescue mentor helper intent to
the agent loop, and block raw owui_chat_turn recursion explicitly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_PATH = ROOT / "codex/gateway/gateway.py"


def load_gateway_module():
    spec = importlib.util.spec_from_file_location("codex_gateway_nested_rescue_test", GATEWAY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load gateway module from {GATEWAY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_mentor_helper_rescues_to_agent_loop(gateway) -> None:
    captured: dict[str, object] = {}
    original = gateway.admin_agent_loop
    try:
        def fake_agent_loop(payload):
            captured["payload"] = dict(payload)
            return {
                "ok": True,
                "requested_workspace": payload["workspace"],
                "controller_workspace": payload["workspace"],
                "planner_source": "smoke",
                "workflow": "review",
                "read_only": True,
                "summary": "rescued",
                "answer": "Rescue OK",
            }

        gateway.admin_agent_loop = fake_agent_loop
        result = gateway.admin_run_workspace(
            {
                "workspace": "ai-stack",
                "runner": "container",
                "timeout": 30,
                "command": [
                    sys.executable,
                    "codex/bin/mentor_codex_local.py",
                    "delegate",
                    "ai-stack",
                    "Prohlédni architekturu gateway/filter/helper vrstvy. Nic needituj.",
                ],
            }
        )
    finally:
        gateway.admin_agent_loop = original

    payload = captured.get("payload")
    if not isinstance(payload, dict):
        raise SystemExit("GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=admin_agent_loop was not called")
    if payload.get("workspace") != "ai-stack":
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=unexpected workspace payload {payload!r}")
    if "Nic needituj" not in str(payload.get("task", "")):
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=unexpected task payload {payload!r}")
    if not result.get("rescued") or result.get("rescue_kind") != "mentor_helper_to_agent_loop":
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=missing rescue marker {result!r}")
    if result.get("runner") != "agent_loop" or result.get("ok") is not True:
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=unexpected result {result!r}")
    if "AGENT_LOOP_OK" not in str(result.get("output", "")):
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=agent loop output missing {result!r}")


def assert_raw_owui_helper_is_blocked(gateway) -> None:
    result = gateway.admin_run_workspace(
        {
            "workspace": "ai-stack",
            "runner": "container",
            "timeout": 30,
            "command": [
                sys.executable,
                "codex/bin/owui_chat_turn.py",
                "--model",
                "codex-local-plan-qwen14b",
                "--prompt",
                "repo: ai-stack\nahoj",
            ],
        }
    )
    if result.get("ok") is not False:
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=owui helper should be blocked {result!r}")
    if result.get("rescue_kind") != "owui_helper_blocked":
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=missing owui block marker {result!r}")
    if "WORKSPACE_RUN_NESTED_OWUI_HELPER_BLOCKED" not in str(result.get("output", "")):
        raise SystemExit(f"GATEWAY_NESTED_HELPER_RESCUE_SMOKE_FAILED\nreason=missing owui block text {result!r}")


def main() -> int:
    gateway = load_gateway_module()
    assert_mentor_helper_rescues_to_agent_loop(gateway)
    assert_raw_owui_helper_is_blocked(gateway)
    print("GATEWAY_NESTED_HELPER_RESCUE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
