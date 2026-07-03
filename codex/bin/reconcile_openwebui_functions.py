#!/usr/bin/env python3
"""Reconcile required OpenWebUI functions for codex-local.

This is intentionally broader than syncing one file: it verifies every required
function exists, is active/global, and matches the local runtime source after
embedding the current capability roadmap.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import sync_openwebui_function as sync


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RequiredFunction:
    function_id: str
    source: str
    name: str
    function_type: str = "filter"


REQUIRED_FUNCTIONS: tuple[RequiredFunction, ...] = (
    RequiredFunction(
        function_id="codex_gateway_admin_filter",
        source="codex/bin/openwebui_gateway_admin_filter.py",
        name="Codex Gateway Admin Filter",
    ),
    RequiredFunction(
        function_id="codex_auto_tools_filter",
        source="codex/bin/openwebui_codex_auto_tools_filter.py",
        name="Codex Auto Tools Filter",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile all required OpenWebUI codex-local functions.")
    parser.add_argument("--base-url", default="http://192.168.0.48:9090")
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--api-key-file", default="codex/state/openwebui-api.key")
    parser.add_argument("--dry-run", action="store_true", help="Report planned repairs without changing OpenWebUI.")
    parser.add_argument("--check-only", action="store_true", help="Fail if any function is missing, inactive, non-global, or stale.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=4.0)
    return parser.parse_args()


def sync_args(args: argparse.Namespace, source: str) -> SimpleNamespace:
    return SimpleNamespace(
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        api_key_file=args.api_key_file,
        source=str(ROOT / source),
        no_activate=False,
        no_global=False,
        dry_run=args.dry_run,
        timeout=args.timeout,
        attempts=args.attempts,
        initial_delay=args.initial_delay,
        max_delay=args.max_delay,
    )


def create_payload(spec: RequiredFunction, content: str) -> dict[str, Any]:
    return {
        "id": spec.function_id,
        "name": spec.name,
        "type": spec.function_type,
        "content": content,
        "is_active": True,
        "is_global": True,
        "meta": {
            "description": "Managed by ai-stack codex-local function reconciler.",
            "managed_by": "ai-stack",
        },
    }


def get_remote(args: SimpleNamespace, function_id: str) -> tuple[dict[str, Any] | None, str]:
    try:
        return sync.request_json(args, "GET", f"/api/v1/functions/id/{function_id}"), ""
    except RuntimeError as exc:
        text = str(exc)
        if "HTTP 404" in text:
            return None, "not_found"
        raise


def create_function(args: SimpleNamespace, spec: RequiredFunction, content: str) -> tuple[dict[str, Any], str]:
    payload = create_payload(spec, content)
    errors: list[str] = []
    for path in ("/api/v1/functions/create", "/api/v1/functions/"):
        try:
            return sync.request_json(args, "POST", path, payload), path
        except RuntimeError as exc:
            errors.append(f"{path}: {exc}")
    raise RuntimeError("function create failed:\n" + "\n".join(errors))


def classify_issues(remote: dict[str, Any] | None, local_hash: str) -> list[str]:
    if remote is None:
        return ["CODEX_LOCAL_FILTER_MISSING"]
    issues: list[str] = []
    if not bool(remote.get("is_active")):
        issues.append("CODEX_LOCAL_FILTER_INACTIVE")
    if not bool(remote.get("is_global")):
        issues.append("CODEX_LOCAL_FILTER_NOT_GLOBAL")
    if sync.sha256(str(remote.get("content") or "")) != local_hash:
        issues.append("CODEX_LOCAL_FILTER_STALE")
    return issues


def reconcile_one(args: argparse.Namespace, spec: RequiredFunction) -> dict[str, Any]:
    per_args = sync_args(args, spec.source)
    source = ROOT / spec.source
    if not source.is_file():
        return {
            "function_id": spec.function_id,
            "source": spec.source,
            "ok": False,
            "issues": ["CODEX_LOCAL_FUNCTION_SOURCE_MISSING"],
            "action": "error",
            "recovery": f"Restore missing source file: {spec.source}",
        }

    content = source.read_text(encoding="utf-8")
    runtime_content, embedded_roadmap = sync.runtime_content(per_args, content)
    local_hash = sync.sha256(runtime_content)
    remote, remote_error = get_remote(per_args, spec.function_id)
    issues = classify_issues(remote, local_hash)

    result: dict[str, Any] = {
        "function_id": spec.function_id,
        "source": spec.source,
        "ok": not issues,
        "issues": issues,
        "remote_error": remote_error,
        "remote_active": None if remote is None else bool(remote.get("is_active")),
        "remote_global": None if remote is None else bool(remote.get("is_global")),
        "embedded_roadmap": embedded_roadmap,
        "local_sha256": local_hash,
        "remote_sha256": "" if remote is None else sync.sha256(str(remote.get("content") or "")),
        "action": "no-op",
    }

    if not issues:
        return result
    if args.check_only:
        result["action"] = "check-failed"
        result["recovery"] = "Run: python3 codex/bin/reconcile_openwebui_functions.py"
        return result
    if args.dry_run:
        result["action"] = "dry-run-repair-needed"
        result["recovery"] = "Run without --dry-run to repair OpenWebUI functions."
        return result

    flag_actions: list[str] = []
    if remote is None:
        updated, strategy = create_function(per_args, spec, runtime_content)
        result["action"] = "created"
        result["update_strategy"] = strategy
    else:
        updated, strategy = sync.update_function_with_fallbacks(per_args, spec.function_id, remote, runtime_content)
        result["action"] = "updated"
        result["update_strategy"] = strategy

    updated, flag_actions = sync.ensure_function_flags(per_args, spec.function_id, updated)
    final_issues = classify_issues(updated, local_hash)
    result.update({
        "ok": not final_issues,
        "issues": final_issues,
        "remote_active": bool(updated.get("is_active")),
        "remote_global": bool(updated.get("is_global")),
        "remote_sha256": sync.sha256(str(updated.get("content") or "")),
        "flag_actions": flag_actions,
    })
    if final_issues:
        result["recovery"] = "OpenWebUI accepted the request but verification still failed; inspect function settings manually."
    return result


def recovery_summary(results: list[dict[str, Any]]) -> str:
    markers = sorted({issue for item in results for issue in item.get("issues", [])})
    if not markers:
        return ""
    return (
        " ".join(markers)
        + "\nRecovery: python3 codex/bin/reconcile_openwebui_functions.py\n"
        + "Then rerun: bash codex/bin/check_ai_stack.sh"
    )


def main() -> int:
    args = parse_args()
    try:
        results = [reconcile_one(args, spec) for spec in REQUIRED_FUNCTIONS]
    except SystemExit as exc:
        message = str(exc)
        if "API key is not set" in message:
            payload = {
                "ok": False,
                "issues": ["OPENWEBUI_API_KEY_MISSING"],
                "recovery": "Store the key in codex/state/openwebui-api.key or set OWUI_API_KEY.",
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "OPENWEBUI_API_KEY_MISSING\n" + payload["recovery"])
            return 2
        raise
    ok = all(bool(item.get("ok")) for item in results)
    payload = {
        "ok": ok,
        "required_count": len(REQUIRED_FUNCTIONS),
        "results": results,
        "recovery": recovery_summary(results),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OPENWEBUI_FUNCTION_RECONCILE")
        print(f"ok={str(ok).lower()}")
        for item in results:
            print(f"function={item['function_id']}")
            print(f"  ok={str(bool(item.get('ok'))).lower()}")
            print(f"  action={item.get('action')}")
            print(f"  active={item.get('remote_active')}")
            print(f"  global={item.get('remote_global')}")
            if item.get("flag_actions"):
                print("  flag_actions=" + ",".join(item["flag_actions"]))
            print(f"  local_sha256={item.get('local_sha256')}")
            print(f"  remote_sha256={item.get('remote_sha256')}")
            issues = item.get("issues") or []
            print("  issues=" + (",".join(issues) if issues else "(none)"))
            if item.get("recovery"):
                print("  recovery=" + str(item["recovery"]))
        if payload["recovery"]:
            print(payload["recovery"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
