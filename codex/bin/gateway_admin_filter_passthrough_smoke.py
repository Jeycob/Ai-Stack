#!/usr/bin/env python3
"""Offline smoke for non-blocking OpenWebUI gateway-admin agent-loop routing."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILTER_PATH = ROOT / "codex/bin/openwebui_gateway_admin_filter.py"


def load_filter_class():
    spec = importlib.util.spec_from_file_location("openwebui_gateway_admin_filter", FILTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load filter module from {FILTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Filter


def main() -> int:
    filter_obj = load_filter_class()()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("agent-loop must pass through to gateway streaming, not execute inside filter")

    filter_obj._gateway_admin_request = fail_if_called
    filter_obj._workspaces = lambda: {"ai-stack": {"path": "/tmp/ai-stack"}}

    natural = {
        "model": "codex-local",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "repo: ai-stack\nProhlédni architekturu gateway/filter/helper vrstvy. Nic needituj.",
            }
        ],
    }
    routed = filter_obj.inlet(natural)
    content = routed["messages"][-1]["content"]
    if routed.get("stream") is not True:
        raise SystemExit(f"expected stream=True for natural agent-loop passthrough, got {routed!r}")
    if "GATEWAY_ADMIN_AGENT_LOOP ai-stack --" not in content:
        raise SystemExit(f"expected natural prompt to become agent-loop command, got {content!r}")

    explicit = {
        "model": "codex-local",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "GATEWAY_ADMIN_AGENT_LOOP ai-stack -- 'Nic needituj. Rekni stav.'",
            }
        ],
    }
    routed = filter_obj.inlet(explicit)
    if routed.get("stream") is not True:
        raise SystemExit(f"expected stream=True for explicit agent-loop passthrough, got {routed!r}")
    if routed["messages"][-1]["content"] != explicit["messages"][-1]["content"]:
        raise SystemExit("explicit agent-loop command should not be rewritten")

    print("GATEWAY_ADMIN_FILTER_AGENT_LOOP_PASSTHROUGH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
