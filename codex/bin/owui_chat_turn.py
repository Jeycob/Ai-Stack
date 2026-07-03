#!/usr/bin/env python3
"""Visible OpenWebUI chat turn helper.

Flow:
1. Load an existing OpenWebUI chat.
2. Append the user prompt to its visible history.
3. Call OpenWebUI /api/chat/completions with the selected model.
4. If the admin/gateway layer schedules a background job, optionally poll its
   status to completion while keeping the visible assistant message alive.
5. Append the assistant response to the same visible chat.

The goal is to avoid silent completions: every agent instruction and response is
left in the configured audit chat.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path


DEFAULT_BASE_URL = "http://192.168.0.48:9090"
DEFAULT_CHAT_ID = "57529037-84b9-42e1-8bae-9eab35b601bd"
DEFAULT_MODEL = "codex-local-plan-qwen14b"
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_API_KEY_FILE = Path(__file__).resolve().parents[1] / "state/openwebui-api.key"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a visible turn through an OpenWebUI chat.")
    parser.add_argument("--base-url", default=os.getenv("OWUI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--chat-id", default=os.getenv("OWUI_AUDIT_CHAT_ID", DEFAULT_CHAT_ID))
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--api-key-file", default=os.getenv("OWUI_API_KEY_FILE", str(DEFAULT_API_KEY_FILE)))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--title", default="Codex audit log - OpenWebUI visible history")
    parser.add_argument("--prompt", help="User prompt text")
    parser.add_argument("--prompt-file", help="User prompt file")
    parser.add_argument("--visible-prompt", help="Human-facing prompt to write into the visible OpenWebUI chat")
    parser.add_argument("--visible-prompt-file", help="Human-facing prompt file for the visible OpenWebUI chat")
    parser.add_argument("--out", help="Write assistant response text to file")
    parser.add_argument("--response-json-out", help="Write raw completion JSON to file")
    parser.add_argument("--turn-key", help="Stable idempotency key for reusing an already running visible turn")
    parser.add_argument("--send-history", action="store_true", help="Send the visible chat chain to the model")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-attempt timeout")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=4.0)
    parser.add_argument("--total-timeout", type=float, default=240.0)
    parser.add_argument("--job-poll-interval", type=float, default=6.0, help="Seconds between follow-up polls for scheduled admin jobs")
    parser.add_argument("--no-follow-scheduled", action="store_true", help="Do not poll scheduled admin jobs to completion")
    parser.add_argument("--no-live-status", action="store_true", help="Do not maintain a visible running assistant message")
    parser.add_argument("--status-interval", type=float, default=8.0, help="Seconds between visible running-status updates")
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=os.getenv("OWUI_STATELESS", "").strip().lower() in {"1", "true", "yes", "on"},
        help="Skip visible chat GET/POST mutations and call only /api/chat/completions.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def log(args: argparse.Namespace, msg: str) -> None:
    if not args.quiet:
        print(msg, file=sys.stderr)


def read_prompt(args: argparse.Namespace) -> str:
    if bool(args.prompt) == bool(args.prompt_file):
        raise SystemExit("Use exactly one of --prompt or --prompt-file")
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    return args.prompt or ""


def read_visible_prompt(args: argparse.Namespace, technical_prompt: str) -> str:
    if args.visible_prompt and args.visible_prompt_file:
        raise SystemExit("Use only one of --visible-prompt or --visible-prompt-file")
    if args.visible_prompt_file:
        return Path(args.visible_prompt_file).read_text(encoding="utf-8")
    if args.visible_prompt:
        return args.visible_prompt
    return technical_prompt


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def retry_delay(args: argparse.Namespace, attempt_index: int) -> float:
    return min(args.max_delay, args.initial_delay * (2 ** attempt_index))


def openwebui_api_key(args: argparse.Namespace) -> str:
    token = os.getenv(args.api_key_env) if args.api_key_env else ""
    if token:
        return token
    if args.api_key_file:
        path = Path(args.api_key_file)
        if path.is_file():
            token = path.read_text(encoding="utf-8").strip()
            if token:
                return token
    raise SystemExit(
        f"OpenWebUI API key is not set; checked env {args.api_key_env!r} "
        f"and file {args.api_key_file!r}"
    )


def http_request(
    args: argparse.Namespace,
    method: str,
    path: str,
    body: dict | None = None,
    allow_error: bool = False,
) -> tuple[int, dict | list | str]:
    token = openwebui_api_key(args)

    url = f"{args.base_url.rstrip('/')}/{path.lstrip('/')}"
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    client = opener()
    deadline = time.monotonic() + args.total_timeout
    last_error: BaseException | None = None

    for attempt in range(1, args.attempts + 1):
        if time.monotonic() >= deadline:
            break
        try:
            with client.open(req, timeout=args.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed: dict | list | str
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = raw
            if allow_error or exc.code not in RETRY_STATUSES:
                return exc.code, parsed
            last_error = exc
            log(args, f"attempt {attempt}: HTTP {exc.code}, retrying")
        except (urllib.error.URLError, TimeoutError, ConnectionError, RemoteDisconnected, BadStatusLine, OSError) as exc:
            last_error = exc
            log(args, f"attempt {attempt}: {type(exc).__name__}: {exc}")

        if attempt < args.attempts:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, retry_delay(args, attempt - 1)))

    raise RuntimeError(f"HTTP request failed after retries: {last_error}")


def append_message(
    messages: dict,
    parent_id: str | None,
    role: str,
    content: str,
    model: str,
    ts: int,
    done: bool = True,
    turn_key: str | None = None,
) -> str:
    msg_id = str(uuid.uuid4())
    if parent_id in messages:
        messages[parent_id].setdefault("childrenIds", [])
        if msg_id not in messages[parent_id]["childrenIds"]:
            messages[parent_id]["childrenIds"].append(msg_id)

    msg = {
        "id": msg_id,
        "parentId": parent_id,
        "childrenIds": [],
        "role": role,
        "content": content,
        "timestamp": ts,
    }
    if role == "assistant":
        msg.update({"model": model, "modelName": model, "done": done})
    else:
        msg.update({"models": [model]})
    if turn_key:
        msg["codexTurnKey"] = turn_key
    messages[msg_id] = msg
    return msg_id


def effective_turn_key(args: argparse.Namespace, visible_prompt: str, technical_prompt: str) -> str:
    if args.turn_key:
        return args.turn_key.strip()
    seed = "\n".join([args.chat_id, args.model, visible_prompt, technical_prompt])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def find_reusable_turn(messages: dict, model: str, visible_prompt: str, turn_key: str, now: int, max_age_s: int = 1800) -> tuple[str | None, str | None]:
    user_id: str | None = None
    assistant_id: str | None = None
    candidates = sorted(
        (
            (msg_id, msg)
            for msg_id, msg in messages.items()
            if isinstance(msg, dict) and msg.get("codexTurnKey") == turn_key
        ),
        key=lambda item: int(item[1].get("timestamp") or 0),
        reverse=True,
    )
    for msg_id, msg in candidates:
        ts = int(msg.get("timestamp") or 0)
        if ts and now - ts > max_age_s:
            continue
        role = msg.get("role")
        if role == "assistant" and msg.get("model") == model and msg.get("done") is False:
            parent_id = msg.get("parentId")
            parent = messages.get(parent_id, {}) if isinstance(parent_id, str) else {}
            if isinstance(parent, dict) and parent.get("role") == "user" and parent.get("content") == visible_prompt:
                return parent_id, msg_id
        if role == "user" and msg.get("content") == visible_prompt and user_id is None:
            user_id = msg_id
    return user_id, assistant_id


def running_text(args: argparse.Namespace, started: float, state: str) -> str:
    elapsed = int(time.monotonic() - started)
    return "\n".join(
        [
            "Codex-local is running.",
            f"model={args.model}",
            f"state={state}",
            f"elapsed_seconds={elapsed}",
            f"updated_at={time.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
    )


def update_visible_assistant(
    args: argparse.Namespace,
    message_id: str,
    content: str,
    done: bool,
) -> bool:
    status, chat_response = http_request(args, "GET", f"/api/v1/chats/{args.chat_id}")
    if status >= 400 or not isinstance(chat_response, dict):
        return False

    chat = chat_response["chat"]
    history = chat.setdefault("history", {})
    messages = history.setdefault("messages", {})
    msg = messages.get(message_id)
    if not isinstance(msg, dict):
        return False

    msg["content"] = content
    msg["done"] = done
    msg["timestamp"] = int(time.time())
    msg["model"] = args.model
    msg["modelName"] = args.model
    history["currentId"] = message_id
    chat["history"] = history
    chat["messages"] = list(messages.keys())
    chat["title"] = args.title
    http_request(args, "POST", f"/api/v1/chats/{args.chat_id}", {"chat": chat})
    return True


def start_live_status(
    args: argparse.Namespace,
    message_id: str,
    started: float,
) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()

    def worker() -> None:
        while not stop.wait(max(1.0, args.status_interval)):
            try:
                update_visible_assistant(
                    args,
                    message_id,
                    running_text(args, started, "waiting for gateway/model response"),
                    done=False,
                )
            except Exception as exc:
                log(args, f"live status update failed: {type(exc).__name__}: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return stop, thread


def chain_messages(messages: dict, current_id: str | None) -> list[dict[str, str]]:
    chain = []
    seen = set()
    msg_id = current_id
    while msg_id and msg_id in messages and msg_id not in seen:
        seen.add(msg_id)
        msg = messages[msg_id]
        role = msg.get("role")
        content = msg.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str):
            chain.append({"role": role, "content": content})
        msg_id = msg.get("parentId")
    chain.reverse()
    return chain


def messages_for_model(messages: dict, user_id: str, technical_prompt: str, send_history: bool) -> list[dict[str, str]]:
    if not send_history:
        return [{"role": "user", "content": technical_prompt}]
    chain = chain_messages(messages, user_id)
    for msg in reversed(chain):
        if msg.get("role") == "user":
            msg["content"] = technical_prompt
            return chain
    chain.append({"role": "user", "content": technical_prompt})
    return chain


def response_text(completion: dict | list | str) -> str:
    if not isinstance(completion, dict):
        return str(completion)
    if "detail" in completion:
        detail = completion["detail"]
        return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    choices = completion.get("choices") or []
    if not choices:
        return json.dumps(completion, ensure_ascii=False, indent=2)
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def is_expected_admin_detail(completion: dict | list | str) -> bool:
    if not isinstance(completion, dict):
        return False
    detail = completion.get("detail")
    if not isinstance(detail, str):
        return False
    prefixes = (
        "FILE ",
        "SSH_KEY_READY",
        "SSH_KEY_EXISTS",
        "SSH_KEYGEN_MISSING",
        "SSH_CLIENT_READY",
        "SSH_CLIENT_INSTALLED",
        "SSH_CLIENT_INSTALL_BLOCKED",
        "SSH_CLIENT_INSTALL_FAILED",
        "GIT_STATUS",
        "GIT_DIFF",
        "GIT_UNTRACK_IGNORED_OK",
        "GIT_PUSH_OK",
        "GIT_PUSH_BLOCKED",
        "PATCH_APPLIED",
        "AI_STACK_CHECK_OK",
        "AI_STACK_CHECK_FAILED",
        "REPO_GUARD_RESULT",
        "WORKSPACE_SCAN_RESULT",
        "WORKSPACE_RUN_SCHEDULED",
        "WORKSPACE_RUN_STATUS_OK",
        "WORKSPACE_RUN_STATUS_FAILED",
        "STACK_DEPLOY_SCHEDULED",
        "STACK_DEPLOY_STATUS",
        "repo_root:",
        "/:",
    )
    return detail.startswith(prefixes)


def parse_key_value_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        out[key] = value.strip()
    return out


def parse_scheduled_followup(text: str) -> tuple[str, str] | None:
    if text.startswith("WORKSPACE_RUN_SCHEDULED"):
        fields = parse_key_value_lines(text)
        job_id = fields.get("job_id", "").strip()
        if job_id:
            return ("workspace", job_id)
    if text.startswith("STACK_DEPLOY_SCHEDULED"):
        return ("deploy", "deploy")
    return None


def compact_job_status_text(raw_text: str) -> str:
    fields = parse_key_value_lines(raw_text)
    lines = []
    if raw_text.startswith("WORKSPACE_RUN_STATUS_OK") or raw_text.startswith("WORKSPACE_RUN_STATUS_FAILED"):
        lines.append(raw_text.splitlines()[0])
        for key in ("job_id", "workspace", "running", "exit_code", "runner_exit_code", "duration_ms"):
            if key in fields:
                lines.append(f"{key}={fields[key]}")
        if "tail" in raw_text:
            tail_idx = raw_text.find("tail")
            preview = raw_text[tail_idx:tail_idx + 600].strip()
            if preview:
                lines.append("")
                lines.append(preview)
        return "\n".join(lines).strip()
    if raw_text.startswith("STACK_DEPLOY_STATUS"):
        lines.append(raw_text.splitlines()[0])
        for key in ("running", "pid", "head", "log"):
            if key in fields:
                lines.append(f"{key}={fields[key]}")
        return "\n".join(lines).strip()
    return raw_text


def follow_scheduled_admin_job(
    args: argparse.Namespace,
    follow_kind: str,
    live_message_id: str | None,
    started: float,
    initial_text: str,
) -> str:
    deadline = time.monotonic() + max(1.0, args.total_timeout)
    last_text = initial_text
    while time.monotonic() < deadline:
        time.sleep(max(1.0, args.job_poll_interval))
        if follow_kind == "workspace":
            fields = parse_key_value_lines(last_text)
            job_id = fields.get("job_id", "").strip()
            if not job_id:
                return last_text
            follow_prompt = f"repo: ai-stack\nGATEWAY_ADMIN_RUN_WORKSPACE_STATUS {job_id}"
        else:
            follow_prompt = "repo: ai-stack\nGATEWAY_ADMIN_DEPLOY_STATUS"

        payload = {"model": args.model, "messages": [{"role": "user", "content": follow_prompt}], "stream": False}
        status, completion = http_request(args, "POST", "/api/chat/completions", payload, allow_error=True)
        polled_text = response_text(completion)
        if status >= 400 and not is_expected_admin_detail(completion):
            return f"OpenWebUI/model call failed with HTTP {status}:\n{polled_text}"
        last_text = polled_text
        compact = compact_job_status_text(polled_text)
        if live_message_id is not None:
            try:
                state = "polling background job"
                update_visible_assistant(
                    args,
                    live_message_id,
                    running_text(args, started, state) + "\n\n" + compact,
                    done=False,
                )
            except Exception as exc:
                log(args, f"poll status update failed: {type(exc).__name__}: {exc}")
        fields = parse_key_value_lines(polled_text)
        if follow_kind == "workspace":
            if fields.get("running", "").lower() == "false":
                return polled_text
        else:
            if fields.get("running", "").lower() == "false":
                return polled_text
    return last_text


def run_stateless_completion(args: argparse.Namespace, technical_prompt: str) -> int:
    payload = {"model": args.model, "messages": [{"role": "user", "content": technical_prompt}], "stream": False}
    completion_status, completion = http_request(args, "POST", "/api/chat/completions", payload, allow_error=True)
    text = response_text(completion)
    if not args.no_follow_scheduled:
        follow = parse_scheduled_followup(text)
        if follow:
            try:
                text = follow_scheduled_admin_job(args, follow[0], None, time.monotonic(), text)
                completion_status = 200
            except Exception as exc:
                text = text.rstrip() + f"\n\nFOLLOW_JOB_FAILED {type(exc).__name__}: {exc}"

    if args.response_json_out:
        Path(args.response_json_out).write_text(json.dumps(completion, ensure_ascii=False, indent=2), encoding="utf-8")
    if completion_status >= 400 and not is_expected_admin_detail(completion):
        text = f"OpenWebUI/model call failed with HTTP {completion_status}:\n{text}"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if completion_status < 400 or is_expected_admin_detail(completion) else 22


def main() -> int:
    args = parse_args()
    technical_prompt = read_prompt(args)
    if args.stateless:
        return run_stateless_completion(args, technical_prompt)
    visible_prompt = read_visible_prompt(args, technical_prompt)
    turn_key = effective_turn_key(args, visible_prompt, technical_prompt)
    status, chat_response = http_request(args, "GET", f"/api/v1/chats/{args.chat_id}")
    if status >= 400 or not isinstance(chat_response, dict):
        raise RuntimeError(f"Unable to load chat {args.chat_id}: {chat_response}")

    chat = chat_response["chat"]
    chat["title"] = args.title
    history = chat.setdefault("history", {})
    messages = history.setdefault("messages", {})
    current_id = history.get("currentId")
    now = int(time.time())
    user_id, live_message_id = find_reusable_turn(messages, args.model, visible_prompt, turn_key, now)

    if user_id is None:
        user_id = append_message(messages, current_id, "user", visible_prompt, args.model, now, turn_key=turn_key)
        history["currentId"] = user_id
        chat["history"] = history
        chat["messages"] = list(messages.keys())
        http_request(args, "POST", f"/api/v1/chats/{args.chat_id}", {"chat": chat})

    live_stop: threading.Event | None = None
    live_thread: threading.Thread | None = None
    started = time.monotonic()
    if not args.no_live_status:
        if live_message_id is None:
            status, chat_response = http_request(args, "GET", f"/api/v1/chats/{args.chat_id}")
            if status < 400 and isinstance(chat_response, dict):
                chat = chat_response["chat"]
                history = chat.setdefault("history", {})
                messages = history.setdefault("messages", {})
                live_message_id = append_message(
                    messages,
                    user_id,
                    "assistant",
                    running_text(args, started, "sent to OpenWebUI gateway"),
                    args.model,
                    int(time.time()),
                    done=False,
                    turn_key=turn_key,
                )
                history["currentId"] = live_message_id
                chat["history"] = history
                chat["messages"] = list(messages.keys())
                chat["title"] = args.title
                http_request(args, "POST", f"/api/v1/chats/{args.chat_id}", {"chat": chat})
        if live_message_id is not None:
            live_stop, live_thread = start_live_status(args, live_message_id, started)

    model_messages = messages_for_model(messages, user_id, technical_prompt, args.send_history)
    completion_payload = {"model": args.model, "messages": model_messages, "stream": False}
    completion_status, completion = http_request(args, "POST", "/api/chat/completions", completion_payload, allow_error=True)
    if live_stop is not None:
        live_stop.set()
    if live_thread is not None:
        live_thread.join(timeout=2.0)
    text = response_text(completion)
    if not args.no_follow_scheduled:
        follow = parse_scheduled_followup(text)
        if follow:
            try:
                text = follow_scheduled_admin_job(args, follow[0], live_message_id, started, text)
                completion_status = 200
            except Exception as exc:
                text = text.rstrip() + f"\n\nFOLLOW_JOB_FAILED {type(exc).__name__}: {exc}"
    if args.response_json_out:
        Path(args.response_json_out).write_text(json.dumps(completion, ensure_ascii=False, indent=2), encoding="utf-8")

    if completion_status >= 400 and not is_expected_admin_detail(completion):
        text = f"OpenWebUI/model call failed with HTTP {completion_status}:\n{text}"

    if live_message_id is not None and update_visible_assistant(args, live_message_id, text, done=True):
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0 if completion_status < 400 or is_expected_admin_detail(completion) else 22

    status, chat_response = http_request(args, "GET", f"/api/v1/chats/{args.chat_id}")
    if status >= 400 or not isinstance(chat_response, dict):
        raise RuntimeError(f"Unable to reload chat {args.chat_id}: {chat_response}")
    chat = chat_response["chat"]
    history = chat.setdefault("history", {})
    messages = history.setdefault("messages", {})
    current_id = history.get("currentId") or user_id
    assistant_id = append_message(messages, current_id, "assistant", text, args.model, int(time.time()), turn_key=turn_key)
    history["currentId"] = assistant_id
    chat["history"] = history
    chat["messages"] = list(messages.keys())
    chat["title"] = args.title
    http_request(args, "POST", f"/api/v1/chats/{args.chat_id}", {"chat": chat})

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if completion_status < 400 or is_expected_admin_detail(completion) else 22


if __name__ == "__main__":
    raise SystemExit(main())
