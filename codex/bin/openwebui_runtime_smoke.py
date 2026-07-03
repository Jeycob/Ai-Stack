#!/usr/bin/env python3
"""Offline smoke for OpenWebUI runtime URL discovery."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.bin.openwebui_runtime import discover_openwebui_base_urls


def assert_compose_webui_url_is_preferred() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "docker-compose.yml").write_text(
            "services:\n  open-webui:\n    environment:\n      - WEBUI_URL=http://192.168.0.48:9090\n",
            encoding="utf-8",
        )
        urls = discover_openwebui_base_urls(root, env={})
    if not urls or urls[0] != "http://192.168.0.48:9090":
        raise SystemExit(f"expected compose WEBUI_URL to lead candidate list, got {urls!r}")
    print("OPENWEBUI_RUNTIME_COMPOSE_URL_OK")


def assert_gateway_public_url_derives_webui_port() -> None:
    urls = discover_openwebui_base_urls(ROOT, env={"CODEX_GATEWAY_PUBLIC_URL": "http://192.168.0.48:9101"})
    if "http://192.168.0.48:9090" not in urls:
        raise SystemExit(f"expected CODEX_GATEWAY_PUBLIC_URL to derive 9090 candidate, got {urls!r}")
    print("OPENWEBUI_RUNTIME_GATEWAY_DERIVATION_OK")


def main() -> int:
    assert_compose_webui_url_is_preferred()
    assert_gateway_public_url_derives_webui_port()
    print("OPENWEBUI_RUNTIME_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
