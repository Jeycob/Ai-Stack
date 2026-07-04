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
    regression = asi.build_regression(transcript, diagnosis, make_args())

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


def run_llm_diagnosis_uses_runtime_output() -> None:
    transcript = {
        "id": "chat-diagnosis",
        "messages": [
            {"role": "user", "content": "repo: Test2\nkde ted jsi?"},
            {"role": "assistant", "content": "WORKSPACE_RUN_FAILED timeout recurse"},
        ],
    }
    args = make_args(prompt="repo: Test2\nkde ted jsi?")

    def fake_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        return (
            {
                "category": "meta_capability_routing_bug",
                "root_cause": "Meta intent fell through to executor recursion instead of deterministic workspace context capability.",
                "patch_scope": [
                    "codex/gateway/gateway.py",
                    "codex/bin/gateway_recovery_smoke.py",
                    ".env",
                ],
                "recovery": "Route the request through workspace_context_status and keep workspace run out of the loop.",
                "confidence": "high",
            },
            '{"category":"meta_capability_routing_bug"}',
            {},
        )

    helpers = {
        "ok": True,
        "structured_json_chat": fake_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "workspace_context_status",
        "extract_unified_diff": lambda text: text,
        "ROLE_PLANNER": "planner",
        "ROLE_AGENT": "agent",
    }

    def run():
        result = asi.build_diagnosis(args, transcript)
        if result.get("source") != "llm_diagnosis_runtime":
            raise SystemExit(f"expected llm diagnosis source, got {result!r}")
        if result.get("category") != "meta_capability_routing_bug":
            raise SystemExit(f"expected llm diagnosis category, got {result!r}")
        if result.get("patch_scope") != [
            "codex/gateway/gateway.py",
            "codex/bin/gateway_recovery_smoke.py",
        ]:
            raise SystemExit(f"expected patch scope filtering to drop blocked paths, got {result!r}")
        if not result.get("llm_raw"):
            raise SystemExit(f"expected llm raw diagnosis artifact, got {result!r}")
        print("AGENT_SELF_IMPROVE_LLM_DIAGNOSIS_OK")

    with_gateway_helpers(helpers, run)


def run_llm_diagnosis_falls_back_cleanly() -> None:
    transcript = {
        "id": "chat-diagnosis-fallback",
        "messages": [
            {"role": "user", "content": "repo: Test2\nvytvor tam ssh klic a vypis mi public"},
            {"role": "assistant", "content": "unsupported capability workspace_ssh_key_create"},
        ],
    }
    args = make_args(prompt="repo: Test2\nvytvor tam ssh klic a vypis mi public")

    def failing_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        raise RuntimeError("diagnosis backend unavailable")

    helpers = {
        "ok": True,
        "structured_json_chat": failing_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "ssh_key_show_public",
        "extract_unified_diff": lambda text: text,
        "ROLE_PLANNER": "planner",
        "ROLE_AGENT": "agent",
    }

    def run():
        result = asi.build_diagnosis(args, transcript)
        if result.get("source") != "structured_diagnosis_runtime_fallback":
            raise SystemExit(f"expected diagnosis fallback marker, got {result!r}")
        if result.get("category") != "capability_alias_or_registry_bug":
            raise SystemExit(f"expected deterministic diagnosis fallback category, got {result!r}")
        if "diagnosis backend unavailable" not in str(result.get("llm_error") or ""):
            raise SystemExit(f"expected diagnosis fallback error detail, got {result!r}")
        print("AGENT_SELF_IMPROVE_LLM_DIAGNOSIS_FALLBACK_OK")

    with_gateway_helpers(helpers, run)


def run_llm_regression_uses_runtime_output() -> None:
    transcript = {
        "id": "chat-regression",
        "messages": [
            {"role": "user", "content": "repo: Test2\nprohledej repo a hledej zminky o capability implementaci"},
            {"role": "assistant", "content": "obecne shrnuti bez skutecneho search"},
        ],
    }
    diagnosis = asi.classify_failure("repo search was hallucinated", "", "")
    args = make_args(prompt="repo: Test2\nprohledej repo a hledej zminky o capability implementaci")

    def fake_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        return (
            {
                "cases": [
                    {
                        "name": "workspace_search_capability_test2",
                        "prompt": "repo: Test2\nprohledej repo a hledej zminky o capability implementaci",
                        "expected_workflow": "workspace_search",
                        "expected_capability": "workspace_search",
                        "expected_marker": "matches",
                    }
                ]
            },
            '{"cases":[{"name":"workspace_search_capability_test2"}]}',
            {},
        )

    helpers = {
        "ok": True,
        "structured_json_chat": fake_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "workspace_search",
        "extract_unified_diff": lambda text: text,
        "ROLE_PLANNER": "planner",
        "ROLE_AGENT": "agent",
    }

    def run():
        result = asi.build_regression(transcript, diagnosis, args)
        if result.get("source") != "llm_regression_runtime":
            raise SystemExit(f"expected llm regression source, got {result!r}")
        cases = result.get("cases") or []
        if len(cases) != 1 or cases[0].get("expected_capability") != "workspace_search":
            raise SystemExit(f"expected workspace_search regression case, got {result!r}")
        if not result.get("llm_raw"):
            raise SystemExit(f"expected llm raw regression artifact, got {result!r}")
        print("AGENT_SELF_IMPROVE_LLM_REGRESSION_OK")

    with_gateway_helpers(helpers, run)


def run_llm_regression_falls_back_cleanly() -> None:
    transcript = {
        "id": "chat-regression-fallback",
        "messages": [
            {"role": "user", "content": "repo: Test2\nkde ted jsi?"},
            {"role": "assistant", "content": "WORKSPACE_RUN_FAILED recurse"},
        ],
    }
    diagnosis = asi.classify_failure("WORKSPACE_RUN_FAILED recurse", "", "")
    args = make_args(prompt="repo: Test2\nkde ted jsi?")

    def failing_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        raise RuntimeError("regression backend unavailable")

    helpers = {
        "ok": True,
        "structured_json_chat": failing_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "workspace_context_status",
        "extract_unified_diff": lambda text: text,
        "ROLE_PLANNER": "planner",
        "ROLE_AGENT": "agent",
    }

    def run():
        result = asi.build_regression(transcript, diagnosis, args)
        if result.get("source") != "structured_regression_runtime_fallback":
            raise SystemExit(f"expected regression fallback marker, got {result!r}")
        cases = result.get("cases") or []
        if not cases or cases[0].get("expected_capability") != "workspace_context_status":
            raise SystemExit(f"expected deterministic fallback regression case, got {result!r}")
        if "regression backend unavailable" not in str(result.get("llm_error") or ""):
            raise SystemExit(f"expected regression fallback error detail, got {result!r}")
        print("AGENT_SELF_IMPROVE_LLM_REGRESSION_FALLBACK_OK")

    with_gateway_helpers(helpers, run)


def run_llm_patch_proposal_uses_runtime_output() -> None:
    transcript = {
        "id": "chat-patch-proposal",
        "messages": [
            {"role": "user", "content": "repo: ai-stack\npridej capability workspace_profile pro bounded workspace profile"},
        ],
    }
    diagnosis = asi.classify_failure("missing capability workspace_profile", "", "")
    regression = asi.build_regression(transcript, diagnosis, make_args())
    args = make_args(
        mode="capability_develop",
        capability_name="workspace_profile",
        target_capability_name="workspace_profile",
        feature_request="Add bounded workspace profiling capability.",
    )
    reasoning = {
        "task_spec": {
            "current_workspace": "ai-stack",
            "user_goal": "Add bounded workspace profiling capability.",
            "target_capability_name": "workspace_profile",
            "required_capabilities": ["agent_capability_develop"],
            "acceptance_criteria": [
                "TaskSpec includes target_capability_name when a capability is being developed.",
                "Generated patch is a unified diff, touches only allowed paths, and passes git apply --check before any apply.",
            ],
        }
    }

    def fake_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        return (
            {
                "target_capability_name": "workspace_profile",
                "acceptance_criteria": [
                    "Capability appears in roadmap or draft artifact with scope, preconditions, executor plan, recovery and tests."
                ],
                "proposed_file_changes": [
                    {
                        "path": "codex/gateway/gateway.py",
                        "change_type": "update",
                        "intent": "Wire the canonical capability into registry and workflow mapping.",
                    },
                    {
                        "path": ".env",
                        "change_type": "update",
                        "intent": "This must be dropped by normalization.",
                    },
                ],
                "codex_local_tasks": [
                    "Draft capability registry/test/docs delta",
                ],
                "senior_codex_tasks": [
                    "Review runtime gateway wiring before promotion",
                ],
            },
            '{"target_capability_name":"workspace_profile"}',
            {},
        )

    helpers = {
        "ok": True,
        "structured_json_chat": fake_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "agent_capability_develop\nworkspace_profile",
        "extract_unified_diff": lambda text: text,
        "ROLE_PLANNER": "planner",
        "ROLE_AGENT": "agent",
    }

    def run():
        with tempfile.TemporaryDirectory(prefix="asi-patch-proposal-") as tmp:
            result = asi.propose_patch(args, Path(tmp), diagnosis, regression, reasoning)
            if result.get("source") != "llm_patch_proposal_runtime":
                raise SystemExit(f"expected llm patch proposal source, got {result!r}")
            if result.get("target_capability_name") != "workspace_profile":
                raise SystemExit(f"expected target capability name in proposal, got {result!r}")
            file_changes = result.get("proposed_file_changes") or []
            if not any(item.get("path") == "codex/gateway/gateway.py" for item in file_changes if isinstance(item, dict)):
                raise SystemExit(f"expected llm-selected gateway change, got {result!r}")
            if any(item.get("path") == ".env" for item in file_changes if isinstance(item, dict)):
                raise SystemExit(f"blocked file should have been dropped from proposal, got {result!r}")
            acceptance = result.get("acceptance_criteria") or []
            if "TaskSpec includes target_capability_name when a capability is being developed." not in acceptance:
                raise SystemExit(f"expected fallback acceptance criteria to be preserved, got {result!r}")
            if "Capability appears in roadmap or draft artifact with scope, preconditions, executor plan, recovery and tests." not in acceptance:
                raise SystemExit(f"expected llm acceptance criteria to merge in, got {result!r}")
            if not result.get("llm_raw"):
                raise SystemExit(f"expected raw llm proposal artifact, got {result!r}")
            print("AGENT_SELF_IMPROVE_LLM_PATCH_PROPOSAL_OK")

    with_gateway_helpers(helpers, run)


def run_llm_patch_proposal_falls_back_cleanly() -> None:
    transcript = {
        "id": "chat-patch-proposal-fallback",
        "messages": [
            {"role": "user", "content": "repo: Test2\nvytvor tam ssh klic a vypis mi public"},
        ],
    }
    diagnosis = asi.classify_failure("unsupported capability workspace_ssh_key_create", "", "")
    regression = asi.build_regression(transcript, diagnosis, make_args())
    args = make_args(prompt="repo: Test2\nvytvor tam ssh klic a vypis mi public")
    reasoning = {
        "task_spec": {
            "current_workspace": "Test2",
            "user_goal": "vytvor tam ssh klic a vypis mi public",
            "required_capabilities": ["ssh_key_show_public"],
            "acceptance_criteria": ["A regression artifact exists before any patch is applied."],
        }
    }

    def failing_structured_json_chat(model_id, messages, schema_name, schema, timeout=0):
        raise RuntimeError("patch proposal backend unavailable")

    helpers = {
        "ok": True,
        "structured_json_chat": failing_structured_json_chat,
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "ssh_key_show_public",
        "extract_unified_diff": lambda text: text,
        "ROLE_PLANNER": "planner",
        "ROLE_AGENT": "agent",
    }

    def run():
        with tempfile.TemporaryDirectory(prefix="asi-patch-proposal-fallback-") as tmp:
            result = asi.propose_patch(args, Path(tmp), diagnosis, regression, reasoning)
            if result.get("source") != "structured_patch_proposal_runtime_fallback":
                raise SystemExit(f"expected fallback patch proposal source, got {result!r}")
            file_changes = result.get("proposed_file_changes") or []
            if not any(item.get("path") == "codex/gateway/gateway.py" for item in file_changes if isinstance(item, dict)):
                raise SystemExit(f"expected deterministic fallback file changes, got {result!r}")
            if "patch proposal backend unavailable" not in str(result.get("llm_error") or ""):
                raise SystemExit(f"expected llm error detail in fallback proposal, got {result!r}")
            print("AGENT_SELF_IMPROVE_LLM_PATCH_PROPOSAL_FALLBACK_OK")

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
    regression = asi.build_regression(transcript, diagnosis, args)

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


def run_llm_diff_generation_uses_runtime_draft() -> None:
    diagnosis = {
        "category": "capability_alias_or_registry_bug",
        "root_cause": "TaskSpec capability alias was not canonicalized.",
        "recovery": "Canonicalize before validation.",
        "patch_scope": ["codex/gateway/gateway.py", "README.md"],
        "expected_behavior": "ssh public key workflow resolves canonically",
    }
    regression = {
        "cases": [
            {
                "name": "ssh_public_key_alias_test2",
                "expected_workflow": "ssh_key_show_public",
                "expected_capability": "ssh_key_show_public",
                "expected_marker": "public_key_path",
            }
        ]
    }
    reasoning = {
        "task_spec": {
            "current_workspace": "ai-stack",
            "user_goal": "Priprav maly patch pro canonical ssh public key capability routing.",
            "desired_end_state": "canonical capability path works",
            "acceptance_criteria": [
                "Capability aliases are canonicalized before validation.",
                "Smoke regression captures the fixed ssh public key path.",
            ],
        }
    }
    proposal = {
        "proposed_file_changes": [
            {"path": "README.md", "intent": "Document the fix."},
            {"path": "codex/gateway/gateway.py", "intent": "Fix canonical capability handling."},
        ]
    }

    diff_text = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,5 +1,6 @@
 # AI Stack
 
+LLM diff draft smoke marker.
 Lokální AI stack pro OpenWebUI, Ollama a izolované Codex/OpenCode workspaces. Repozitář slouží jako verzovaná konfigurace pro domácí AI prostředí, správu coding agentů a budoucí integrace typu Home Assistant nebo analýza výdajů.
 
 ## Komponenty
"""

    def fake_ollama_chat(model_id, messages, timeout=0, response_format=None):
        return {
            "choices": [
                {
                    "message": {
                        "content": f"```diff\n{diff_text}```"
                    }
                }
            ],
            "usage": {"total_tokens": 123},
        }

    helpers = {
        "ok": True,
        "structured_json_chat": lambda *args, **kwargs: ({}, "{}", {}),
        "codex_local_runtime_model_name": lambda **_: "codex-local",
        "normalize_agent_taskspec": lambda spec, *_args: spec,
        "agent_taskspec_schema": lambda: {"type": "object"},
        "agent_capability_catalog": lambda: "ssh_key_show_public",
        "extract_unified_diff": lambda raw: diff_text if "diff --git" in raw else (_ for _ in ()).throw(ValueError("missing diff")),
        "ROLE_PLANNER": "planner",
        "ROLE_AGENT": "agent",
    }

    def run():
        from codex.gateway import gateway as gateway_module

        previous_ollama_chat = gateway_module.ollama_chat
        try:
            gateway_module.ollama_chat = fake_ollama_chat
            with tempfile.TemporaryDirectory(prefix="asi-llm-diff-") as tmp:
                args = make_args(
                    audit_root=tmp,
                    mode="generate_unified_diff",
                    prompt="repo: ai-stack\noprav canonical ssh key capability routing",
                    command_timeout=120,
                )
                result = asi.generate_unified_diff(args, Path(tmp), diagnosis, regression, reasoning, proposal)
                if result.get("source") != "llm_unified_diff":
                    raise SystemExit(f"expected llm diff source, got {result!r}")
                if result.get("ok") is not True:
                    raise SystemExit(f"expected llm diff to pass git apply --check, got {result!r}")
                llm_attempt = result.get("llm_attempt") or {}
                if llm_attempt.get("model") != "codex-local":
                    raise SystemExit(f"expected llm attempt model audit, got {result!r}")
                if (llm_attempt.get("check") or {}).get("ok") is not True:
                    raise SystemExit(f"expected llm attempt check metadata, got {result!r}")
                print("AGENT_SELF_IMPROVE_LLM_DIFF_OK")
        finally:
            gateway_module.ollama_chat = previous_ollama_chat

    with_gateway_helpers(helpers, run)


def main() -> int:
    run_llm_diagnosis_uses_runtime_output()
    run_llm_diagnosis_falls_back_cleanly()
    run_llm_regression_uses_runtime_output()
    run_llm_regression_falls_back_cleanly()
    run_llm_patch_proposal_uses_runtime_output()
    run_llm_patch_proposal_falls_back_cleanly()
    run_llm_reasoning_uses_gateway_normalizer()
    run_llm_reasoning_falls_back_cleanly()
    run_llm_diff_generation_uses_runtime_draft()
    print("AGENT_SELF_IMPROVE_REASONING_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
