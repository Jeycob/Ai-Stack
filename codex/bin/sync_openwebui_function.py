#!/usr/bin/env python3
"""Sync a versioned OpenWebUI function source file into the running OpenWebUI API.

The script never prints API keys or function secrets. It compares content hashes,
updates only when needed, and verifies the remote content after update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path

RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
DROP_FIELDS = {"user", "created_at", "updated_at"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync a local function source file to OpenWebUI.")
    parser.add_argument("--base-url", default=os.getenv("OWUI_BASE_URL", "http://192.168.0.48:9090"))
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--function-id", default="codex_gateway_admin_filter")
    parser.add_argument("--source", default="codex/bin/openwebui_gateway_admin_filter.py")
    parser.add_argument("--dry-run", action="store_true", help="Only report whether an update is needed.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=4.0)
    return parser.parse_args()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_json(args: argparse.Namespace, method: str, path: str, body: dict | None = None) -> dict:
    token = os.getenv(args.api_key_env)
    if not token:
        raise SystemExit(f"{args.api_key_env} is not set")
    url = f"{args.base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_error: BaseException | None = None
    for attempt in range(1, args.attempts + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code not in RETRY_STATUSES:
                raise RuntimeError(f"HTTP {exc.code} from {path}: {raw[:1000]}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, RemoteDisconnected, BadStatusLine, OSError) as exc:
            last_error = exc
        if attempt < args.attempts:
            delay = min(args.max_delay, args.initial_delay * (2 ** (attempt - 1)))
            time.sleep(delay)
    raise RuntimeError(f"Request failed after retries: {type(last_error).__name__}: {last_error}")


def sanitized_payload(remote: dict, content: str) -> dict:
    payload = {k: v for k, v in remote.items() if k not in DROP_FIELDS}
    payload["content"] = content
    return payload


def main() -> int:
    args = parse_args()
    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"source file not found: {source}")
    content = source.read_text(encoding="utf-8")
    local_hash = sha256(content)

    remote = request_json(args, "GET", f"/api/v1/functions/id/{args.function_id}")
    remote_content = remote.get("content") or ""
    remote_hash = sha256(remote_content)
    changed = local_hash != remote_hash

    print(f"function_id={args.function_id}")
    print(f"source={source}")
    print(f"remote_active={remote.get('is_active')}")
    print(f"remote_global={remote.get('is_global')}")
    print(f"local_sha256={local_hash}")
    print(f"remote_sha256={remote_hash}")
    print(f"changed={str(changed).lower()}")

    if args.dry_run or not changed:
        print("action=dry-run" if args.dry_run else "action=no-op")
        return 0

    updated = request_json(args, "POST", f"/api/v1/functions/id/{args.function_id}/update", sanitized_payload(remote, content))
    updated_hash = sha256(updated.get("content") or "")
    if updated_hash != local_hash:
        raise RuntimeError("update verification failed: remote content hash does not match local source")
    print("action=updated")
    print(f"updated_active={updated.get('is_active')}")
    print(f"updated_global={updated.get('is_global')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
