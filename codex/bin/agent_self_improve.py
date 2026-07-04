#!/usr/bin/env python3
"""Audited self-improvement routine for codex-local.

The helper turns a real OpenWebUI failure into durable evidence:

1. collect a transcript or use a provided transcript file
2. diagnose the failure class and minimal patch scope
3. write a regression scenario artifact
4. generate a bounded unified diff draft
5. optionally validate/apply a small guarded patch
6. run the local smoke suite
7. optionally schedule deploy and E2E verification

It is intentionally conservative. The script never prints secrets, never reads
private key material, and never applies a patch unless the caller provides a
patch file and dry-run is disabled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_ROOT = ROOT / "codex/audit/self-improve"
DEFAULT_OPENWEBUI_BASE_URL = "http://192.168.0.48:9090"
DEFAULT_OPENWEBUI_KEY_FILE = ROOT / "codex/state/openwebui-api.key"
DEFAULT_GATEWAY_URL = "http://192.168.0.48:9101"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----", re.S),
    re.compile(r"(?im)^.*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)\s*[:=].*?$"),
]

ALLOWED_PATCH_PREFIXES = (
    "codex/bin/",
    "codex/gateway/",
    "docs/",
)
ALLOWED_PATCH_FILES = {
    "README.md",
    "docker-compose.yml",
    "start_docker.bat",
}
BLOCKED_PATCH_PREFIXES = (
    "codex/state/",
    "codex/audit/",
    "logs/",
)

VERIFY_COMMANDS = [
    [
        "python3",
        "-m",
        "py_compile",
        "codex/gateway/gateway.py",
        "codex/bin/agent_self_improve.py",
        "codex/bin/gateway_recovery_smoke.py",
        "codex/bin/filter_route_smoke.py",
        "codex/bin/openwebui_gateway_admin_filter.py",
        "codex/bin/openwebui_codex_auto_tools_filter.py",
        "codex/bin/gateway_runtime_health_smoke.py",
        "codex/bin/gateway_runtime_fingerprint_check.py",
    ],
    ["python3", "codex/bin/owui_chat_turn_preflight_smoke.py"],
    ["python3", "codex/bin/agent_self_improve_smoke.py"],
    ["python3", "codex/bin/workspace_context_regression_smoke.py"],
    ["python3", "codex/bin/gateway_recovery_smoke.py"],
    ["python3", "codex/bin/filter_route_smoke.py", "--json"],
    ["python3", "codex/bin/gateway_admin_filter_passthrough_smoke.py"],
    ["python3", "codex/bin/gateway_runtime_health_smoke.py"],
]

REPRODUCE_COMMANDS = [
    ["python3", "codex/bin/gateway_recovery_smoke.py"],
    ["python3", "codex/bin/filter_route_smoke.py", "--json"],
    ["python3", "codex/bin/workspace_context_regression_smoke.py"],
]

SELF_IMPROVE_PHASES = [
    "collect_context",
    "reproduce",
    "reason",
    "propose_patch",
    "generate_unified_diff",
    "apply_guarded_patch",
    "verify",
    "e2e",
    "report",
]


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(value: str, fallback: str = "case") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return (slug or fallback)[:80]


def artifact_dir_for(args: argparse.Namespace) -> Path:
    case_hint = args.failure_marker or args.expected_behavior or args.chat_id or args.chat_url or args.prompt or args.feature_request or "case"
    digest_source = "\n".join(
        [
            str(args.workspace),
            str(args.chat_id or args.chat_url),
            str(args.failure_marker),
            str(args.expected_behavior),
            str(args.prompt),
            str(args.feature_request),
            str(os.getpid()),
            str(time.time_ns()),
        ]
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:10]
    dirname = f"{utc_stamp()}-{digest}-pid{os.getpid()}-{slugify(case_hint)}"
    return Path(args.audit_root) / dirname


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    text = value
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(text), encoding="utf-8")


def truncated_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


def openwebui_api_key(args: argparse.Namespace) -> str:
    token = os.getenv(args.openwebui_api_key_env, "").strip()
    if token:
        return token
    key_file = Path(args.openwebui_api_key_file)
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip()
    return ""


def parse_chat_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urllib.parse.urlparse(raw)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "c":
            return urllib.parse.unquote(parts[-1])
        return urllib.parse.unquote(parts[-1]) if parts else ""
    return raw


def http_json(method: str, url: str, token: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw or "{}")


def normalize_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else payload
    history = chat.get("history") if isinstance(chat.get("history"), dict) else {}
    messages = history.get("messages") if isinstance(history.get("messages"), dict) else {}
    ordered: list[dict[str, Any]] = []

    def created(item: dict[str, Any]) -> int:
        value = item.get("timestamp") or item.get("created_at") or item.get("created") or 0
        try:
            return int(value)
        except Exception:
            return 0

    for msg_id, message in messages.items():
        if not isinstance(message, dict):
            continue
        ordered.append(
            {
                "id": msg_id,
                "role": str(message.get("role") or ""),
                "content": str(message.get("content") or ""),
                "created": created(message),
                "model": str(message.get("model") or ""),
            }
        )
    ordered.sort(key=lambda item: (item.get("created") or 0, item.get("id") or ""))
    return {
        "id": str(chat.get("id") or payload.get("id") or ""),
        "title": str(chat.get("title") or payload.get("title") or ""),
        "messages": ordered,
        "message_count": len(ordered),
    }


def collect_transcript(args: argparse.Namespace) -> dict[str, Any]:
    if args.transcript_file:
        payload = json.loads(read_text(Path(args.transcript_file)))
        if "messages" in payload:
            return payload
        return normalize_chat_payload(payload)

    chat_id = parse_chat_id(args.chat_id or args.chat_url)
    if not chat_id:
        return {
            "degraded": True,
            "reason": "chat_id_missing",
            "messages": [],
            "message_count": 0,
        }
    token = openwebui_api_key(args)
    if not token:
        return {
            "degraded": True,
            "reason": "openwebui_api_key_missing",
            "chat_id": chat_id,
            "messages": [],
            "message_count": 0,
        }
    url = args.openwebui_base_url.rstrip("/") + f"/api/v1/chats/{urllib.parse.quote(chat_id)}"
    try:
        payload = http_json("GET", url, token, None, args.timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        return {
            "degraded": True,
            "reason": "openwebui_fetch_failed",
            "chat_id": chat_id,
            "error": f"{type(exc).__name__}: {exc}",
            "messages": [],
            "message_count": 0,
        }
    transcript = normalize_chat_payload(payload)
    transcript["chat_id"] = chat_id
    return transcript


def transcript_text(transcript: dict[str, Any]) -> str:
    lines = []
    for message in transcript.get("messages") or []:
        if not isinstance(message, dict):
            continue
        lines.append(f"{message.get('role')}: {message.get('content')}")
    return "\n\n".join(lines)


def last_user_prompt(transcript: dict[str, Any]) -> str:
    for message in reversed(transcript.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def extract_markers(text: str) -> dict[str, Any]:
    markers: dict[str, Any] = {}
    workflow = re.findall(r"(?im)\bworkflow=([A-Za-z0-9_.:-]+)", text)
    if workflow:
        markers["workflows"] = workflow[-8:]
    missing = re.findall(r"(?im)missing_capabilities[=:\[]+([^\]\n]+)", text)
    if missing:
        markers["missing_capabilities_raw"] = missing[-5:]
    for marker in (
        "WORKSPACE_RUN_FAILED",
        "NEEDS_ATTENTION",
        "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN",
        "CODEX_LOCAL_FILTER_INACTIVE",
        "CODEX_LOCAL_FILTER_STALE",
        "GATEWAY_ADMIN_TOKEN_MISSING",
        "LOCAL_REPO_CREATE_FAILED",
    ):
        if marker in text:
            markers.setdefault("markers", []).append(marker)
    return markers


def classify_failure(text: str, expected_behavior: str, failure_marker: str) -> dict[str, Any]:
    lower = text.lower()
    marker = failure_marker or ""
    expected = expected_behavior or ""
    category = "unknown"
    root_cause = "Insufficient evidence; keep diagnosis artifact and add a targeted regression before patching."
    patch_scope = ["codex/gateway/gateway.py", "codex/bin/gateway_recovery_smoke.py"]
    recovery = "Add or run a deterministic regression that captures the observed failure before changing routing."

    if "missing_capabilities" in lower or "unsupported capability" in lower:
        category = "capability_alias_or_registry_bug"
        root_cause = "TaskSpec capability validation likely used a raw or unsupported capability name instead of canonical form."
        patch_scope = ["codex/gateway/gateway.py", "docs/codex-local-capability-roadmap.json", "codex/bin/gateway_recovery_smoke.py"]
        recovery = "Canonicalize capabilities before validation and prove alias support with TaskSpec-path regression tests."
    elif "workspace_run_failed" in lower or "executed_command=python3 codex/bin/mentor_codex_local.py" in lower:
        category = "false_executor_or_recursion_bug"
        root_cause = "A user/meta intent was sent through a workspace command path or helper recursion instead of capability planning."
        patch_scope = ["codex/gateway/gateway.py", "codex/bin/openwebui_codex_auto_tools_filter.py", "codex/bin/gateway_recovery_smoke.py"]
        recovery = "Route through TaskSpec/meta capability and block helper recursion from workspace run."
    elif "failed to fetch" in lower or "timed out" in lower or "disconnect" in lower:
        category = "transport_timeout_or_streaming_bug"
        root_cause = "OpenWebUI did not receive progress quickly enough or the gateway/filter path blocked too long."
        patch_scope = ["codex/gateway/gateway.py", "codex/bin/owui_chat_turn.py", "codex/bin/openwebui_gateway_admin_filter.py"]
        recovery = "Prefer streaming/heartbeat or scheduled jobs for long-running agent capability work."
    elif "runtime_fingerprint" in lower or "runtime_commit" in lower or "stale" in lower:
        category = "runtime_drift"
        root_cause = "The running gateway process may not match the checked-out ai-stack source."
        patch_scope = ["codex/gateway/gateway.py", "codex/bin/start_codex_stack.sh", "codex/bin/gateway_runtime_fingerprint_check.py"]
        recovery = "Fail start/deploy when live epoch/fingerprint does not match the current source."
    elif any(cue in lower for cue in ("kde ted jsi", "kde teď jsi", "capability", "prepni se")):
        category = "meta_capability_routing_bug"
        root_cause = "A deterministic meta intent fell through to review/run instead of workspace context or capability catalog."
        patch_scope = ["codex/gateway/gateway.py", "codex/bin/gateway_recovery_smoke.py"]
        recovery = "Represent the meta request as a canonical meta capability and execute it without repo review."
    elif expected:
        category = "expected_behavior_regression"
        root_cause = "Expected behavior was supplied by the caller; create a regression and patch the smallest layer that violates it."
        recovery = "Use the expected behavior as the assertion before patching."

    return {
        "category": category,
        "root_cause": root_cause,
        "patch_scope": patch_scope,
        "recovery": recovery,
        "failure_marker": marker,
        "expected_behavior": expected,
    }


def infer_regression(transcript: dict[str, Any], diagnosis: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    prompt = args.prompt or last_user_prompt(transcript)
    text = transcript_text(transcript)
    expected = args.expected_behavior or diagnosis.get("expected_behavior") or ""
    cases: list[dict[str, Any]] = []

    known_patterns = [
        {
            "name": "meta_workspace_status_test2",
            "prompt": "repo: Test2\nkde ted jsi?",
            "expected_workflow": "meta",
            "expected_capability": "workspace_context_status",
            "expected_marker": "current_workspace",
        },
        {
            "name": "meta_capability_catalog_test2",
            "prompt": "repo: Test2\njake mas capability?",
            "expected_workflow": "meta",
            "expected_capability": "capability_catalog_show",
            "expected_marker": "implemented",
        },
        {
            "name": "ssh_public_key_alias_test2",
            "prompt": "repo: Test2\nvytvor tam ssh klic a vypis mi public",
            "expected_workflow": "ssh_key_show_public",
            "expected_capability": "ssh_key_show_public",
            "expected_marker": "public_key_path",
        },
        {
            "name": "workspace_search_capability_test2",
            "prompt": "repo: Test2\nprohledej repo a hledej zminky o capability implementaci",
            "expected_workflow": "workspace_search",
            "expected_capability": "workspace_search",
            "expected_marker": "matches",
        },
    ]
    for case in known_patterns:
        if case["prompt"].splitlines()[-1].lower() in (prompt or text).lower():
            cases.append(case)

    if not cases and prompt:
        cases.append(
            {
                "name": slugify(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12], "observed_case"),
                "prompt": prompt,
                "expected_behavior": expected or "Preserve user intent, use TaskSpec capability path, and return concrete recovery on blocker.",
                "expected_workflow": "",
                "expected_capability": "",
                "expected_marker": "",
            }
        )

    return {
        "kind": "codex-local-self-improve-regression",
        "created_at": utc_stamp(),
        "source_chat_id": transcript.get("chat_id") or transcript.get("id") or "",
        "diagnosis_category": diagnosis.get("category"),
        "prompt": prompt,
        "expected_behavior": expected,
        "cases": cases,
        "notes": [
            "Regression artifact is intentionally data-only; code changes must still target TaskSpec/capability semantics, not one prompt string.",
            "If a new capability is required, add it to registry, executor, roadmap and tests before marking fixed.",
        ],
    }


def reproduce(args: argparse.Namespace, audit_dir: Path, regression: dict[str, Any]) -> dict[str, Any]:
    commands = list(REPRODUCE_COMMANDS)
    results = [run_command(command, args.command_timeout) for command in commands]
    ok = all(item.get("exit_code") == 0 for item in results)
    payload = {
        "ok": ok,
        "phase": "reproduce",
        "mode": args.mode,
        "case_count": len(regression.get("cases") or []),
        "commands": results,
    }
    write_json(audit_dir / "reproduce-results.json", payload)
    lines = []
    for item in results:
        command = " ".join(shlex.quote(part) for part in item["command"])
        lines.append(f"$ {command}")
        lines.append(f"exit_code={item['exit_code']} duration_ms={item['duration_ms']}")
        output = str(item.get("output") or "").strip()
        if output:
            lines.append(output)
        lines.append("")
    write_text(audit_dir / "reproduce-results.txt", "\n".join(lines))
    return payload


def reasoning_task_spec(transcript: dict[str, Any], diagnosis: dict[str, Any], regression: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    prompt = args.prompt or last_user_prompt(transcript) or args.feature_request
    target_capability_name = args.target_capability_name or args.capability_name or ""
    capability_hint = "agent_capability_develop" if target_capability_name or args.mode == "capability_develop" else ""
    required_capabilities = []
    for case in regression.get("cases") or []:
        if isinstance(case, dict) and case.get("expected_capability"):
            required_capabilities.append(case["expected_capability"])
    if capability_hint:
        required_capabilities.append(capability_hint)
    required_capabilities = sorted({str(item) for item in required_capabilities if item})
    missing_inputs = []
    if args.mode == "capability_develop" and not (args.capability_name or args.feature_request or prompt):
        missing_inputs.append("feature_request_or_capability_name")

    return {
        "current_workspace": args.workspace,
        "user_goal": prompt,
        "is_new_workspace_request": False,
        "is_existing_workspace_task": True,
        "target_repo_name": "",
        "target_capability_name": target_capability_name,
        "remote_url": "",
        "desired_end_state": args.expected_behavior
        or diagnosis.get("expected_behavior")
        or "codex-local failure is reproduced, understood, guarded by a regression, and has a small reviewed patch path.",
        "required_capabilities": required_capabilities,
        "missing_inputs": missing_inputs,
        "risk_level": "medium" if args.mode in {"patch", "deploy", "full", "capability_develop"} else "low",
        "recovery_plan": diagnosis.get("recovery"),
        "repair_context": diagnosis.get("previous_cycle_failure") or {},
        "acceptance_criteria": [
            "A regression artifact exists before any patch is applied.",
            "Patch paths stay inside audited ai-stack runtime/code/doc paths.",
            "Verification smoke suite passes before deploy or E2E is reported as complete.",
            "Runtime fingerprint/source epoch gate passes before live E2E/deploy.",
        ],
    }


def reason(args: argparse.Namespace, audit_dir: Path, transcript: dict[str, Any], diagnosis: dict[str, Any], regression: dict[str, Any]) -> dict[str, Any]:
    task_spec = reasoning_task_spec(transcript, diagnosis, regression, args)
    payload = {
        "ok": not task_spec.get("missing_inputs"),
        "phase": "reason",
        "planner": "structured_taskspec_runtime",
        "llm_first_contract": {
            "intent_source": "TaskSpec fields and capability registry",
            "deterministic_code_role": "canonicalize, validate, execute, recover",
            "forbidden_shortcut": "prompt-specific workflow if/else patches",
        },
        "task_spec": task_spec,
        "diagnosis_category": diagnosis.get("category"),
    }
    write_json(audit_dir / "reasoning.json", payload)
    return payload


def capability_development_plan(args: argparse.Namespace, diagnosis: dict[str, Any], regression: dict[str, Any]) -> dict[str, Any]:
    capability = slugify(args.target_capability_name or args.capability_name or "new_capability", "new_capability")
    feature = args.feature_request or args.prompt or diagnosis.get("expected_behavior") or "Extend codex-local with an audited capability."
    return {
        "ok": True,
        "phase": "capability_develop",
        "capability_name": capability,
        "feature_request": feature,
        "architecture_contract": "OpenWebUI filter -> gateway agent loop -> TaskSpec -> capability registry -> workflow executor -> tests/E2E",
        "implementation_checklist": [
            "Add canonical capability name and aliases to registry.",
            "Map capability to a workflow without adding prompt-specific router branches.",
            "Implement or reuse a bounded executor with explicit inputs and recovery.",
            "Add roadmap entry and README/docs section.",
            "Add unit/smoke regression for TaskSpec path and failure recovery.",
            "Run py_compile, route/filter/recovery smoke, and dry-run E2E artifact.",
        ],
        "expected_files": [
            "codex/gateway/gateway.py",
            "docs/codex-local-capability-roadmap.json",
            "codex/bin/gateway_recovery_smoke.py",
            "README.md",
        ],
        "regression_cases": [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)],
        "acceptance_criteria": [
            "TaskSpec includes target_capability_name when a capability is being developed.",
            "Capability appears in roadmap or draft artifact with scope, preconditions, executor plan, recovery and tests.",
            "Generated patch is a unified diff, touches only allowed paths, and passes git apply --check before any apply.",
            "Runtime deploy/E2E remains blocked by fingerprint/source epoch drift.",
        ],
        "safety_boundaries": [
            "No secrets, .env, tokens, or private SSH keys in artifacts.",
            "No destructive host operation outside explicit audited capability.",
            "ai-stack runtime changes require guarded patch, verify, fingerprint gate, and deploy/E2E report.",
        ],
    }


def proposal_change_plan(
    args: argparse.Namespace,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
    capability_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    task_spec = reasoning.get("task_spec") or {}
    acceptance = task_spec.get("acceptance_criteria") or []
    feature_request = args.feature_request or args.prompt or diagnosis.get("expected_behavior") or ""
    if capability_plan:
        capability = capability_plan.get("capability_name") or "new_capability"
        return [
            {
                "path": "docs/codex-local-capability-roadmap.json",
                "change_type": "update",
                "intent": "Register the capability draft in the roadmap-backed registry source of truth.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.json",
                "change_type": "create_or_update",
                "intent": "Describe the capability contract, aliases, bounded executor plan, and senior review boundary.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.smoke.json",
                "change_type": "create_or_update",
                "intent": "Define a machine-checkable smoke contract for registry aliases, wiring, and artifact integrity.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.gateway-integration.json",
                "change_type": "create_or_update",
                "intent": "Draft concrete gateway touchpoints for canonical capability wiring without prompt-specific routing.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.gateway.patch.md",
                "change_type": "create_or_update",
                "intent": "Prepare a review-friendly gateway patch fragment for senior Codex review.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.runtime.patch.diff",
                "change_type": "create_or_update",
                "intent": "Prepare a runtime patch candidate that stays review-only until guarded promotion and fingerprint-gated deploy.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.wiring.json",
                "change_type": "create_or_update",
                "intent": "Capture file-level implementation steps, recovery rules, and offload split for the new capability.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.executor-contract.json",
                "change_type": "create_or_update",
                "intent": "Define explicit executor inputs, preconditions, return schema and recovery contract for the new capability.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.executor-dispatch.json",
                "change_type": "create_or_update",
                "intent": "Describe the concrete gateway dispatch hook, handler name, request payload, and verify path for the new capability.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"docs/capability-drafts/{capability}.implementation-workorder.json",
                "change_type": "create_or_update",
                "intent": "Give codex-local a concrete bounded work order for implementing, verifying, and escalating the new capability draft.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"codex/bin/capability_drafts/{capability}_executor_stub.py",
                "change_type": "create_or_update",
                "intent": "Provide a bounded executor stub that codex-local can extend into a real capability implementation.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"codex/bin/capability_drafts/{capability}_runtime_hook_stub.py",
                "change_type": "create_or_update",
                "intent": "Document the runtime hook shape before touching live gateway execution paths.",
                "acceptance_criteria": acceptance,
            },
            {
                "path": f"codex/bin/capability_drafts/{capability}_smoke.py",
                "change_type": "create_or_update",
                "intent": "Provide a bounded smoke scaffold for the capability implementation and recovery behavior.",
                "acceptance_criteria": acceptance,
            },
        ]
    regression_cases = [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)]
    case_slug = failure_case_slug(regression, diagnosis, reasoning)
    return [
        {
            "path": f"docs/self-improve-cases/{case_slug}.json",
            "change_type": "create_or_update",
            "intent": "Record the observed failure pattern as a reusable regression case.",
            "acceptance_criteria": acceptance,
        },
        {
            "path": f"docs/self-improve-cases/{case_slug}.smoke.json",
            "change_type": "create_or_update",
            "intent": "Define expected workflow, capability, and output markers for future regression runs.",
            "acceptance_criteria": acceptance,
        },
        {
            "path": f"docs/self-improve-cases/{case_slug}.patch.md",
            "change_type": "create_or_update",
            "intent": "Summarize the smallest architectural patch scope for this failure pattern.",
            "acceptance_criteria": acceptance,
        },
        {
            "path": f"docs/self-improve-cases/{case_slug}.runtime.patch.diff",
            "change_type": "create_or_update",
            "intent": "Prepare a review-only runtime candidate diff for the failure pattern.",
            "acceptance_criteria": acceptance,
        },
        {
            "path": f"codex/bin/self_improve_cases/{case_slug}_smoke.py",
            "change_type": "create_or_update",
            "intent": "Provide a bounded smoke scaffold that reproduces the failure case without OpenWebUI mutation.",
            "acceptance_criteria": acceptance,
        },
        {
            "path": "codex/gateway/gateway.py",
            "change_type": "review_then_patch",
            "intent": "Apply the smallest runtime fix only after the regression artifact exists and the generated diff has passed git apply --check.",
            "acceptance_criteria": acceptance or [feature_request or diagnosis.get("root_cause") or "Fix the reproduced failure."],
            "related_cases": regression_cases,
        },
    ]


def proposal_offload_split(capability_plan: dict[str, Any] | None) -> dict[str, list[str]]:
    codex_local = [
        "Collect transcript and diagnosis artifacts",
        "Draft acceptance criteria from TaskSpec and regression cases",
        "Prepare file-by-file patch plan",
        "Generate guarded unified diff draft",
        "Run local py_compile and smoke checks",
        "Write recovery report and apply manifest",
    ]
    senior = [
        "Review runtime patch candidates touching gateway execution",
        "Approve any guarded apply beyond dry-run",
        "Approve deploy/E2E after fingerprint gate is green",
    ]
    if capability_plan:
        codex_local.append("Draft new capability registry/docs/smoke scaffolds")
        senior.append("Approve bounded executor semantics for the new capability")
    return {"codex_local": codex_local, "senior_codex": senior}


def propose_patch(
    args: argparse.Namespace,
    audit_dir: Path,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> dict[str, Any]:
    patch_text = read_text(Path(args.patch_file)) if args.patch_file else ""
    paths = changed_paths_from_patch(patch_text) if patch_text else []
    capability_plan = capability_development_plan(args, diagnosis, regression) if args.mode == "capability_develop" or args.capability_name else None
    file_change_plan = proposal_change_plan(args, diagnosis, regression, reasoning, capability_plan)
    offload_split = proposal_offload_split(capability_plan)
    task_spec = reasoning.get("task_spec") or {}
    proposal = {
        "ok": True,
        "phase": "propose_patch",
        "has_patch_file": bool(args.patch_file),
        "patch_file": args.patch_file,
        "changed_paths": paths,
        "blocked_paths": [path for path in paths if not patch_path_allowed(path)],
        "minimal_patch_scope": diagnosis.get("patch_scope") or [],
        "reasoning_task_spec": reasoning.get("task_spec") or {},
        "capability_development": capability_plan,
        "target_capability_name": capability_plan.get("capability_name") if capability_plan else "",
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "proposed_file_changes": file_change_plan,
        "offload_split": offload_split,
        "unified_diff_expectations": {
            "must_pass_git_apply_check": True,
            "allowed_patch_prefixes": list(ALLOWED_PATCH_PREFIXES),
            "allowed_patch_files": sorted(ALLOWED_PATCH_FILES),
            "blocked_patch_prefixes": list(BLOCKED_PATCH_PREFIXES),
            "runtime_review_required": True,
        },
        "proposal": [
            "Keep the fix at the smallest violated layer: TaskSpec/canonical capability/registry/executor/test/docs.",
            "Add regression before changing behavior.",
            "Apply only an explicit unified diff that passes path guard and git apply --check.",
            "Run verification before deploy/E2E.",
        ],
    }
    if patch_text:
        proposal["patch_sha256"] = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        proposal["patch_preview"] = patch_text[:8000]
    else:
        proposal["manual_next_step"] = (
            "No patch file supplied. Use this proposal to ask codex-local for a unified diff, "
            "review it, then rerun self-improve with --patch-file and --mode patch/full."
        )
    write_json(audit_dir / "patch-proposal.json", proposal)
    markdown = [
        "# Agent Self-Improve Patch Proposal",
        "",
        f"Diagnosis: `{diagnosis.get('category')}`",
        "",
        "## Scope",
        "",
    ]
    for item in diagnosis.get("patch_scope") or []:
        markdown.append(f"- `{item}`")
    markdown.extend(["", "## Proposed File Changes", ""])
    for item in file_change_plan:
        markdown.append(f"- `{item['path']}`: {item['intent']}")
    markdown.extend(["", "## Acceptance Criteria", ""])
    for item in proposal["acceptance_criteria"]:
        markdown.append(f"- {item}")
    markdown.extend(["", "## Guard Rails", ""])
    for item in proposal["proposal"]:
        markdown.append(f"- {item}")
    markdown.extend(["", "## Offload Split", "", "### Codex-Local", ""])
    for item in offload_split["codex_local"]:
        markdown.append(f"- {item}")
    markdown.extend(["", "### Senior Codex", ""])
    for item in offload_split["senior_codex"]:
        markdown.append(f"- {item}")
    if capability_plan:
        markdown.extend(["", "## Capability Development", ""])
        markdown.append(f"Capability: `{capability_plan['capability_name']}`")
        markdown.append("")
        for item in capability_plan["implementation_checklist"]:
            markdown.append(f"- {item}")
    write_text(audit_dir / "patch-proposal.md", "\n".join(markdown) + "\n")
    return proposal


def unified_diff_for_file(rel: str, before: str, after: str) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    fromfile = "/dev/null" if before == "" and not (ROOT / rel).exists() else f"a/{rel}"
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=fromfile,
            tofile=f"b/{rel}",
        )
    )


def capability_draft_shape(capability: str, feature_request: str, reasoning: dict[str, Any]) -> dict[str, Any]:
    capability = slugify(capability, "new_capability")
    if capability.startswith("workspace_action_"):
        action = capability.removeprefix("workspace_action_")
        return {
            "scope": "workspace_runtime",
            "workflow": "action",
            "planned_workflow": "action",
            "executor": f"reuse_workspace_action_runner:{action}",
            "aliases": [capability, f"workspace_action:{action}", action],
            "summary": feature_request or f"Draft audited workspace action capability for {action}.",
        }
    if capability.startswith("workspace_"):
        return {
            "scope": "workspace_capability",
            "workflow": "clarify",
            "planned_workflow": "autopilot",
            "executor": "reuse_bounded_workspace_executor_or_add_explicit_workspace_handler",
            "aliases": [capability],
            "summary": feature_request or f"Draft workspace-scoped capability for {capability}.",
        }
    if capability.startswith("agent_"):
        return {
            "scope": "stack_runtime",
            "workflow": "clarify",
            "planned_workflow": "self_improve",
            "executor": "guarded_stack_runtime_executor_or_existing_admin_capability_reuse",
            "aliases": [capability],
            "summary": feature_request or f"Draft stack/runtime capability for {capability}.",
        }
    if capability.startswith("web_") or "web" in capability:
        return {
            "scope": "public_web",
            "workflow": "clarify",
            "planned_workflow": "web_answer",
            "executor": "reuse_public_web_fetch_or_answer_executor",
            "aliases": [capability],
            "summary": feature_request or f"Draft public-web capability for {capability}.",
        }
    return {
        "scope": "workspace_capability",
        "workflow": "clarify",
        "planned_workflow": "clarify",
        "executor": "pending_guarded_executor_or_existing_pattern_reuse",
        "aliases": [capability],
        "summary": feature_request or f"Draft capability proposed by agent_self_improve for {capability}.",
    }


def capability_executor_contract(capability: str, feature_request: str, reasoning: dict[str, Any]) -> dict[str, Any]:
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    desired_end_state = task_spec.get("desired_end_state") or ""
    workspace_scoped = shape["scope"].startswith("workspace")
    requires_manual_gate = shape["scope"] == "stack_runtime"
    inputs = []
    if workspace_scoped:
        inputs.append({"name": "workspace", "type": "string", "required": True, "source": "resolved workspace context"})
    if task_spec.get("target_capability_name"):
        inputs.append({"name": "target_capability_name", "type": "string", "required": True, "source": "TaskSpec"})
    if task_spec.get("remote_url"):
        inputs.append({"name": "remote_url", "type": "string", "required": False, "source": "TaskSpec"})
    if not inputs:
        inputs.append({"name": "request", "type": "object", "required": True, "source": "TaskSpec + runtime context"})
    preconditions = [
        "Capability name is canonicalized before registry lookup.",
        "TaskSpec desired_end_state is populated.",
        "Unknown capabilities fail closed as NEEDS_ATTENTION.",
    ]
    if workspace_scoped:
        preconditions.append("Resolved workspace exists in codex/workspaces.json or a bootstrap step has already created it.")
    if requires_manual_gate:
        preconditions.append("Runtime/source fingerprint gate must be green before deploy or E2E.")
    recovery = [
        "On missing bounded executor, keep capability non-implemented and return MANUAL_STEP_REQUIRED with the generated draft bundle.",
        "On verify failure, feed failing verify output into the next max_cycles iteration.",
        "Never replace the user's goal with a different workflow just because one capability is missing.",
    ]
    return_schema = {
        "ok": "bool",
        "capability": capability,
        "workflow": shape["planned_workflow"],
        "desired_end_state": desired_end_state,
        "summary": "short execution or blocker summary",
        "artifacts": ["optional generated paths or runtime references"],
        "recovery": "next safe step if blocked",
    }
    return {
        "kind": "codex-local-capability-executor-contract",
        "capability_name": capability,
        "scope": shape["scope"],
        "planned_workflow": shape["planned_workflow"],
        "executor_pattern": shape["executor"],
        "requires_manual_gate": requires_manual_gate,
        "inputs": inputs,
        "preconditions": preconditions,
        "return_schema": return_schema,
        "recovery_contract": recovery,
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "generated_by": "agent_self_improve.generate_unified_diff",
    }


def capability_test_matrix(capability: str, feature_request: str, reasoning: dict[str, Any]) -> dict[str, Any]:
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    return {
        "kind": "codex-local-capability-test-matrix",
        "capability_name": capability,
        "workflow": shape["planned_workflow"],
        "scenarios": [
            {
                "name": "taskspec_canonicalization",
                "goal": "TaskSpec normalization preserves target_capability_name and selects the canonical capability.",
                "expected": {"required_capabilities": [capability]},
            },
            {
                "name": "registry_contract",
                "goal": "Roadmap-backed registry entry exposes aliases, workflow and implemented=false draft state.",
                "expected": {"implemented": False, "draft": True, "planned_workflow": shape["planned_workflow"]},
            },
            {
                "name": "guarded_diff_generation",
                "goal": "Generated diff remains within allowed ai-stack paths and passes git apply --check.",
                "expected": {"git_apply_check_exit_code": 0},
            },
            {
                "name": "runtime_recovery",
                "goal": "If the executor remains unimplemented, runtime returns MANUAL_STEP_REQUIRED or NEEDS_ATTENTION instead of hallucinated success.",
                "expected": {"manual_or_needs_attention": True},
            },
        ],
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "generated_by": "agent_self_improve.generate_unified_diff",
    }


def roadmap_with_capability(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = "docs/codex-local-capability-roadmap.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else '{\n  "version": 1,\n  "capabilities": {}\n}\n'
    try:
        payload = json.loads(before)
    except json.JSONDecodeError:
        payload = {"version": 1, "capabilities": {}}
    capabilities = payload.setdefault("capabilities", {})
    shape = capability_draft_shape(capability, feature_request, reasoning)
    capabilities[capability] = {
        **(capabilities.get(capability) if isinstance(capabilities.get(capability), dict) else {}),
        "scope": shape["scope"],
        "workflow": shape["workflow"],
        "planned_workflow": shape["planned_workflow"],
        "implemented": False,
        "draft": True,
        "summary": shape["summary"],
        "executor": shape["executor"],
        "aliases": shape["aliases"],
        "acceptance_criteria": (reasoning.get("task_spec") or {}).get("acceptance_criteria") or [],
        "tests": [
            "TaskSpec normalization preserves target_capability_name and canonical capability selection.",
            "Gateway recovery smoke covers routing into agent_capability_develop/self_improve.",
            "Generated unified diff passes git apply --check before guarded apply.",
        ],
    }
    after = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_draft_file(capability: str, feature_request: str, diagnosis: dict[str, Any], regression: dict[str, Any], reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    draft = {
        "kind": "codex-local-capability-draft",
        "capability_name": capability,
        "feature_request": feature_request,
        "created_by": "agent_self_improve.generate_unified_diff",
        "architecture_contract": "OpenWebUI filter -> gateway agent loop -> TaskSpec -> capability registry -> workflow executor -> smoke/E2E tests -> report",
        "desired_end_state": task_spec.get("desired_end_state") or "",
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "registry_entry": {
            "workflow": shape["workflow"],
            "planned_workflow": shape["planned_workflow"],
            "scope": shape["scope"],
            "implemented": False,
            "summary": shape["summary"],
        },
        "aliases": shape["aliases"],
        "workflow_mapping": {
            "status": "draft",
            "effective_runtime_workflow": shape["workflow"],
            "planned_workflow": shape["planned_workflow"],
            "note": "The draft stays non-implemented until senior review approves a bounded executor or pattern reuse.",
        },
        "executor_contract": capability_executor_contract(capability, feature_request, reasoning),
        "test_matrix": capability_test_matrix(capability, feature_request, reasoning),
        "executor_plan": [
            f"Preferred executor pattern: {shape['executor']}.",
            "Define explicit inputs and preconditions.",
            "Reuse an existing bounded executor when possible instead of adding prompt-specific routing.",
            "Return NEEDS_ATTENTION or MANUAL_STEP_REQUIRED with concrete recovery on blocker.",
        ],
        "tests": [
            "TaskSpec normalization maps the capability to the expected canonical name.",
            "Gateway recovery smoke covers success and blocker behavior.",
            "E2E smoke records expected workflow/output marker before deploy is considered complete.",
        ],
        "diagnosis_category": diagnosis.get("category"),
        "regression_cases": [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)],
        "senior_review_required": [
            "Approve runtime executor scope.",
            "Review generated diff before apply.",
            "Approve deploy when fingerprint gate passes.",
        ],
    }
    after = json.dumps(draft, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_smoke_contract_file(capability: str, feature_request: str, diagnosis: dict[str, Any], regression: dict[str, Any], reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.smoke.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    contract = {
        "kind": "codex-local-capability-draft-smoke",
        "capability_name": capability,
        "summary": shape["summary"],
        "expected_registry": {
            "implemented": False,
            "draft": True,
            "scope": shape["scope"],
            "workflow": shape["workflow"],
            "planned_workflow": shape["planned_workflow"],
            "aliases": shape["aliases"],
            "executor": shape["executor"],
        },
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "regression_cases": [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)],
        "verifier_expectations": {
            "canonical_alias_roundtrip": shape["aliases"],
            "required_paths": [
                f"docs/capability-drafts/{capability}.json",
                f"docs/capability-drafts/{capability}.gateway-integration.json",
                f"docs/capability-drafts/{capability}.gateway.patch.md",
                f"docs/capability-drafts/{capability}.runtime.patch.diff",
                f"docs/capability-drafts/{capability}.wiring.json",
                f"docs/capability-drafts/{capability}.executor-contract.json",
                f"docs/capability-drafts/{capability}.executor-dispatch.json",
                f"docs/capability-drafts/{capability}.implementation-workorder.json",
                f"codex/bin/capability_drafts/{capability}_executor_stub.py",
                f"codex/bin/capability_drafts/{capability}_runtime_hook_stub.py",
                f"codex/bin/capability_drafts/{capability}_smoke.py",
                "docs/codex-local-capability-roadmap.json",
            ],
            "required_markers": {
                "gateway_integration_kind": "codex-local-capability-gateway-integration-draft",
                "gateway_patch_fragment_marker": "codex-local-capability-gateway-patch-fragment",
                "runtime_patch_candidate_marker": "codex-local-capability-runtime-patch-candidate",
                "wiring_kind": "codex-local-capability-wiring-blueprint",
                "executor_dispatch_kind": "codex-local-capability-executor-dispatch-plan",
                "implementation_workorder_kind": "codex-local-capability-implementation-workorder",
                "runtime_hook_marker": "CAPABILITY_RUNTIME_HOOK_STUB",
                "executor_capability_constant": capability,
                "smoke_marker": "CAPABILITY_DRAFT_SMOKE_SCAFFOLD",
            },
        },
        "generated_by": "agent_self_improve.generate_unified_diff",
        "review_status": "draft",
        "notes": [
            "This contract is validated by gateway_recovery_smoke generic draft checks.",
            "Applying the diff keeps the capability non-implemented until a bounded executor is approved.",
        ],
    }
    after = json.dumps(contract, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_wiring_blueprint_file(
    capability: str,
    feature_request: str,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.wiring.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    regression_cases = [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)]
    blueprint = {
        "kind": "codex-local-capability-wiring-blueprint",
        "capability_name": capability,
        "summary": shape["summary"],
        "goal": task_spec.get("desired_end_state") or feature_request,
        "planned_runtime_contract": {
            "scope": shape["scope"],
            "workflow": shape["workflow"],
            "planned_workflow": shape["planned_workflow"],
            "executor_pattern": shape["executor"],
            "aliases": shape["aliases"],
        },
        "touchpoints": [
            {
                "file": "codex/gateway/gateway.py",
                "symbols": [
                    "canonicalize_agent_capability",
                    "canonicalize_agent_capabilities",
                    "normalize_agent_taskspec",
                    "split_agent_capabilities",
                    "agent_taskspec_to_plan",
                    "agent_capability_registry",
                ],
                "responsibility": "Canonicalization, TaskSpec normalization, capability validation, workflow mapping, registry exposure.",
            },
            {
                "file": "codex/bin/gateway_recovery_smoke.py",
                "symbols": [
                    "assert_agent_self_improve_capability",
                    "assert_capability_draft_contracts",
                ],
                "responsibility": "Capability routing, alias/canonicalization, and draft-contract regression coverage.",
            },
            {
                "file": "codex/bin/agent_self_improve_smoke.py",
                "symbols": [
                    "run_capability_develop_mode",
                    "run_generate_unified_diff_mode",
                    "run_patch_mode_dry_run",
                ],
                "responsibility": "Generated diff shape, guarded patch path, and self-improve offload contract.",
            },
            {
                "file": f"docs/capability-drafts/{capability}.executor-contract.json",
                "symbols": [
                    "inputs",
                    "preconditions",
                    "return_schema",
                    "recovery_contract",
                ],
                "responsibility": "Concrete executor contract for follow-up implementation and review.",
            },
        ],
        "implementation_steps": [
            "Add or confirm canonical capability name and aliases in the registry source of truth.",
            "Map the canonical capability to a workflow without introducing prompt-specific routing branches.",
            "Reuse an existing bounded executor pattern where possible; otherwise add an explicit executor hook with recovery output.",
            "Extend route/recovery/workspace smoke coverage for the new capability behavior.",
            "Update roadmap/docs and keep generated diff within audited ai-stack paths.",
        ],
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "regression_cases": regression_cases,
        "failure_recovery": {
            "when_registry_missing": f"Return NEEDS_ATTENTION with missing capability `{capability}` and propose `agent_capability_develop` follow-up.",
            "when_executor_missing": "Keep the capability draft non-implemented and return MANUAL_STEP_REQUIRED with the wiring blueprint and smoke contract.",
            "when_verify_fails": "Feed verify output into the next max_cycles iteration and regenerate the diff instead of silently falling back.",
        },
        "review_split": {
            "codex_local_offload": [
                "Repo reconnaissance",
                "Acceptance criteria draft",
                "Regression scenario draft",
                "Unified diff draft generation",
                "Smoke command plan",
            ],
            "senior_codex_review": [
                "Runtime executor scope approval",
                "Guard-rail review before apply",
                "Deploy/E2E approval when fingerprint gate is green",
            ],
        },
        "generated_by": "agent_self_improve.generate_unified_diff",
        "diagnosis_category": diagnosis.get("category"),
    }
    after = json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_executor_contract_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.executor-contract.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    after = json.dumps(capability_executor_contract(capability, feature_request, reasoning), ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_executor_dispatch_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.executor-dispatch.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    dispatch = {
        "kind": "codex-local-capability-executor-dispatch-plan",
        "capability_name": capability,
        "target_file": "codex/gateway/gateway.py",
        "handler_name": f"run_{capability}_capability",
        "helper_module": f"codex.bin.capability_drafts.{capability}_executor_stub",
        "helper_entrypoint": "draft_execute",
        "workflow": shape["workflow"],
        "planned_workflow": shape["planned_workflow"],
        "executor_pattern": shape["executor"],
        "request_payload": {
            "capability": capability,
            "workspace": "resolved workspace context when workspace-scoped",
            "desired_end_state": task_spec.get("desired_end_state") or "",
            "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
            "task_spec": "canonicalized TaskSpec JSON",
        },
        "dispatcher_binding": {
            "selector": f"required_capability == {capability!r}",
            "manual_gate": shape["scope"] == "stack_runtime",
            "fallback": "Return MANUAL_STEP_REQUIRED when the reviewed executor is not wired yet.",
        },
        "verify_commands": [
            "python3 -m py_compile codex/gateway/gateway.py codex/bin/capability_drafts/*.py",
            "python3 codex/bin/gateway_recovery_smoke.py",
            "python3 codex/bin/agent_self_improve_smoke.py",
        ],
        "generated_by": "agent_self_improve.generate_unified_diff",
    }
    after = json.dumps(dispatch, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_implementation_workorder_file(
    capability: str,
    feature_request: str,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.implementation-workorder.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    regression_cases = [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)]
    workorder = {
        "kind": "codex-local-capability-implementation-workorder",
        "capability_name": capability,
        "summary": shape["summary"],
        "goal": task_spec.get("desired_end_state") or feature_request,
        "bounded_scope": {
            "workflow": shape["workflow"],
            "planned_workflow": shape["planned_workflow"],
            "scope": shape["scope"],
            "executor_pattern": shape["executor"],
        },
        "inputs": {
            "feature_request": feature_request,
            "target_capability_name": capability,
            "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
            "regression_cases": regression_cases,
        },
        "artifacts_to_consume": [
            f"docs/capability-drafts/{capability}.json",
            f"docs/capability-drafts/{capability}.smoke.json",
            f"docs/capability-drafts/{capability}.gateway-integration.json",
            f"docs/capability-drafts/{capability}.gateway.patch.md",
            f"docs/capability-drafts/{capability}.runtime.patch.diff",
            f"docs/capability-drafts/{capability}.wiring.json",
            f"docs/capability-drafts/{capability}.executor-contract.json",
            f"docs/capability-drafts/{capability}.executor-dispatch.json",
            f"codex/bin/capability_drafts/{capability}_executor_stub.py",
            f"codex/bin/capability_drafts/{capability}_runtime_hook_stub.py",
            f"codex/bin/capability_drafts/{capability}_smoke.py",
        ],
        "codex_local_steps": [
            {
                "id": "review-draft-contracts",
                "description": "Read the generated capability draft artifacts and confirm the canonical capability name, workflow, and executor contract are aligned.",
                "outputs": ["confirmed capability shape", "implementation notes"],
            },
            {
                "id": "prepare-runtime-hunks",
                "description": "Use the gateway integration draft, runtime patch candidate, and runtime hook scaffold to prepare the smallest safe gateway/runtime hunks.",
                "outputs": ["review-ready runtime hunk draft"],
            },
            {
                "id": "prepare-tests",
                "description": "Extend bounded smoke coverage so the new capability is exercised through TaskSpec, registry, recovery, and generated draft artifacts.",
                "outputs": ["updated smoke/regression plan"],
            },
            {
                "id": "run-verify",
                "description": "Run the bounded verify commands and capture failures for the next max_cycles iteration if needed.",
                "outputs": ["verify output", "next-cycle recovery input on failure"],
            },
        ],
        "senior_review_checkpoints": [
            "Approve gateway.py runtime hook shape before any reviewed promotion patch is applied.",
            "Approve any new executor semantics that widen runtime authority.",
            "Approve deploy/E2E only after runtime fingerprint gate passes.",
        ],
        "verify_commands": [
            "python3 -m py_compile codex/gateway/gateway.py codex/bin/capability_drafts/*.py",
            "python3 codex/bin/gateway_recovery_smoke.py",
            "python3 codex/bin/agent_self_improve_smoke.py",
        ],
        "escalation_points": [
            "If generated hunks would touch paths outside the allowed self-improve patch scope, stop and return MANUAL_STEP_REQUIRED.",
            "If verify fails, feed the failure output into the next max_cycles iteration instead of widening the runtime patch.",
            "If runtime/source fingerprint drifts, block deploy/E2E and return the recovery command.",
        ],
        "generated_by": "agent_self_improve.generate_unified_diff",
        "diagnosis_category": diagnosis.get("category"),
    }
    after = json.dumps(workorder, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_gateway_integration_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.gateway-integration.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    planned_workflow = "self_improve" if capability.startswith("agent_") else shape["workflow"]
    integration = {
        "kind": "codex-local-capability-gateway-integration-draft",
        "capability_name": capability,
        "summary": shape["summary"],
        "target_file": "codex/gateway/gateway.py",
        "integration_order": [
            "AGENT_CAPABILITY_TO_WORKFLOW",
            "CANONICAL_AGENT_CAPABILITY_ALIASES",
            "agent_capability_registry",
            "agent_taskspec_to_plan",
            "executor_or_admin_handler",
        ],
        "snippets": {
            "workflow_map": {
                "anchor": "AGENT_CAPABILITY_TO_WORKFLOW",
                "code": {
                    capability: planned_workflow,
                },
            },
            "canonical_aliases": {
                "anchor": "CANONICAL_AGENT_CAPABILITY_ALIASES",
                "code": {
                    alias: capability for alias in shape["aliases"]
                },
            },
            "registry_entry": {
                "anchor": "CORE_AGENT_CAPABILITIES or roadmap-backed registry merge",
                "code": {
                    capability: {
                        "workflow": planned_workflow,
                        "summary": shape["summary"],
                        "scope": shape["scope"],
                        "implemented": False,
                        "draft": True,
                        "executor": shape["executor"],
                    }
                },
            },
            "taskspec_plan_binding": {
                "anchor": "agent_taskspec_to_plan",
                "code": {
                    "required_capability": capability,
                    "workflow": planned_workflow,
                    "desired_end_state": task_spec.get("desired_end_state") or "",
                    "note": "Prefer canonical capability semantics; do not add prompt-specific routing.",
                },
            },
            "executor_hook": {
                "anchor": "workflow executor dispatch",
                "code": {
                    "handler_name": f"run_{capability}_capability",
                    "helper_module": f"codex.bin.capability_drafts.{capability}_executor_stub",
                    "helper_entrypoint": "draft_execute",
                    "executor_pattern": shape["executor"],
                    "planned_workflow": planned_workflow,
                    "manual_gate": True,
                    "note": "Keep as draft until bounded executor or admin reuse is approved.",
                },
            },
        },
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "generated_by": "agent_self_improve.generate_unified_diff",
    }
    after = json.dumps(integration, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def capability_gateway_patch_fragment_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.gateway.patch.md"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    planned_workflow = "self_improve" if capability.startswith("agent_") else shape["workflow"]
    alias_lines = "\n".join(f'+    "{alias}": "{capability}",' for alias in shape["aliases"])
    after = (
        f"# Gateway Patch Fragment for `{capability}`\n\n"
        "kind: codex-local-capability-gateway-patch-fragment\n"
        f"capability_name: {capability}\n"
        "target_file: codex/gateway/gateway.py\n"
        "status: draft\n\n"
        "This artifact is a review-friendly fragment draft. It is not auto-applied;\n"
        "the guarded apply path still operates on the generated unified diff and must\n"
        "pass `git apply --check` before any runtime patch is considered.\n\n"
        "## Workflow Map Fragment\n\n"
        "```diff\n"
        "@@ AGENT_CAPABILITY_TO_WORKFLOW @@\n"
        f'+    "{capability}": "{planned_workflow}",\n'
        "```\n\n"
        "## Canonical Alias Fragment\n\n"
        "```diff\n"
        "@@ CANONICAL_AGENT_CAPABILITY_ALIASES @@\n"
        f"{alias_lines}\n"
        "```\n\n"
        "## Registry Fragment\n\n"
        "```diff\n"
        "@@ agent_capability_registry @@\n"
        f'+    "{capability}": {{\n'
        f'+        "workflow": "{planned_workflow}",\n'
        f'+        "scope": "{shape["scope"]}",\n'
        '+        "implemented": False,\n'
        '+        "draft": True,\n'
        f'+        "executor": "{shape["executor"]}",\n'
        f'+        "summary": "{shape["summary"]}",\n'
        "+    },\n"
        "```\n\n"
        "## TaskSpec Binding Fragment\n\n"
        "```diff\n"
        "@@ agent_taskspec_to_plan @@\n"
        f'+# capability: {capability}\n'
        f'+# desired_end_state: {task_spec.get("desired_end_state") or ""}\n'
        f'+# planned_workflow: {planned_workflow}\n'
        "```\n\n"
        "## Executor Hook Fragment\n\n"
        "```diff\n"
        "@@ executor_or_admin_handler @@\n"
        f'+# executor_pattern: {shape["executor"]}\n'
        f'+# capability: {capability}\n'
        "+# manual_gate: True\n"
        "```\n"
    )
    return rel, unified_diff_for_file(rel, before, after)


def capability_runtime_patch_candidate_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"docs/capability-drafts/{capability}.runtime.patch.diff"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    planned_workflow = "self_improve" if capability.startswith("agent_") else shape["workflow"]
    handler_name = f"run_{capability}_capability"
    alias_lines = "\n".join(f'+    "{alias}": "{capability}",' for alias in shape["aliases"])
    after = (
        "### codex-local-capability-runtime-patch-candidate\n"
        "diff --git a/codex/gateway/gateway.py b/codex/gateway/gateway.py\n"
        "--- a/codex/gateway/gateway.py\n"
        "+++ b/codex/gateway/gateway.py\n"
        "@@ AGENT_CAPABILITY_TO_WORKFLOW @@\n"
        f'+    "{capability}": "{planned_workflow}",\n'
        "@@ CANONICAL_AGENT_CAPABILITY_ALIASES @@\n"
        f"{alias_lines}\n"
        "@@ agent_capability_registry @@\n"
        f'+    "{capability}": {{\n'
        f'+        "workflow": "{planned_workflow}",\n'
        f'+        "scope": "{shape["scope"]}",\n'
        '+        "implemented": False,\n'
        '+        "draft": True,\n'
        f'+        "executor": "{shape["executor"]}",\n'
        f'+        "summary": "{shape["summary"]}",\n'
        "+    },\n"
        "@@ agent_taskspec_to_plan @@\n"
        f'+    # target_capability_name={capability}\n'
        f'+    # desired_end_state={task_spec.get("desired_end_state") or ""}\n'
        "@@ capability_dispatch_helper @@\n"
        f"+def {handler_name}(self, task_spec, workspace):\n"
        "+    payload = {\n"
        f'+        "capability": "{capability}",\n'
        '+        "workspace": workspace,\n'
        '+        "task_spec": task_spec,\n'
        '+        "desired_end_state": task_spec.get("desired_end_state", ""),\n'
        '+        "acceptance_criteria": task_spec.get("acceptance_criteria", []),\n'
        "+    }\n"
        f"+    # delegate to codex/bin/capability_drafts/{capability}_executor_stub.py::draft_execute\n"
        "+    return payload\n"
        "@@ executor_or_admin_handler @@\n"
        f'+    # runtime_patch_candidate for {capability} using {shape["executor"]}\n'
        f'+    # dispatch_handler: {handler_name}\n'
        f'+    # selector: required_capability == "{capability}"\n'
    )
    return rel, unified_diff_for_file(rel, before, after)


def capability_executor_stub_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"codex/bin/capability_drafts/{capability}_executor_stub.py"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    acceptance = task_spec.get("acceptance_criteria") or []
    executor_contract = capability_executor_contract(capability, feature_request, reasoning)
    payload = {
        "capability_name": capability,
        "scope": shape["scope"],
        "workflow": shape["workflow"],
        "planned_workflow": shape["planned_workflow"],
        "executor_pattern": shape["executor"],
        "aliases": shape["aliases"],
        "desired_end_state": task_spec.get("desired_end_state") or "",
        "acceptance_criteria": acceptance,
        "inputs": executor_contract.get("inputs") or [],
        "preconditions": executor_contract.get("preconditions") or [],
        "return_schema": executor_contract.get("return_schema") or {},
    }
    after = (
        "#!/usr/bin/env python3\n"
        f'"""Draft executor scaffold for `{capability}`.\n\n'
        "Generated by agent_self_improve. This file is intentionally not wired into\n"
        "runtime automatically; it exists as a bounded patch scaffold for senior review.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n\n"
        f"CAPABILITY_NAME = {capability!r}\n"
        f"SCOPE = {shape['scope']!r}\n"
        f"WORKFLOW = {shape['workflow']!r}\n"
        f"PLANNED_WORKFLOW = {shape['planned_workflow']!r}\n"
        f"EXECUTOR_PATTERN = {shape['executor']!r}\n"
        f"ALIASES = {shape['aliases']!r}\n"
        f"SUMMARY = {shape['summary']!r}\n\n"
        "def draft_executor_spec() -> dict:\n"
        f"    return {json.dumps(payload, ensure_ascii=False, indent=4)}\n\n"
        "def draft_execute(request: dict) -> dict:\n"
        "    spec = draft_executor_spec()\n"
        "    return {\n"
        "        'ok': False,\n"
        "        'capability': CAPABILITY_NAME,\n"
        "        'workflow': PLANNED_WORKFLOW,\n"
        "        'summary': 'Executor stub only; senior review must approve runtime wiring.',\n"
        "        'artifacts': [],\n"
        "        'recovery': 'Promote the reviewed executor contract into gateway runtime or reuse an existing bounded executor pattern.',\n"
        "        'request': request,\n"
        "        'spec': spec,\n"
        "    }\n\n"
        "def main() -> int:\n"
        "    print(json.dumps(draft_executor_spec(), ensure_ascii=False, indent=2))\n"
        "    return 0\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )
    return rel, unified_diff_for_file(rel, before, after)


def capability_runtime_hook_stub_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"codex/bin/capability_drafts/{capability}_runtime_hook_stub.py"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    registry_entry = {
        "workflow": shape["workflow"],
        "planned_workflow": shape["planned_workflow"],
        "implemented": False,
        "draft": True,
        "summary": shape["summary"],
        "scope": shape["scope"],
        "executor": shape["executor"],
        "aliases": shape["aliases"],
    }
    hook_payload = {
        "capability_name": capability,
        "registry_entry": registry_entry,
        "canonical_aliases": shape["aliases"],
        "taskspec_binding": {
            "required_capability": capability,
            "target_capability_name": capability,
            "desired_end_state": task_spec.get("desired_end_state") or "",
        },
        "plan_binding": {
            "workflow": "self_improve" if capability.startswith("agent_") else shape["workflow"],
            "executor_pattern": shape["executor"],
            "note": "Draft runtime hook only. Senior Codex must decide whether to wire directly into gateway.py or reuse an existing executor path.",
        },
    }
    after = (
        "#!/usr/bin/env python3\n"
        f'"""Draft runtime hook scaffold for `{capability}`.\n\n'
        "Generated by agent_self_improve. This file captures the concrete registry,\n"
        "alias, TaskSpec and workflow binding shape that would be needed before a\n"
        "real runtime integration patch touches gateway.py.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n\n"
        f"CAPABILITY_NAME = {capability!r}\n\n"
        "def draft_runtime_hook() -> dict:\n"
        f"    return {json.dumps(hook_payload, ensure_ascii=False, indent=4)}\n\n"
        "def draft_registry_entry() -> dict:\n"
        "    return draft_runtime_hook()['registry_entry']\n\n"
        "def draft_aliases() -> list[str]:\n"
        "    return list(draft_runtime_hook()['canonical_aliases'])\n\n"
        "def main() -> int:\n"
        "    print('CAPABILITY_RUNTIME_HOOK_STUB')\n"
        "    print(json.dumps(draft_runtime_hook(), ensure_ascii=False, indent=2))\n"
        "    return 0\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )
    return rel, unified_diff_for_file(rel, before, after)


def capability_smoke_stub_file(capability: str, feature_request: str, reasoning: dict[str, Any]) -> tuple[str, str]:
    rel = f"codex/bin/capability_drafts/{capability}_smoke.py"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    shape = capability_draft_shape(capability, feature_request, reasoning)
    expected = {
        "capability_name": capability,
        "planned_workflow": shape["planned_workflow"],
        "scope": shape["scope"],
        "desired_end_state": task_spec.get("desired_end_state") or "",
    }
    after = (
        "#!/usr/bin/env python3\n"
        f'"""Draft smoke scaffold for `{capability}`.\n\n'
        "Generated by agent_self_improve. This is a bounded test scaffold that can be\n"
        "turned into a real smoke/regression once the executor is approved.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n\n"
        f"EXPECTED = {json.dumps(expected, ensure_ascii=False, indent=4)}\n\n"
        "def main() -> int:\n"
        "    print('CAPABILITY_DRAFT_SMOKE_SCAFFOLD')\n"
        "    print(json.dumps(EXPECTED, ensure_ascii=False, indent=2))\n"
        "    return 0\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )
    return rel, unified_diff_for_file(rel, before, after)


def failure_case_slug(regression: dict[str, Any], diagnosis: dict[str, Any], reasoning: dict[str, Any]) -> str:
    cases = regression.get("cases") or []
    for case in cases:
        if isinstance(case, dict) and case.get("name"):
            return slugify(str(case.get("name")), "observed_case")
    prompt = str((reasoning.get("task_spec") or {}).get("user_goal") or "")
    if prompt:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        return slugify(digest, "observed_case")
    return slugify(str(diagnosis.get("category") or "observed_case"), "observed_case")


def failure_regression_case_file(
    regression_slug: str,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str]:
    rel = f"docs/self-improve-cases/{regression_slug}.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    first_case = next((case for case in (regression.get("cases") or []) if isinstance(case, dict)), {})
    payload = {
        "kind": "codex-local-self-improve-case",
        "case_name": regression_slug,
        "diagnosis_category": diagnosis.get("category"),
        "root_cause": diagnosis.get("root_cause"),
        "workspace": task_spec.get("current_workspace") or "",
        "prompt": first_case.get("prompt") or task_spec.get("user_goal") or "",
        "expected_behavior": regression.get("expected_behavior") or task_spec.get("desired_end_state") or "",
        "expected_workflow": first_case.get("expected_workflow") or "",
        "expected_capability": first_case.get("expected_capability") or "",
        "expected_marker": first_case.get("expected_marker") or "",
        "patch_scope": diagnosis.get("patch_scope") or [],
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
        "reproduce_commands": REPRODUCE_COMMANDS,
        "notes": [
            "This artifact is a safe self-improve bundle for a smaller feature/fix or recovery case.",
            "It is intentionally registry/taskspec/recovery first and does not auto-apply runtime changes.",
        ],
    }
    after = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def failure_regression_smoke_contract_file(
    regression_slug: str,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str]:
    rel = f"docs/self-improve-cases/{regression_slug}.smoke.json"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    first_case = next((case for case in (regression.get("cases") or []) if isinstance(case, dict)), {})
    payload = {
        "kind": "codex-local-self-improve-case-smoke",
        "case_name": regression_slug,
        "diagnosis_category": diagnosis.get("category"),
        "expected": {
            "workspace": task_spec.get("current_workspace") or "",
            "workflow": first_case.get("expected_workflow") or "",
            "capability": first_case.get("expected_capability") or "",
            "marker": first_case.get("expected_marker") or "",
        },
        "required_paths": [
            f"docs/self-improve-cases/{regression_slug}.json",
            f"docs/self-improve-cases/{regression_slug}.patch.md",
            f"docs/self-improve-cases/{regression_slug}.runtime.patch.diff",
            f"codex/bin/self_improve_cases/{regression_slug}_smoke.py",
        ],
        "required_markers": {
            "case_kind": "codex-local-self-improve-case",
            "patch_fragment_kind": "codex-local-self-improve-patch-fragment",
            "runtime_patch_candidate_marker": "codex-local-self-improve-runtime-patch-candidate",
            "smoke_stub_marker": "SELF_IMPROVE_CASE_SMOKE_SCAFFOLD",
        },
        "acceptance_criteria": task_spec.get("acceptance_criteria") or [],
    }
    after = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return rel, unified_diff_for_file(rel, before, after)


def failure_regression_patch_fragment_file(
    regression_slug: str,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str]:
    rel = f"docs/self-improve-cases/{regression_slug}.patch.md"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    first_case = next((case for case in (regression.get("cases") or []) if isinstance(case, dict)), {})
    scope_lines = "\n".join(f"- `{item}`" for item in (diagnosis.get("patch_scope") or [])) or "- `codex/gateway/gateway.py`"
    after = (
        f"# Self-Improve Patch Fragment for `{regression_slug}`\n\n"
        "kind: codex-local-self-improve-patch-fragment\n"
        f"diagnosis_category: {diagnosis.get('category') or ''}\n"
        f"workspace: {task_spec.get('current_workspace') or ''}\n"
        "status: draft\n\n"
        "## Intended Patch Scope\n\n"
        f"{scope_lines}\n\n"
        "## Regression Guard Fragment\n\n"
        "```diff\n"
        "@@ add or extend regression smoke @@\n"
        f"+# case: {regression_slug}\n"
        f"+# expected_workflow: {first_case.get('expected_workflow') or ''}\n"
        f"+# expected_capability: {first_case.get('expected_capability') or ''}\n"
        f"+# expected_marker: {first_case.get('expected_marker') or ''}\n"
        "```\n\n"
        "## TaskSpec / Capability Fragment\n\n"
        "```diff\n"
        "@@ planner or capability canonicalization layer @@\n"
        f"+# desired_end_state: {task_spec.get('desired_end_state') or ''}\n"
        "+# preserve user intent, do not widen into unrelated workflow fallback\n"
        "```\n\n"
        "## Recovery Fragment\n\n"
        "```diff\n"
        "@@ failure recovery output @@\n"
        f"+# root_cause: {diagnosis.get('root_cause') or ''}\n"
        f"+# recovery: {diagnosis.get('recovery') or ''}\n"
        "```\n"
    )
    return rel, unified_diff_for_file(rel, before, after)


def failure_regression_runtime_patch_candidate_file(
    regression_slug: str,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str]:
    rel = f"docs/self-improve-cases/{regression_slug}.runtime.patch.diff"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    first_case = next((case for case in (regression.get("cases") or []) if isinstance(case, dict)), {})
    after = (
        "### codex-local-self-improve-runtime-patch-candidate\n"
        "diff --git a/codex/gateway/gateway.py b/codex/gateway/gateway.py\n"
        "--- a/codex/gateway/gateway.py\n"
        "+++ b/codex/gateway/gateway.py\n"
        "@@ planner or capability canonicalization layer @@\n"
        f'+    # case={regression_slug}\n'
        f'+    # expected_workflow={first_case.get("expected_workflow") or ""}\n'
        f'+    # expected_capability={first_case.get("expected_capability") or ""}\n'
        "@@ failure recovery output @@\n"
        f'+    # root_cause={diagnosis.get("root_cause") or ""}\n'
        f'+    # recovery={diagnosis.get("recovery") or ""}\n'
        "@@ regression binding @@\n"
        f'+    # desired_end_state={task_spec.get("desired_end_state") or ""}\n'
        "+    # keep user intent stable; do not widen into unrelated fallback\n"
    )
    return rel, unified_diff_for_file(rel, before, after)


def failure_regression_smoke_stub_file(
    regression_slug: str,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str]:
    rel = f"codex/bin/self_improve_cases/{regression_slug}_smoke.py"
    path = ROOT / rel
    before = read_text(path) if path.is_file() else ""
    task_spec = reasoning.get("task_spec") or {}
    first_case = next((case for case in (regression.get("cases") or []) if isinstance(case, dict)), {})
    expected = {
        "case_name": regression_slug,
        "diagnosis_category": diagnosis.get("category"),
        "workspace": task_spec.get("current_workspace") or "",
        "workflow": first_case.get("expected_workflow") or "",
        "capability": first_case.get("expected_capability") or "",
        "marker": first_case.get("expected_marker") or "",
    }
    after = (
        "#!/usr/bin/env python3\n"
        f'"""Draft smoke scaffold for self-improve case `{regression_slug}`.\n\n'
        "Generated by agent_self_improve. This is a bounded smoke stub for a\n"
        "smaller failure/fix case and should be promoted to a real regression once\n"
        "the runtime patch is reviewed.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n\n"
        f"EXPECTED = {json.dumps(expected, ensure_ascii=False, indent=4)}\n\n"
        "def main() -> int:\n"
        "    print('SELF_IMPROVE_CASE_SMOKE_SCAFFOLD')\n"
        "    print(json.dumps(EXPECTED, ensure_ascii=False, indent=2))\n"
        "    return 0\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )
    return rel, unified_diff_for_file(rel, before, after)


def check_patch_text(patch_text: str, timeout: int) -> dict[str, Any]:
    paths = changed_paths_from_patch(patch_text)
    blocked = [path for path in paths if not patch_path_allowed(path)]
    result: dict[str, Any] = {
        "paths": paths,
        "blocked_paths": blocked,
        "git_apply_check_exit_code": None,
        "git_apply_check_output": "",
    }
    if blocked:
        result["ok"] = False
        result["message"] = "Generated diff touches paths outside audited ai-stack self-improve scope."
        return result
    proc = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=ROOT,
        input=patch_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    result["git_apply_check_exit_code"] = proc.returncode
    result["git_apply_check_output"] = proc.stdout[-4000:]
    result["ok"] = proc.returncode == 0
    if proc.returncode != 0:
        result["message"] = "Generated diff did not pass git apply --check."
    return result


def generate_unified_diff(
    args: argparse.Namespace,
    audit_dir: Path,
    diagnosis: dict[str, Any],
    regression: dict[str, Any],
    reasoning: dict[str, Any],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    if args.patch_file:
        patch_text = read_text(Path(args.patch_file))
        source = "provided_patch_file"
    else:
        task_spec = reasoning.get("task_spec") or {}
        capability = slugify(
            args.target_capability_name
            or args.capability_name
            or str(task_spec.get("target_capability_name") or "")
            or "new_capability",
            "new_capability",
        )
        feature_request = args.feature_request or args.prompt or str(task_spec.get("user_goal") or "")
        patch_parts: list[str] = []
        if proposal.get("capability_development") or args.mode == "capability_develop" or args.capability_name or args.target_capability_name:
            for rel, diff in (
                roadmap_with_capability(capability, feature_request, reasoning),
                capability_draft_file(capability, feature_request, diagnosis, regression, reasoning),
                capability_smoke_contract_file(capability, feature_request, diagnosis, regression, reasoning),
                capability_gateway_integration_file(capability, feature_request, reasoning),
                capability_gateway_patch_fragment_file(capability, feature_request, reasoning),
                capability_runtime_patch_candidate_file(capability, feature_request, reasoning),
                capability_wiring_blueprint_file(capability, feature_request, diagnosis, regression, reasoning),
                capability_executor_contract_file(capability, feature_request, reasoning),
                capability_executor_dispatch_file(capability, feature_request, reasoning),
                capability_implementation_workorder_file(capability, feature_request, diagnosis, regression, reasoning),
                capability_executor_stub_file(capability, feature_request, reasoning),
                capability_runtime_hook_stub_file(capability, feature_request, reasoning),
                capability_smoke_stub_file(capability, feature_request, reasoning),
            ):
                if diff:
                    patch_parts.append(diff)
            source = "capability_development_template"
        else:
            regression_slug = failure_case_slug(regression, diagnosis, reasoning)
            for rel, diff in (
                failure_regression_case_file(regression_slug, diagnosis, regression, reasoning),
                failure_regression_smoke_contract_file(regression_slug, diagnosis, regression, reasoning),
                failure_regression_patch_fragment_file(regression_slug, diagnosis, regression, reasoning),
                failure_regression_runtime_patch_candidate_file(regression_slug, diagnosis, regression, reasoning),
                failure_regression_smoke_stub_file(regression_slug, diagnosis, regression, reasoning),
            ):
                if diff:
                    patch_parts.append(diff)
            source = "failure_regression_template"
        patch_text = "".join(patch_parts)

    patch_file = audit_dir / "generated-unified.diff"
    if patch_text:
        write_text(patch_file, patch_text)
        check = check_patch_text(patch_text, args.command_timeout)
        review_only_paths = sorted(
            [
                path
                for path in (check.get("paths") or [])
                if review_only_patch_path(path)
            ]
        )
        safe_apply_paths = sorted([path for path in (check.get("paths") or []) if path not in review_only_paths])
        safe_apply_patch_file = audit_dir / "safe-apply-candidate.diff"
        review_only_patch_file = audit_dir / "runtime-review-only.diff"
        safe_apply_patch_text = filter_patch_text(patch_text, set(safe_apply_paths)) if safe_apply_paths else ""
        review_only_patch_text = filter_patch_text(patch_text, set(review_only_paths)) if review_only_paths else ""
        if safe_apply_patch_text:
            write_text(safe_apply_patch_file, safe_apply_patch_text)
        if review_only_patch_text:
            write_text(review_only_patch_file, review_only_patch_text)
        result = {
            "ok": bool(check.get("ok")),
            "phase": "generate_unified_diff",
            "source": source,
            "patch_file": str(patch_file),
            "patch_sha256": hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
            "paths": check.get("paths") or [],
            "blocked_paths": check.get("blocked_paths") or [],
            "safe_apply_candidate_paths": safe_apply_paths,
            "safe_apply_candidate_patch_file": str(safe_apply_patch_file) if safe_apply_patch_text else "",
            "safe_apply_candidate_patch_sha256": (
                hashlib.sha256(safe_apply_patch_text.encode("utf-8")).hexdigest() if safe_apply_patch_text else ""
            ),
            "review_only_runtime_artifacts": review_only_paths,
            "review_only_patch_file": str(review_only_patch_file) if review_only_patch_text else "",
            "review_only_patch_sha256": (
                hashlib.sha256(review_only_patch_text.encode("utf-8")).hexdigest() if review_only_patch_text else ""
            ),
            "git_apply_check_exit_code": check.get("git_apply_check_exit_code"),
            "git_apply_check_output": check.get("git_apply_check_output"),
            "message": check.get("message") or "Generated unified diff passed git apply --check.",
        }
    else:
        result = {
            "ok": False,
            "phase": "generate_unified_diff",
            "source": source,
            "patch_file": "",
            "paths": [],
            "blocked_paths": [],
            "recovery": (
                "No safe deterministic diff generator matched this TaskSpec. "
                "Ask codex-local for an explicit unified diff or provide --patch-file for guarded apply."
            ),
        }
    write_json(audit_dir / "generated-diff-result.json", result)
    return result


def changed_paths_from_patch(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            rel = line[6:].strip()
            if rel != "/dev/null":
                paths.append(rel)
        elif line.startswith("*** Update File: "):
            paths.append(line.removeprefix("*** Update File: ").strip())
        elif line.startswith("*** Add File: "):
            paths.append(line.removeprefix("*** Add File: ").strip())
    return sorted({p for p in paths if p})


def split_patch_sections(patch_text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current: list[str] = []
    current_path = ""
    for line in patch_text.splitlines(keepends=True):
        if line.startswith("diff --git a/"):
            if current and current_path:
                sections.append((current_path, "".join(current)))
            current = [line]
            current_path = ""
            continue
        if line.startswith("--- ") and current:
            if current_path or any(part.startswith("+++ b/") for part in current):
                sections.append((current_path, "".join(current)))
            current = [line]
            current_path = ""
            continue
        if line.startswith("--- ") and not current:
            current = [line]
            current_path = ""
            continue
        current.append(line)
        if line.startswith("+++ b/"):
            rel = line[6:].strip()
            if rel != "/dev/null":
                current_path = rel
    if current and current_path:
        sections.append((current_path, "".join(current)))
    return sections


def filter_patch_text(patch_text: str, keep_paths: set[str]) -> str:
    parts = [chunk for rel, chunk in split_patch_sections(patch_text) if rel in keep_paths]
    return "".join(parts)


def review_only_patch_path(rel: str) -> bool:
    normalized = rel.replace("\\", "/").lstrip("/")
    if normalized.startswith("docs/capability-drafts/"):
        return True
    return normalized.endswith(".runtime.patch.diff") or normalized.endswith(".gateway.patch.md")


def patch_path_allowed(rel: str) -> bool:
    normalized = rel.replace("\\", "/").lstrip("/")
    if any(normalized.startswith(prefix) for prefix in BLOCKED_PATCH_PREFIXES):
        return False
    return normalized in ALLOWED_PATCH_FILES or any(normalized.startswith(prefix) for prefix in ALLOWED_PATCH_PREFIXES)


def validate_or_apply_patch(args: argparse.Namespace, audit_dir: Path, generated_patch_file: str = "") -> dict[str, Any]:
    patch_file = args.patch_file or generated_patch_file
    if not patch_file:
        return {
            "ok": True,
            "mode": "no_patch_file",
            "applied": False,
            "message": "No patch file supplied; self-improve recorded diagnosis/regression and ran verification only.",
        }
    patch_path = Path(patch_file)
    patch_text = read_text(patch_path)
    paths = changed_paths_from_patch(patch_text)
    blocked = [path for path in paths if not patch_path_allowed(path)]
    result = {
        "ok": not blocked,
        "mode": "dry_run" if args.dry_run else "apply",
        "patch_file": str(patch_path),
        "paths": paths,
        "blocked_paths": blocked,
        "applied": False,
    }
    if blocked:
        result["message"] = "Patch touches paths outside audited ai-stack self-improve scope."
        return result

    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=args.command_timeout,
    )
    result["git_apply_check_exit_code"] = check.returncode
    result["git_apply_check_output"] = check.stdout[-4000:]
    if check.returncode != 0:
        result["ok"] = False
        result["message"] = "git apply --check failed."
        return result
    if args.dry_run:
        result["message"] = "Patch validated but not applied because dry_run=true."
        return result

    apply_proc = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=args.command_timeout,
    )
    result["applied"] = apply_proc.returncode == 0
    result["ok"] = apply_proc.returncode == 0
    result["git_apply_exit_code"] = apply_proc.returncode
    result["git_apply_output"] = apply_proc.stdout[-4000:]
    write_json(audit_dir / "patch-result.json", result)
    return result


def run_command(command: list[str], timeout: int) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return {
            "command": command,
            "exit_code": proc.returncode,
            "duration_ms": int((time.time() - started) * 1000),
            "output": proc.stdout[-12000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": 124,
            "duration_ms": int((time.time() - started) * 1000),
            "output": str(exc.stdout or exc.stderr or "timed out")[-12000:],
        }


def effective_verify_commands() -> list[list[str]]:
    commands = list(VERIFY_COMMANDS)
    if os.getenv("AGENT_SELF_IMPROVE_SMOKE_RUNNING") or os.getenv("AGENT_SELF_IMPROVE_NESTED_VERIFY"):
        commands = [command for command in commands if command != ["python3", "codex/bin/agent_self_improve_smoke.py"]]
    return commands


def runtime_gate(args: argparse.Namespace, audit_dir: Path, phase: str) -> dict[str, Any]:
    if args.dry_run:
        result = {
            "ok": True,
            "phase": phase,
            "dry_run": True,
            "skipped": True,
            "message": "Runtime fingerprint gate prepared but not executed because dry_run=true.",
        }
        write_json(audit_dir / f"runtime-gate-{phase}.json", result)
        return result
    command = [
        "python3",
        "codex/bin/gateway_runtime_fingerprint_check.py",
        "--base-url",
        args.gateway_url,
        "--timeout",
        str(min(args.timeout, 15)),
        "--json",
    ]
    result = run_command(command, args.command_timeout)
    result["ok"] = result.get("exit_code") == 0
    result["phase"] = phase
    parsed = {}
    try:
        parsed = json.loads(str(result.get("output") or "{}"))
    except json.JSONDecodeError:
        parsed = {}
    if parsed:
        result["fingerprint_check"] = parsed
    if not result["ok"]:
        result["marker"] = parsed.get("marker") or "CODEX_LOCAL_RUNTIME_FINGERPRINT_GATE_FAILED"
        result["recovery"] = parsed.get("recovery") or "Restartuj ai-stack gateway a ověř /health proti aktuálnímu checkoutu."
    write_json(audit_dir / f"runtime-gate-{phase}.json", result)
    return result


def verify(args: argparse.Namespace, audit_dir: Path) -> dict[str, Any]:
    commands = effective_verify_commands()
    results = [run_command(command, args.command_timeout) for command in commands]
    ok = all(item.get("exit_code") == 0 for item in results)
    lines = []
    for item in results:
        command = " ".join(shlex.quote(part) for part in item["command"])
        lines.append(f"$ {command}")
        lines.append(f"exit_code={item['exit_code']} duration_ms={item['duration_ms']}")
        output = str(item.get("output") or "").strip()
        if output:
            lines.append(output)
        lines.append("")
    write_text(audit_dir / "test-results.txt", "\n".join(lines))
    return {"ok": ok, "commands": results}


def phase_failure_detail(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "phase": name,
        "reason": payload.get("reason") or "",
        "message": payload.get("message") or "",
    }
    if payload.get("marker"):
        detail["marker"] = payload.get("marker")
    task_spec = payload.get("task_spec") or {}
    if isinstance(task_spec, dict) and task_spec:
        if task_spec.get("missing_inputs"):
            detail["missing_inputs"] = list(task_spec.get("missing_inputs") or [])
        if task_spec.get("required_capabilities"):
            detail["required_capabilities"] = list(task_spec.get("required_capabilities") or [])
        if task_spec.get("target_capability_name"):
            detail["target_capability_name"] = task_spec.get("target_capability_name")
    if payload.get("patch_file"):
        detail["patch_file"] = payload.get("patch_file")
    if payload.get("paths"):
        detail["paths"] = list(payload.get("paths") or [])[:20]
    if payload.get("blocked_paths"):
        detail["blocked_paths"] = list(payload.get("blocked_paths") or [])[:20]
    if payload.get("git_apply_check_exit_code") is not None:
        detail["git_apply_check_exit_code"] = payload.get("git_apply_check_exit_code")
    if payload.get("git_apply_check_output"):
        detail["git_apply_check_output_tail"] = truncated_text(payload.get("git_apply_check_output"), 2000)
    if payload.get("git_apply_exit_code") is not None:
        detail["git_apply_exit_code"] = payload.get("git_apply_exit_code")
    if payload.get("git_apply_output"):
        detail["git_apply_output_tail"] = truncated_text(payload.get("git_apply_output"), 2000)
    commands = payload.get("commands") or []
    if commands:
        detail["failing_commands"] = [
            {
                "command": item.get("command") or [],
                "exit_code": item.get("exit_code"),
                "output_tail": truncated_text(item.get("output"), 1200),
            }
            for item in commands
            if isinstance(item, dict) and item.get("exit_code") not in {0, None}
        ]
    fingerprint = payload.get("fingerprint_check") or {}
    if isinstance(fingerprint, dict) and fingerprint:
        detail["fingerprint_check"] = {
            "marker": fingerprint.get("marker") or "",
            "runtime_commit": fingerprint.get("runtime_commit") or "",
            "runtime_fingerprint": fingerprint.get("runtime_fingerprint") or "",
            "gateway_source_epoch": fingerprint.get("gateway_source_epoch") or "",
        }
    return detail


def summary_phase_payload(summary: dict[str, Any], phase_name: str) -> dict[str, Any]:
    cycles = summary.get("cycles") or []
    if cycles and isinstance(cycles[-1], dict):
        payload = ((cycles[-1].get("phases") or {}).get(phase_name) or {})
        if isinstance(payload, dict) and payload:
            return payload
    payload = summary.get(phase_name) or {}
    return payload if isinstance(payload, dict) else {}


def verify_summary(verify_result: dict[str, Any]) -> dict[str, Any]:
    commands = verify_result.get("commands") or []
    passed = sum(1 for item in commands if isinstance(item, dict) and item.get("exit_code") == 0)
    failed = sum(1 for item in commands if isinstance(item, dict) and item.get("exit_code") not in {0, None})
    return {
        "command_count": len(commands),
        "passed": passed,
        "failed": failed,
        "all_green": bool(commands) and failed == 0,
        "failed_commands": [
            " ".join(str(part) for part in (item.get("command") or []))
            for item in commands
            if isinstance(item, dict) and item.get("exit_code") not in {0, None}
        ],
    }


def phase_status_summary(summary: dict[str, Any]) -> dict[str, str]:
    phase_map = {
        "collect_context": "ok",
        "diagnose": "ok",
        "reproduce": summary.get("reproduce", {}),
        "reason": summary.get("reason", {}),
        "propose_patch": summary.get("proposal", {}),
        "generate_unified_diff": summary.get("generated_diff", {}),
        "apply_guarded_patch": summary.get("patch", {}),
        "verify": summary.get("verify", {}),
        "deploy": summary.get("deploy", {}),
        "e2e": summary.get("e2e", {}),
    }
    result: dict[str, str] = {}
    for phase, payload in phase_map.items():
        if payload == "ok":
            result[phase] = "ok"
            continue
        if not isinstance(payload, dict):
            result[phase] = "unknown"
            continue
        if payload.get("skipped"):
            result[phase] = "skipped"
        elif payload.get("ok"):
            result[phase] = "ok"
        elif payload:
            result[phase] = "failed"
        else:
            result[phase] = "pending"
    return result


def capability_patch_readiness(
    summary: dict[str, Any],
    proposal_result: dict[str, Any],
    generated_diff_result: dict[str, Any],
) -> dict[str, Any]:
    capability_development = proposal_result.get("capability_development") or {}
    capability = capability_development.get("capability_name") or ""
    generated_paths = {str(path) for path in (generated_diff_result.get("paths") or [])}
    if not capability:
        return {
            "target_capability_name": "",
            "enabled": False,
            "ready_for_review": False,
            "ready_for_apply": False,
            "missing_artifacts": [],
        }
    required = [
        "docs/codex-local-capability-roadmap.json",
        f"docs/capability-drafts/{capability}.json",
        f"docs/capability-drafts/{capability}.smoke.json",
        f"docs/capability-drafts/{capability}.gateway-integration.json",
        f"docs/capability-drafts/{capability}.gateway.patch.md",
        f"docs/capability-drafts/{capability}.runtime.patch.diff",
        f"docs/capability-drafts/{capability}.wiring.json",
        f"docs/capability-drafts/{capability}.executor-contract.json",
        f"docs/capability-drafts/{capability}.executor-dispatch.json",
        f"codex/bin/capability_drafts/{capability}_executor_stub.py",
        f"codex/bin/capability_drafts/{capability}_runtime_hook_stub.py",
        f"codex/bin/capability_drafts/{capability}_smoke.py",
    ]
    missing = [path for path in required if path not in generated_paths]
    verify_info = verify_summary(summary_phase_payload(summary, "verify"))
    return {
        "target_capability_name": capability,
        "enabled": True,
        "ready_for_review": generated_diff_result.get("ok") is True and not missing,
        "ready_for_apply": (
            generated_diff_result.get("ok") is True
            and not missing
            and not (generated_diff_result.get("blocked_paths") or [])
            and verify_info.get("all_green") is True
            and summary.get("dry_run") is False
        ),
        "missing_artifacts": missing,
        "verify_all_green": verify_info.get("all_green"),
        "git_apply_check_exit_code": generated_diff_result.get("git_apply_check_exit_code"),
    }


def build_report(summary: dict[str, Any], proposal_result: dict[str, Any], generated_diff_result: dict[str, Any]) -> dict[str, Any]:
    codex_local_offload = [
        "repository exploration",
        "regression artifact proposal",
        "acceptance criteria drafting",
        "capability implementation checklist",
        "unified diff draft generation for allowed paths",
        "smoke command execution",
        "recovery report drafting",
    ]
    senior_review = [
        "applying runtime patches",
        "deploying ai-stack",
        "approving new host/runtime capability boundaries",
        "reviewing security-sensitive recovery",
    ]
    capability_development = proposal_result.get("capability_development") or {}
    generated_paths = generated_diff_result.get("paths") or []
    artifact_dir = Path(str(summary.get("artifact_dir") or "."))
    generated_artifacts = [
        str(path.relative_to(artifact_dir))
        for path in sorted(artifact_dir.rglob("*"))
        if path.is_file()
    ]
    completed_offload = []
    if summary.get("reproduce", {}).get("ok"):
        completed_offload.append("repository exploration")
        completed_offload.append("regression artifact proposal")
    if proposal_result.get("acceptance_criteria") or (proposal_result.get("reasoning_task_spec") or {}).get("acceptance_criteria"):
        completed_offload.append("acceptance criteria drafting")
    if capability_development:
        completed_offload.append("capability implementation checklist")
    if generated_diff_result.get("ok"):
        completed_offload.append("unified diff draft generation for allowed paths")
    if summary_phase_payload(summary, "verify").get("ok"):
        completed_offload.append("smoke command execution")
    completed_offload.append("recovery report drafting")
    completed_offload = sorted(set(completed_offload), key=codex_local_offload.index)
    pending_offload = [item for item in codex_local_offload if item not in completed_offload]
    total_work_buckets = len(codex_local_offload) + len(senior_review)
    offload_ratio = round((len(codex_local_offload) / total_work_buckets) * 100, 1) if total_work_buckets else 0.0
    verify_info = verify_summary(summary_phase_payload(summary, "verify"))
    phase_status = phase_status_summary(summary)
    readiness = capability_patch_readiness(summary, proposal_result, generated_diff_result)
    report = {
        "safe_to_offload_to_codex_local": codex_local_offload,
        "completed_by_codex_local_in_this_run": completed_offload,
        "pending_codex_local_work": pending_offload,
        "codex_senior_review_required_for": senior_review,
        "offload_ratio_percent": offload_ratio,
        "target_capability_name": capability_development.get("capability_name") or "",
        "phase_status": phase_status,
        "verify_summary": verify_info,
        "capability_patch_readiness": readiness,
        "generated_patch_paths": generated_paths,
        "safe_apply_candidate_paths": generated_diff_result.get("safe_apply_candidate_paths") or [],
        "safe_apply_candidate_patch_file": generated_diff_result.get("safe_apply_candidate_patch_file") or "",
        "review_only_patch_file": generated_diff_result.get("review_only_patch_file") or "",
        "generated_artifacts": generated_artifacts,
        "patch_application_decision": (
            "applied"
            if summary.get("patch", {}).get("applied")
            else ("validated_only" if generated_diff_result.get("ok") and summary.get("dry_run") else "not_applied")
        ),
        "why_patch_was_not_applied": (
            ""
            if summary.get("patch", {}).get("applied")
            else (
                "dry_run=true"
                if summary.get("dry_run")
                else str(summary.get("patch", {}).get("message") or summary.get("generated_diff", {}).get("message") or "")
            )
        ),
    }
    return report


def build_guarded_apply_manifest(summary: dict[str, Any], generated_diff_result: dict[str, Any]) -> dict[str, Any]:
    generated_paths = [str(path) for path in (generated_diff_result.get("paths") or [])]
    review_only_artifacts = sorted(
        [
            path
            for path in generated_paths
            if path.endswith(".runtime.patch.diff") or path.endswith(".gateway.patch.md")
        ]
    )
    safe_apply_candidate_paths = sorted(
        [
            path
            for path in generated_paths
            if path not in review_only_artifacts
        ]
    )
    promotable_runtime_candidates = sorted(
        [
            path
            for path in review_only_artifacts
            if path.endswith(".runtime.patch.diff")
        ]
    )
    verify_commands = [" ".join(command) for command in effective_verify_commands()]
    generated_diff_ok = bool(generated_diff_result.get("ok"))
    blocked_paths = generated_diff_result.get("blocked_paths") or []
    if blocked_paths:
        decision = "blocked_paths"
    elif review_only_artifacts:
        decision = "safe_apply_candidate_with_runtime_review"
    elif generated_diff_ok:
        decision = "safe_apply_candidate"
    else:
        decision = "no_apply_candidate"
    return {
        "kind": "codex-local-guarded-apply-manifest",
        "workspace": summary.get("workspace") or "",
        "mode": summary.get("mode") or "",
        "dry_run": bool(summary.get("dry_run")),
        "generated_diff_ok": generated_diff_ok,
        "generated_patch_file": generated_diff_result.get("patch_file") or "",
        "generated_patch_sha256": generated_diff_result.get("patch_sha256") or "",
        "safe_apply_candidate_patch_file": generated_diff_result.get("safe_apply_candidate_patch_file") or "",
        "safe_apply_candidate_patch_sha256": generated_diff_result.get("safe_apply_candidate_patch_sha256") or "",
        "review_only_patch_file": generated_diff_result.get("review_only_patch_file") or "",
        "review_only_patch_sha256": generated_diff_result.get("review_only_patch_sha256") or "",
        "decision": decision,
        "safe_apply_candidate_paths": safe_apply_candidate_paths,
        "review_only_runtime_artifacts": review_only_artifacts,
        "promotable_runtime_candidates": promotable_runtime_candidates,
        "blocked_paths": blocked_paths,
        "minimum_verify_commands": verify_commands,
        "runtime_gate_required_for": ["deploy", "e2e", "runtime_apply_promotion"],
        "manual_review_required": [
            "review runtime patch candidates before touching gateway.py",
            "confirm generated diff still matches current repo state",
            "re-run verify commands and live runtime fingerprint check before deploy",
        ],
        "promotion_blockers": (
            blocked_paths
            or (
                [
                    "runtime candidate artifacts are review-only by design",
                    "live runtime fingerprint gate must pass before promotion",
                    "senior Codex review must approve gateway.py changes",
                ]
                if promotable_runtime_candidates
                else []
            )
        ),
        "promotion_ready": bool(generated_diff_ok and not blocked_paths and not promotable_runtime_candidates),
        "manual_promotion_steps": [
            "Review runtime patch candidate diff against current gateway.py.",
            "Promote only the approved hunk set into a guarded unified diff patch file.",
            "Run the minimum verify commands again on the promoted patch.",
            "Run live gateway_runtime_fingerprint_check.py before and after deploy.",
        ],
        "manual_promotion_command_template": (
            "python3 codex/bin/agent_self_improve.py --workspace {workspace} "
            "--mode patch --patch-file /path/to/reviewed.patch"
        ),
        "promotion_rule": (
            "Generated unified diff may be applied only after senior review; runtime patch candidate artifacts "
            "remain review-only and must not be auto-applied."
        ),
    }


def write_final_report(
    audit_dir: Path,
    summary: dict[str, Any],
    proposal_result: dict[str, Any],
    generated_diff_result: dict[str, Any],
) -> dict[str, Any]:
    report = build_report(summary, proposal_result, generated_diff_result)
    manifest = build_guarded_apply_manifest(summary, generated_diff_result)
    write_json(audit_dir / "self-improve-report.json", report)
    write_json(audit_dir / "guarded-apply-manifest.json", manifest)
    lines = [
        "# Agent Self-Improve Report",
        "",
        f"- workspace: `{summary.get('workspace')}`",
        f"- mode: `{summary.get('mode')}`",
        f"- dry_run: `{summary.get('dry_run')}`",
        f"- diagnosis_category: `{summary.get('diagnosis_category')}`",
        f"- root_cause: `{summary.get('root_cause')}`",
        f"- cycles_completed: `{summary.get('cycles_completed')}` / `{summary.get('cycles_requested')}`",
        f"- target_capability_name: `{report.get('target_capability_name') or '-'}'",
        f"- patch_application_decision: `{report.get('patch_application_decision')}`",
        f"- guarded_apply_decision: `{manifest.get('decision')}`",
        f"- offload_ratio_percent: `{report.get('offload_ratio_percent')}`",
        "",
        "## Offloaded To Codex-Local",
        "",
    ]
    for item in report["safe_to_offload_to_codex_local"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Completed By Codex-Local In This Run", ""])
    for item in report["completed_by_codex_local_in_this_run"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Pending Codex-Local Work", ""])
    if report["pending_codex_local_work"]:
        for item in report["pending_codex_local_work"]:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(["", "## Senior Codex Review Still Required", ""])
    for item in report["codex_senior_review_required_for"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Phase Status", ""])
    for phase, status in report["phase_status"].items():
        lines.append(f"- {phase}: `{status}`")
    lines.extend(["", "## Verify Summary", ""])
    lines.append(f"- command_count: `{report['verify_summary'].get('command_count')}`")
    lines.append(f"- passed: `{report['verify_summary'].get('passed')}`")
    lines.append(f"- failed: `{report['verify_summary'].get('failed')}`")
    lines.append(f"- all_green: `{report['verify_summary'].get('all_green')}`")
    if report["verify_summary"].get("failed_commands"):
        lines.append("- failed commands:")
        for item in report["verify_summary"]["failed_commands"]:
            lines.append(f"  - `{item}`")
    lines.extend(["", "## Capability Patch Readiness", ""])
    readiness = report["capability_patch_readiness"]
    lines.append(f"- enabled: `{readiness.get('enabled')}`")
    lines.append(f"- target_capability_name: `{readiness.get('target_capability_name') or '-'}`")
    lines.append(f"- ready_for_review: `{readiness.get('ready_for_review')}`")
    lines.append(f"- ready_for_apply: `{readiness.get('ready_for_apply')}`")
    lines.append(f"- verify_all_green: `{readiness.get('verify_all_green')}`")
    lines.append(f"- git_apply_check_exit_code: `{readiness.get('git_apply_check_exit_code')}`")
    if readiness.get("missing_artifacts"):
        lines.append("- missing artifacts:")
        for item in readiness["missing_artifacts"]:
            lines.append(f"  - `{item}`")
    lines.extend(["", "## Generated Patch Paths", ""])
    if report["generated_patch_paths"]:
        for item in report["generated_patch_paths"]:
            lines.append(f"- `{item}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Safe Apply Candidate", ""])
    lines.append(f"- patch_file: `{report.get('safe_apply_candidate_patch_file') or '-'}`")
    if report.get("safe_apply_candidate_paths"):
        for item in report["safe_apply_candidate_paths"]:
            lines.append(f"- `{item}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Review-Only Patch Bundle", ""])
    lines.append(f"- patch_file: `{report.get('review_only_patch_file') or '-'}`")
    lines.extend(["", "## Artifacts", ""])
    for item in report["generated_artifacts"]:
        lines.append(f"- `{item}`")
    lines.extend(["", "## Guarded Apply Manifest", ""])
    lines.append(f"- decision: `{manifest.get('decision')}`")
    if manifest.get("safe_apply_candidate_paths"):
        lines.append("- safe apply candidate paths:")
        for item in manifest["safe_apply_candidate_paths"]:
            lines.append(f"  - `{item}`")
    if manifest.get("review_only_runtime_artifacts"):
        lines.append("- runtime review-only artifacts:")
        for item in manifest["review_only_runtime_artifacts"]:
            lines.append(f"  - `{item}`")
    if manifest.get("promotable_runtime_candidates"):
        lines.append("- promotable runtime candidates:")
        for item in manifest["promotable_runtime_candidates"]:
            lines.append(f"  - `{item}`")
    lines.append("- minimum verify commands:")
    for item in manifest["minimum_verify_commands"]:
        lines.append(f"  - `{item}`")
    if manifest.get("promotion_blockers"):
        lines.append("- promotion blockers:")
        for item in manifest["promotion_blockers"]:
            lines.append(f"  - {item}")
    lines.append(f"- promotion_ready: `{manifest.get('promotion_ready')}`")
    if report.get("why_patch_was_not_applied"):
        lines.extend(["", "## Patch Apply Recovery", "", f"- {report['why_patch_was_not_applied']}"])
    write_text(audit_dir / "self-improve-report.md", "\n".join(lines) + "\n")
    return report


def deploy(args: argparse.Namespace, audit_dir: Path) -> dict[str, Any]:
    gate = runtime_gate(args, audit_dir, "deploy")
    if not gate.get("ok"):
        return {
            "ok": False,
            "skipped": True,
            "reason": "runtime_fingerprint_gate_failed",
            "gate": gate,
            "message": "Deploy is blocked until runtime source epoch/fingerprint matches.",
        }
    command = [
        "python3",
        "codex/bin/gateway_admin.py",
        "--base-url",
        args.gateway_url,
        "deploy",
        "--branch",
        args.branch,
        "--force",
    ]
    if args.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "scheduled": False,
            "command": command,
            "message": "Deploy command prepared but not executed because dry_run=true.",
        }
    result = run_command(command, args.command_timeout)
    result["ok"] = result.get("exit_code") == 0
    write_json(audit_dir / "deploy-result.json", result)
    return result


def e2e(args: argparse.Namespace, audit_dir: Path, regression: dict[str, Any]) -> dict[str, Any]:
    gate = runtime_gate(args, audit_dir, "e2e")
    if not gate.get("ok"):
        return {
            "ok": False,
            "skipped": True,
            "reason": "runtime_fingerprint_gate_failed",
            "gate": gate,
            "message": "E2E is blocked until runtime source epoch/fingerprint matches.",
        }
    cases = regression.get("cases") or []
    prompt = args.e2e_prompt or (cases[0].get("prompt") if cases and isinstance(cases[0], dict) else "")
    if not prompt:
        return {"ok": False, "skipped": True, "reason": "no_e2e_prompt"}
    command = [
        "python3",
        "codex/bin/owui_chat_turn.py",
        "--base-url",
        args.openwebui_base_url,
        "--api-key-file",
        args.openwebui_api_key_file,
        "--model",
        args.model,
        "--stateless",
        "--prompt",
        prompt,
        "--timeout",
        str(min(args.timeout, 60)),
        "--total-timeout",
        str(args.command_timeout),
        "--attempts",
        "3",
        "--skip-codex-preflight",
    ]
    if args.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "executed": False,
            "command": command,
        }
    if not openwebui_api_key(args):
        return {
            "ok": False,
            "skipped": True,
            "reason": "openwebui_api_key_missing",
            "command": command,
        }
    result = run_command(command, args.command_timeout)
    result["ok"] = result.get("exit_code") == 0
    write_json(audit_dir / "e2e-result.json", result)
    return result


def diff_for_changed_files(paths: list[str]) -> str:
    chunks = []
    for rel in paths:
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            current = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError:
            continue
        before = []
        chunks.extend(difflib.unified_diff(before, current, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    return "".join(chunks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audited codex-local self-improvement routine.")
    parser.add_argument("--workspace", default="ai-stack")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--chat-url", default="")
    parser.add_argument("--transcript-file")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--failure-marker", default="")
    parser.add_argument("--expected-behavior", default="")
    parser.add_argument(
        "--mode",
        choices=[
            "diagnose",
            "reproduce",
            "propose_patch",
            "generate_unified_diff",
            "patch",
            "verify",
            "deploy",
            "e2e",
            "capability_develop",
            "full",
        ],
        default="diagnose",
    )
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--max-cycles", type=int, default=1)
    parser.add_argument("--patch-file", default="")
    parser.add_argument("--capability-name", default="")
    parser.add_argument("--target-capability-name", default="")
    parser.add_argument("--feature-request", default="")
    parser.add_argument("--audit-root", default=str(DEFAULT_AUDIT_ROOT))
    parser.add_argument("--openwebui-base-url", default=os.getenv("OWUI_BASE_URL", DEFAULT_OPENWEBUI_BASE_URL))
    parser.add_argument("--openwebui-api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--openwebui-api-key-file", default=os.getenv("OWUI_API_KEY_FILE", str(DEFAULT_OPENWEBUI_KEY_FILE)))
    parser.add_argument("--gateway-url", default=os.getenv("CODEX_GATEWAY_URL", DEFAULT_GATEWAY_URL))
    parser.add_argument("--model", default=os.getenv("CODEX_LOCAL_MODEL", "codex-local"))
    parser.add_argument("--branch", default="main")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--command-timeout", type=int, default=180)
    parser.add_argument("--e2e-prompt", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", args.workspace):
        raise SystemExit("workspace must match [A-Za-z0-9_.-]{1,80}")
    if args.max_cycles < 1 or args.max_cycles > 3:
        raise SystemExit("max_cycles must be between 1 and 3")
    if args.target_capability_name and not args.capability_name:
        args.capability_name = args.target_capability_name

    audit_dir = artifact_dir_for(args)
    audit_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        audit_dir / "run-manifest.json",
        {
            "kind": "codex-local-agent-self-improve-run",
            "created_at": utc_stamp(),
            "pid": os.getpid(),
            "workspace": args.workspace,
            "mode": args.mode,
            "max_cycles": args.max_cycles,
            "dry_run": bool(args.dry_run),
            "phases": SELF_IMPROVE_PHASES,
            "runtime_gate_required_for": ["deploy", "e2e"],
        },
    )

    transcript = collect_transcript(args)
    write_json(audit_dir / "transcript.json", transcript)

    text = transcript_text(transcript)
    if args.prompt:
        text = text + "\n\nuser: " + args.prompt
    markers = extract_markers(text)
    diagnosis = classify_failure(text, args.expected_behavior, args.failure_marker)
    diagnosis.update(
        {
            "workspace": args.workspace,
            "markers": markers,
            "artifact_dir": str(audit_dir),
            "dry_run": bool(args.dry_run),
            "mode": args.mode,
        }
    )
    write_json(audit_dir / "diagnosis.json", diagnosis)

    regression = infer_regression(transcript, diagnosis, args)
    write_json(audit_dir / "regression.json", regression)

    patch_result: dict[str, Any] = {"ok": True, "skipped": True}
    verify_result: dict[str, Any] = {"ok": True, "skipped": True}
    deploy_result: dict[str, Any] = {"ok": True, "skipped": True}
    e2e_result: dict[str, Any] = {"ok": True, "skipped": True}
    reproduce_result: dict[str, Any] = {"ok": True, "skipped": True}
    reasoning_result: dict[str, Any] = {"ok": True, "skipped": True}
    proposal_result: dict[str, Any] = {"ok": True, "skipped": True}
    generated_diff_result: dict[str, Any] = {"ok": True, "skipped": True}
    cycle_results: list[dict[str, Any]] = []
    previous_cycle_context: dict[str, Any] = {}

    cycle_modes = {"propose_patch", "generate_unified_diff", "patch", "capability_develop", "full"}
    cycles_to_run = args.max_cycles if args.mode in cycle_modes else 1

    for cycle in range(1, cycles_to_run + 1):
        cycle_dir = audit_dir / f"cycle-{cycle:02d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        cycle_record: dict[str, Any] = {
            "cycle": cycle,
            "ok": True,
            "phases": {},
            "previous_cycle_context": previous_cycle_context,
        }

        if args.mode in {"reproduce", "propose_patch", "generate_unified_diff", "patch", "capability_develop", "full"}:
            reproduce_result = reproduce(args, cycle_dir, regression)
            cycle_record["phases"]["reproduce"] = reproduce_result
            cycle_record["ok"] = cycle_record["ok"] and bool(reproduce_result.get("ok"))

        if args.mode in {"propose_patch", "generate_unified_diff", "patch", "capability_develop", "full"}:
            if previous_cycle_context:
                diagnosis["previous_cycle_failure"] = previous_cycle_context
            reasoning_result = reason(args, cycle_dir, transcript, diagnosis, regression)
            cycle_record["phases"]["reason"] = reasoning_result
            cycle_record["ok"] = cycle_record["ok"] and bool(reasoning_result.get("ok"))

            proposal_result = propose_patch(args, cycle_dir, diagnosis, regression, reasoning_result)
            cycle_record["phases"]["propose_patch"] = proposal_result
            cycle_record["ok"] = cycle_record["ok"] and bool(proposal_result.get("ok"))

            generated_diff_result = generate_unified_diff(
                args,
                cycle_dir,
                diagnosis,
                regression,
                reasoning_result,
                proposal_result,
            )
            cycle_record["phases"]["generate_unified_diff"] = generated_diff_result
            cycle_record["ok"] = cycle_record["ok"] and bool(generated_diff_result.get("ok"))

        if args.mode in {"patch", "full"}:
            patch_result = validate_or_apply_patch(args, cycle_dir, str(generated_diff_result.get("patch_file") or ""))
            cycle_record["phases"]["apply_guarded_patch"] = patch_result
            cycle_record["ok"] = cycle_record["ok"] and bool(patch_result.get("ok"))

        if args.mode in {"verify", "patch", "capability_develop", "full"}:
            if not cycle_record["ok"]:
                verify_result = {
                    "ok": False,
                    "skipped": True,
                    "reason": "previous_phase_failed",
                    "message": "Verify skipped because an earlier self-improve phase failed.",
                }
            else:
                verify_result = verify(args, cycle_dir)
            cycle_record["phases"]["verify"] = verify_result
            cycle_record["ok"] = cycle_record["ok"] and bool(verify_result.get("ok"))

        if args.mode in {"deploy", "full"}:
            if not verify_result.get("ok") and not verify_result.get("skipped"):
                deploy_result = {
                    "ok": False,
                    "skipped": True,
                    "reason": "verify_failed",
                    "message": "Self-improve never deploys when verification failed.",
                }
            else:
                deploy_result = deploy(args, cycle_dir)
            cycle_record["phases"]["deploy"] = deploy_result
            cycle_record["ok"] = cycle_record["ok"] and bool(deploy_result.get("ok"))

        if args.mode in {"e2e", "full"}:
            if args.mode == "full" and (not deploy_result.get("ok") and not deploy_result.get("skipped")):
                e2e_result = {
                    "ok": False,
                    "skipped": True,
                    "reason": "deploy_failed",
                    "message": "Self-improve never runs E2E after a failed deploy.",
                }
            else:
                e2e_result = e2e(args, cycle_dir, regression)
            cycle_record["phases"]["e2e"] = e2e_result
            cycle_record["ok"] = cycle_record["ok"] and bool(e2e_result.get("ok"))

        cycle_results.append(cycle_record)
        write_json(cycle_dir / "cycle-summary.json", cycle_record)
        if cycle_record["ok"]:
            break
        failed_phase_names = [
            name
            for name, payload in cycle_record.get("phases", {}).items()
            if isinstance(payload, dict) and not payload.get("ok")
        ]
        previous_cycle_context = {
            "failed_cycle": cycle,
            "cycle_dir": str(cycle_dir),
            "failed_phases": failed_phase_names,
            "failed_phase_details": [
                phase_failure_detail(name, payload)
                for name, payload in cycle_record.get("phases", {}).items()
                if isinstance(payload, dict) and not payload.get("ok")
            ],
            "verify_output_tail": (
                str((verify_result.get("commands") or [{}])[-1].get("output") or "")[-2000:]
                if isinstance(verify_result, dict) and verify_result.get("commands")
                else ""
            ),
            "recovery": "Use the failed phase output as additional context for the next generate_unified_diff cycle.",
        }

    learned_pattern = {
        "kind": "codex-local-failure-pattern",
        "created_at": utc_stamp(),
        "workspace": args.workspace,
        "diagnosis_category": diagnosis.get("category"),
        "root_cause": diagnosis.get("root_cause"),
        "regression_cases": [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)],
        "recommended_capability_scope": proposal_result.get("capability_development") or {},
        "status": "verified" if verify_result.get("ok") and not verify_result.get("skipped") else "recorded",
    }
    write_json(audit_dir / "learned-pattern.json", learned_pattern)

    summary = {
        "ok": all(
            bool(item.get("ok"))
            for item in (
                reproduce_result,
                reasoning_result,
                proposal_result,
                generated_diff_result,
                patch_result,
                verify_result,
                deploy_result,
                e2e_result,
            )
        ),
        "artifact_dir": str(audit_dir),
        "workspace": args.workspace,
        "mode": args.mode,
        "dry_run": bool(args.dry_run),
        "cycles_requested": cycles_to_run,
        "cycles_completed": len(cycle_results),
        "cycles": cycle_results,
        "diagnosis_category": diagnosis.get("category"),
        "root_cause": diagnosis.get("root_cause"),
        "patch_scope": diagnosis.get("patch_scope"),
        "regression_cases": [case.get("name") for case in regression.get("cases") or [] if isinstance(case, dict)],
        "reproduce": {
            "ok": reproduce_result.get("ok"),
            "skipped": reproduce_result.get("skipped", False),
            "command_count": len(reproduce_result.get("commands") or []),
        },
        "reason": {
            "ok": reasoning_result.get("ok"),
            "skipped": reasoning_result.get("skipped", False),
        },
        "proposal": {
            "ok": proposal_result.get("ok"),
            "skipped": proposal_result.get("skipped", False),
            "has_patch_file": proposal_result.get("has_patch_file", False),
            "capability_development": bool(proposal_result.get("capability_development")),
        },
        "generated_diff": {
            "ok": generated_diff_result.get("ok"),
            "skipped": generated_diff_result.get("skipped", False),
            "patch_file": generated_diff_result.get("patch_file"),
            "paths": generated_diff_result.get("paths") or [],
            "blocked_paths": generated_diff_result.get("blocked_paths") or [],
            "safe_apply_candidate_paths": generated_diff_result.get("safe_apply_candidate_paths") or [],
            "safe_apply_candidate_patch_file": generated_diff_result.get("safe_apply_candidate_patch_file") or "",
            "review_only_runtime_artifacts": generated_diff_result.get("review_only_runtime_artifacts") or [],
            "review_only_patch_file": generated_diff_result.get("review_only_patch_file") or "",
            "source": generated_diff_result.get("source"),
        },
        "patch": patch_result,
        "verify": {
            "ok": verify_result.get("ok"),
            "skipped": verify_result.get("skipped", False),
            "command_count": len(verify_result.get("commands") or []),
        },
        "deploy": deploy_result,
        "e2e": e2e_result,
    }
    summary["report"] = write_final_report(audit_dir, summary, proposal_result, generated_diff_result)
    write_json(audit_dir / "summary.json", summary)
    if args.json:
        print(json.dumps(redact(summary), ensure_ascii=False, indent=2))
    else:
        print("AGENT_SELF_IMPROVE_OK" if summary["ok"] else "AGENT_SELF_IMPROVE_NEEDS_ATTENTION")
        print(f"artifact_dir={summary['artifact_dir']}")
        print(f"diagnosis_category={summary['diagnosis_category']}")
        print(f"root_cause={summary['root_cause']}")
        print(f"regression_cases={','.join(summary['regression_cases']) or '(none)'}")
        print(f"verify_ok={summary['verify']['ok']}")
        print(f"deploy_ok={summary['deploy'].get('ok')}")
        print(f"e2e_ok={summary['e2e'].get('ok')}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
