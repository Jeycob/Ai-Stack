#!/usr/bin/env python3
"""Offline regression smoke for stateless OpenWebUI nested turns.

This verifies that owui_chat_turn.py in stateless mode only calls the
completion endpoint and does not touch visible chat GET/POST APIs. It protects
the nested gateway -> helper -> OpenWebUI flow from re-entrant chat deadlocks.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TURN_PATH = ROOT / "codex/bin/owui_chat_turn.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline regression smoke for owui_chat_turn stateless mode.")
    parser.add_argument("--expected-text", default="STATELESS_OK")
    return parser.parse_args()


def load_turn_module():
    spec = importlib.util.spec_from_file_location("owui_chat_turn_test", TURN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load turn helper from {TURN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    args = parse_args()
    turn = load_turn_module()
    calls: list[tuple[str, str]] = []

    def fake_http(http_args, method: str, path: str, body=None, allow_error: bool = False):
        calls.append((method, path))
        if path != "/api/chat/completions":
            raise AssertionError(f"stateless mode touched unexpected endpoint: {method} {path}")
        return 200, {"choices": [{"message": {"content": args.expected_text}}]}

    turn.http_request = fake_http

    class SmokeArgs:
        model = "codex-local"
        no_follow_scheduled = True
        response_json_out = ""
        out = ""
        skip_codex_preflight = True

    rc = turn.run_stateless_completion(SmokeArgs(), "repo: ai-stack\nOdpovez jednim slovem: ok")
    if rc != 0:
        raise SystemExit(f"STATELESS_CHAT_SMOKE_FAILED\nreason=unexpected exit code {rc}")
    if calls != [("POST", "/api/chat/completions")]:
        raise SystemExit(
            "STATELESS_CHAT_SMOKE_FAILED\n"
            f"reason=unexpected endpoint sequence {calls!r}"
        )

    print("STATELESS_CHAT_SMOKE_OK")
    print(f"call_count={len(calls)}")
    print(f"endpoint={calls[0][1]}")
    print(f"response_text={args.expected_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
