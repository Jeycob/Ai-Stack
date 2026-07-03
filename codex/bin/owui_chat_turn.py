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
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path
from urllib.parse import urlparse, urlunparse


DEFAULT_BASE_URL = "http://192.168.0.48:9090"
DEFAULT_CHAT_ID = "57529037-84b9-42e1-8bae-9eab35b601bd"
DEFAULT_MODEL = "codex-local-plan-qwen14b"
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_API_KEY_FILE = Path(__file__).resolve().parents[1] / "state/openwebui-api.key"
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_RECONCILE_SCRIPT = ROOT / "codex/bin/reconcile_openwebui_functions.py"


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
    parser.add_argument(
        "--skip-codex-preflight",
        action="store_true",
        default=os.getenv("OWUI_SKIP_CODEX_PREFLIGHT", "").strip().lower() in {"1", "true", "yes", "on"},
        help="Skip codex-local capability-first preflight checks before calling OpenWebUI completions.",
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


def is_codex_local_model(model: str) -> bool:
    return str(model or "").startswith("codex-local-")


def default_gateway_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    scheme = parsed.scheme or "http"
    netloc = host
    if parsed.username or parsed.password:
        auth = parsed.username or ""
        if parsed.password:
            auth += ":" + parsed.password
        netloc = auth + "@" + netloc
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    netloc += ":9101"
    return urlunparse((scheme, netloc, "", "", "", ""))


def gateway_base_url(args: argparse.Namespace) -> str:
    value = os.getenv("CODEX_GATEWAY_URL", "").strip()
    if value:
        return value.rstrip("/")
    return default_gateway_base_url(args.base_url).rstrip("/")


def gateway_health_status(args: argparse.Namespace) -> dict:
    url = f"{gateway_base_url(args)}/health"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    client = opener()
    with client.open(req, timeout=args.timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw or "{}")


def local_gateway_runtime_fingerprint() -> str:
    try:
        from codex.gateway import gateway as gateway_module
    except Exception:
        return ""
    fn = getattr(gateway_module, "runtime_fingerprint", None)
    if not callable(fn):
        return ""
    try:
        value = str(fn()).strip()
    except Exception:
        return ""
    return value


def local_repo_root() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except Exception:
        return str(ROOT)
    value = (proc.stdout or "").strip()
    return value or str(ROOT)


def local_repo_commit_short() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except Exception:
        return ""
    return (proc.stdout or "").strip()


def runtime_checkout_identity(health: dict) -> dict[str, str | bool]:
    local_root = local_repo_root()
    local_commit = local_repo_commit_short()
    remote_root = str(health.get("runtime_repo_root") or "").strip()
    remote_commit = str(health.get("runtime_commit") or "").strip()
    same_checkout = bool(remote_root) and Path(remote_root).resolve() == Path(local_root).resolve()
    same_commit = bool(remote_commit) and bool(local_commit) and remote_commit == local_commit
    return {
        "local_repo_root": local_root,
        "remote_repo_root": remote_root,
        "local_repo_commit": local_commit,
        "remote_repo_commit": remote_commit,
        "same_checkout": same_checkout,
        "same_commit": same_commit,
    }


def run_codex_reconcile_check(args: argparse.Namespace) -> dict:
    script = DEFAULT_RECONCILE_SCRIPT
    if not script.is_file():
        return {
            "ok": False,
            "issues": ["CODEX_LOCAL_RECONCILER_MISSING"],
            "recovery": f"Restore missing script: {script}",
        }
    cmd = [
        sys.executable,
        str(script),
        "--base-url",
        args.base_url,
        "--api-key-file",
        args.api_key_file,
        "--check-only",
        "--json",
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(10.0, min(args.total_timeout, 60.0)),
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "issues": ["CODEX_LOCAL_RECONCILE_PARSE_FAILED"],
            "recovery": "Run: python3 codex/bin/reconcile_openwebui_functions.py --check-only --json",
            "raw": (proc.stdout or "")[:2000],
        }
    return payload if isinstance(payload, dict) else {
        "ok": False,
        "issues": ["CODEX_LOCAL_RECONCILE_BAD_PAYLOAD"],
        "recovery": "Run: python3 codex/bin/reconcile_openwebui_functions.py --check-only --json",
    }


def codex_preflight_failure_text(marker: str, recovery: str, *, details: dict | None = None) -> str:
    lines = [marker]
    if recovery.strip():
        lines.append(f"recovery={recovery.strip()}")
    if details:
        try:
            rendered = json.dumps(details, ensure_ascii=False, indent=2)
        except TypeError:
            rendered = str(details)
        lines.extend(["details:", "```json", rendered[:12000], "```"])
    return "\n".join(lines).strip()


def codex_preflight_guard(args: argparse.Namespace) -> str | None:
    if args.skip_codex_preflight or not is_codex_local_model(args.model):
        return None

    try:
        health = gateway_health_status(args)
    except Exception as exc:
        return codex_preflight_failure_text(
            "CODEX_LOCAL_GATEWAY_UNAVAILABLE",
            f"Zkontroluj gateway na {gateway_base_url(args)}/health a pak spusť bash codex/bin/check_ai_stack.sh",
            details={"error": f"{type(exc).__name__}: {exc}", "gateway_url": gateway_base_url(args)},
        )

    readiness_issues = [str(item) for item in (health.get("readiness_issues") or [])]
    capability_mode = str(health.get("capability_mode") or "").strip()
    natural_route = str(health.get("natural_codex_local_route") or "").strip()
    if capability_mode != "agent-first" or natural_route != "agent_loop":
        return codex_preflight_failure_text(
            "CODEX_LOCAL_AGENT_ROUTE_DEGRADED",
            "Restartuj gateway/runtime a ověř agent-first routing přes bash codex/bin/check_ai_stack.sh",
            details={"gateway_health": health},
        )
    if health.get("gateway_admin", {}).get("lan_admin_ready") is False or "GATEWAY_ADMIN_TOKEN_MISSING" in readiness_issues:
        return codex_preflight_failure_text(
            "GATEWAY_ADMIN_TOKEN_MISSING",
            "Ulož gateway admin token a znovu spusť codex/bin/start_codex_stack.sh nebo bash codex/bin/check_ai_stack.sh",
            details={"gateway_health": health},
        )
    if health.get("codex_local_ready") is not True:
        marker = readiness_issues[0] if readiness_issues else "CODEX_LOCAL_RUNTIME_NOT_READY"
        return codex_preflight_failure_text(
            marker,
            "Oprav readiness issues z /health a pak spusť bash codex/bin/check_ai_stack.sh",
            details={"gateway_health": health},
        )

    remote_fingerprint = str(health.get("runtime_fingerprint") or "").strip()
    local_fingerprint = local_gateway_runtime_fingerprint()
    checkout = runtime_checkout_identity(health)
    same_commit = bool(checkout.get("same_commit"))
    if not remote_fingerprint:
        return codex_preflight_failure_text(
            "CODEX_LOCAL_RUNTIME_FINGERPRINT_MISSING",
            "Nasad a restartuj aktuální ai-stack runtime; /health musí vracet runtime_fingerprint.",
            details={"gateway_health": health, "local_runtime_fingerprint": local_fingerprint, "checkout": checkout},
        )
    if bool(checkout.get("same_checkout")) and local_fingerprint and remote_fingerprint != local_fingerprint and not same_commit:
        return codex_preflight_failure_text(
            "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN",
            "Běží starý gateway/runtime proces nad novějším repem. Restartuj stack přes codex/bin/start_codex_stack.sh nebo codex/bin/deploy_ai_stack.sh.",
            details={
                "gateway_health": health,
                "local_runtime_fingerprint": local_fingerprint,
                "remote_runtime_fingerprint": remote_fingerprint,
                "checkout": checkout,
            },
        )
    if not bool(checkout.get("same_checkout")) and local_fingerprint and remote_fingerprint != local_fingerprint:
        if not same_commit:
            return codex_preflight_failure_text(
                "CODEX_LOCAL_RUNTIME_CLONE_DRIFT",
                "Lokální clone není na stejném commitu jako běžící runtime. Synchronizuj repo nebo helper spusť z live runtime checkoutu.",
                details={
                    "gateway_health": health,
                    "local_runtime_fingerprint": local_fingerprint,
                    "remote_runtime_fingerprint": remote_fingerprint,
                    "checkout": checkout,
                },
            )

    reconcile = run_codex_reconcile_check(args)
    if not bool(reconcile.get("ok")):
        issues = [str(item) for item in (reconcile.get("issues") or [])]
        if not issues:
            for result in reconcile.get("results") or []:
                issues.extend(str(item) for item in (result.get("issues") or []))
        marker = issues[0] if issues else "CODEX_LOCAL_FILTER_STALE"
        recovery = str(reconcile.get("recovery") or "Run: python3 codex/bin/reconcile_openwebui_functions.py").strip()
        return codex_preflight_failure_text(marker, recovery, details=reconcile)

    return None


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


def completion_exit_code(completion_status: int, completion: dict | list | str) -> int:
    return 0 if completion_status < 400 or is_expected_admin_detail(completion) else 22


def log_visible_chat_degraded(args: argparse.Namespace, stage: str, exc: Exception) -> None:
    log(
        args,
        (
            "OWUI_VISIBLE_CHAT_DEGRADED "
            f"stage={stage} "
            "recovery=Retry OpenWebUI chat sync or rerun with --stateless "
            f"error={type(exc).__name__}: {exc}"
        ),
    )


def fallback_to_stateless(args: argparse.Namespace, technical_prompt: str, stage: str, exc: Exception) -> int:
    log_visible_chat_degraded(args, stage, exc)
    return run_stateless_completion(args, technical_prompt, skip_preflight=True)


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


def run_stateless_completion(args: argparse.Namespace, technical_prompt: str, skip_preflight: bool = False) -> int:
    if not skip_preflight:
        preflight = codex_preflight_guard(args)
        if preflight:
            if args.out:
                Path(args.out).write_text(preflight, encoding="utf-8")
            print(preflight)
            return 23
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
    return completion_exit_code(completion_status, completion)


def main() -> int:
    args = parse_args()
    technical_prompt = read_prompt(args)
    if args.stateless:
        return run_stateless_completion(args, technical_prompt)
    preflight = codex_preflight_guard(args)
    if preflight:
        if args.out:
            Path(args.out).write_text(preflight, encoding="utf-8")
        print(preflight)
        return 23
    visible_prompt = read_visible_prompt(args, technical_prompt)
    turn_key = effective_turn_key(args, visible_prompt, technical_prompt)
    try:
        status, chat_response = http_request(args, "GET", f"/api/v1/chats/{args.chat_id}")
    except Exception as exc:
        return fallback_to_stateless(args, technical_prompt, "load-chat", exc)
    if status >= 400 or not isinstance(chat_response, dict):
        return fallback_to_stateless(
            args,
            technical_prompt,
            "load-chat",
            RuntimeError(f"Unable to load chat {args.chat_id}: {chat_response}"),
        )

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
        try:
            http_request(args, "POST", f"/api/v1/chats/{args.chat_id}", {"chat": chat})
        except Exception as exc:
            return fallback_to_stateless(args, technical_prompt, "append-user", exc)

    live_stop: threading.Event | None = None
    live_thread: threading.Thread | None = None
    started = time.monotonic()
    if not args.no_live_status:
        if live_message_id is None:
            try:
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
            except Exception as exc:
                log_visible_chat_degraded(args, "start-live-status", exc)
                live_message_id = None
        if live_message_id is not None:
            live_stop, live_thread = start_live_status(args, live_message_id, started)

    model_messages = messages_for_model(messages, user_id, technical_prompt, args.send_history)
    completion_payload = {"model": args.model, "messages": model_messages, "stream": False}
    try:
        completion_status, completion = http_request(args, "POST", "/api/chat/completions", completion_payload, allow_error=True)
    except Exception as exc:
        if live_stop is not None:
            live_stop.set()
        if live_thread is not None:
            live_thread.join(timeout=2.0)
        return fallback_to_stateless(args, technical_prompt, "chat-completions", exc)
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

    updated_visible = False
    if live_message_id is not None:
        try:
            updated_visible = update_visible_assistant(args, live_message_id, text, done=True)
        except Exception as exc:
            log_visible_chat_degraded(args, "finalize-live-status", exc)
            updated_visible = False

    if updated_visible:
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return completion_exit_code(completion_status, completion)

    try:
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
    except Exception as exc:
        log_visible_chat_degraded(args, "append-assistant", exc)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return completion_exit_code(completion_status, completion)


if __name__ == "__main__":
    raise SystemExit(main())
