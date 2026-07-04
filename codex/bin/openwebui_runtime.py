#!/usr/bin/env python3
"""Helpers for discovering reachable OpenWebUI URLs."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse


def _compose_webui_url(repo_root: Path) -> str:
    compose = repo_root / "docker-compose.yml"
    if not compose.is_file():
        return ""
    try:
        text = compose.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"(?im)^\s*-\s*WEBUI_URL\s*=\s*(\S+)\s*$", text)
    if not match:
        return ""
    return match.group(1).strip().strip("'\"")


def _base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _replace_port(base_url: str, port: int) -> str:
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{int(port)}"


def discover_openwebui_base_urls(repo_root: Path, env: dict[str, str] | None = None) -> list[str]:
    env = env or os.environ
    candidates = []

    for key in (
        "OPENWEBUI_URL",
        "OPENWEBUI_PUBLIC_URL",
        "WEBUI_URL",
        "OPENWEBUI_HEALTH_URL",
        "OPENWEBUI_LOADER_URL",
    ):
        candidates.append(_base_url(env.get(key, "")))

    compose_url = _compose_webui_url(repo_root)
    if compose_url:
        candidates.append(_base_url(compose_url))

    gateway_public = _base_url(env.get("CODEX_GATEWAY_PUBLIC_URL", ""))
    if gateway_public:
        candidates.append(_replace_port(gateway_public, 9090))

    candidates.extend(
        [
            "http://127.0.0.1:9090",
            "http://localhost:9090",
        ]
    )

    result = []
    seen = set()
    for item in candidates:
        base = _base_url(item)
        if not base or base in seen:
            continue
        seen.add(base)
        result.append(base)
    return result


def discover_gateway_base_urls(repo_root: Path, env: dict[str, str] | None = None) -> list[str]:
    env = env or os.environ
    candidates = []

    for key in (
        "CODEX_GATEWAY_URL",
        "CODEX_GATEWAY_PUBLIC_URL",
        "GATEWAY_URL",
    ):
        candidates.append(_base_url(env.get(key, "")))

    compose_url = _compose_webui_url(repo_root)
    if compose_url:
        candidates.append(_replace_port(_base_url(compose_url), 9101))

    openwebui_base = _base_url(env.get("OPENWEBUI_URL", "") or env.get("WEBUI_URL", ""))
    if openwebui_base:
        candidates.append(_replace_port(openwebui_base, 9101))

    candidates.extend(
        [
            "http://127.0.0.1:9101",
            "http://localhost:9101",
            "http://192.168.0.48:9101",
        ]
    )

    result = []
    seen = set()
    for item in candidates:
        base = _base_url(item)
        if not base or base in seen:
            continue
        seen.add(base)
        result.append(base)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve candidate OpenWebUI base URLs.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    urls = discover_openwebui_base_urls(Path(args.repo_root))
    if args.json:
        print(json.dumps({"urls": urls}, ensure_ascii=False, indent=2))
    else:
        for item in urls:
            print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
