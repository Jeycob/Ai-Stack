#!/usr/bin/env python3
"""Offline regression smoke for codex-local preflight guards in owui_chat_turn.

This verifies that codex-local turns fail fast with explicit recovery markers
when capability-first runtime prerequisites are not ready, instead of falling
through to a plain OpenWebUI model completion.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TURN_PATH = ROOT / "codex/bin/owui_chat_turn.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline regression smoke for owui_chat_turn codex-local preflight.")
    parser.add_argument("--model", default="codex-local")
    return parser.parse_args()


def load_turn_module():
    spec = importlib.util.spec_from_file_location("owui_chat_turn_preflight_test", TURN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load turn helper from {TURN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SmokeArgs:
    model = "codex-local"
    no_follow_scheduled = True
    response_json_out = ""
    out = ""
    skip_codex_preflight = False
    base_url = "http://192.168.0.48:9090"
    timeout = 5.0
    total_timeout = 30.0
    api_key_file = str(ROOT / "codex/state/openwebui-api.key")


def local_runtime_fingerprint(turn) -> str:
    fn = getattr(turn, "local_gateway_runtime_fingerprint", None)
    if not callable(fn):
        raise SystemExit("PREFLIGHT_SMOKE_SETUP_FAILED\nreason=local_gateway_runtime_fingerprint missing")
    value = str(fn()).strip()
    if not value:
        raise SystemExit("PREFLIGHT_SMOKE_SETUP_FAILED\nreason=empty local gateway fingerprint")
    return value


def assert_token_missing_short_circuits(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        raise AssertionError("completion endpoint should not be reached when preflight fails")

    def fake_health(_args):
        return {
            "ok": True,
            "codex_local_ready": False,
            "capability_mode": "agent-first",
            "natural_codex_local_route": "agent_loop",
            "runtime_fingerprint": local_runtime_fingerprint(turn),
            "gateway_admin": {"lan_admin_ready": False},
            "readiness_issues": ["GATEWAY_ADMIN_TOKEN_MISSING"],
        }

    turn.http_request = fake_http
    turn.gateway_health_status = fake_health
    turn.run_codex_reconcile_check = lambda _args: {"ok": True}
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nProhlédni workspace.")
    joined = "\n".join(text_chunks)
    if rc != 23:
        raise SystemExit(f"PREFLIGHT_TOKEN_MISSING_FAILED\nreason=unexpected exit code {rc}")
    if "GATEWAY_ADMIN_TOKEN_MISSING" not in joined:
        raise SystemExit(f"PREFLIGHT_TOKEN_MISSING_FAILED\nreason=missing marker in {joined!r}")
    if calls:
        raise SystemExit(f"PREFLIGHT_TOKEN_MISSING_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_TOKEN_MISSING_OK")


def assert_filter_stale_short_circuits(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        raise AssertionError("completion endpoint should not be reached when filter reconcile fails")

    def fake_health(_args):
        return {
            "ok": True,
            "codex_local_ready": True,
            "capability_mode": "agent-first",
            "natural_codex_local_route": "agent_loop",
            "runtime_fingerprint": local_runtime_fingerprint(turn),
            "gateway_admin": {"lan_admin_ready": True},
            "readiness_issues": [],
        }

    turn.http_request = fake_http
    turn.gateway_health_status = fake_health
    turn.run_codex_reconcile_check = lambda _args: {
        "ok": False,
        "issues": [],
        "results": [{"issues": ["CODEX_LOCAL_FILTER_STALE"]}],
        "recovery": "Run: python3 codex/bin/reconcile_openwebui_functions.py",
    }
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nProhlédni workspace.")
    joined = "\n".join(text_chunks)
    if rc != 23:
        raise SystemExit(f"PREFLIGHT_FILTER_STALE_FAILED\nreason=unexpected exit code {rc}")
    if "CODEX_LOCAL_FILTER_STALE" not in joined:
        raise SystemExit(f"PREFLIGHT_FILTER_STALE_FAILED\nreason=missing marker in {joined!r}")
    if calls:
        raise SystemExit(f"PREFLIGHT_FILTER_STALE_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_FILTER_STALE_OK")


def assert_ready_reaches_completion(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path != "/api/chat/completions":
            raise AssertionError(f"ready stateless path touched unexpected endpoint: {method} {path}")
        return 200, {"choices": [{"message": {"content": "READY_OK"}}]}

    def fake_health(_args):
        return {
            "ok": True,
            "codex_local_ready": True,
            "capability_mode": "agent-first",
            "natural_codex_local_route": "agent_loop",
            "runtime_fingerprint": local_runtime_fingerprint(turn),
            "gateway_admin": {"lan_admin_ready": True},
            "readiness_issues": [],
        }

    turn.http_request = fake_http
    turn.gateway_health_status = fake_health
    turn.run_codex_reconcile_check = lambda _args: {"ok": True}
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nProhlédni workspace.")
    joined = "\n".join(text_chunks)
    if rc != 0:
        raise SystemExit(f"PREFLIGHT_READY_FAILED\nreason=unexpected exit code {rc}")
    if "READY_OK" not in joined:
        raise SystemExit(f"PREFLIGHT_READY_FAILED\nreason=missing completion text in {joined!r}")
    if calls != [("POST", "/api/chat/completions")]:
        raise SystemExit(f"PREFLIGHT_READY_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_READY_OK")


def assert_sse_completion_response_parses(turn) -> None:
    raw = "\n".join(
        [
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk","created":123,"model":"codex-local","choices":[{"index":0,"delta":{"role":"assistant","content":"AGENT_"},"finish_reason":null}]}',
            "",
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk","created":123,"model":"codex-local","choices":[{"index":0,"delta":{"content":"LOOP_OK"},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ]
    )
    parsed = turn.parse_openwebui_response(raw, "text/event-stream")
    text = turn.response_text(parsed)
    if text != "AGENT_LOOP_OK":
        raise SystemExit(f"PREFLIGHT_SSE_PARSE_FAILED\nreason=unexpected parsed text {text!r}")
    if parsed.get("model") != "codex-local":
        raise SystemExit(f"PREFLIGHT_SSE_PARSE_FAILED\nreason=unexpected model {parsed!r}")
    print("OWUI_SSE_COMPLETION_PARSE_OK")


def assert_runtime_split_brain_short_circuits(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        raise AssertionError("completion endpoint should not be reached when runtime fingerprint mismatches")

    def fake_health(_args):
        return {
            "ok": True,
            "codex_local_ready": True,
            "capability_mode": "agent-first",
            "natural_codex_local_route": "agent_loop",
            "runtime_fingerprint": "stale-runtime-fingerprint",
            "runtime_repo_root": turn.local_repo_root(),
            "runtime_commit": "different999",
            "gateway_admin": {"lan_admin_ready": True},
            "readiness_issues": [],
        }

    turn.http_request = fake_http
    turn.gateway_health_status = fake_health
    turn.run_codex_reconcile_check = lambda _args: {"ok": True}
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nProhlédni workspace.")
    joined = "\n".join(text_chunks)
    if rc != 23:
        raise SystemExit(f"PREFLIGHT_RUNTIME_SPLIT_BRAIN_FAILED\nreason=unexpected exit code {rc}")
    if "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN" not in joined:
        raise SystemExit(f"PREFLIGHT_RUNTIME_SPLIT_BRAIN_FAILED\nreason=missing marker in {joined!r}")
    if calls:
        raise SystemExit(f"PREFLIGHT_RUNTIME_SPLIT_BRAIN_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_RUNTIME_SPLIT_BRAIN_OK")


def assert_same_checkout_same_commit_ignores_fingerprint_warning(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path != "/api/chat/completions":
            raise AssertionError(f"same-checkout warning path touched unexpected endpoint: {method} {path}")
        return 200, {"choices": [{"message": {"content": "SAME_CHECKOUT_COMMIT_OK"}}]}

    def fake_health(_args):
        return {
            "ok": True,
            "codex_local_ready": True,
            "capability_mode": "agent-first",
            "natural_codex_local_route": "agent_loop",
            "runtime_fingerprint": "different-runtime-fingerprint",
            "runtime_repo_root": turn.local_repo_root(),
            "runtime_commit": turn.local_repo_commit_short(),
            "gateway_admin": {"lan_admin_ready": True},
            "readiness_issues": [],
        }

    turn.http_request = fake_http
    turn.gateway_health_status = fake_health
    turn.run_codex_reconcile_check = lambda _args: {"ok": True}
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nProhlédni workspace.")
    joined = "\n".join(text_chunks)
    if rc != 0:
        raise SystemExit(f"PREFLIGHT_SAME_CHECKOUT_SAME_COMMIT_FAILED\nreason=unexpected exit code {rc}")
    if "SAME_CHECKOUT_COMMIT_OK" not in joined:
        raise SystemExit(f"PREFLIGHT_SAME_CHECKOUT_SAME_COMMIT_FAILED\nreason=missing completion text in {joined!r}")
    if calls != [("POST", "/api/chat/completions")]:
        raise SystemExit(f"PREFLIGHT_SAME_CHECKOUT_SAME_COMMIT_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_SAME_CHECKOUT_SAME_COMMIT_OK")


def assert_foreign_clone_same_commit_reaches_completion(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path != "/api/chat/completions":
            raise AssertionError(f"foreign clone path touched unexpected endpoint: {method} {path}")
        return 200, {"choices": [{"message": {"content": "FOREIGN_CLONE_OK"}}]}

    turn.http_request = fake_http
    turn.gateway_health_status = lambda _args: {
        "ok": True,
        "codex_local_ready": True,
        "capability_mode": "agent-first",
        "natural_codex_local_route": "agent_loop",
        "runtime_fingerprint": "remote-fingerprint-different",
        "runtime_repo_root": "/mnt/c/Repositories/ai-stack",
        "runtime_commit": "same123",
        "gateway_admin": {"lan_admin_ready": True},
        "readiness_issues": [],
    }
    turn.run_codex_reconcile_check = lambda _args: {"ok": True}
    turn.local_repo_root = lambda: "/mnt/c/newRepos/Ai-Stack"
    turn.local_repo_commit_short = lambda: "same123"
    turn.local_gateway_runtime_fingerprint = lambda: "local-fingerprint-different"
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nProhlédni workspace.")
    joined = "\n".join(text_chunks)
    if rc != 0:
        raise SystemExit(f"PREFLIGHT_FOREIGN_CLONE_SAME_COMMIT_FAILED\nreason=unexpected exit code {rc}")
    if "FOREIGN_CLONE_OK" not in joined:
        raise SystemExit(f"PREFLIGHT_FOREIGN_CLONE_SAME_COMMIT_FAILED\nreason=missing completion text in {joined!r}")
    if calls != [("POST", "/api/chat/completions")]:
        raise SystemExit(f"PREFLIGHT_FOREIGN_CLONE_SAME_COMMIT_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_FOREIGN_CLONE_SAME_COMMIT_OK")


def assert_foreign_clone_different_commit_blocks_regular_chat(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        raise AssertionError("completion endpoint should not be reached for regular chat during clone drift")

    turn.http_request = fake_http
    turn.gateway_health_status = lambda _args: {
        "ok": True,
        "codex_local_ready": True,
        "capability_mode": "agent-first",
        "natural_codex_local_route": "agent_loop",
        "runtime_fingerprint": "remote-fingerprint-different",
        "runtime_repo_root": "/mnt/c/Repositories/ai-stack",
        "runtime_commit": "old123",
        "gateway_admin": {"lan_admin_ready": True},
        "readiness_issues": [],
    }
    turn.run_codex_reconcile_check = lambda _args: {"ok": True}
    turn.local_repo_root = lambda: "/mnt/c/newRepos/Ai-Stack"
    turn.local_repo_commit_short = lambda: "new456"
    turn.local_repo_tracking_commit_short = lambda ref="origin/main": "new456"
    turn.local_repo_status_short = lambda: ""
    turn.local_gateway_runtime_fingerprint = lambda: "local-fingerprint-different"
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nProhlédni workspace.")
    joined = "\n".join(text_chunks)
    if rc != 23:
        raise SystemExit(f"PREFLIGHT_FOREIGN_CLONE_DIFF_COMMIT_BLOCK_FAILED\nreason=unexpected exit code {rc}")
    if "CODEX_LOCAL_RUNTIME_CLONE_DRIFT" not in joined:
        raise SystemExit(f"PREFLIGHT_FOREIGN_CLONE_DIFF_COMMIT_BLOCK_FAILED\nreason=missing marker in {joined!r}")
    if calls:
        raise SystemExit(f"PREFLIGHT_FOREIGN_CLONE_DIFF_COMMIT_BLOCK_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_FOREIGN_CLONE_DIFF_COMMIT_BLOCK_OK")


def assert_explicit_deploy_recovers_foreign_clone_drift(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path != "/api/chat/completions":
            raise AssertionError(f"deploy drift recovery touched unexpected endpoint: {method} {path}")
        return 200, {"choices": [{"message": {"content": "STACK_DEPLOY_SCHEDULED"}}]}

    turn.http_request = fake_http
    turn.gateway_health_status = lambda _args: {
        "ok": True,
        "codex_local_ready": True,
        "capability_mode": "agent-first",
        "natural_codex_local_route": "agent_loop",
        "runtime_fingerprint": "remote-fingerprint-different",
        "runtime_repo_root": "/mnt/c/Repositories/ai-stack",
        "runtime_commit": "old123",
        "gateway_admin": {"lan_admin_ready": True},
        "readiness_issues": [],
    }
    turn.run_codex_reconcile_check = lambda _args: {
        "ok": False,
        "issues": ["CODEX_LOCAL_FILTER_STALE"],
        "recovery": "This must be bypassed only for explicit deploy drift recovery.",
    }
    turn.local_repo_root = lambda: "/mnt/c/newRepos/Ai-Stack"
    turn.local_repo_commit_short = lambda: "new456"
    turn.local_repo_tracking_commit_short = lambda ref="origin/main": "new456"
    turn.local_repo_status_short = lambda: ""
    turn.local_gateway_runtime_fingerprint = lambda: "local-fingerprint-different"
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nGATEWAY_ADMIN_DEPLOY_STACK main --force")
    joined = "\n".join(text_chunks)
    if rc != 0:
        raise SystemExit(f"PREFLIGHT_DEPLOY_DRIFT_RECOVERY_FAILED\nreason=unexpected exit code {rc}")
    if "STACK_DEPLOY_SCHEDULED" not in joined:
        raise SystemExit(f"PREFLIGHT_DEPLOY_DRIFT_RECOVERY_FAILED\nreason=missing deploy response in {joined!r}")
    if calls != [("POST", "/api/chat/completions")]:
        raise SystemExit(f"PREFLIGHT_DEPLOY_DRIFT_RECOVERY_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_DEPLOY_DRIFT_RECOVERY_OK")


def assert_deploy_status_allowed_during_foreign_clone_drift(turn) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path != "/api/chat/completions":
            raise AssertionError(f"deploy status drift recovery touched unexpected endpoint: {method} {path}")
        return 200, {"choices": [{"message": {"content": "STACK_DEPLOY_STATUS\nrunning=False"}}]}

    turn.http_request = fake_http
    turn.gateway_health_status = lambda _args: {
        "ok": True,
        "codex_local_ready": True,
        "capability_mode": "agent-first",
        "natural_codex_local_route": "agent_loop",
        "runtime_fingerprint": "remote-fingerprint-different",
        "runtime_repo_root": "/mnt/c/Repositories/ai-stack",
        "runtime_commit": "old123",
        "gateway_admin": {"lan_admin_ready": True},
        "readiness_issues": [],
    }
    turn.run_codex_reconcile_check = lambda _args: {"ok": False, "issues": ["CODEX_LOCAL_FILTER_STALE"]}
    turn.local_repo_root = lambda: "/mnt/c/newRepos/Ai-Stack"
    turn.local_repo_commit_short = lambda: "new456"
    turn.local_repo_tracking_commit_short = lambda ref="origin/main": ""
    turn.local_repo_status_short = lambda: " M README.md"
    turn.local_gateway_runtime_fingerprint = lambda: "local-fingerprint-different"
    text_chunks: list[str] = []
    turn.print = lambda *args, **kwargs: text_chunks.append(" ".join(str(x) for x in args))

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nGATEWAY_ADMIN_DEPLOY_STATUS")
    joined = "\n".join(text_chunks)
    if rc != 0:
        raise SystemExit(f"PREFLIGHT_DEPLOY_STATUS_DRIFT_FAILED\nreason=unexpected exit code {rc}")
    if "STACK_DEPLOY_STATUS" not in joined:
        raise SystemExit(f"PREFLIGHT_DEPLOY_STATUS_DRIFT_FAILED\nreason=missing status response in {joined!r}")
    if calls != [("POST", "/api/chat/completions")]:
        raise SystemExit(f"PREFLIGHT_DEPLOY_STATUS_DRIFT_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_PREFLIGHT_DEPLOY_STATUS_DRIFT_OK")


def main() -> int:
    _args = parse_args()
    turn = load_turn_module()
    assert_token_missing_short_circuits(turn)
    turn = load_turn_module()
    assert_filter_stale_short_circuits(turn)
    turn = load_turn_module()
    assert_runtime_split_brain_short_circuits(turn)
    turn = load_turn_module()
    assert_same_checkout_same_commit_ignores_fingerprint_warning(turn)
    turn = load_turn_module()
    assert_foreign_clone_same_commit_reaches_completion(turn)
    turn = load_turn_module()
    assert_foreign_clone_different_commit_blocks_regular_chat(turn)
    turn = load_turn_module()
    assert_explicit_deploy_recovers_foreign_clone_drift(turn)
    turn = load_turn_module()
    assert_deploy_status_allowed_during_foreign_clone_drift(turn)
    turn = load_turn_module()
    assert_ready_reaches_completion(turn)
    turn = load_turn_module()
    assert_sse_completion_response_parses(turn)
    print("OWUI_CHAT_TURN_PREFLIGHT_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
