#!/usr/bin/env python3
"""Offline regression smoke for visible-chat history auto-send in owui_chat_turn.

This protects the mentor/audit-chat workflow from losing workspace context on
follow-up turns. The helper should stay one-shot on the first visible turn, but
automatically include prior chat history on subsequent visible turns unless the
caller explicitly disables it with --no-send-history.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TURN_PATH = ROOT / "codex/bin/owui_chat_turn.py"


def load_turn_module():
    spec = importlib.util.spec_from_file_location("owui_chat_turn_history_test", TURN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load turn helper from {TURN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect(condition: bool, marker: str, details: str) -> None:
    if not condition:
        raise SystemExit(f"{marker}\n{details}")


def main() -> int:
    turn = load_turn_module()

    first_turn_messages = {
        "user-1": {
            "id": "user-1",
            "role": "user",
            "content": "repo: ai-stack\nPrvni task",
            "parentId": None,
        }
    }
    expect(
        turn.auto_send_history(first_turn_messages, "user-1") is False,
        "OWUI_CHAT_TURN_HISTORY_SMOKE_FAILED",
        "reason=first visible turn unexpectedly enabled history",
    )

    followup_messages = {
        "user-1": {
            "id": "user-1",
            "role": "user",
            "content": "vytvor mi nove repository TestCode\nvygeneruj do nej ssh klic",
            "parentId": None,
        },
        "assistant-1": {
            "id": "assistant-1",
            "role": "assistant",
            "content": (
                "AGENT_LOOP_OK\n"
                "requested_workspace=ai-stack\n"
                "controller_workspace=ai-stack\n"
                "execution:\n"
                "{\"workspace\": \"TestCode\", \"public_key_path\": \"codex/state/ssh/github-TestCode_ed25519.pub\"}"
            ),
            "parentId": "user-1",
        },
        "user-2": {
            "id": "user-2",
            "role": "user",
            "content": "vrat mi public key",
            "parentId": "assistant-1",
        },
    }
    expect(
        turn.auto_send_history(followup_messages, "user-2") is True,
        "OWUI_CHAT_TURN_HISTORY_SMOKE_FAILED",
        "reason=follow-up visible turn did not enable history automatically",
    )

    model_messages = turn.messages_for_model(followup_messages, "user-2", "repo: ai-stack\nvrat mi public key", True)
    expect(
        len(model_messages) == 3,
        "OWUI_CHAT_TURN_HISTORY_SMOKE_FAILED",
        f"reason=unexpected chained message count {len(model_messages)}",
    )
    expect(
        model_messages[-1]["content"] == "repo: ai-stack\nvrat mi public key",
        "OWUI_CHAT_TURN_HISTORY_SMOKE_FAILED",
        "reason=technical prompt was not applied to latest user turn",
    )

    print("OWUI_CHAT_TURN_HISTORY_SMOKE_OK")
    print("first_turn_send_history=false")
    print("followup_send_history=true")
    print(f"followup_chain_messages={len(model_messages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
