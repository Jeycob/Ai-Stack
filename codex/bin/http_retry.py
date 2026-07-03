#!/usr/bin/env python3
"""Small HTTP helper with retries/backoff for flaky local services.

Uses only the Python standard library and disables proxy lookup by default.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path


RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HTTP request with retry/backoff.")
    parser.add_argument("method", help="HTTP method, e.g. GET or POST")
    parser.add_argument("url", help="Full URL")
    parser.add_argument("--header", action="append", default=[], help="Header line, e.g. 'Content-Type: application/json'")
    parser.add_argument("--header-file", help="File containing one header per line")
    parser.add_argument("--bearer-env", help="Read bearer token from this environment variable")
    parser.add_argument("--bearer-file", help="Read bearer token from this file if --bearer-env is unset")
    parser.add_argument("--data-file", help="Request body file")
    parser.add_argument("--json", dest="json_text", help="Inline JSON request body")
    parser.add_argument("--json-file", help="JSON request body file")
    parser.add_argument("--out", help="Write final response body to this file")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-attempt socket timeout")
    parser.add_argument("--attempts", type=int, default=10, help="Maximum attempts")
    parser.add_argument("--initial-delay", type=float, default=0.5, help="Initial retry delay in seconds")
    parser.add_argument("--max-delay", type=float, default=5.0, help="Maximum retry delay in seconds")
    parser.add_argument("--total-timeout", type=float, default=120.0, help="Maximum wall-clock time")
    parser.add_argument("--retry-status", action="append", type=int, default=[], help="Extra HTTP status to retry")
    parser.add_argument("--pretty-json", action="store_true", help="Pretty-print JSON response to stdout")
    parser.add_argument("--quiet", action="store_true", help="Suppress retry logs")
    parser.add_argument("--use-proxy", action="store_true", help="Honor proxy environment variables")
    return parser.parse_args()


def read_headers(args: argparse.Namespace) -> dict[str, str]:
    lines: list[str] = []
    lines.extend(args.header)
    if args.header_file:
        lines.extend(Path(args.header_file).read_text(encoding="utf-8").splitlines())

    headers: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise SystemExit(f"Invalid header line: {line!r}")
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()

    if args.bearer_env or args.bearer_file:
        token = os.environ.get(args.bearer_env or "") if args.bearer_env else ""
        if not token and args.bearer_file:
            path = Path(args.bearer_file)
            if path.is_file():
                token = path.read_text(encoding="utf-8").strip()
        if not token:
            sources = [s for s in [args.bearer_env, args.bearer_file] if s]
            raise SystemExit("Bearer token is not set; checked " + ", ".join(sources))
        headers["Authorization"] = f"Bearer {token}"
    return headers


def read_body(args: argparse.Namespace, headers: dict[str, str]) -> bytes | None:
    sources = [bool(args.data_file), bool(args.json_text), bool(args.json_file)]
    if sum(sources) > 1:
        raise SystemExit("Use only one of --data-file, --json, or --json-file")
    if args.data_file:
        return Path(args.data_file).read_bytes()
    if args.json_file:
        headers.setdefault("Content-Type", "application/json")
        return Path(args.json_file).read_bytes()
    if args.json_text:
        headers.setdefault("Content-Type", "application/json")
        return args.json_text.encode("utf-8")
    return None


def make_opener(use_proxy: bool) -> urllib.request.OpenerDirector:
    if use_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def retry_delay(base: float, max_delay: float, attempt_index: int) -> float:
    delay = min(max_delay, base * (2 ** attempt_index))
    jitter = random.uniform(0, delay * 0.2)
    return delay + jitter


def log(args: argparse.Namespace, message: str) -> None:
    if not args.quiet:
        print(message, file=sys.stderr)


def request_once(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def write_or_print(args: argparse.Namespace, body: bytes) -> None:
    if args.out:
        Path(args.out).write_bytes(body)
        return
    text = body.decode("utf-8", errors="replace")
    if args.pretty_json:
        try:
            print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
            return
        except json.JSONDecodeError:
            pass
    print(text, end="" if text.endswith("\n") else "\n")


def main() -> int:
    args = parse_args()
    headers = read_headers(args)
    body = read_body(args, headers)
    opener = make_opener(args.use_proxy)
    retry_statuses = RETRY_STATUSES | set(args.retry_status)
    deadline = time.monotonic() + args.total_timeout

    last_status = None
    last_body = b""
    last_error = None

    for attempt in range(1, max(1, args.attempts) + 1):
        if time.monotonic() >= deadline:
            break
        try:
            status, resp_body, _ = request_once(opener, args.method, args.url, headers, body, args.timeout)
            last_status, last_body, last_error = status, resp_body, None
            if status < 400 or status not in retry_statuses:
                write_or_print(args, resp_body)
                return 0 if status < 400 else 22
            log(args, f"attempt {attempt}: HTTP {status}, retrying")
        except (urllib.error.URLError, TimeoutError, ConnectionError, RemoteDisconnected, BadStatusLine, OSError) as exc:
            last_error = exc
            log(args, f"attempt {attempt}: {type(exc).__name__}: {exc}")

        if attempt >= args.attempts:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(remaining, retry_delay(args.initial_delay, args.max_delay, attempt - 1)))

    if last_error is not None:
        print(f"FAILED after retries: {type(last_error).__name__}: {last_error}", file=sys.stderr)
        return 7
    write_or_print(args, last_body)
    return 0 if last_status and last_status < 400 else 22


if __name__ == "__main__":
    raise SystemExit(main())
