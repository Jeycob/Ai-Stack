#!/usr/bin/env python3
"""End-to-end smoke test for the visible OpenWebUI audit chat flow.

This helper wraps owui_chat_turn.py, then reloads the target chat and verifies
that the visible user prompt and a completed assistant response for the same
turn key are present in chat history.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import owui_chat_turn as turn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the visible OpenWebUI audit chat flow end to end.")
    parser.add_argument("--base-url", default=turn.DEFAULT_BASE_URL)
    parser.add_argument("--chat-id", default=turn.DEFAULT_CHAT_ID)
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--api-key-file", default=str(turn.DEFAULT_API_KEY_FILE))
    parser.add_argument("--model", default=turn.DEFAULT_MODEL)
    parser.add_argument("--title", default="Codex audit log - OpenWebUI visible history")
    parser.add_argument("--prompt", help="Technical prompt text")
    parser.add_argument("--prompt-file", help="Technical prompt file")
    parser.add_argument("--visible-prompt", help="Visible human-facing prompt text")
    parser.add_argument("--visible-prompt-file", help="Visible human-facing prompt file")
    parser.add_argument("--expected-substring", help="Require the assistant reply to contain this substring")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=4.0)
    parser.add_argument("--total-timeout", type=float, default=240.0)
    parser.add_argument("--status-interval", type=float, default=3.0)
    parser.add_argument("--turn-key", help="Stable explicit turn key to reuse")
    parser.add_argument("--send-history", action="store_true")
    parser.add_argument("--no-live-status", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def load_chat(args: argparse.Namespace) -> dict:
    status, payload = turn.http_request(args, "GET", f"/api/v1/chats/{args.chat_id}")
    if status >= 400 or not isinstance(payload, dict):
        raise RuntimeError(f"Unable to load chat {args.chat_id}: {payload}")
    return payload["chat"]


def chat_messages(chat: dict) -> dict:
    history = chat.setdefault("history", {})
    return history.setdefault("messages", {})


def read_prompt(args: argparse.Namespace) -> tuple[str, str]:
    technical = turn.read_prompt(args)
    visible = turn.read_visible_prompt(args, technical)
    return technical, visible


def turn_messages(messages: dict, turn_key: str, model: str, visible_prompt: str) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    user_matches: list[tuple[str, dict]] = []
    assistant_matches: list[tuple[str, dict]] = []
    for msg_id, msg in messages.items():
        if not isinstance(msg, dict):
            continue
        if msg.get("codexTurnKey") != turn_key:
            continue
        role = msg.get("role")
        if role == "user" and msg.get("content") == visible_prompt:
            user_matches.append((msg_id, msg))
        elif role == "assistant" and msg.get("model") == model:
            assistant_matches.append((msg_id, msg))
    user_matches.sort(key=lambda item: int(item[1].get("timestamp") or 0))
    assistant_matches.sort(key=lambda item: int(item[1].get("timestamp") or 0))
    return user_matches, assistant_matches


def latest_completed_assistant(assistant_matches: list[tuple[str, dict]]) -> tuple[str, dict] | None:
    completed = [item for item in assistant_matches if item[1].get("done") is True]
    return completed[-1] if completed else None


def run_turn(args: argparse.Namespace, technical: str, visible: str) -> tuple[int, str, dict]:
    script = Path(__file__).resolve().parent / "owui_chat_turn.py"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as prompt_file:
        prompt_file.write(technical)
        prompt_path = prompt_file.name
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as visible_file:
        visible_file.write(visible)
        visible_path = visible_file.name
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as resp_file:
        response_json_path = resp_file.name

    try:
        cmd = [
            sys.executable,
            str(script),
            "--base-url",
            args.base_url,
            "--chat-id",
            args.chat_id,
            "--api-key-env",
            args.api_key_env,
            "--api-key-file",
            args.api_key_file,
            "--model",
            args.model,
            "--title",
            args.title,
            "--prompt-file",
            prompt_path,
            "--visible-prompt-file",
            visible_path,
            "--response-json-out",
            response_json_path,
            "--timeout",
            str(args.timeout),
            "--attempts",
            str(args.attempts),
            "--initial-delay",
            str(args.initial_delay),
            "--max-delay",
            str(args.max_delay),
            "--total-timeout",
            str(args.total_timeout),
            "--status-interval",
            str(args.status_interval),
        ]
        if args.turn_key:
            cmd.extend(["--turn-key", args.turn_key])
        if args.send_history:
            cmd.append("--send-history")
        if args.no_live_status:
            cmd.append("--no-live-status")
        if args.quiet:
            cmd.append("--quiet")

        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        raw_completion = {}
        response_json = Path(response_json_path)
        if response_json.is_file() and response_json.stat().st_size > 0:
            raw_completion = json.loads(response_json.read_text(encoding="utf-8"))
        return proc.returncode, proc.stdout or "", raw_completion
    finally:
        for path in [prompt_path, visible_path, response_json_path]:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    args = parse_args()
    technical, visible = read_prompt(args)
    turn_key = turn.effective_turn_key(args, visible, technical)
    before = load_chat(args)
    before_messages = chat_messages(before)
    before_ids = set(before_messages.keys())
    before_users, before_assistants = turn_messages(before_messages, turn_key, args.model, visible)

    rc, output, raw_completion = run_turn(args, technical, visible)
    after = load_chat(args)
    after_messages = chat_messages(after)
    after_users, after_assistants = turn_messages(after_messages, turn_key, args.model, visible)
    completed = latest_completed_assistant(after_assistants)

    if not after_users:
        raise SystemExit("OWUI_CHAT_SMOKE_FAILED\nreason=no visible user message found for turn key")
    if completed is None:
        raise SystemExit("OWUI_CHAT_SMOKE_FAILED\nreason=no completed assistant message found for turn key")

    assistant_id, assistant_msg = completed
    assistant_text = str(assistant_msg.get("content") or "")
    if args.expected_substring and args.expected_substring not in assistant_text:
        raise SystemExit(
            "OWUI_CHAT_SMOKE_FAILED\n"
            f"reason=assistant reply missing expected substring {args.expected_substring!r}\n"
            f"assistant_text={assistant_text}"
        )

    new_ids = sorted(set(after_messages.keys()) - before_ids)
    print("OWUI_CHAT_SMOKE_OK")
    print(f"chat_id={args.chat_id}")
    print(f"model={args.model}")
    print(f"turn_key={turn_key}")
    print(f"runner_exit_code={rc}")
    print(f"message_count_before={len(before_messages)}")
    print(f"message_count_after={len(after_messages)}")
    print(f"new_message_count={len(new_ids)}")
    print(f"turn_user_messages_before={len(before_users)}")
    print(f"turn_user_messages_after={len(after_users)}")
    print(f"turn_assistant_messages_before={len(before_assistants)}")
    print(f"turn_assistant_messages_after={len(after_assistants)}")
    print(f"assistant_message_id={assistant_id}")
    print(f"assistant_done={assistant_msg.get('done')}")
    print(f"chat_current_id={after.get('history', {}).get('currentId')}")
    print(f"completion_choices={len(raw_completion.get('choices', [])) if isinstance(raw_completion, dict) else 0}")
    print("assistant_text:")
    print(assistant_text.rstrip())
    if output.strip():
        print("runner_output:")
        print(output.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
