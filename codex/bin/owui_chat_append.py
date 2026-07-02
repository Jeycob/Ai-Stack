#!/usr/bin/env python3
"""Append messages to an OpenWebUI chat JSON response.

This script is intentionally offline-only. Use owui_request.sh to GET the chat,
run this script to prepare an update payload, then POST it back.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append messages to OpenWebUI chat JSON.")
    parser.add_argument("--input", required=True, help="Chat JSON response from /api/v1/chats/{id}")
    parser.add_argument("--out", required=True, help="Output payload JSON for POST /api/v1/chats/{id}")
    parser.add_argument("--title", help="Optional chat title")
    parser.add_argument("--model", default="codex-local-plan-qwen14b", help="Model metadata for appended assistant messages")
    parser.add_argument("--user", action="append", default=[], help="Append user message text")
    parser.add_argument("--user-file", action="append", default=[], help="Append user message from file")
    parser.add_argument("--assistant", action="append", default=[], help="Append assistant message text")
    parser.add_argument("--assistant-file", action="append", default=[], help="Append assistant message from file")
    return parser.parse_args()


def append_message(messages: dict, parent_id: str | None, role: str, content: str, model: str, ts: int) -> str:
    msg_id = str(uuid.uuid4())
    if parent_id in messages:
        messages[parent_id].setdefault("childrenIds", [])
        if msg_id not in messages[parent_id]["childrenIds"]:
            messages[parent_id]["childrenIds"].append(msg_id)

    message = {
        "id": msg_id,
        "parentId": parent_id,
        "childrenIds": [],
        "role": role,
        "content": content,
        "timestamp": ts,
    }
    if role == "assistant":
        message.update({"model": model, "modelName": model, "done": True})
    else:
        message.update({"models": [model]})
    messages[msg_id] = message
    return msg_id


def main() -> int:
    args = parse_args()
    response = json.loads(Path(args.input).read_text(encoding="utf-8"))
    chat = response["chat"]
    if args.title:
        chat["title"] = args.title

    history = chat.setdefault("history", {})
    messages = history.setdefault("messages", {})
    current_id = history.get("currentId")
    ts = int(time.time())

    pairs: list[tuple[str, str]] = [("user", text) for text in args.user]
    pairs.extend(("user", Path(path).read_text(encoding="utf-8")) for path in args.user_file)
    pairs.extend(("assistant", text) for text in args.assistant)
    pairs.extend(("assistant", Path(path).read_text(encoding="utf-8")) for path in args.assistant_file)

    for role, content in pairs:
        current_id = append_message(messages, current_id, role, content, args.model, ts)
        ts += 1

    history["currentId"] = current_id
    chat["history"] = history
    chat["messages"] = list(messages.keys())
    Path(args.out).write_text(json.dumps({"chat": chat}, ensure_ascii=False), encoding="utf-8")
    print(f"prepared {len(pairs)} appended messages; currentId={current_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
