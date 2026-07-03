#!/usr/bin/env python3
"""Small CLI for Codex gateway admin operations.

This is the non-UI fallback path for deployment/status checks. It uses only the
standard library, never prints bearer tokens, and can run without a token only
against localhost because the gateway itself permits localhost admin calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://192.168.0.48:9101"
DEFAULT_TOKEN_FILE = ROOT / "codex/state/codex-gateway-admin.token"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class GatewayAdminError(RuntimeError):
    pass


def is_local_url(base_url: str) -> bool:
    parsed = urllib.parse.urlparse(base_url)
    return parsed.hostname in LOCAL_HOSTS


def read_token(args: argparse.Namespace) -> str:
    token = os.getenv(args.token_env, "").strip() if args.token_env else ""
    if token:
        return token

    token_file = Path(args.token_file)
    if token_file.is_file():
        return token_file.read_text(encoding="utf-8").strip()
    return ""


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    token: str,
    timeout: float,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise GatewayAdminError(f"HTTP {exc.code} from {url}: {raw[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise GatewayAdminError(f"Connection failed for {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GatewayAdminError(f"Bad JSON from {url}: {exc}") from exc


def request_json_retry(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    token: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, args.attempts + 1):
        try:
            return request_json(method, url, payload, token, args.timeout)
        except GatewayAdminError as exc:
            last_error = exc
            message = str(exc)
            retryable = any(f"HTTP {status}" in message for status in RETRY_STATUSES) or "Connection failed" in message
            if attempt >= args.attempts or not retryable:
                break
            if not args.quiet:
                print(f"attempt {attempt}: {message}", file=sys.stderr)
            time.sleep(min(args.max_delay, args.initial_delay * (2 ** (attempt - 1))))
    raise GatewayAdminError(str(last_error) if last_error else "request failed")


def admin_request(args: argparse.Namespace, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    token = read_token(args)
    if path.startswith("/v1/admin/") and not token and not is_local_url(base_url):
        raise GatewayAdminError(
            "CODEX_GATEWAY_ADMIN_TOKEN_MISSING\n"
            f"base_url={base_url}\n"
            f"checked_env={args.token_env}\n"
            f"checked_file={args.token_file}\n"
            "Use localhost on the runtime host or provide a gateway admin token."
        )
    return request_json_retry("POST", base_url + path, payload or {}, token, args)


def public_get(args: argparse.Namespace, path: str) -> dict[str, Any]:
    return request_json_retry("GET", args.base_url.rstrip("/") + path, None, "", args)


def add_common_options(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    default = None if defaults else argparse.SUPPRESS
    parser.add_argument("--base-url", default=os.getenv("CODEX_GATEWAY_URL", DEFAULT_BASE_URL) if defaults else default)
    parser.add_argument("--token-env", default="CODEX_GATEWAY_ADMIN_TOKEN" if defaults else default)
    parser.add_argument("--token-file", default=os.getenv("CODEX_GATEWAY_ADMIN_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)) if defaults else default)
    parser.add_argument("--timeout", type=float, default=20.0 if defaults else default)
    parser.add_argument("--attempts", type=int, default=6 if defaults else default)
    parser.add_argument("--initial-delay", type=float, default=0.5 if defaults else default)
    parser.add_argument("--max-delay", type=float, default=5.0 if defaults else default)
    parser.add_argument("--json", action="store_true", default=False if defaults else default, help="Print raw JSON response.")
    parser.add_argument("--dry-run", action="store_true", default=False if defaults else default, help="Print the request that would be sent.")
    parser.add_argument("--quiet", action="store_true", default=False if defaults else default)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Call Codex gateway admin endpoints.")
    add_common_options(parser, defaults=True)

    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("health", "models", "workspaces", "deploy-status"):
        sub_parser = sub.add_parser(name)
        add_common_options(sub_parser, defaults=False)

    deploy = sub.add_parser("deploy")
    add_common_options(deploy, defaults=False)
    deploy.add_argument("--branch", default="main")
    deploy.add_argument("--force", action="store_true")

    web_fetch = sub.add_parser("web-fetch")
    add_common_options(web_fetch, defaults=False)
    web_fetch.add_argument("url")
    web_fetch.add_argument("--max-bytes", type=int, default=300_000)
    web_fetch.add_argument("--timeout-seconds", type=int, default=20)

    web_answer = sub.add_parser("web-answer")
    add_common_options(web_answer, defaults=False)
    web_answer.add_argument("url")
    web_answer.add_argument("question", nargs="+")
    web_answer.add_argument("--max-bytes", type=int, default=300_000)
    web_answer.add_argument("--timeout-seconds", type=int, default=20)

    self_improve = sub.add_parser("self-improve")
    add_common_options(self_improve, defaults=False)
    self_improve.add_argument("--workspace", default="ai-stack")
    self_improve.add_argument("--chat-id", default="")
    self_improve.add_argument("--chat-url", default="")
    self_improve.add_argument("--failure-marker", default="")
    self_improve.add_argument("--expected-behavior", default="")
    self_improve.add_argument("--mode", default="diagnose")
    self_improve.add_argument("--apply", action="store_true", help="Run with dry_run=false.")
    self_improve.add_argument("--max-cycles", type=int, default=1)
    return parser.parse_args()


def request_preview(args: argparse.Namespace, path: str, payload: dict[str, Any] | None, method: str) -> dict[str, Any]:
    token = read_token(args)
    return {
        "method": method,
        "url": args.base_url.rstrip("/") + path,
        "payload": payload,
        "auth": "bearer-present" if token else ("localhost-no-token" if is_local_url(args.base_url) else "missing-token"),
    }


def print_payload(payload: dict[str, Any], raw_json: bool) -> None:
    if raw_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}={json.dumps(value, ensure_ascii=False)}")
        else:
            print(f"{key}={value}")


def main() -> int:
    args = parse_args()
    try:
        if args.command == "health":
            path, method, payload = "/health", "GET", None
            result = request_preview(args, path, payload, method) if args.dry_run else public_get(args, path)
        elif args.command == "models":
            path, method, payload = "/v1/models", "GET", None
            result = request_preview(args, path, payload, method) if args.dry_run else public_get(args, path)
        elif args.command == "workspaces":
            path, method, payload = "/v1/workspaces", "GET", None
            result = request_preview(args, path, payload, method) if args.dry_run else public_get(args, path)
        elif args.command == "deploy":
            path = "/v1/admin/stack/deploy"
            payload = {"branch": args.branch, "force": args.force}
            result = request_preview(args, path, payload, "POST") if args.dry_run else admin_request(args, path, payload)
        elif args.command == "deploy-status":
            path = "/v1/admin/stack/deploy/status"
            payload = {}
            result = request_preview(args, path, payload, "POST") if args.dry_run else admin_request(args, path, payload)
        elif args.command == "web-fetch":
            path = "/v1/admin/web/fetch"
            payload = {
                "url": args.url,
                "max_bytes": args.max_bytes,
                "timeout": args.timeout_seconds,
            }
            result = request_preview(args, path, payload, "POST") if args.dry_run else admin_request(args, path, payload)
        elif args.command == "web-answer":
            path = "/v1/admin/web/answer"
            payload = {
                "url": args.url,
                "question": " ".join(args.question),
                "max_bytes": args.max_bytes,
                "timeout": args.timeout_seconds,
            }
            result = request_preview(args, path, payload, "POST") if args.dry_run else admin_request(args, path, payload)
        elif args.command == "self-improve":
            path = "/v1/admin/agent/self-improve"
            payload = {
                "workspace": args.workspace,
                "chat_id": args.chat_id,
                "chat_url": args.chat_url,
                "failure_marker": args.failure_marker,
                "expected_behavior": args.expected_behavior,
                "mode": args.mode,
                "dry_run": not args.apply,
                "max_cycles": args.max_cycles,
            }
            result = request_preview(args, path, payload, "POST") if args.dry_run else admin_request(args, path, payload)
        else:
            raise GatewayAdminError(f"Unknown command: {args.command}")
    except GatewayAdminError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print_payload(result, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
