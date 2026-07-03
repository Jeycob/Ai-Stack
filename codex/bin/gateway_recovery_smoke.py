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
        ("Prohlédni architekturu. Nic needituj.", "review"),
        ("vytvor repozitar: svatektest", "bootstrap"),
        ("vytvor mi nove repository TestCode\nvygeneruj do nej ssh klic", "bootstrap"),
        ("repo: TestCode\ninitni git repo a pushni sem git@github.com:owner/repo.git", "workspace_git_publish"),
        ("kdo ma dneska svatek? stahni mi to z seznam.cz", "web_answer"),
        ("spust prikaz: pwd", "run"),
        ("pullni ai-stack a nasad", "deploy"),
    ]
    for task, workflow in cases:
        plan = gateway.agent_fallback_plan(task, "ai-stack", "ai-stack", True)
        if not plan:
            raise SystemExit(f"expected fallback plan for {task!r}")
        got = plan[0].get("workflow")
        if got != workflow:
            raise SystemExit(f"expected workflow={workflow!r}, got {got!r} for {task!r}")
    print("AGENT_FALLBACK_PLAN_OK")


def assert_bootstrap_followup_inference() -> None:
    task = "vytvor repozitar: svatektest a pak stahni co je treba a pust to"
    plan, _raw = gateway.agent_fallback_plan(task, "ai-stack", "ai-stack", True)
    if plan.get("workflow") != "bootstrap":
        raise SystemExit(f"expected bootstrap workflow for follow-up inference, got {plan!r}")
    followups = plan.get("followup_actions") or []
    if "install" not in followups or "smoke" not in followups:
        raise SystemExit(f"expected install+smoke followups, got {plan!r}")

    normalized = gateway.normalize_agent_plan(
        {
            "workflow": "bootstrap",
            "reason": "smoke bootstrap",
            "read_only": False,
            "workspace": "ai-stack",
            "repo_name": "svatektest",
            "followup_actions": [],
            "confidence": "high",
        },
        "ai-stack",
        "ai-stack",
        True,
        task,
    )
    followups = normalized.get("followup_actions") or []
    if "install" not in followups or "smoke" not in followups:
        raise SystemExit(f"expected normalized install+smoke followups, got {normalized!r}")
    print("BOOTSTRAP_FOLLOWUP_INFERENCE_OK")


def assert_bootstrap_beats_workspace_ssh_for_new_repo() -> None:
    for task in (
        "vytvor mi nove repository TestCode\nvygeneruj do nej ssh klic",
        "vytvor mi nove repository TestCode; vygeneruj do nej ssh klic",
    ):
        plan, _raw = gateway.agent_fallback_plan(task, "ai-stack", "ai-stack", True)
        if plan.get("workflow") != "bootstrap":
            raise SystemExit(f"expected bootstrap workflow for combined bootstrap+ssh prompt, got {plan!r}")
        normalized = gateway.normalize_agent_plan(
            {
                "workflow": "review",
                "reason": "planner drift smoke",
                "read_only": False,
                "workspace": "ai-stack",
                "confidence": "medium",
            },
            "ai-stack",
            "ai-stack",
            True,
            task,
        )
        if normalized.get("workflow") != "bootstrap":
            raise SystemExit(f"expected normalized bootstrap workflow for combined bootstrap+ssh prompt, got {normalized!r}")
        if normalized.get("repo_name") != "TestCode":
            raise SystemExit(f"expected repo_name='TestCode', got {normalized!r}")
    print("BOOTSTRAP_BEATS_SSH_INTENT_OK")


def assert_verify_prefers_action_over_run_without_explicit_command() -> None:
    task = "Ověř projekt a vrať stručný audit výsledků."
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
    if normalized.get("workflow") != "action":
        raise SystemExit(f"expected action workflow for verify prompt, got {normalized!r}")
    if normalized.get("action") != "verify":
        raise SystemExit(f"expected verify action for verify prompt, got {normalized!r}")
    if normalized.get("command") != []:
        raise SystemExit(f"expected command to be cleared after run->action normalization, got {normalized!r}")
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
        return_value="Fallback review answer",
    ):
        result = gateway.admin_agent_loop({"workspace": "ai-stack", "task": "Prohlédni architekturu. Nic needituj."})
    if result.get("planner_source") != "fallback":
        raise SystemExit(f"expected planner_source='fallback', got {result.get('planner_source')!r}")
    if result.get("workflow") != "review":
        raise SystemExit(f"expected workflow='review', got {result.get('workflow')!r}")
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
    }
    if routed != expected:
        raise SystemExit(f"expected codex-local payload routing {expected!r}, got {routed!r}")
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


def main() -> int:
    assert_agent_loop_parse()
    assert_fallback_plans()
    assert_bootstrap_followup_inference()
    assert_bootstrap_beats_workspace_ssh_for_new_repo()
    assert_verify_prefers_action_over_run_without_explicit_command()
    assert_explicit_command_stays_run()
    assert_workspace_git_publish_manual_recovery()
    assert_agent_loop_prefers_llm_plan()
    assert_agent_loop_uses_fallback_when_llm_breaks()
    assert_codex_local_payload_routing()
    assert_codex_local_completion_prefers_agent_loop()
    assert_codex_local_completion_bootstrap_routing()
    assert_codex_local_completion_web_answer_routing()
    assert_codex_local_completion_hard_fails_on_agent_loop_error()
    assert_agent_loop_human_answers()
    assert_host_runner_requires_explicit_capability()
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
