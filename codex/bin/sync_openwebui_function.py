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
import re
import textwrap
import sys
import time
import urllib.error
import urllib.request
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path

RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
PRIMARY_MUTABLE_FIELDS = ("id", "name", "type", "meta")
ROADMAP_SENTINEL = "EMBEDDED_CAPABILITY_ROADMAP = None"
MANIFEST_FIELD_RE = re.compile(r"(?im)^(title|author|version|description):\s*(.+?)\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync a local function source file to OpenWebUI.")
    parser.add_argument("--base-url", default=os.getenv("OWUI_BASE_URL", "http://192.168.0.48:9090"))
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--api-key-file", default=os.getenv("OWUI_API_KEY_FILE", "codex/state/openwebui-api.key"))
    parser.add_argument("--function-id", default="codex_gateway_admin_filter")
    parser.add_argument("--source", default="codex/bin/openwebui_gateway_admin_filter.py")
    parser.add_argument("--no-activate", action="store_true", help="Do not force the OpenWebUI function active.")
    parser.add_argument("--no-global", action="store_true", help="Do not force the OpenWebUI function global.")
    parser.add_argument("--dry-run", action="store_true", help="Only report whether an update is needed.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=4.0)
    return parser.parse_args()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def request_json(args: argparse.Namespace, method: str, path: str, body: dict | None = None) -> dict:
    token = openwebui_api_key(args)
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


def target_active(args: argparse.Namespace) -> bool:
    return not args.no_activate


def target_global(args: argparse.Namespace) -> bool:
    return not args.no_global


def runtime_content(args: argparse.Namespace, content: str) -> tuple[str, bool]:
    source = Path(args.source)
    roadmap_path = source.parents[2] / "docs" / "codex-local-capability-roadmap.json"
    if ROADMAP_SENTINEL not in content or not roadmap_path.is_file():
        return content, False
    payload = json.loads(roadmap_path.read_text(encoding="utf-8"))
    replacement = "EMBEDDED_CAPABILITY_ROADMAP = " + json.dumps(payload, ensure_ascii=False, indent=2)
    return content.replace(ROADMAP_SENTINEL, replacement, 1), True


def parse_manifest(content: str) -> dict[str, str]:
    manifest: dict[str, str] = {}
    head = textwrap.dedent(str(content or "")).split('"""', 2)
    if len(head) >= 3:
        header = head[1]
    else:
        header = str(content or "").split("\n\n", 1)[0]
    for key, value in MANIFEST_FIELD_RE.findall(header):
        manifest[key.lower()] = value.strip()
    return manifest


def desired_meta(content: str, remote: dict | None = None) -> dict:
    manifest = parse_manifest(content)
    remote_meta = remote.get("meta") if isinstance(remote, dict) else None
    merged: dict = {}
    if isinstance(remote_meta, dict):
        merged.update(remote_meta)
    if "description" not in merged and manifest.get("description"):
        merged["description"] = manifest["description"]
    if manifest:
        existing_manifest = merged.get("manifest")
        if not isinstance(existing_manifest, dict):
            existing_manifest = {}
        manifest_payload = dict(existing_manifest)
        manifest_payload.update(manifest)
        merged["manifest"] = manifest_payload
    if "description" not in merged:
        merged["description"] = "Managed by ai-stack codex-local function sync."
    return merged


def update_payload_variant(
    args: argparse.Namespace,
    remote: dict,
    content: str,
    *,
    include_meta: bool,
    include_flags: bool,
    include_user_id: bool,
) -> dict:
    payload = {k: remote[k] for k in PRIMARY_MUTABLE_FIELDS if k in remote}
    if not include_meta:
        payload.pop("meta", None)
    else:
        payload["meta"] = desired_meta(content, remote)
    payload["content"] = content
    if include_flags:
        payload["is_active"] = target_active(args)
        payload["is_global"] = target_global(args)
    if include_user_id and "user_id" in remote:
        payload["user_id"] = remote["user_id"]
    return payload


def update_function_with_fallbacks(args: argparse.Namespace, function_id: str, remote: dict, content: str) -> tuple[dict, str]:
    full_payload = {k: v for k, v in remote.items() if k not in {"created_at", "updated_at"}}
    full_payload["content"] = content
    full_payload["meta"] = desired_meta(content, remote)
    full_payload["is_active"] = target_active(args)
    full_payload["is_global"] = target_global(args)
    attempts = [
        ("full-minus-timestamps", full_payload),
        (
            "id+name+type+meta+content+flags+user",
            update_payload_variant(
                args,
                remote,
                content,
                include_meta=True,
                include_flags=True,
                include_user_id=True,
            ),
        ),
        (
            "id+name+type+meta+content+user",
            update_payload_variant(
                args,
                remote,
                content,
                include_meta=True,
                include_flags=False,
                include_user_id=True,
            ),
        ),
        (
            "id+name+type+meta+content+flags",
            update_payload_variant(
                args,
                remote,
                content,
                include_meta=True,
                include_flags=True,
                include_user_id=False,
            ),
        ),
        (
            "id+name+type+meta+content",
            update_payload_variant(
                args,
                remote,
                content,
                include_meta=True,
                include_flags=False,
                include_user_id=False,
            ),
        ),
        (
            "id+name+type+content+flags",
            update_payload_variant(
                args,
                remote,
                content,
                include_meta=False,
                include_flags=True,
                include_user_id=False,
            ),
        ),
        (
            "id+name+type+content",
            update_payload_variant(
                args,
                remote,
                content,
                include_meta=False,
                include_flags=False,
                include_user_id=False,
            ),
        ),
    ]
    errors: list[str] = []
    for strategy, payload in attempts:
        try:
            updated = request_json(args, "POST", f"/api/v1/functions/id/{function_id}/update", payload)
            return updated, strategy
        except RuntimeError as exc:
            errors.append(f"{strategy}: {exc}")
    raise RuntimeError(
        "update failed for every payload strategy:\n" + "\n".join(errors)
    )


def ensure_function_flags(args: argparse.Namespace, function_id: str, remote: dict) -> tuple[dict, list[str]]:
    """Force active/global flags with OpenWebUI toggle endpoints.

    OpenWebUI accepts is_active/is_global in create/update payloads on some
    versions, but the runtime router exposes canonical toggle endpoints. Use
    GET-before-toggle semantics so this stays idempotent and does not flap.
    """
    actions: list[str] = []
    current = remote
    if bool(current.get("is_active")) != target_active(args):
        current = request_json(args, "POST", f"/api/v1/functions/id/{function_id}/toggle")
        actions.append("toggle-active")
    if bool(current.get("is_global")) != target_global(args):
        current = request_json(args, "POST", f"/api/v1/functions/id/{function_id}/toggle/global")
        actions.append("toggle-global")
    if actions:
        current = request_json(args, "GET", f"/api/v1/functions/id/{function_id}")
    return current, actions


def main() -> int:
    args = parse_args()
    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"source file not found: {source}")
    content = source.read_text(encoding="utf-8")
    content, embedded_roadmap = runtime_content(args, content)
    local_hash = sha256(content)

    remote = request_json(args, "GET", f"/api/v1/functions/id/{args.function_id}")
    remote_content = remote.get("content") or ""
    remote_hash = sha256(remote_content)
    active_changed = bool(remote.get("is_active")) != target_active(args)
    global_changed = bool(remote.get("is_global")) != target_global(args)
    changed = local_hash != remote_hash or active_changed or global_changed

    print(f"function_id={args.function_id}")
    print(f"source={source}")
    print(f"remote_active={remote.get('is_active')}")
    print(f"remote_global={remote.get('is_global')}")
    print(f"target_active={target_active(args)}")
    print(f"target_global={target_global(args)}")
    print(f"embedded_roadmap={str(embedded_roadmap).lower()}")
    print(f"local_sha256={local_hash}")
    print(f"remote_sha256={remote_hash}")
    print(f"active_changed={str(active_changed).lower()}")
    print(f"global_changed={str(global_changed).lower()}")
    print(f"changed={str(changed).lower()}")

    if args.dry_run or not changed:
        print("action=dry-run" if args.dry_run else "action=no-op")
        return 0

    updated, strategy = update_function_with_fallbacks(args, args.function_id, remote, content)
    updated, flag_actions = ensure_function_flags(args, args.function_id, updated)
    updated_hash = sha256(updated.get("content") or "")
    if updated_hash != local_hash:
        raise RuntimeError("update verification failed: remote content hash does not match local source")
    if bool(updated.get("is_active")) != target_active(args):
        raise RuntimeError("update verification failed: active state does not match target")
    if bool(updated.get("is_global")) != target_global(args):
        raise RuntimeError("update verification failed: global state does not match target")
    print("action=updated")
    print(f"update_strategy={strategy}")
    if flag_actions:
        print(f"flag_actions={','.join(flag_actions)}")
    print(f"updated_active={updated.get('is_active')}")
    print(f"updated_global={updated.get('is_global')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
