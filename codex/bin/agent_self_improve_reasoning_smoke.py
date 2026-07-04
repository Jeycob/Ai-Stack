#!/usr/bin/env python3
"""Offline smoke coverage for the LLM-first reasoning path in agent_self_improve."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.bin import agent_self_improve as asi


def make_args(**overrides) -> argparse.Namespace:
    base = {
        "workspace": "ai-stack",
        "chat_id": "",
        "chat_url": "",
        "transcript_file": "",
        "prompt": "repo: Test2\nvytvor tam ssh klic a vypis mi public",
        "failure_marker": "",
        "expected_behavior": "",
        "mode": "diagnose",
        "dry_run": True,
        "max_cycles": 1,
        "patch_file": "",
        "capability_name": "",
        "target_capability_name": "",
        "feature_request": "",
        "audit_root": "",
        "openwebui_base_url": "http://127.0.0.1:9090",
        "openwebui_api_key_env": "OWUI_API_KEY",
        "openwebui_api_key_file": "",
        "gateway_url": "http://127.0.0.1:9101",
        "model": "codex-local",
        "branch": "main",
        "timeout": 30.0,
        "command_timeout": 120,
        "e2e_prompt": "",
        "json": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def with_gateway_helpers(helpers: dict, fn):
    previous = asi._GATEWAY_REASONING_HELPERS
    previous_smoke = os.environ.pop("AGENT_SELF_IMPROVE_SMOKE_RUNNING", None)
    try:
        asi._GATEWAY_REASONING_HELPERS = helpers
        return fn()
    finally:
        asi._GATEWAY_REASONING_HELPERS = previous
        if previous_smoke is not None:
            os.environ["AGENT_SELF_IMPROVE_SMOKE_RUNNING"] = previous_smoke


def run_llm_reasoning_uses_gateway_normalizer() -> None:
    transcript = {
        "id": "chat-test",
        "messages": [
            {"role": "user", "content": "repo: Test2\nvytvor tam ssh klic a vypis mi public"},
        ],
    }
    diagnosis = asi.classify_failure("unsupported capability workspace_ssh_key_show_public", "", "")
    regression = asi.infer_regression(transcript, diagnosis, make_args())

    def fake_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        return (
            {
                "current_workspace": "Test2",
                "user_goal": "vytvorit nebo znovu pouzit SSH klic a vratit public key",
                "is_new_workspace_request": False,
                "is_existing_workspace_task": True,
                "target_repo_name": "Test2",
                "target_capability_name": "",
                "remote_url": "",
                "desired_end_state": "workspace_public_key_returned",
                "required_capabilities": ["workspace_ssh_key_show_public"],
                "missing_inputs": [],
                "risk_level": "low",
                "recovery_plan": "If key does not exist, create it first.",
                "read_only": False,
                "command": [],
                "action": "",
                "run_after": "",
                "followup_actions": [],
                "url": "",
                "question": "",
                "search_query": "",
                "ssh_comment": "Test2@local",
                "confidence": "high",
            },
            '{"required_capabilities":["workspace_ssh_key_show_public"]}',
            {},
        )

    def fake_normalize_agent_taskspec(spec, requested_workspace, controller_workspace, workspace_exists, task):
        normalized = dict(spec)
        normalized["required_capabilities"] = ["ssh_key_show_public"]
        normalized["current_workspace"] = "Test2"
        normalized["target_repo_name"] = "Test2"
        return normalized

    helpers = {
        "ok": True,
        "structured_json_chat": fake_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": fake_normalize_agent_taskspec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "ssh_key_show_public",
        "ROLE_PLANNER": "planner",
    }

    def run():
        with tempfile.TemporaryDirectory(prefix="asi-reasoning-") as tmp:
            args = make_args(audit_root=tmp)
            result = asi.reason(args, Path(tmp), transcript, diagnosis, regression)
            if result.get("planner") != "llm_taskspec_runtime":
                raise SystemExit(f"expected llm planner path, got {result!r}")
            task_spec = result.get("task_spec") or {}
            if task_spec.get("required_capabilities") != ["ssh_key_show_public"]:
                raise SystemExit(f"expected canonical capability from gateway normalizer, got {result!r}")
            if task_spec.get("current_workspace") != "Test2":
                raise SystemExit(f"expected normalized workspace context, got {result!r}")
            if not result.get("planner_raw"):
                raise SystemExit(f"expected raw planner output for auditability, got {result!r}")
            print("AGENT_SELF_IMPROVE_LLM_REASONING_OK")

    with_gateway_helpers(helpers, run)


def run_llm_reasoning_falls_back_cleanly() -> None:
    transcript = {
        "id": "chat-test",
        "messages": [
            {"role": "user", "content": "pridej capability workspace_profile pro bounded workspace profile"},
        ],
    }
    args = make_args(
        mode="capability_develop",
        prompt="pridej capability workspace_profile pro bounded workspace profile",
        capability_name="workspace_profile",
        target_capability_name="workspace_profile",
        feature_request="Add bounded workspace profiling capability.",
    )
    diagnosis = asi.classify_failure("timeout and disconnect", "", "")
    regression = asi.infer_regression(transcript, diagnosis, args)

    def failing_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        raise RuntimeError("planner backend unavailable")

    helpers = {
        "ok": True,
        "structured_json_chat": failing_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "agent_capability_develop",
        "ROLE_PLANNER": "planner",
    }

    def run():
        with tempfile.TemporaryDirectory(prefix="asi-reasoning-fallback-") as tmp:
            result = asi.reason(args, Path(tmp), transcript, diagnosis, regression)
            if result.get("planner") != "structured_taskspec_runtime_fallback":
                raise SystemExit(f"expected fallback planner marker, got {result!r}")
            task_spec = result.get("task_spec") or {}
            if task_spec.get("target_capability_name") != "workspace_profile":
                raise SystemExit(f"expected fallback spec to preserve target_capability_name, got {result!r}")
            if "agent_capability_develop" not in (task_spec.get("required_capabilities") or []):
                raise SystemExit(f"expected fallback spec to preserve capability_develop intent, got {result!r}")
            if "planner backend unavailable" not in str(result.get("planner_error") or ""):
                raise SystemExit(f"expected planner error in fallback payload, got {result!r}")
            print("AGENT_SELF_IMPROVE_LLM_FALLBACK_OK")

    with_gateway_helpers(helpers, run)


def main() -> int:
    run_llm_reasoning_uses_gateway_normalizer()
    run_llm_reasoning_falls_back_cleanly()
    print("AGENT_SELF_IMPROVE_REASONING_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
