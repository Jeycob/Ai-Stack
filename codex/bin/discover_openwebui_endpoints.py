#!/usr/bin/env python3
"""Safely inspect selected OpenWebUI API endpoints.

The helper intentionally uses only GET, HEAD, and OPTIONS. It never sends
mutating methods, never prints API keys, and prints bounded metadata by default.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_PATHS = [
    "/api/config",
    "/api/v1/functions/",
    "/api/v1/functions/list",
    "/api/v1/tools/",
    "/api/v1/tools/list",
    "/api/v1/models/",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe OpenWebUI endpoint discovery helper.")
    parser.add_argument("--base-url", default=os.getenv("OWUI_BASE_URL", "http://192.168.0.48:9090"))
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--method", choices=sorted(SAFE_METHODS), default="GET")
    parser.add_argument("--path", action="append", help="Path to probe; repeatable. Defaults to a small safe list.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=3.0)
    parser.add_argument("--sample-chars", type=int, default=0, help="Optional sanitized body sample length. Default 0 prints no body sample.")
    parser.add_argument("--json", action="store_true", help="Emit JSON lines instead of text.")
    return parser.parse_args()


def validate_path(path: str) -> str:
    if not path.startswith("/") or path.startswith("//") or "\x00" in path:
        raise ValueError(f"Unsafe endpoint path: {path!r}")
    if any(part == ".." for part in Path(path).parts):
        raise ValueError(f"Unsafe endpoint path traversal: {path!r}")
    return path


def make_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def retry_delay(args: argparse.Namespace, attempt_index: int) -> float:
    return min(args.max_delay, args.initial_delay * (2 ** attempt_index))


def summarize_body(raw: bytes, sample_chars: int) -> dict:
    text = raw.decode("utf-8", errors="replace")
    summary = {"bytes": len(raw)}
    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        summary.update({"json_type": "list", "items": len(parsed)})
        if parsed and isinstance(parsed[0], dict):
            summary["first_keys"] = sorted(str(k) for k in parsed[0].keys())[:12]
    elif isinstance(parsed, dict):
        summary.update({"json_type": "object", "keys": sorted(str(k) for k in parsed.keys())[:20]})
    elif parsed is None:
        summary["json_type"] = "none" if not text else "non-json"
    else:
        summary["json_type"] = type(parsed).__name__
    if sample_chars > 0:
        sample = text[:sample_chars].replace("\n", " ").replace("\r", " ")
        summary["sample"] = sample
    return summary


def request_once(args: argparse.Namespace, opener: urllib.request.OpenerDirector, path: str) -> dict:
    method = args.method.upper()
    if method not in SAFE_METHODS:
        raise ValueError(f"Unsafe HTTP method: {method}")
    token = os.getenv(args.api_key_env)
    url = f"{args.base_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method=method)
    with opener.open(req, timeout=args.timeout) as resp:
        body = b"" if method == "HEAD" else resp.read()
        return {
            "path": path,
            "method": method,
            "status": resp.status,
            "content_type": resp.headers.get("content-type", ""),
            "body": summarize_body(body, args.sample_chars),
        }


def request_with_retry(args: argparse.Namespace, opener: urllib.request.OpenerDirector, path: str) -> dict:
    last_error = None
    for attempt in range(1, args.attempts + 1):
        try:
            return request_once(args, opener, path)
        except urllib.error.HTTPError as exc:
            body = b"" if args.method.upper() == "HEAD" else exc.read()
            if exc.code not in RETRY_STATUSES:
                return {
                    "path": path,
                    "method": args.method.upper(),
                    "status": exc.code,
                    "content_type": exc.headers.get("content-type", ""),
                    "body": summarize_body(body, args.sample_chars),
                    "error": "HTTPError",
                }
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, RemoteDisconnected, BadStatusLine, OSError) as exc:
            last_error = exc
        if attempt < args.attempts:
            time.sleep(retry_delay(args, attempt - 1))
    return {
        "path": path,
        "method": args.method.upper(),
        "status": None,
        "content_type": "",
        "body": {"bytes": 0},
        "error": f"{type(last_error).__name__}: {last_error}",
    }


def emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    body = result.get("body") or {}
    bits = [
        f"{result['method']} {result['path']}",
        f"status={result.get('status')}",
        f"type={result.get('content_type') or '-'}",
        f"bytes={body.get('bytes', 0)}",
    ]
    if "json_type" in body:
        bits.append(f"json={body['json_type']}")
    if "items" in body:
        bits.append(f"items={body['items']}")
    if "keys" in body:
        bits.append("keys=" + ",".join(body["keys"]))
    if "first_keys" in body:
        bits.append("first_keys=" + ",".join(body["first_keys"]))
    if "error" in result:
        bits.append(f"error={result['error']}")
    if "sample" in body:
        bits.append(f"sample={body['sample']}")
    print(" | ".join(bits))


def main() -> int:
    args = parse_args()
    paths = [validate_path(p) for p in (args.path or DEFAULT_PATHS)]
    opener = make_opener()
    print(f"base_url={args.base_url.rstrip('/')}")
    print(f"method={args.method.upper()}")
    print(f"api_key_env={args.api_key_env} set={str(bool(os.getenv(args.api_key_env))).lower()}")
    print("mutating_methods=disabled")
    for path in paths:
        emit(request_with_retry(args, opener, path), args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
