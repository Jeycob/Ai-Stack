#!/usr/bin/env python3
"""Offline regression smoke for visible-chat fallback in owui_chat_turn.

This verifies that visible chat GET/POST failures do not crash the helper.
Instead, the helper must fall back to stateless completion and still return the
assistant result.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TURN_PATH = ROOT / "codex/bin/owui_chat_turn.py"


def load_turn_module():
    spec = importlib.util.spec_from_file_location("owui_chat_turn_visible_fallback_test", TURN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load turn helper from {TURN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SmokeArgs:
    model = "codex-local"
    stateless = False
    skip_codex_preflight = True
    no_follow_scheduled = True
    no_live_status = True
    response_json_out = ""
    out = ""
    quiet = True
    chat_id = "chat-smoke"
    title = "visible fallback smoke"
    send_history = False
    turn_key = ""
    status_interval = 5.0


def assert_initial_chat_load_falls_back(turn) -> None:
    printed: list[str] = []
    calls: list[tuple[str, str]] = []

    turn.parse_args = lambda: SmokeArgs()
    turn.read_prompt = lambda _args: "repo: ai-stack\nProhlédni workspace."
    turn.read_visible_prompt = lambda _args, technical: technical
    turn.codex_preflight_guard = lambda _args: None
    turn.print = lambda *args, **kwargs: printed.append(" ".join(str(x) for x in args))

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path.startswith("/api/v1/chats/"):
            raise RuntimeError("visible chat unavailable")
        if path == "/api/chat/completions":
            return 200, {"choices": [{"message": {"content": "FALLBACK_OK"}}]}
        raise AssertionError(f"unexpected endpoint: {method} {path}")

    turn.http_request = fake_http
    rc = turn.main()
    joined = "\n".join(printed)
    if rc != 0:
        raise SystemExit(f"VISIBLE_FALLBACK_LOAD_FAILED\nreason=unexpected exit code {rc}")
    if "FALLBACK_OK" not in joined:
        raise SystemExit(f"VISIBLE_FALLBACK_LOAD_FAILED\nreason=missing completion text in {joined!r}")
    if calls != [("GET", "/api/v1/chats/chat-smoke"), ("POST", "/api/chat/completions")]:
        raise SystemExit(f"VISIBLE_FALLBACK_LOAD_FAILED\nreason=unexpected HTTP calls {calls!r}")
    print("OWUI_VISIBLE_FALLBACK_LOAD_OK")


def assert_final_assistant_append_failure_keeps_completion(turn) -> None:
    printed: list[str] = []
    calls: list[tuple[str, str]] = []

    turn.parse_args = lambda: SmokeArgs()
    turn.read_prompt = lambda _args: "repo: ai-stack\nProhlédni workspace."
    turn.read_visible_prompt = lambda _args, technical: technical
    turn.codex_preflight_guard = lambda _args: None
    turn.print = lambda *args, **kwargs: printed.append(" ".join(str(x) for x in args))

    chat_payload = {"chat": {"history": {"messages": {}, "currentId": None}, "messages": []}}

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path == "/api/chat/completions":
            return 200, {"choices": [{"message": {"content": "APPEND_FALLBACK_OK"}}]}
        if path == "/api/v1/chats/chat-smoke":
            if method == "GET":
                if len([item for item in calls if item == ("GET", "/api/v1/chats/chat-smoke")]) >= 2:
                    raise RuntimeError("assistant append reload failed")
                return 200, chat_payload
            if method == "POST":
                return 200, {"ok": True}
        raise AssertionError(f"unexpected endpoint: {method} {path}")

    turn.http_request = fake_http
    rc = turn.main()
    joined = "\n".join(printed)
    if rc != 0:
        raise SystemExit(f"VISIBLE_FALLBACK_APPEND_FAILED\nreason=unexpected exit code {rc}")
    if "APPEND_FALLBACK_OK" not in joined:
        raise SystemExit(f"VISIBLE_FALLBACK_APPEND_FAILED\nreason=missing completion text in {joined!r}")
    print("OWUI_VISIBLE_FALLBACK_APPEND_OK")


def assert_completion_timeout_falls_back_to_stateless(turn) -> None:
    printed: list[str] = []
    calls: list[tuple[str, str]] = []

    turn.parse_args = lambda: SmokeArgs()
    turn.read_prompt = lambda _args: "repo: ai-stack\nProhlédni workspace."
    turn.read_visible_prompt = lambda _args, technical: technical
    turn.codex_preflight_guard = lambda _args: None
    turn.print = lambda *args, **kwargs: printed.append(" ".join(str(x) for x in args))

    chat_payload = {"chat": {"history": {"messages": {}, "currentId": None}, "messages": []}}
    state = {"completion_calls": 0}

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path == "/api/v1/chats/chat-smoke":
            if method == "GET":
                return 200, chat_payload
            if method == "POST":
                return 200, {"ok": True}
        if path == "/api/chat/completions":
            state["completion_calls"] += 1
            if state["completion_calls"] == 1:
                raise RuntimeError("completion timeout")
            return 200, {"choices": [{"message": {"content": "COMPLETION_FALLBACK_OK"}}]}
        raise AssertionError(f"unexpected endpoint: {method} {path}")

    turn.http_request = fake_http
    rc = turn.main()
    joined = "\n".join(printed)
    if rc != 0:
        raise SystemExit(f"VISIBLE_FALLBACK_COMPLETION_FAILED\nreason=unexpected exit code {rc}")
    if "COMPLETION_FALLBACK_OK" not in joined:
        raise SystemExit(f"VISIBLE_FALLBACK_COMPLETION_FAILED\nreason=missing fallback completion in {joined!r}")
    if calls.count(("POST", "/api/chat/completions")) != 2:
        raise SystemExit(f"VISIBLE_FALLBACK_COMPLETION_FAILED\nreason=expected visible+stateless completion retry, got {calls!r}")
    print("OWUI_VISIBLE_FALLBACK_COMPLETION_OK")


def main() -> int:
    turn = load_turn_module()
    assert_initial_chat_load_falls_back(turn)
    turn = load_turn_module()
    assert_final_assistant_append_failure_keeps_completion(turn)
    turn = load_turn_module()
    assert_completion_timeout_falls_back_to_stateless(turn)
    print("OWUI_CHAT_TURN_VISIBLE_FALLBACK_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
