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
import tempfile
import json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.gateway import gateway


def assert_no_hardcoded_direct_answer_router() -> None:
    text = (ROOT / "codex/gateway/gateway.py").read_text(encoding="utf-8")
    forbidden = (
        "czech_runtime_date_text",
        "direct_identity_answer_requested",
        "direct_runtime_date_requested",
        "deterministic_direct_answer",
    )
    found = [name for name in forbidden if name in text]
    if found:
        raise SystemExit(f"direct_answer must stay LLM/context-first; forbidden hardcoded helpers found: {found!r}")
    print("NO_HARDCODED_DIRECT_ANSWER_ROUTER_OK")


def assert_agent_loop_parse() -> None:
    parsed = gateway.explicit_agent_loop_request(
        "repo: ai-stack\n"
        "GATEWAY_ADMIN_AGENT_LOOP ai-stack -- Prohlédni workspace. Nic needituj."
    )
    if parsed != {"workspace": "ai-stack", "task": "Prohlédni workspace. Nic needituj."}:
        raise SystemExit(f"expected explicit agent-loop parse, got {parsed!r}")
    print("AGENT_LOOP_PARSE_OK")


def assert_fallback_plans() -> None:
    cases = [
        ("repo: TestCode\ninitni git repo a pushni sem git@github.com:owner/repo.git", "workspace_git_publish"),
        ("https://www.seznam.cz/", "web_fetch"),
        ("`pwd`", "run"),
    ]
    for task, workflow in cases:
        plan = gateway.agent_fallback_plan(task, "ai-stack", "ai-stack", True)
        if not plan:
            raise SystemExit(f"expected fallback plan for {task!r}")
        got = plan[0].get("workflow")
        if got != workflow:
            raise SystemExit(f"expected workflow={workflow!r}, got {got!r} for {task!r}")
    if gateway.agent_fallback_plan("kdo ma dneska svatek? stahni mi to z seznam.cz", "ai-stack", "ai-stack", True):
        raise SystemExit("natural-language web question without URL must require LLM planner, not fallback phrase routing")
    if gateway.agent_fallback_plan("pullni ai-stack a nasad", "ai-stack", "ai-stack", True):
        raise SystemExit("natural-language deploy request must require LLM planner, not fallback phrase routing")
    if gateway.agent_fallback_plan("vytvor repozitar: svatektest", "ai-stack", "ai-stack", True):
        raise SystemExit("natural-language bootstrap must require LLM TaskSpec, not fallback phrase routing")
    if gateway.agent_fallback_plan("Prohlédni architekturu. Nic needituj.", "ai-stack", "ai-stack", True):
        raise SystemExit("natural-language read-only review must require LLM TaskSpec, not fallback phrase routing")
    print("AGENT_FALLBACK_PLAN_OK")


def assert_bootstrap_followup_inference() -> None:
    task = "vytvor repozitar: svatektest a pak stahni co je treba a pust to"
    if gateway.agent_fallback_plan(task, "ai-stack", "ai-stack", True):
        raise SystemExit("legacy fallback must not bootstrap or infer follow-up actions from Czech/English phrases")

    normalized = gateway.normalize_agent_plan(
        {
            "workflow": "bootstrap",
            "reason": "smoke bootstrap",
            "read_only": False,
            "workspace": "ai-stack",
            "repo_name": "svatektest",
            "followup_actions": ["install", "smoke"],
            "confidence": "high",
            "capability_locked": True,
        },
        "ai-stack",
        "ai-stack",
        True,
        task,
    )
    followups = normalized.get("followup_actions") or []
    if "install" not in followups or "smoke" not in followups:
        raise SystemExit(f"TaskSpec-provided install+smoke followups should be preserved, got {normalized!r}")
    print("BOOTSTRAP_FOLLOWUP_INFERENCE_OK")


def assert_bootstrap_beats_workspace_ssh_for_new_repo() -> None:
    cases = (
        ("vytvor mi nove repository TestCode\nvygeneruj do nej ssh klic", "TestCode"),
        ("vytvor mi nove repository TestCode; vygeneruj do nej ssh klic", "TestCode"),
        ("Test11:\nvytvoř repository a vygeneruj ssh klic; pred push/deploy pockej na potvrzeni", "Test11"),
    )
    for task, expected_name in cases:
        if gateway.agent_fallback_plan(task, "ai-stack", "ai-stack", True):
            raise SystemExit(f"combined bootstrap+ssh prompt must require TaskSpec planner, got fallback for {task!r}")
        normalized = gateway.normalize_agent_plan(
            {
                "workflow": "bootstrap",
                "reason": "TaskSpec selected new workspace bootstrap",
                "read_only": False,
                "workspace": "ai-stack",
                "repo_name": expected_name,
                "confidence": "medium",
                "capability_locked": True,
                "required_capabilities": ["workspace_repo_bootstrap"],
            },
            "ai-stack",
            "ai-stack",
            True,
            task,
        )
        if normalized.get("workflow") != "bootstrap":
            raise SystemExit(f"expected normalized bootstrap workflow for combined bootstrap+ssh prompt, got {normalized!r}")
        if normalized.get("repo_name") != expected_name:
            raise SystemExit(f"expected repo_name={expected_name!r}, got {normalized!r}")
    print("BOOTSTRAP_BEATS_SSH_INTENT_OK")


def assert_verify_prefers_action_over_run_without_explicit_command() -> None:
    task = "Ověř projekt a vrať stručný audit výsledků."
    normalized = gateway.normalize_agent_plan(
        {
            "workflow": "action",
            "reason": "TaskSpec selected verify",
            "read_only": False,
            "workspace": "ai-stack",
            "action": "verify",
            "command": [],
            "confidence": "medium",
            "capability_locked": True,
            "required_capabilities": ["workspace_action:verify"],
        },
        "ai-stack",
        "ai-stack",
        True,
        task,
    )
    if normalized.get("workflow") != "action":
        raise SystemExit(f"TaskSpec-provided action workflow should stay action, got {normalized!r}")
    if normalized.get("action") != "verify":
        raise SystemExit(f"expected verify action for verify prompt, got {normalized!r}")
    if normalized.get("command") != []:
        raise SystemExit(f"expected command to be cleared after run->action normalization, got {normalized!r}")

    drift = gateway.normalize_agent_plan(
        {
            "workflow": "run",
            "reason": "planner drift smoke",
            "read_only": False,
            "workspace": "ai-stack",
            "command": ["pwd"],
            "confidence": "medium",
        },
        "ai-stack",
        "ai-stack",
        True,
        task,
    )
    if drift.get("workflow") != "run":
        raise SystemExit(f"natural-language verify phrase must not rewrite run workflow without TaskSpec capability, got {drift!r}")
    print("VERIFY_ACTION_NORMALIZATION_OK")


def assert_explicit_command_stays_run() -> None:
    task = "Spusť příkaz: pwd a vrať výstup."
    with patch.object(
        gateway,
        "load_workspace_action_registry",
        return_value={
            "verify": {
                "cues": ["ověř projekt", "over projekt", "verify project"],
            }
        },
    ):
        normalized = gateway.normalize_agent_plan(
            {
                "workflow": "run",
                "reason": "explicit command smoke",
                "read_only": False,
                "workspace": "ai-stack",
                "command": ["pwd"],
                "confidence": "high",
            },
            "ai-stack",
            "ai-stack",
            True,
            task,
        )
    if normalized.get("workflow") != "run":
        raise SystemExit(f"explicit command should stay run, got {normalized!r}")
    if normalized.get("command") != ["pwd"]:
        raise SystemExit(f"explicit run command changed unexpectedly, got {normalized!r}")
    print("EXPLICIT_RUN_NORMALIZATION_OK")


def assert_workspace_git_publish_manual_recovery() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        workspace_root = tmp / "TestCode"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspaces_file = tmp / "workspaces.json"
        workspaces_file.write_text(
            json.dumps(
                {
                    "default": "ai-stack",
                    "workspaces": {
                        "ai-stack": {"path": str(tmp / "ai-stack"), "port": 4098, "cpus": 8, "memory": "16g"},
                        "TestCode": {"path": str(workspace_root), "port": 4100, "cpus": 8, "memory": "16g"},
                    },
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        with patch.object(gateway, "WORKSPACES_FILE", str(workspaces_file)), patch.object(
            gateway,
            "ensure_workspace_runtime_ssh_key",
            return_value={
                "workspace": "TestCode",
                "container_private_key": "/home/opencode/.ssh/github-TestCode_ed25519",
                "container_public_key": "/home/opencode/.ssh/github-TestCode_ed25519.pub",
                "public_key": "ssh-ed25519 AAAATEST TestCode@local",
                "source_key": {
                    "public_key_path": "codex/state/ssh/github-TestCode_ed25519.pub",
                    "public_key": "ssh-ed25519 AAAATEST TestCode@local",
                },
            },
        ), patch.object(
            gateway,
            "admin_run_workspace",
            return_value={
                "ok": False,
                "runner": "container",
                "output": "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.",
            },
        ):
            result = gateway.admin_workspace_git_publish({
                "workspace": "TestCode",
                "remote_url": "git@github.com:owner/repo.git",
            })
    if result.get("status") != "MANUAL_STEP_REQUIRED":
        raise SystemExit(f"expected MANUAL_STEP_REQUIRED, got {result!r}")
    if "ssh-ed25519" not in str(result.get("public_key") or ""):
        raise SystemExit(f"expected public key in manual recovery, got {result!r}")
    print("WORKSPACE_GIT_PUBLISH_MANUAL_RECOVERY_OK")


def assert_capability_locked_plan_stays_on_taskspec_workflow() -> None:
    task = "repo: TestCode\ninitni git repo a pushni sem git@github.com:owner/repo.git"
    normalized = gateway.normalize_agent_plan(
        {
            "workflow": "workspace_git_publish",
            "workspace": "TestCode",
            "remote_url": "git@github.com:owner/repo.git",
            "required_capabilities": ["workspace_git_publish"],
            "capability_locked": True,
            "confidence": "high",
        },
        "TestCode",
        "ai-stack",
        True,
        task,
    )
    if normalized.get("workflow") != "workspace_git_publish":
        raise SystemExit(f"capability_locked plan drifted unexpectedly, got {normalized!r}")

    review_locked = gateway.normalize_agent_plan(
        {
            "workflow": "review",
            "workspace": "TestCode",
            "required_capabilities": ["workspace_review"],
            "capability_locked": True,
            "confidence": "high",
        },
        "TestCode",
        "ai-stack",
        True,
        "TestCode oprav to a spust testy",
    )
    if review_locked.get("workflow") != "review":
        raise SystemExit(f"capability_locked review should stay review, got {review_locked!r}")
    print("CAPABILITY_LOCKED_NORMALIZATION_OK")


def assert_workspace_action_capability_registry_mapping() -> None:
    taskspec = gateway.normalize_agent_taskspec(
        {
            "current_workspace": "ai-stack",
            "user_goal": "verify the project",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "workspace_verified",
            "required_capabilities": ["workspace_action:verify"],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "Use verify capability.",
            "read_only": False,
        },
        "ai-stack",
        "ai-stack",
        True,
        "Ověř projekt a vrať stručný audit výsledků.",
    )
    plan = gateway.agent_taskspec_to_plan(taskspec, "ai-stack", "ai-stack", True, "Ověř projekt a vrať stručný audit výsledků.")
    if plan.get("workflow") != "action" or plan.get("action") != "verify":
        raise SystemExit(f"workspace_action capability should resolve to verify action, got {plan!r}")
    print("WORKSPACE_ACTION_CAPABILITY_REGISTRY_OK")


def assert_read_only_instruction_overrides_action_words() -> None:
    task = "repo: ai-stack\nNic needituj. Odpovez jednou vetou: live smoke ok."
    taskspec = gateway.normalize_agent_taskspec(
        {
            "current_workspace": "ai-stack",
            "user_goal": "Perform a live smoke test.",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "live smoke ok",
            "required_capabilities": ["action"],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "Return NEEDS_ATTENTION if unsupported.",
            "read_only": False,
            "action": "smoke",
            "command": ["smoke"],
        },
        "ai-stack",
        "ai-stack",
        True,
        task,
    )
    plan = gateway.agent_taskspec_to_plan(taskspec, "ai-stack", "ai-stack", True, task)
    if taskspec.get("read_only") is not True:
        raise SystemExit(f"read-only instruction should override planner read_only=false, got {taskspec!r}")
    if taskspec.get("required_capabilities") != ["review"]:
        raise SystemExit(f"read-only answer should not keep action capability, got {taskspec!r}")
    if taskspec.get("missing_capabilities"):
        raise SystemExit(f"read-only answer should not report missing action capability, got {taskspec!r}")
    if taskspec.get("action") or taskspec.get("command"):
        raise SystemExit(f"read-only answer should clear action/command, got {taskspec!r}")
    if plan.get("workflow") != "review" or plan.get("read_only") is not True:
        raise SystemExit(f"read-only answer should resolve to review workflow, got {plan!r}")
    print("READ_ONLY_ACTION_WORD_OVERRIDE_OK")


def assert_taskspec_capability_selector_repairs_existing_workspace_publish() -> None:
    task = "repo: TestCode\ninitni git repo a pushni sem git@github.com:owner/repo.git"
    selector_response = {
        "choices": [
            {
                "message": {
                    "content": '{"required_capabilities":["workspace_git_publish"],"desired_end_state":"git_init_origin_commit_push_main","confidence":"high","recovery_plan":"If auth fails, return MANUAL_STEP_REQUIRED with the public key."}'
                }
            }
        ]
    }
    with patch.object(gateway, "ollama_chat", return_value=selector_response):
        taskspec = gateway.normalize_agent_taskspec({}, "TestCode", "ai-stack", True, task)
    if taskspec.get("required_capabilities") != ["workspace_git_publish"]:
        raise SystemExit(f"expected LLM selector to repair required_capabilities, got {taskspec!r}")
    if taskspec.get("capability_selector_source") != "llm_capability_selector":
        raise SystemExit(f"expected capability_selector_source=llm_capability_selector, got {taskspec!r}")
    print("TASKSPEC_CAPABILITY_SELECTOR_PUBLISH_OK")


def assert_taskspec_capability_selector_falls_back_to_heuristics() -> None:
    task = "repo: TestCode\ninitni git repo a pushni sem git@github.com:owner/repo.git"
    with patch.object(gateway, "ollama_chat", side_effect=RuntimeError("selector offline")):
        taskspec = gateway.normalize_agent_taskspec({}, "TestCode", "ai-stack", True, task)
    if taskspec.get("required_capabilities") != ["workspace_git_publish"]:
        raise SystemExit(f"expected heuristic fallback to keep git publish capability, got {taskspec!r}")
    if taskspec.get("capability_selector_source") != "structural_fallback":
        raise SystemExit(f"expected capability_selector_source=structural_fallback, got {taskspec!r}")
    print("TASKSPEC_CAPABILITY_SELECTOR_STRUCTURAL_FALLBACK_OK")


def assert_autopilot_llm_candidate_selection() -> None:
    def fake_action(payload):
        action = str(payload.get("action") or "")
        dry_run = bool(payload.get("dry_run"))
        if dry_run:
            if action == "verify":
                return {
                    "ok": True,
                    "action": "verify",
                    "verify_steps": [
                        {"action": "install", "supported": True, "command": ["npm", "install"], "resolved_from": "package.json:scripts"},
                        {"action": "verify", "supported": True, "command": ["npm", "run", "verify"], "resolved_from": "package.json:scripts"},
                    ],
                    "duration_ms": 1,
                }
            if action == "install":
                return {"ok": True, "action": "install", "planned_only": True, "command": ["npm", "install"], "resolved_from": "package.json:scripts", "output": ""}
            return {"ok": False, "action": action, "output": ""}
        return {
            "ok": action == "verify",
            "action": action,
            "exit_code": 0 if action == "verify" else 1,
            "runner_exit_code": 0 if action == "verify" else 1,
            "duration_ms": 2,
            "resolved_from": "package.json:scripts",
            "command": ["npm", "run", action],
            "output": f"ran {action}",
            "error": "" if action == "verify" else "failed",
        }

    with patch.object(
        gateway,
        "ollama_chat",
        return_value={"choices": [{"message": {"content": '{"action":"verify","reason":"Nejdřív chci levné ověření místo instalace."}'}}]},
    ), patch.object(gateway, "admin_workspace_action", side_effect=fake_action):
        result = gateway.admin_workspace_autopilot({
            "workspace": "ai-stack",
            "allow_actions": ["install", "verify"],
            "max_steps": 1,
            "task": "Ověř projekt a pokračuj nejbližším bezpečným krokem.",
            "desired_end_state": "workspace_verified",
        })

    if result.get("ok") is not True:
        raise SystemExit(f"expected autopilot to complete selected verify step, got {result!r}")
    executed = result.get("executed_actions") or []
    if not executed or executed[0].get("action") != "verify":
        raise SystemExit(f"expected LLM planner to choose verify over install, got {result!r}")
    if result.get("planner_source") != "llm":
        raise SystemExit(f"expected planner_source='llm', got {result!r}")
    print("AUTOPILOT_LLM_SELECTION_OK")


def assert_autopilot_planner_fallback() -> None:
    def fake_action(payload):
        action = str(payload.get("action") or "")
        dry_run = bool(payload.get("dry_run"))
        if dry_run:
            if action == "verify":
                return {
                    "ok": True,
                    "action": "verify",
                    "verify_steps": [
                        {"action": "install", "supported": True, "command": ["npm", "install"], "resolved_from": "package.json:scripts"},
                    ],
                    "duration_ms": 1,
                }
            if action == "install":
                return {"ok": True, "action": "install", "planned_only": True, "command": ["npm", "install"], "resolved_from": "package.json:scripts", "output": ""}
            return {"ok": False, "action": action, "output": ""}
        return {
            "ok": action == "install",
            "action": action,
            "exit_code": 0 if action == "install" else 1,
            "runner_exit_code": 0 if action == "install" else 1,
            "duration_ms": 2,
            "resolved_from": "package.json:scripts",
            "command": ["npm", "run", action],
            "output": f"ran {action}",
            "error": "" if action == "install" else "failed",
        }

    with patch.object(
        gateway,
        "ollama_chat",
        return_value={"choices": [{"message": {"content": '{"action":"destroy_everything","reason":"bad"}'}}]},
    ), patch.object(gateway, "admin_workspace_action", side_effect=fake_action):
        result = gateway.admin_workspace_autopilot({
            "workspace": "ai-stack",
            "allow_actions": ["install", "verify"],
            "max_steps": 1,
            "task": "Připrav prostředí a pokračuj.",
            "desired_end_state": "workspace_ready",
        })

    if result.get("ok") is not True:
        raise SystemExit(f"expected fallback autopilot to execute first safe action, got {result!r}")
    executed = result.get("executed_actions") or []
    if not executed or executed[0].get("action") != "install":
        raise SystemExit(f"expected fallback autopilot to choose install, got {result!r}")
    if result.get("planner_source") != "fallback":
        raise SystemExit(f"expected planner_source='fallback', got {result!r}")
    print("AUTOPILOT_PLANNER_FALLBACK_OK")


def assert_unknown_capability_needs_attention() -> None:
    task = "Synchronizuj produkční databázi přes neexistující remote capability."
    taskspec = gateway.normalize_agent_taskspec(
        {
            "current_workspace": "ai-stack",
            "user_goal": "sync production database",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "production_database_synced",
            "required_capabilities": ["remote_database_write"],
            "missing_inputs": [],
            "risk_level": "high",
            "recovery_plan": "Add an audited remote_database_write capability before executing.",
            "read_only": False,
        },
        "ai-stack",
        "ai-stack",
        True,
        task,
    )
    plan = gateway.agent_taskspec_to_plan(taskspec, "ai-stack", "ai-stack", True, task)
    if plan.get("workflow") != "clarify":
        raise SystemExit(f"expected clarify workflow for unknown capability, got {plan!r}")
    if plan.get("missing_capabilities") != ["remote_database_write"]:
        raise SystemExit(f"expected missing_capabilities to carry unknown capability, got {plan!r}")

    with patch.object(
        gateway,
        "agent_controller_workspace",
        return_value=("ai-stack", True, {"ai-stack": {"path": str(ROOT)}}),
    ), patch.object(
        gateway,
        "agent_plan",
        return_value=(plan, taskspec, '{"required_capabilities":["remote_database_write"]}'),
    ):
        result = gateway.admin_agent_loop({"workspace": "ai-stack", "task": task})
    if result.get("ok"):
        raise SystemExit(f"unknown capability must not be ok, got {result!r}")
    if result.get("workflow") != "clarify":
        raise SystemExit(f"expected clarify workflow in agent result, got {result!r}")
    recovery = result.get("recovery") or {}
    if recovery.get("missing_capabilities") != ["remote_database_write"]:
        raise SystemExit(f"expected recovery to list missing capability, got {result!r}")
    text = gateway.agent_loop_response_text(result)
    if "AGENT_LOOP_NEEDS_ATTENTION" not in text or "remote_database_write" not in text:
        raise SystemExit(f"expected visible NEEDS_ATTENTION with missing capability, got {text!r}")
    print("UNKNOWN_CAPABILITY_NEEDS_ATTENTION_OK")


def _taskspec_plan(spec: dict, task: str, workspace: str = "Test2", workspace_exists: bool = True):
    taskspec = gateway.normalize_agent_taskspec(spec, workspace, "ai-stack", workspace_exists, task)
    plan = gateway.agent_taskspec_to_plan(taskspec, workspace, "ai-stack", workspace_exists, task)
    return taskspec, plan


def assert_taskspec_capability_alias_canonicalization() -> None:
    task = "vytvor tam ssh klic"
    taskspec, plan = _taskspec_plan(
        {
            "current_workspace": "Test2",
            "user_goal": "create workspace ssh key",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "workspace_ssh_key_ready",
            "required_capabilities": ["workspace_ssh_key_create"],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "create key idempotently",
            "read_only": False,
        },
        task,
    )
    if taskspec.get("required_capabilities") != ["ssh_key_create"]:
        raise SystemExit(f"expected canonical ssh_key_create, got {taskspec!r}")
    if taskspec.get("missing_capabilities"):
        raise SystemExit(f"alias must not be missing, got {taskspec!r}")
    if plan.get("workflow") != "ssh_key_create":
        raise SystemExit(f"expected ssh_key_create workflow, got {plan!r}")

    mixed, mixed_plan = _taskspec_plan(
        {
            "current_workspace": "Test2",
            "user_goal": "create and show workspace ssh key",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "workspace_public_key_returned",
            "required_capabilities": ["workspace_ssh_key_create", "ssh_key_create", "ssh_key_show_public"],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "show public key",
            "read_only": False,
        },
        "vytvor tam ssh klic a vypis mi public",
    )
    if mixed.get("required_capabilities") != ["ssh_key_show_public"]:
        raise SystemExit(f"show_public should dominate create, got {mixed!r}")
    if mixed.get("missing_capabilities"):
        raise SystemExit(f"mixed canonical caps must not be missing, got {mixed!r}")
    if mixed_plan.get("workflow") != "ssh_key_show_public":
        raise SystemExit(f"expected ssh_key_show_public workflow, got {mixed_plan!r}")
    print("TASKSPEC_CAPABILITY_ALIAS_CANONICALIZATION_OK")


def assert_taskspec_public_key_prompt_uses_show_public() -> None:
    taskspec, plan = _taskspec_plan(
        {
            "current_workspace": "Test2",
            "user_goal": "create or reuse ssh key and show public key",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "workspace_public_key_returned",
            "required_capabilities": ["workspace_ssh_key_create"],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "show public key",
            "read_only": False,
        },
        "vytvor tam ssh klic a vypis mi public",
    )
    if "ssh_key_show_public" not in taskspec.get("required_capabilities", []):
        raise SystemExit(f"public key prompt should canonicalize to ssh_key_show_public, got {taskspec!r}")
    if "ssh_key_create" in taskspec.get("required_capabilities", []):
        raise SystemExit(f"show_public should subsume create, got {taskspec!r}")
    if taskspec.get("missing_capabilities"):
        raise SystemExit(f"expected no missing capability, got {taskspec!r}")
    if plan.get("workflow") != "ssh_key_show_public":
        raise SystemExit(f"expected ssh_key_show_public workflow, got {plan!r}")
    print("TASKSPEC_PUBLIC_KEY_PROMPT_SHOW_PUBLIC_OK")


def assert_taskspec_unknown_capability_stays_missing() -> None:
    taskspec, plan = _taskspec_plan(
        {
            "current_workspace": "Test2",
            "user_goal": "write to remote database",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "remote_database_written",
            "required_capabilities": ["remote_database_write"],
            "missing_inputs": [],
            "risk_level": "high",
            "recovery_plan": "add audited database capability",
            "read_only": False,
        },
        "zapis do remote databaze",
    )
    if plan.get("workflow") != "clarify":
        raise SystemExit(f"unknown capability should clarify, got {plan!r}")
    if plan.get("missing_capabilities") != ["remote_database_write"]:
        raise SystemExit(f"unknown capability should remain missing, got {plan!r}")
    print("TASKSPEC_UNKNOWN_CAPABILITY_STAYS_MISSING_OK")


def assert_taskspec_meta_capabilities() -> None:
    for capability, task in (
        ("workspace_context_set", "Prepni se do workspace Test2"),
        ("workspace_context_status", "kde ted jsi?"),
        ("capability_catalog_show", "jake mas capability?"),
    ):
        taskspec, plan = _taskspec_plan(
            {
                "current_workspace": "Test2",
                "user_goal": task,
                "is_new_workspace_request": False,
                "is_existing_workspace_task": True,
                "target_repo_name": "",
                "remote_url": "",
                "desired_end_state": "metadata_returned",
                "required_capabilities": [capability],
                "missing_inputs": [],
                "risk_level": "low",
                "recovery_plan": "return deterministic metadata",
                "read_only": False,
            },
            task,
        )
        if taskspec.get("missing_capabilities"):
            raise SystemExit(f"meta capability must not be missing for {task!r}: {taskspec!r}")
        if plan.get("workflow") != "meta":
            raise SystemExit(f"meta capability should map to meta workflow for {task!r}: {plan!r}")
    print("TASKSPEC_META_CAPABILITIES_OK")


def assert_workspace_search_capability() -> None:
    taskspec, plan = _taskspec_plan(
        {
            "current_workspace": "Test2",
            "user_goal": "search repository for capability implementation",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "search_results_returned",
            "required_capabilities": ["workspace_search"],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "return bounded search results",
            "read_only": True,
            "search_query": "capability",
        },
        "prohledej repo a hledej capability",
    )
    if taskspec.get("missing_capabilities") or taskspec.get("missing_inputs"):
        raise SystemExit(f"workspace_search should be supported with query, got {taskspec!r}")
    if plan.get("workflow") != "workspace_search":
        raise SystemExit(f"workspace_search capability should map to search workflow, got {plan!r}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        workspace_root = tmp / "Test2"
        workspace_root.mkdir()
        (workspace_root / "README.md").write_text("capability implementation marker\n", encoding="utf-8")
        workspaces_file = tmp / "workspaces.json"
        workspaces_file.write_text(
            json.dumps(
                {
                    "default": "Test2",
                    "workspaces": {
                        "Test2": {"path": str(workspace_root), "port": 4100, "cpus": 8, "memory": "16g"}
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with patch.object(gateway, "WORKSPACES_FILE", str(workspaces_file)):
            result = gateway.admin_workspace_search({"workspace": "Test2", "query": "capability", "max_matches": 10})
            with patch.dict(gateway.os.environ, {"PATH": ""}):
                fallback_result = gateway.admin_workspace_search({"workspace": "Test2", "query": "capability", "max_matches": 10})
    if not result.get("ok") or result.get("match_count", 0) < 1:
        raise SystemExit(f"expected bounded workspace search result, got {result!r}")
    if not fallback_result.get("ok") or fallback_result.get("search_backend") != "python_fallback" or fallback_result.get("match_count", 0) < 1:
        raise SystemExit(f"expected bounded Python fallback search result without rg, got {fallback_result!r}")
    print("WORKSPACE_SEARCH_CAPABILITY_OK")


def assert_taskspec_v2_intents_and_renderer() -> None:
    direct, direct_plan = _taskspec_plan(
        {
            "intent_class": "direct_answer",
            "referents": {},
            "current_workspace": "ai-stack",
            "target_workspace": "",
            "user_goal": "answer simple math",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": False,
            "target_repo_name": "",
            "target_capability_name": "",
            "remote_url": "",
            "desired_end_state": "answer_returned",
            "required_capabilities": [],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "",
            "read_only": False,
            "command": [],
            "action": "",
            "run_after": "",
            "followup_actions": [],
            "execution_plan": [],
            "url": "",
            "question": "",
            "search_query": "",
            "ssh_comment": "",
            "confidence": "high",
            "needs_user_input": False,
            "answer_visibility": "summary",
        },
        "kolik je 10+52",
        workspace="ai-stack",
        workspace_exists=True,
    )
    if direct.get("required_capabilities") != ["direct_answer"] or direct_plan.get("workflow") != "direct_answer":
        raise SystemExit(f"direct_answer intent should not route to repo workflow, got {direct!r} {direct_plan!r}")

    direct_spec = dict(direct)
    direct_loop_plan = dict(direct_plan)
    with patch.object(gateway, "agent_plan", return_value=(direct_loop_plan, direct_spec, {"source": "smoke"})):
        with patch.object(
            gateway,
            "ollama_chat",
            return_value={"choices": [{"message": {"content": "62"}}], "usage": {"total_tokens": 3}},
        ):
            direct_result = gateway.admin_agent_loop({"workspace": "ai-stack", "task": "kolik je 10+52"})
    if direct_result.get("workflow") != "direct_answer" or str(direct_result.get("answer") or "").strip() != "62":
        raise SystemExit(f"direct answer should return 62 through agent loop, got {direct_result!r}")
    rendered_direct = gateway.agent_loop_response_text(direct_result)
    if rendered_direct.splitlines()[0].strip() != "62" or "workflow=direct_answer" not in rendered_direct:
        raise SystemExit(f"direct answer renderer should be concise with debug hidden below, got {rendered_direct!r}")

    creative, creative_plan = _taskspec_plan(
        {**direct, "intent_class": "creative_answer", "required_capabilities": [], "user_goal": "write a short story"},
        "write me a story",
        workspace="ai-stack",
        workspace_exists=True,
    )
    if creative.get("required_capabilities") != ["creative_answer"] or creative_plan.get("workflow") != "direct_answer":
        raise SystemExit(f"creative_answer intent should use direct workflow, got {creative!r} {creative_plan!r}")

    help_spec, help_plan = _taskspec_plan(
        {**direct, "intent_class": "capability_help", "required_capabilities": [], "user_goal": "show capabilities"},
        "jake mas capability?",
        workspace="ai-stack",
        workspace_exists=True,
    )
    if "capability_catalog_show" not in help_spec.get("required_capabilities", []) or help_plan.get("workflow") != "meta":
        raise SystemExit(f"capability_help should map to meta catalog, got {help_spec!r} {help_plan!r}")
    help_meta = gateway.admin_agent_meta(help_plan, "ai-stack", "ai-stack", True, {"ai-stack": {"path": "/repo/ai-stack"}})
    help_answer = str(help_meta.get("answer") or "")
    if "codex-local" not in help_answer or "`repo: Test2`" not in help_answer:
        raise SystemExit(f"capability_help answer should be human and practical, got {help_meta!r}")
    generic_help = gateway.normalize_agent_taskspec(
        {
            "intent_class": "capability_help",
            "current_workspace": "ai-stack",
            "user_goal": "show capabilities",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": False,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "human capability summary returned",
            "required_capabilities": [],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "",
            "read_only": True,
        },
        "ai-stack",
        "ai-stack",
        True,
        "co umis?",
    )
    if generic_help.get("intent_class") != "capability_help" or "capability_catalog_show" not in generic_help.get("required_capabilities", []):
        raise SystemExit(f"TaskSpec capability_help should normalize to capability catalog, got {generic_help!r}")

    offline_help = gateway.normalize_agent_taskspec(
        {
            "current_workspace": "ai-stack",
            "user_goal": "show capabilities",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": False,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "human capability summary returned",
            "required_capabilities": [],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "",
            "read_only": True,
        },
        "ai-stack",
        "ai-stack",
        True,
        "co umis?",
    )
    if offline_help.get("intent_class") == "capability_help" or "capability_catalog_show" in offline_help.get("required_capabilities", []):
        raise SystemExit(f"gateway core must not infer capability_help from raw language without TaskSpec, got {offline_help!r}")

    context_meta = gateway.admin_agent_meta(
        {"meta_capability": "workspace_context_status"},
        "Test2",
        "ai-stack",
        True,
        {"Test2": {"path": "/repo/Test2"}, "ai-stack": {"path": "/repo/ai-stack"}},
    )
    context_answer = str(context_meta.get("answer") or "")
    if "workspace `Test2`" not in context_answer or "/repo/Test2" not in context_answer:
        raise SystemExit(f"workspace_context_status answer should mention workspace and path, got {context_meta!r}")

    web_spec, web_plan = _taskspec_plan(
        {
            **direct,
            "intent_class": "web_search",
            "user_goal": "find public answer",
            "required_capabilities": [],
            "question": "kdo ma dneska svatek",
            "search_query": "kdo ma dneska svatek",
        },
        "vyhledej to kdekoliv",
        workspace="ai-stack",
        workspace_exists=True,
    )
    if web_spec.get("required_capabilities") != ["public_web_search"] or web_plan.get("workflow") != "web_search":
        raise SystemExit(f"web_search without URL should use public_web_search, got {web_spec!r} {web_plan!r}")
    if "workspace_search" in web_spec.get("required_capabilities", []):
        raise SystemExit(f"public web search must not become workspace_search, got {web_spec!r}")
    followup_web = gateway.normalize_agent_taskspec(
        {
            **direct,
            "intent_class": "web_search",
            "referents": {"to": "kdo ma dneska svatek?"},
            "required_capabilities": [],
            "question": "",
            "search_query": "",
        },
        "Test2",
        "ai-stack",
        True,
        "vyhledej to kdekoliv",
        "user: kdo ma dneska svatek?\nassistant: Muzu to dohledat na webu.\nuser: vyhledej to kdekoliv",
    )
    if followup_web.get("search_query") != "kdo ma dneska svatek?" or "public_web_search" not in followup_web.get("required_capabilities", []):
        raise SystemExit(f"follow-up web referent should reuse previous topic, got {followup_web!r}")

    unresolved_followup_web = gateway.normalize_agent_taskspec(
        {
            **direct,
            "intent_class": "web_search",
            "required_capabilities": [],
            "question": "",
            "search_query": "",
        },
        "Test2",
        "ai-stack",
        True,
        "vyhledej to kdekoliv",
        "user: kdo ma dneska svatek?\nassistant: Muzu to dohledat na webu.\nuser: vyhledej to kdekoliv",
    )
    if "resolved_search_query" not in unresolved_followup_web.get("missing_inputs", []):
        raise SystemExit(f"gateway core must not resolve follow-up web text without TaskSpec referent, got {unresolved_followup_web!r}")

    chain_spec, chain_plan = _taskspec_plan(
        {
            **direct,
            "intent_class": "workspace_action_chain",
            "user_goal": "install build test and expose app",
            "required_capabilities": [],
            "read_only": False,
            "execution_plan": [
                {"capability": "workspace_action_chain", "goal": "install/build/test"},
                {"capability": "workspace_expose_preview", "goal": "show preview"},
            ],
        },
        "vytvor appku, nainstaluj, buildni, otestuj a vystav preview",
        workspace="Test2",
        workspace_exists=True,
    )
    if chain_plan.get("workflow") != "autopilot" or "workspace_action_chain" not in chain_spec.get("required_capabilities", []):
        raise SystemExit(f"workspace action chain should route to autopilot, got {chain_spec!r} {chain_plan!r}")
    if "workspace_expose_preview" not in chain_spec.get("required_capabilities", []):
        raise SystemExit(f"workspace action chain with preview should keep preview capability, got {chain_spec!r}")

    autopilot_preview_text = gateway.agent_loop_human_answer(
        {
            "ok": True,
            "workflow": "autopilot",
            "requested_workspace": "Test2",
            "execution": {
                "executed_actions": [{"action": "install"}, {"action": "build"}, {"action": "smoke"}],
                "wants_preview": True,
                "preview_url": "http://127.0.0.1:4173",
            },
        }
    )
    if "Preview běží na `http://127.0.0.1:4173`." not in autopilot_preview_text:
        raise SystemExit(f"autopilot preview answer should expose preview url, got {autopilot_preview_text!r}")

    confirm_spec, confirm_plan = _taskspec_plan(
        {
            **direct,
            "intent_class": "workspace_action_chain",
            "required_capabilities": ["await_user_confirmation", "workspace_git_publish"],
            "needs_user_input": True,
            "read_only": False,
            "remote_url": "git@github.com:owner/repo.git",
            "recovery_plan": "Potvrď push na GitHub.",
        },
        "pushni to po potvrzeni",
        workspace="Test2",
        workspace_exists=True,
    )
    if "await_user_confirmation" not in confirm_spec.get("required_capabilities", []) or confirm_plan.get("workflow") != "clarify":
        raise SystemExit(f"push/deploy confirmation should stay explicit, got {confirm_spec!r} {confirm_plan!r}")
    if confirm_plan.get("workflow") != "clarify" or confirm_plan.get("needs_user_input") is not True:
        raise SystemExit(f"await confirmation should pause in clarify workflow, got {confirm_spec!r} {confirm_plan!r}")

    rendered = gateway.agent_loop_response_text(
        {
            "ok": True,
            "requested_workspace": "Test2",
            "controller_workspace": "ai-stack",
            "planner_source": "llm",
            "workflow": "edit",
            "read_only": False,
            "summary": "Safe edit applied and verified.",
            "answer": "Hotovo.",
            "plan": {"workflow": "edit", "reason": "smoke"},
            "execution": {"files": ["README.md"], "run_after": "test"},
        }
    )
    first_line = rendered.splitlines()[0]
    if "AGENT_LOOP" in first_line:
        raise SystemExit(f"renderer should start with human answer, got {rendered!r}")
    for marker in ("Ve workspace Test2 jsem upravil README.md.", "Změněné soubory: `README.md`", "CODEX_DEBUG", "AGENT_LOOP_OK", "workflow=edit"):
        if marker not in rendered:
            raise SystemExit(f"renderer missing marker {marker!r}: {rendered!r}")
    print("TASKSPEC_V2_INTENTS_AND_RENDERER_OK")


def assert_agent_self_improve_capability() -> None:
    registry = gateway.agent_capability_registry()
    entry = registry.get("agent_self_improve")
    if not entry or entry.get("workflow") != "self_improve" or not entry.get("implemented"):
        raise SystemExit(f"agent_self_improve must be implemented in registry, got {entry!r}")
    develop_entry = registry.get("agent_capability_develop")
    if not develop_entry or develop_entry.get("workflow") != "self_improve" or not develop_entry.get("implemented"):
        raise SystemExit(f"agent_capability_develop must be implemented in registry, got {develop_entry!r}")
    taskspec, plan = _taskspec_plan(
        {
            "current_workspace": "ai-stack",
            "user_goal": "diagnose OpenWebUI failure and create regression artifact",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "failure_pattern_recorded",
            "required_capabilities": ["self_improvement"],
            "missing_inputs": [],
            "risk_level": "medium",
            "recovery_plan": "collect transcript and run smoke verification",
            "read_only": False,
        },
        "zpracuj fail z OpenWebUI chatu a vytvor self-improve regression",
        workspace="ai-stack",
        workspace_exists=True,
    )
    if taskspec.get("required_capabilities") != ["agent_self_improve"]:
        raise SystemExit(f"expected canonical agent_self_improve, got {taskspec!r}")
    if taskspec.get("missing_capabilities"):
        raise SystemExit(f"self-improve capability must not be missing, got {taskspec!r}")
    if plan.get("workflow") != "self_improve":
        raise SystemExit(f"expected self_improve workflow, got {plan!r}")

    taskspec, plan = _taskspec_plan(
        {
            "current_workspace": "ai-stack",
            "user_goal": "add a new codex-local capability for bounded workspace profiling",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "capability_design_artifact_created",
            "required_capabilities": ["capability_implement"],
            "missing_inputs": [],
            "risk_level": "medium",
            "recovery_plan": "propose registry, executor, tests and docs",
            "read_only": False,
        },
        "navrhni novou capability pro codex-local",
        workspace="ai-stack",
        workspace_exists=True,
    )
    if taskspec.get("required_capabilities") != ["agent_capability_develop"]:
        raise SystemExit(f"expected canonical agent_capability_develop, got {taskspec!r}")
    if taskspec.get("missing_capabilities"):
        raise SystemExit(f"capability develop must not be missing, got {taskspec!r}")
    if plan.get("workflow") != "self_improve":
        raise SystemExit(f"expected self_improve workflow for capability develop, got {plan!r}")

    with patch.object(gateway, "agent_select_capabilities_with_llm", side_effect=RuntimeError("offline smoke")):
        taskspec, plan = _taskspec_plan(
            {
                "current_workspace": "ai-stack",
                "user_goal": "add a workspace profiling capability for repository summaries",
                "is_new_workspace_request": False,
                "is_existing_workspace_task": True,
                "target_repo_name": "",
                "target_capability_name": "workspace_profile",
                "remote_url": "",
                "desired_end_state": "workspace_profile capability patch draft created",
                "required_capabilities": [],
                "missing_inputs": [],
                "risk_level": "medium",
                "recovery_plan": "generate guarded capability patch draft",
                "read_only": False,
            },
            "přidej capability workspace_profile pro shrnutí workspace",
            workspace="ai-stack",
            workspace_exists=True,
        )
    if taskspec.get("target_capability_name") != "workspace_profile":
        raise SystemExit(f"expected target_capability_name to survive normalization, got {taskspec!r}")
    if taskspec.get("required_capabilities") != ["agent_capability_develop"]:
        raise SystemExit(f"expected target capability to imply agent_capability_develop, got {taskspec!r}")
    if plan.get("workflow") != "self_improve" or plan.get("target_capability_name") != "workspace_profile":
        raise SystemExit(f"expected self_improve plan with target capability, got {plan!r}")
    with patch.object(gateway, "agent_select_capabilities_with_llm", side_effect=RuntimeError("offline smoke")):
        taskspec, plan = _taskspec_plan(
            {
                "current_workspace": "ai-stack",
                "user_goal": "přidej capability workspace_search_index pro rychlejší capability search workflow",
                "is_new_workspace_request": False,
                "is_existing_workspace_task": True,
                "target_repo_name": "",
                "target_capability_name": "",
                "remote_url": "",
                "desired_end_state": "workspace_search_index capability patch draft created",
                "required_capabilities": [],
                "missing_inputs": [],
                "risk_level": "medium",
                "recovery_plan": "generate guarded capability patch draft",
                "read_only": False,
            },
            "přidej capability workspace_search_index pro rychlejší capability search workflow",
            workspace="ai-stack",
            workspace_exists=True,
        )
    if taskspec.get("target_capability_name"):
        raise SystemExit(f"gateway core must not infer target_capability_name from natural language, got {taskspec!r}")
    if taskspec.get("required_capabilities") != ["clarify_or_infer_capability"] or plan.get("workflow") != "clarify":
        raise SystemExit(f"natural capability prompt without TaskSpec should clarify instead of parsing language, got {taskspec!r} {plan!r}")

    with patch.object(gateway, "agent_select_capabilities_with_llm", side_effect=RuntimeError("offline smoke")):
        taskspec, plan = _taskspec_plan(
            {
                "current_workspace": "ai-stack",
                "user_goal": "přidej capability workspace_search_index pro rychlejší capability search workflow",
                "is_new_workspace_request": False,
                "is_existing_workspace_task": True,
                "target_repo_name": "",
                "target_capability_name": "workspace_search_index",
                "remote_url": "",
                "desired_end_state": "workspace_search_index capability patch draft created",
                "required_capabilities": ["agent_capability_develop"],
                "missing_inputs": [],
                "risk_level": "medium",
                "recovery_plan": "generate guarded capability patch draft",
                "read_only": False,
            },
            "přidej capability workspace_search_index pro rychlejší capability search workflow",
            workspace="ai-stack",
            workspace_exists=True,
        )
    if taskspec.get("target_capability_name") != "workspace_search_index":
        raise SystemExit(f"expected TaskSpec target_capability_name to survive normalization, got {taskspec!r}")
    if taskspec.get("required_capabilities") != ["agent_capability_develop"]:
        raise SystemExit(f"expected TaskSpec target capability to canonicalize to agent_capability_develop, got {taskspec!r}")
    if plan.get("workflow") != "self_improve" or plan.get("target_capability_name") != "workspace_search_index":
        raise SystemExit(f"expected TaskSpec capability prompt to route into self_improve, got {plan!r}")
    with tempfile.TemporaryDirectory(prefix="gateway-cap-roadmap-") as tmp:
        roadmap_path = Path(tmp) / "roadmap.json"
        roadmap_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "capabilities": {
                        "workspace_profile": {
                            "scope": "workspace_capability",
                            "workflow": "clarify",
                            "planned_workflow": "autopilot",
                            "implemented": False,
                            "draft": True,
                            "summary": "Draft bounded workspace profiling capability.",
                            "aliases": ["profile_workspace", "workspace-profiling"],
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with patch.object(gateway, "CAPABILITY_ROADMAP_FILE", roadmap_path):
            registry = gateway.agent_capability_registry()
            runtime_draft = registry.get("workspace_profile") or {}
            if runtime_draft.get("implemented") is not False:
                raise SystemExit(f"expected roadmap-backed draft capability to stay unimplemented, got {runtime_draft!r}")
            if runtime_draft.get("planned_workflow") != "autopilot":
                raise SystemExit(f"expected runtime draft planned workflow from roadmap metadata, got {runtime_draft!r}")
            if gateway.canonicalize_agent_capability("workspace-profiling") != "workspace_profile":
                raise SystemExit("expected dynamic roadmap alias to canonicalize to workspace_profile")
    print("AGENT_SELF_IMPROVE_CAPABILITY_OK")


def assert_admin_agent_self_improve_forwarding() -> None:
    captured = {}

    class DummyProc:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(cmd, cwd=None, text=None, stdout=None, stderr=None, timeout=None):
        captured["cmd"] = list(cmd)
        captured["cwd"] = str(cwd)
        captured["timeout"] = timeout
        payload = {
            "ok": True,
            "artifact_dir": "/tmp/agent-self-improve-forwarding",
            "mode": "capability_develop",
            "dry_run": True,
        }
        return DummyProc(json.dumps(payload, ensure_ascii=False))

    with patch.object(gateway.subprocess, "run", side_effect=fake_run):
        result = gateway.admin_agent_self_improve(
            {
                "workspace": "ai-stack",
                "mode": "capability_develop",
                "dry_run": True,
                "max_cycles": 9,
                "timeout": 123,
                "prompt": "pridej capability workspace_profile",
                "capability_name": "workspace_profile",
                "target_capability_name": "workspace_profile",
                "feature_request": "Add bounded workspace profiling capability.",
            }
        )
    cmd = captured.get("cmd") or []
    if "--mode" not in cmd or cmd[cmd.index("--mode") + 1] != "capability_develop":
        raise SystemExit(f"expected forwarded capability_develop mode, got {cmd!r}")
    if "--target-capability-name" not in cmd or cmd[cmd.index("--target-capability-name") + 1] != "workspace_profile":
        raise SystemExit(f"expected forwarded target capability name, got {cmd!r}")
    if "--capability-name" not in cmd or cmd[cmd.index("--capability-name") + 1] != "workspace_profile":
        raise SystemExit(f"expected forwarded capability name, got {cmd!r}")
    if "--feature-request" not in cmd or cmd[cmd.index("--feature-request") + 1] != "Add bounded workspace profiling capability.":
        raise SystemExit(f"expected forwarded feature request, got {cmd!r}")
    if "--max-cycles" not in cmd or cmd[cmd.index("--max-cycles") + 1] != "3":
        raise SystemExit(f"expected max_cycles clamp to 3, got {cmd!r}")
    if "--dry-run" not in cmd:
        raise SystemExit(f"expected dry-run flag in forwarded command, got {cmd!r}")
    if captured.get("timeout") != 123:
        raise SystemExit(f"expected forwarded timeout=123, got {captured!r}")
    if not result.get("ok") or result.get("mode") != "capability_develop" or result.get("dry_run") is not True:
        raise SystemExit(f"expected successful forwarding result, got {result!r}")
    print("ADMIN_AGENT_SELF_IMPROVE_FORWARDING_OK")


def assert_capability_draft_contracts() -> None:
    roadmap_path = ROOT / "docs" / "codex-local-capability-roadmap.json"
    if not roadmap_path.is_file():
        print("CAPABILITY_DRAFT_CONTRACTS_SKIPPED")
        return
    roadmap = json.loads(roadmap_path.read_text(encoding="utf-8"))
    capabilities = roadmap.get("capabilities") or {}
    draft_dir = ROOT / "docs" / "capability-drafts"
    if not draft_dir.is_dir():
        print("CAPABILITY_DRAFT_CONTRACTS_OK")
        return

    contract_files = sorted(draft_dir.glob("*.smoke.json"))
    for contract_file in contract_files:
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
        capability = str(contract.get("capability_name") or "").strip()
        if not capability:
            raise SystemExit(f"draft smoke contract missing capability_name: {contract_file}")
        expected = contract.get("expected_registry") or {}
        roadmap_entry = capabilities.get(capability) or {}
        if not roadmap_entry:
            raise SystemExit(f"roadmap missing capability {capability!r} declared by {contract_file}")
        if bool(roadmap_entry.get("implemented", True)) != bool(expected.get("implemented", False)):
            raise SystemExit(f"implemented mismatch for {capability}: roadmap={roadmap_entry!r} contract={expected!r}")
        if bool(roadmap_entry.get("draft", False)) != bool(expected.get("draft", True)):
            raise SystemExit(f"draft mismatch for {capability}: roadmap={roadmap_entry!r} contract={expected!r}")
        for key in ("scope", "workflow", "planned_workflow", "executor"):
            wanted = str(expected.get(key) or "").strip()
            got = str(roadmap_entry.get(key) or "").strip()
            if wanted and got != wanted:
                raise SystemExit(f"{key} mismatch for {capability}: wanted={wanted!r} got={got!r}")
        expected_aliases = gateway.canonicalize_agent_capabilities(expected.get("aliases") or [])
        roadmap_aliases = gateway.canonicalize_agent_capabilities(roadmap_entry.get("aliases") or [])
        if expected_aliases != roadmap_aliases:
            raise SystemExit(f"alias mismatch for {capability}: roadmap={roadmap_aliases!r} contract={expected_aliases!r}")
        for alias in contract.get("verifier_expectations", {}).get("canonical_alias_roundtrip") or []:
            if gateway.canonicalize_agent_capability(alias) != capability:
                raise SystemExit(f"alias {alias!r} did not canonicalize to {capability!r}")
        for rel in contract.get("verifier_expectations", {}).get("required_paths") or []:
            if not (ROOT / rel).is_file():
                raise SystemExit(f"required capability draft path missing for {capability}: {rel}")
        markers = contract.get("verifier_expectations", {}).get("required_markers") or {}
        gateway_integration_path = ROOT / f"docs/capability-drafts/{capability}.gateway-integration.json"
        gateway_patch_fragment_path = ROOT / f"docs/capability-drafts/{capability}.gateway.patch.md"
        gateway_runtime_patch_candidate_path = ROOT / f"docs/capability-drafts/{capability}.runtime.patch.diff"
        gateway_promotion_patch_path = ROOT / f"docs/capability-drafts/{capability}.promotion.patch.diff"
        wiring_path = ROOT / f"docs/capability-drafts/{capability}.wiring.json"
        executor_contract_path = ROOT / f"docs/capability-drafts/{capability}.executor-contract.json"
        executor_dispatch_path = ROOT / f"docs/capability-drafts/{capability}.executor-dispatch.json"
        implementation_workorder_path = ROOT / f"docs/capability-drafts/{capability}.implementation-workorder.json"
        executor_stub_path = ROOT / f"codex/bin/capability_drafts/{capability}_executor_stub.py"
        runtime_hook_stub_path = ROOT / f"codex/bin/capability_drafts/{capability}_runtime_hook_stub.py"
        smoke_stub_path = ROOT / f"codex/bin/capability_drafts/{capability}_smoke.py"
        if gateway_integration_path.is_file():
            integration = json.loads(gateway_integration_path.read_text(encoding="utf-8"))
            if str(integration.get("kind") or "") != str(markers.get("gateway_integration_kind") or ""):
                raise SystemExit(f"gateway integration kind mismatch for {capability}: {integration!r}")
            workflow_map = (((integration.get("snippets") or {}).get("workflow_map") or {}).get("code") or {})
            if capability not in workflow_map:
                raise SystemExit(f"gateway integration draft missing workflow map for {capability}: {integration!r}")
        if gateway_patch_fragment_path.is_file():
            patch_fragment_text = gateway_patch_fragment_path.read_text(encoding="utf-8")
            if str(markers.get("gateway_patch_fragment_marker") or "").strip() not in patch_fragment_text:
                raise SystemExit(f"gateway patch fragment marker mismatch for {capability}: {gateway_patch_fragment_path}")
            for required in (
                "@@ AGENT_CAPABILITY_TO_WORKFLOW @@",
                "@@ CANONICAL_AGENT_CAPABILITY_ALIASES @@",
                "@@ agent_capability_registry @@",
                "@@ agent_taskspec_to_plan @@",
                "@@ executor_or_admin_handler @@",
            ):
                if required not in patch_fragment_text:
                    raise SystemExit(f"gateway patch fragment missing {required!r} for {capability}: {gateway_patch_fragment_path}")
        if gateway_runtime_patch_candidate_path.is_file():
            runtime_patch_candidate_text = gateway_runtime_patch_candidate_path.read_text(encoding="utf-8")
            if str(markers.get("runtime_patch_candidate_marker") or "").strip() not in runtime_patch_candidate_text:
                raise SystemExit(f"runtime patch candidate marker mismatch for {capability}: {gateway_runtime_patch_candidate_path}")
            for required in (
                "diff --git a/codex/gateway/gateway.py b/codex/gateway/gateway.py",
                "@@ AGENT_CAPABILITY_TO_WORKFLOW @@",
                "@@ CANONICAL_AGENT_CAPABILITY_ALIASES @@",
                "@@ agent_capability_registry @@",
                "@@ capability_dispatch_helper @@",
                "@@ executor_or_admin_handler @@",
            ):
                if required not in runtime_patch_candidate_text:
                    raise SystemExit(f"runtime patch candidate missing {required!r} for {capability}: {gateway_runtime_patch_candidate_path}")
        if gateway_promotion_patch_path.is_file():
            promotion_patch_text = gateway_promotion_patch_path.read_text(encoding="utf-8")
            for required in (
                "diff --git a/codex/gateway/gateway.py b/codex/gateway/gateway.py",
                f'+    "{capability}": ',
            ):
                if required not in promotion_patch_text:
                    raise SystemExit(f"promotion patch candidate missing {required!r} for {capability}: {gateway_promotion_patch_path}")
        if wiring_path.is_file():
            wiring = json.loads(wiring_path.read_text(encoding="utf-8"))
            if str(wiring.get("kind") or "") != str(markers.get("wiring_kind") or ""):
                raise SystemExit(f"wiring kind mismatch for {capability}: {wiring!r}")
            if not (wiring.get("touchpoints") or []):
                raise SystemExit(f"wiring blueprint missing touchpoints for {capability}: {wiring!r}")
        if executor_contract_path.is_file():
            executor_contract = json.loads(executor_contract_path.read_text(encoding="utf-8"))
            if str(executor_contract.get("kind") or "") != "codex-local-capability-executor-contract":
                raise SystemExit(f"executor contract kind mismatch for {capability}: {executor_contract!r}")
            if not (executor_contract.get("inputs") or []):
                raise SystemExit(f"executor contract missing inputs for {capability}: {executor_contract!r}")
            if not (executor_contract.get("return_schema") or {}):
                raise SystemExit(f"executor contract missing return schema for {capability}: {executor_contract!r}")
        if executor_dispatch_path.is_file():
            executor_dispatch = json.loads(executor_dispatch_path.read_text(encoding="utf-8"))
            if str(executor_dispatch.get("kind") or "") != str(markers.get("executor_dispatch_kind") or ""):
                raise SystemExit(f"executor dispatch kind mismatch for {capability}: {executor_dispatch!r}")
            if not str(executor_dispatch.get("handler_name") or "").strip():
                raise SystemExit(f"executor dispatch missing handler_name for {capability}: {executor_dispatch!r}")
            if not str(executor_dispatch.get("helper_entrypoint") or "").strip():
                raise SystemExit(f"executor dispatch missing helper_entrypoint for {capability}: {executor_dispatch!r}")
        if implementation_workorder_path.is_file():
            workorder = json.loads(implementation_workorder_path.read_text(encoding="utf-8"))
            if str(workorder.get("kind") or "") != str(markers.get("implementation_workorder_kind") or ""):
                raise SystemExit(f"implementation workorder kind mismatch for {capability}: {workorder!r}")
            if not (workorder.get("codex_local_steps") or []):
                raise SystemExit(f"implementation workorder missing codex_local_steps for {capability}: {workorder!r}")
            if not (workorder.get("verify_commands") or []):
                raise SystemExit(f"implementation workorder missing verify_commands for {capability}: {workorder!r}")
        if executor_stub_path.is_file():
            executor_text = executor_stub_path.read_text(encoding="utf-8")
            capability_constant = str(markers.get("executor_capability_constant") or "").strip()
            if capability_constant and f"CAPABILITY_NAME = '{capability_constant}'" not in executor_text and f'CAPABILITY_NAME = "{capability_constant}"' not in executor_text:
                raise SystemExit(f"executor stub capability constant mismatch for {capability}: {executor_stub_path}")
        if runtime_hook_stub_path.is_file():
            runtime_hook_text = runtime_hook_stub_path.read_text(encoding="utf-8")
            if str(markers.get("runtime_hook_marker") or "").strip() and str(markers.get("runtime_hook_marker")).strip() not in runtime_hook_text:
                raise SystemExit(f"runtime hook stub marker missing for {capability}: {runtime_hook_stub_path}")
        if smoke_stub_path.is_file():
            smoke_text = smoke_stub_path.read_text(encoding="utf-8")
            if str(markers.get("smoke_marker") or "").strip() and str(markers.get("smoke_marker")).strip() not in smoke_text:
                raise SystemExit(f"smoke stub marker missing for {capability}: {smoke_stub_path}")
    print("CAPABILITY_DRAFT_CONTRACTS_OK")


def assert_runtime_capability_contracts() -> None:
    registry = gateway.agent_capability_registry()
    issues = gateway.agent_capability_contract_issues(registry)
    if issues:
        raise SystemExit(f"runtime capability contract issues: {issues!r}")
    expectations = {
        "workspace_git_publish": ("workspace_git_publish", {"workspace", "remote_url"}, "medium"),
        "ssh_key_show_public": ("ssh_key_show_public", {"workspace"}, "medium"),
        "public_web_search": ("web_search", {"query"}, "low"),
        "agent_self_improve": ("self_improve", {"workspace"}, "high"),
        "workspace_action:verify": ("action", {"workspace", "action"}, "medium"),
    }
    for capability, (workflow, required_inputs, risk_level) in expectations.items():
        contract = gateway.agent_capability_contract(capability, registry)
        if contract.get("workflow") != workflow:
            raise SystemExit(f"{capability}: expected workflow={workflow!r}, got {contract!r}")
        required = set((contract.get("input_schema") or {}).get("required") or [])
        if not required_inputs.issubset(required):
            raise SystemExit(f"{capability}: expected required inputs {required_inputs!r}, got {contract!r}")
        safety = contract.get("safety_profile") or {}
        if safety.get("risk_level") != risk_level:
            raise SystemExit(f"{capability}: expected risk={risk_level!r}, got {contract!r}")
        if not contract.get("executor") or not contract.get("recovery") or not contract.get("tests"):
            raise SystemExit(f"{capability}: contract missing executor/recovery/tests, got {contract!r}")
    print("RUNTIME_CAPABILITY_CONTRACTS_OK")


def assert_agent_loop_meta_response() -> None:
    taskspec, plan = _taskspec_plan(
        {
            "current_workspace": "Test2",
            "user_goal": "report current workspace",
            "is_new_workspace_request": False,
            "is_existing_workspace_task": True,
            "target_repo_name": "",
            "remote_url": "",
            "desired_end_state": "workspace_context_returned",
            "required_capabilities": ["workspace_context_status"],
            "missing_inputs": [],
            "risk_level": "low",
            "recovery_plan": "return metadata",
            "read_only": False,
        },
        "kde ted jsi?",
    )
    with patch.object(
        gateway,
        "agent_controller_workspace",
        return_value=("Test2", True, {"Test2": {"path": str(ROOT), "port": 4100}}),
    ), patch.object(
        gateway,
        "agent_plan",
        return_value=(plan, taskspec, '{"required_capabilities":["workspace_context_status"]}'),
    ):
        result = gateway.admin_agent_loop({"workspace": "Test2", "task": "kde ted jsi?"})
    if not result.get("ok") or result.get("workflow") != "meta":
        raise SystemExit(f"expected deterministic meta agent response, got {result!r}")
    text = gateway.agent_loop_response_text(result)
    if "Test2" not in text:
        raise SystemExit(f"meta response should mention Test2, got {text!r}")
    print("AGENT_LOOP_META_RESPONSE_OK")


def assert_agent_loop_prefers_llm_plan() -> None:
    llm_plan = {
        "workflow": "review",
        "reason": "LLM planner smoke",
        "read_only": True,
        "workspace": "ai-stack",
        "action": "",
        "command": [],
        "run_after": "",
        "followup_actions": [],
        "repo_name": "",
        "github": False,
        "url": "",
        "question": "",
        "confidence": "high",
    }
    with patch.object(
        gateway,
        "agent_controller_workspace",
        return_value=("ai-stack", True, {"ai-stack": {"path": str(ROOT)}}),
    ), patch.object(
        gateway,
        "agent_plan",
        return_value=(
            llm_plan,
            {
                "current_workspace": "ai-stack",
                "user_goal": "read-only review",
                "is_new_workspace_request": False,
                "is_existing_workspace_task": True,
                "target_repo_name": "",
                "remote_url": "",
                "desired_end_state": "review_returned",
                "required_capabilities": ["clarify_or_infer_capability"],
                "missing_inputs": [],
                "risk_level": "low",
                "recovery_plan": "none",
                "read_only": True,
                "command": [],
                "action": "",
                "run_after": "",
                "followup_actions": [],
                "url": "",
                "question": "",
                "ssh_comment": "",
                "confidence": "high",
            },
            '{"current_workspace":"ai-stack"}',
        ),
    ) as mocked_plan, patch.object(
        gateway,
        "agent_fallback_plan",
        side_effect=RuntimeError("fallback should stay unused when llm plan succeeds"),
    ), patch.object(
        gateway,
        "agent_review_response",
        return_value="LLM-first review answer",
    ):
        result = gateway.admin_agent_loop({"workspace": "ai-stack", "task": "Prohlédni architekturu. Nic needituj."})
    mocked_plan.assert_called_once()
    if result.get("planner_source") != "llm":
        raise SystemExit(f"expected planner_source='llm', got {result.get('planner_source')!r}")
    if result.get("workflow") != "review":
        raise SystemExit(f"expected workflow='review', got {result.get('workflow')!r}")
    print("AGENT_LOOP_LLM_FIRST_OK")


def assert_agent_loop_uses_fallback_when_llm_breaks() -> None:
    with patch.object(
        gateway,
        "agent_controller_workspace",
        return_value=("ai-stack", True, {"ai-stack": {"path": str(ROOT)}}),
    ), patch.object(
        gateway,
        "agent_plan",
        side_effect=RuntimeError("planner offline"),
    ), patch.object(
        gateway,
        "agent_review_response",
        side_effect=RuntimeError("natural-language fallback must not run review"),
    ):
        result = gateway.admin_agent_loop({"workspace": "ai-stack", "task": "Prohlédni architekturu. Nic needituj."})
    if result.get("planner_source") != "fallback":
        raise SystemExit(f"expected planner_source='fallback', got {result.get('planner_source')!r}")
    if result.get("workflow") != "clarify":
        raise SystemExit(f"expected workflow='clarify', got {result.get('workflow')!r}")
    if result.get("routing_provenance") not in {"fallback:planner_offline", "fallback:structural"}:
        raise SystemExit(f"expected explicit fallback provenance, got {result.get('routing_provenance')!r}")
    print("AGENT_LOOP_FALLBACK_OK")


def assert_codex_local_payload_routing() -> None:
    payload = {
        "model": "codex-local-plan-qwen14b",
        "messages": [
            {
                "role": "user",
                "content": "repo: ai-stack\nProhlédni architekturu gateway/filter/helper vrstvy. Nic needituj.",
            }
        ],
    }
    with patch.object(
        gateway,
        "load_registry",
        return_value=("ai-stack", {"ai-stack": {"path": str(ROOT)}}),
    ):
        routed = gateway.codex_local_agent_loop_payload(payload)
    expected = {
        "workspace": "ai-stack",
        "task": "Prohlédni architekturu gateway/filter/helper vrstvy. Nic needituj.",
        "model": "codex-local-plan-qwen14b",
    }
    comparable = {key: routed.get(key) for key in expected}
    if comparable != expected:
        raise SystemExit(f"expected codex-local payload routing {expected!r}, got {routed!r}")
    if routed.get("messages") != payload["messages"]:
        raise SystemExit(f"expected codex-local payload to preserve messages, got {routed!r}")
    print("CODEX_LOCAL_PAYLOAD_ROUTING_OK")


def assert_codex_local_completion_prefers_agent_loop() -> None:
    payload = {
        "model": "codex-local-plan-qwen14b",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "repo: ai-stack\nProhlédni architekturu gateway/filter/helper vrstvy. Nic needituj.",
            }
        ],
    }

    fake_result = {
        "ok": True,
        "requested_workspace": "ai-stack",
        "controller_workspace": "ai-stack",
        "workspace_exists": True,
        "workflow": "review",
        "read_only": True,
        "plan": {
            "workflow": "review",
            "confidence": "high",
            "reason": "offline smoke",
            "read_only": True,
        },
        "summary": "Read-only review completed.",
        "answer": "offline smoke answer",
    }

    with patch.object(
        gateway,
        "load_registry",
        return_value=("ai-stack", {"ai-stack": {"path": str(ROOT)}}),
    ), patch.object(gateway, "admin_agent_loop", return_value=fake_result) as mocked_loop, patch.object(
        gateway,
        "ollama_chat",
        side_effect=RuntimeError("plain-llm path should not be used for natural codex-local prompt"),
    ):
        response = gateway.completion(payload)

    mocked_loop.assert_called_once()
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if "AGENT_LOOP_OK" not in content or "workflow=review" not in content:
        raise SystemExit(f"expected AGENT_LOOP review response, got {content!r}")
    print("CODEX_LOCAL_COMPLETION_AGENT_LOOP_OK")


def assert_codex_local_completion_bootstrap_routing() -> None:
    payload = {
        "model": "codex-local-plan-qwen14b",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "vytvor repozitar: svatektest a pak stahni co je treba a pust to",
            }
        ],
    }

    fake_result = {
        "ok": True,
        "requested_workspace": "ai-stack",
        "controller_workspace": "ai-stack",
        "workspace_exists": True,
        "workflow": "bootstrap",
        "read_only": False,
        "planner_source": "llm",
        "plan": {
            "workflow": "bootstrap",
            "confidence": "high",
            "reason": "offline bootstrap smoke",
            "read_only": False,
            "repo_name": "svatektest",
            "followup_actions": ["install", "smoke"],
        },
        "summary": "Bootstrap completed.",
        "execution": {"action": "create_local_repo", "repo_name": "svatektest"},
    }

    with patch.object(
        gateway,
        "load_registry",
        return_value=("ai-stack", {"ai-stack": {"path": str(ROOT)}}),
    ), patch.object(gateway, "admin_agent_loop", return_value=fake_result) as mocked_loop, patch.object(
        gateway,
        "ollama_chat",
        side_effect=RuntimeError("plain-llm path should not be used for bootstrap codex-local prompt"),
    ):
        response = gateway.completion(payload)

    mocked_loop.assert_called_once()
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if "AGENT_LOOP_OK" not in content or "workflow=bootstrap" not in content:
        raise SystemExit(f"expected AGENT_LOOP bootstrap response, got {content!r}")
    if '"repo_name": "svatektest"' not in content:
        raise SystemExit(f"expected bootstrap repo_name in response, got {content!r}")
    print("CODEX_LOCAL_COMPLETION_BOOTSTRAP_ROUTING_OK")


def assert_codex_local_completion_web_answer_routing() -> None:
    payload = {
        "model": "codex-local-plan-qwen14b",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "kdo ma dneska svatek? stahni mi to z seznam.cz",
            }
        ],
    }

    fake_result = {
        "ok": True,
        "requested_workspace": "ai-stack",
        "controller_workspace": "ai-stack",
        "workspace_exists": True,
        "workflow": "web_answer",
        "read_only": False,
        "planner_source": "llm",
        "plan": {
            "workflow": "web_answer",
            "confidence": "high",
            "reason": "offline web smoke",
            "read_only": False,
            "url": "https://www.seznam.cz/",
            "question": "kdo ma dneska svatek? stahni mi to z seznam.cz",
        },
        "summary": "Public web answer completed.",
        "execution": {"action": "web_answer", "url": "https://www.seznam.cz/"},
    }

    with patch.object(
        gateway,
        "load_registry",
        return_value=("ai-stack", {"ai-stack": {"path": str(ROOT)}}),
    ), patch.object(gateway, "admin_agent_loop", return_value=fake_result) as mocked_loop, patch.object(
        gateway,
        "ollama_chat",
        side_effect=RuntimeError("plain-llm path should not be used for web-answer codex-local prompt"),
    ):
        response = gateway.completion(payload)

    mocked_loop.assert_called_once()
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if "AGENT_LOOP_OK" not in content or "workflow=web_answer" not in content:
        raise SystemExit(f"expected AGENT_LOOP web_answer response, got {content!r}")
    if '"url": "https://www.seznam.cz/"' not in content:
        raise SystemExit(f"expected web url in response, got {content!r}")
    print("CODEX_LOCAL_COMPLETION_WEB_ANSWER_ROUTING_OK")


def assert_codex_local_completion_hard_fails_on_agent_loop_error() -> None:
    payload = {
        "model": "codex-local-plan-qwen14b",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "repo: ai-stack\nProhlédni architekturu gateway/filter/helper vrstvy. Nic needituj.",
            }
        ],
    }

    with patch.object(
        gateway,
        "load_registry",
        return_value=("ai-stack", {"ai-stack": {"path": str(ROOT)}}),
    ), patch.object(
        gateway,
        "admin_agent_loop",
        side_effect=RuntimeError("simulated agent-loop break"),
    ), patch.object(
        gateway,
        "ollama_chat",
        side_effect=RuntimeError("plain-llm fallback should stay unused for codex-local"),
    ):
        response = gateway.completion(payload)

    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if "CODEX_LOCAL_AGENT_LOOP_FAILED" not in content:
        raise SystemExit(f"expected hard agent-loop failure marker, got {content!r}")
    if "recovery=" not in content:
        raise SystemExit(f"expected recovery hint in hard failure response, got {content!r}")
    if "Tuhle akci jsem sam" in content:
        raise SystemExit(f"plain LLM fallback leaked into codex-local failure path: {content!r}")
    print("CODEX_LOCAL_COMPLETION_HARD_FAILURE_OK")


def assert_normal_chat_tool_routing_is_structural_only() -> None:
    natural_payloads = [
        {"model": "qwen2.5-coder:14b", "messages": [{"role": "user", "content": "vytvor ssh klic pro github"}]},
        {"model": "qwen2.5-coder:14b", "messages": [{"role": "user", "content": "stahni mi to ze seznam.cz"}]},
        {"model": "qwen2.5-coder:14b", "messages": [{"role": "user", "content": "git push do githubu"}]},
    ]
    for payload in natural_payloads:
        if gateway.normal_chat_requires_tool(payload):
            raise SystemExit(f"non-codex chat must not route to gateway by keyword, got {payload!r}")
    url_payload = {"model": "qwen2.5-coder:14b", "messages": [{"role": "user", "content": "https://www.seznam.cz/"}]}
    if not gateway.normal_chat_requires_tool(url_payload):
        raise SystemExit("concrete URL should remain a structural gateway route for compatibility")
    print("NORMAL_CHAT_TOOL_ROUTING_STRUCTURAL_ONLY_OK")


def assert_agent_loop_human_answers() -> None:
    run_text = gateway.agent_loop_response_text(
        {
            "ok": True,
            "requested_workspace": "ai-stack",
            "controller_workspace": "ai-stack",
            "planner_source": "llm",
            "workflow": "run",
            "read_only": False,
            "summary": "Workspace command completed.",
            "plan": {"workflow": "run", "reason": "smoke", "read_only": False},
            "execution": {
                "runner": "container",
                "command": ["pwd"],
                "output": "/workspace\n",
            },
        }
    )
    if "Ve workspace ai-stack jsem spustil `pwd` přes container runner" not in run_text:
        raise SystemExit(f"expected human run summary, got {run_text!r}")

    action_text = gateway.agent_loop_response_text(
        {
            "ok": False,
            "requested_workspace": "Test2",
            "controller_workspace": "Test2",
            "planner_source": "llm",
            "workflow": "action",
            "read_only": False,
            "summary": "Workspace action verify failed.",
            "plan": {"workflow": "action", "action": "verify", "reason": "smoke", "read_only": False},
            "execution": {"runner": "container", "output": "npm error Missing script: test"},
            "recovery": {"text": "Action verify failed; inspect package.json and apply the smallest fix before retrying."},
        }
    )
    if "Akce `verify` ve workspace Test2 selhala." not in action_text:
        raise SystemExit(f"expected human action failure summary, got {action_text!r}")

    bootstrap_text = gateway.agent_loop_response_text(
        {
            "ok": True,
            "requested_workspace": "svatektest",
            "controller_workspace": "ai-stack",
            "planner_source": "llm",
            "workflow": "bootstrap",
            "read_only": False,
            "summary": "Repository bootstrap completed.",
            "plan": {"workflow": "bootstrap", "repo_name": "svatektest", "reason": "smoke", "read_only": False},
            "execution": {
                "name": "svatektest",
                "github_requested": True,
                "ssh_key": {"public_key_path": "codex/state/ssh/github-svatektest_ed25519.pub"},
            },
            "followup": {
                "executed_actions": [{"action": "install"}, {"action": "smoke"}],
            },
        }
    )
    if "Bootstrap workspace `svatektest` doběhl." not in bootstrap_text:
        raise SystemExit(f"expected human bootstrap summary, got {bootstrap_text!r}")
    if "Následně proběhly kroky: install, smoke." not in bootstrap_text:
        raise SystemExit(f"expected bootstrap follow-up summary, got {bootstrap_text!r}")

    deploy_text = gateway.agent_loop_response_text(
        {
            "ok": True,
            "requested_workspace": "ai-stack",
            "controller_workspace": "ai-stack",
            "planner_source": "llm",
            "workflow": "deploy",
            "read_only": False,
            "summary": "ai-stack deploy scheduled.",
            "plan": {"workflow": "deploy", "reason": "smoke", "read_only": False},
            "execution": {"pid": 4321},
        }
    )
    if "Nasazení ai-stack jsem naplánoval." not in deploy_text:
        raise SystemExit(f"expected human deploy summary, got {deploy_text!r}")
    print("AGENT_LOOP_HUMAN_ANSWER_OK")


def assert_host_runner_requires_explicit_capability() -> None:
    run_result = gateway.admin_run_workspace({
        "workspace": "ai-stack",
        "runner": "host",
        "command": ["pwd"],
        "timeout": 30,
    })
    if run_result.get("ok") is not False:
        raise SystemExit(f"expected host runner denial for workspace run, got {run_result!r}")
    if run_result.get("marker") != "WORKSPACE_RUN_HOST_REQUIRES_EXPLICIT_CAPABILITY":
        raise SystemExit(f"expected workspace run host denial marker, got {run_result!r}")

    action_result = gateway.admin_workspace_action({
        "workspace": "ai-stack",
        "action": "verify",
        "runner": "host",
        "timeout": 30,
    })
    if action_result.get("ok") is not False:
        raise SystemExit(f"expected host runner denial for workspace action, got {action_result!r}")
    if action_result.get("marker") != "WORKSPACE_ACTION_HOST_REQUIRES_EXPLICIT_CAPABILITY":
        raise SystemExit(f"expected workspace action host denial marker, got {action_result!r}")
    print("HOST_RUNNER_EXPLICIT_CAPABILITY_OK")


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
    retry_action = str(result.get("retry_action", ""))
    retry_runner = str(result.get("retry_runner", ""))
    if target != expected_target:
        raise SystemExit(f"expected patch_target={expected_target!r}, got {target!r} for action={action}")
    if expected_fragment not in text:
        raise SystemExit(f"expected fragment {expected_fragment!r} in text {text!r} for action={action}")
    if retry_action != action:
        raise SystemExit(f"expected retry_action={action!r}, got {retry_action!r}")
    if retry_runner != "container":
        raise SystemExit(f"expected retry_runner='container', got {retry_runner!r}")
    print(f"RECOVERY_RULE_OK action={action} target={target}")


def assert_deploy_status_diagnostics() -> None:
    deploy_tail = "\n".join(
        [
            "[2026-07-04T19:31:39+02:00] Pulling latest git revision",
            "before=dfeb57e",
            "after=28a3bbc",
            "DEPLOY_BLOCKED_ROOT_RESTART_REQUIRED",
            "Manual fallback:",
            "sudo /mnt/c/Repositories/ai-stack/codex/bin/deploy_ai_stack.sh --restart-only",
        ]
    )
    run_outputs = {
        ("git", "rev-parse", "--short", "HEAD"): "dfeb57e",
        ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): "origin/main",
        ("git", "rev-parse", "--short", "origin/main"): "28a3bbc",
        ("git", "remote", "get-url", "origin"): "git@github.com:Jeycob/Ai-Stack.git",
        ("git", "status", "--short", "--branch"): "## main...origin/main\n?? codex/tags.json",
        ("id", "-un"): "sklenik",
    }

    def fake_run_ro(command, root, timeout=8):
        return run_outputs.get(tuple(command), "")

    with patch.object(gateway, "run_ro", side_effect=fake_run_ro), patch.object(
        gateway, "tail_text", return_value=deploy_tail
    ):
        result = gateway.admin_deploy_status({})

    if result.get("origin_head") != "28a3bbc" or result.get("upstream") != "origin/main":
        raise SystemExit(f"deploy status should include upstream/origin diagnostics, got {result!r}")
    if result.get("last_before") != "dfeb57e" or result.get("last_after") != "28a3bbc":
        raise SystemExit(f"deploy status should parse before/after from log, got {result!r}")
    if result.get("deployment_blocker") != "DEPLOY_BLOCKED_ROOT_RESTART_REQUIRED":
        raise SystemExit(f"deploy status should expose root restart blocker, got {result!r}")
    if result.get("restart_required") is not True:
        raise SystemExit(f"deploy status should set restart_required, got {result!r}")
    if "deploy_ai_stack.sh --restart-only" not in str(result.get("manual_restart_command") or ""):
        raise SystemExit(f"deploy status should include manual restart command, got {result!r}")
    if "NOPASSWD" not in str(result.get("sudoers_entry") or ""):
        raise SystemExit(f"deploy status should include narrow sudoers hint, got {result!r}")
    print("DEPLOY_STATUS_DIAGNOSTICS_OK")


def assert_deploy_status_runtime_gate_blocks_stale_process() -> None:
    run_outputs = {
        ("git", "rev-parse", "--short", "HEAD"): "0a68163",
        ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): "origin/main",
        ("git", "rev-parse", "--short", "origin/main"): "0a68163",
        ("git", "remote", "get-url", "origin"): "git@github.com:Jeycob/Ai-Stack.git",
        ("git", "status", "--short", "--branch"): "## main...origin/main",
        ("id", "-un"): "sklenik",
    }

    def fake_run_ro(command, root, timeout=8):
        if "gateway_runtime_fingerprint_check.py" in " ".join(command):
            return json.dumps(
                {
                    "ok": False,
                    "marker": "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN",
                    "recovery": "Restartuj stack a znovu ověř /health.",
                }
            )
        return run_outputs.get(tuple(command), "")

    with patch.object(gateway, "run_ro", side_effect=fake_run_ro), patch.object(
        gateway, "tail_text", return_value="[2026-07-04T21:00:00+02:00] AI Stack deploy finished\nDEPLOY_OK\n"
    ):
        result = gateway.admin_deploy_status({})

    if result.get("deployment_blocker") != "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN":
        raise SystemExit(f"deploy status should expose runtime split-brain blocker, got {result!r}")
    if result.get("restart_required") is not True:
        raise SystemExit(f"deploy status should require restart for split-brain, got {result!r}")
    if (result.get("runtime_gate") or {}).get("marker") != "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN":
        raise SystemExit(f"deploy status should include runtime gate payload, got {result!r}")
    print("DEPLOY_STATUS_RUNTIME_GATE_OK")


def assert_agent_loop_passes_conversation_context() -> None:
    captured = {}

    def fake_agent_plan(task, requested_workspace, controller_workspace, workspace_exists, conversation_context=""):
        captured["context"] = conversation_context
        return (
            {
                "intent_class": "web_search",
                "workflow": "web_search",
                "reason": "follow-up search",
                "read_only": True,
                "workspace": "ai-stack",
                "action": "",
                "command": [],
                "run_after": "",
                "followup_actions": [],
                "repo_name": "",
                "target_capability_name": "",
                "github": False,
                "remote_url": "",
                "desired_end_state": "public web result",
                "required_capabilities": ["public_web_search"],
                "missing_capabilities": [],
                "missing_inputs": [],
                "meta_capability": "",
                "search_query": "kdo ma dneska svatek",
                "url": "",
                "question": "kdo ma dneska svatek",
                "ssh_comment": "",
                "confidence": "high",
                "referents": {"to": "kdo ma dneska svatek"},
                "target_workspace": "",
                "execution_plan": [],
                "needs_user_input": False,
                "answer_visibility": "summary",
                "capability_locked": True,
            },
            {
                "intent_class": "web_search",
                "referents": {"to": "kdo ma dneska svatek"},
                "current_workspace": "ai-stack",
                "target_workspace": "",
                "user_goal": "vyhledej předchozí dotaz",
                "required_capabilities": ["public_web_search"],
                "missing_capabilities": [],
                "missing_inputs": [],
                "read_only": True,
                "search_query": "kdo ma dneska svatek",
                "question": "kdo ma dneska svatek",
                "recovery_plan": "",
            },
            {"source": "fake"},
        )

    with patch.object(gateway, "agent_plan", side_effect=fake_agent_plan), patch.object(
        gateway,
        "admin_public_web_search",
        return_value={
            "ok": True,
            "answer": "Našel jsem veřejné výsledky.",
            "query": "kdo ma dneska svatek",
            "results": [{"title": "Svátky", "url": "https://example.test"}],
        },
    ):
        result = gateway.admin_agent_loop(
            {
                "workspace": "ai-stack",
                "task": "vyhledej to kdekoliv",
                "messages": [
                    {"role": "user", "content": "kdo ma dneska svatek?"},
                    {"role": "assistant", "content": "Mám to vyhledat na webu."},
                    {"role": "user", "content": "vyhledej to kdekoliv"},
                ],
            }
        )
    if not result.get("ok") or result.get("workflow") != "web_search":
        raise SystemExit(f"expected web_search result, got {result!r}")
    if "kdo ma dneska svatek" not in captured.get("context", ""):
        raise SystemExit(f"conversation context did not preserve referent source, got {captured!r}")
    print("AGENT_LOOP_CONVERSATION_CONTEXT_OK")


def main() -> int:
    assert_no_hardcoded_direct_answer_router()
    assert_agent_loop_parse()
    assert_fallback_plans()
    assert_bootstrap_followup_inference()
    assert_bootstrap_beats_workspace_ssh_for_new_repo()
    assert_verify_prefers_action_over_run_without_explicit_command()
    assert_explicit_command_stays_run()
    assert_workspace_git_publish_manual_recovery()
    assert_capability_locked_plan_stays_on_taskspec_workflow()
    assert_workspace_action_capability_registry_mapping()
    assert_read_only_instruction_overrides_action_words()
    assert_taskspec_capability_selector_repairs_existing_workspace_publish()
    assert_taskspec_capability_selector_falls_back_to_heuristics()
    assert_autopilot_llm_candidate_selection()
    assert_autopilot_planner_fallback()
    assert_unknown_capability_needs_attention()
    assert_taskspec_capability_alias_canonicalization()
    assert_taskspec_public_key_prompt_uses_show_public()
    assert_taskspec_unknown_capability_stays_missing()
    assert_taskspec_meta_capabilities()
    assert_workspace_search_capability()
    assert_taskspec_v2_intents_and_renderer()
    assert_agent_self_improve_capability()
    assert_admin_agent_self_improve_forwarding()
    assert_capability_draft_contracts()
    assert_runtime_capability_contracts()
    assert_agent_loop_meta_response()
    assert_agent_loop_prefers_llm_plan()
    assert_agent_loop_uses_fallback_when_llm_breaks()
    assert_codex_local_payload_routing()
    assert_codex_local_completion_prefers_agent_loop()
    assert_codex_local_completion_bootstrap_routing()
    assert_codex_local_completion_web_answer_routing()
    assert_codex_local_completion_hard_fails_on_agent_loop_error()
    assert_normal_chat_tool_routing_is_structural_only()
    assert_agent_loop_human_answers()
    assert_host_runner_requires_explicit_capability()
    assert_deploy_status_diagnostics()
    assert_deploy_status_runtime_gate_blocks_stale_process()
    assert_agent_loop_passes_conversation_context()
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
