#!/usr/bin/env python3
"""Offline smoke for gateway runtime fingerprint URL discovery and drift logic."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.bin import gateway_runtime_fingerprint_check as check


def assert_request_json_disables_proxy() -> None:
    text = (ROOT / "codex/bin/gateway_runtime_fingerprint_check.py").read_text(encoding="utf-8")
    if "ProxyHandler({})" not in text or "opener.open(req, timeout=timeout)" not in text:
        raise SystemExit("GATEWAY_RUNTIME_FINGERPRINT_PROXY_GUARD_FAILED")
    print("GATEWAY_RUNTIME_FINGERPRINT_PROXY_GUARD_OK")


def run_main(args: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    old_argv = sys.argv
    try:
        sys.argv = ["gateway_runtime_fingerprint_check.py", *args]
        with contextlib.redirect_stdout(stdout):
            try:
                rc = check.main()
            except SystemExit as exc:
                rc = int(exc.code or 0)
    finally:
        sys.argv = old_argv
    return rc, stdout.getvalue()


def assert_candidate_fallback() -> None:
    calls: list[str] = []

    def fake_request_json(url: str, timeout: float) -> dict:
        del timeout
        calls.append(url)
        if url == "http://127.0.0.1:9101/health":
            raise OSError("Connection refused")
        if url == "http://192.168.0.48:9101/health":
            return {
                "ok": True,
                "capability_mode": "agent-first",
                "natural_codex_local_route": "agent_loop",
                "runtime_fingerprint": "fp-123",
                "gateway_source_epoch": "epoch-123",
                "runtime_repo_root": str(ROOT),
                "runtime_commit": "abc1234",
            }
        raise AssertionError(f"unexpected url {url}")

    with patch.object(check, "request_json", side_effect=fake_request_json), patch.object(
        check, "local_repo_root", return_value=str(ROOT)
    ), patch.object(
        check, "local_repo_commit_short", return_value="abc1234"
    ), patch.object(
        check.gateway, "runtime_fingerprint", return_value="fp-123"
    ), patch.object(
        check.gateway, "GATEWAY_SOURCE_EPOCH", "epoch-123"
    ), patch.object(
        check, "candidate_base_urls", return_value=["http://127.0.0.1:9101", "http://192.168.0.48:9101"]
    ):
        rc, out = run_main(["--json"])
    if rc != 0:
        raise SystemExit(f"expected successful fallback runtime check, got rc={rc} out={out}")
    payload = json.loads(out)
    if payload.get("ok") is not True or payload.get("url") != "http://192.168.0.48:9101/health":
        raise SystemExit(f"expected LAN fallback health URL, got {payload!r}")
    tried = payload.get("tried") or []
    if len(tried) != 2 or tried[0].get("ok") is not False or tried[1].get("ok") is not True:
        raise SystemExit(f"expected tried history with failed localhost then successful LAN, got {payload!r}")
    if calls != ["http://127.0.0.1:9101/health", "http://192.168.0.48:9101/health"]:
        raise SystemExit(f"unexpected request order {calls!r}")
    print("GATEWAY_RUNTIME_FINGERPRINT_CHECK_FALLBACK_OK")


def assert_unavailable_reports_tried() -> None:
    def fake_request_json(url: str, timeout: float) -> dict:
        del timeout
        raise OSError(f"blocked {url}")

    with patch.object(check, "request_json", side_effect=fake_request_json), patch.object(
        check.gateway, "runtime_fingerprint", return_value="fp-123"
    ), patch.object(
        check.gateway, "GATEWAY_SOURCE_EPOCH", "epoch-123"
    ), patch.object(
        check, "candidate_base_urls", return_value=["http://127.0.0.1:9101", "http://192.168.0.48:9101"]
    ):
        rc, out = run_main(["--json"])
    if rc == 0:
        raise SystemExit(f"expected unavailable runtime check to fail, got out={out}")
    payload = json.loads(out)
    if payload.get("marker") != "CODEX_LOCAL_GATEWAY_UNAVAILABLE":
        raise SystemExit(f"expected unavailable marker, got {payload!r}")
    tried = payload.get("tried") or []
    if len(tried) != 2:
        raise SystemExit(f"expected tried list in unavailable payload, got {payload!r}")
    print("GATEWAY_RUNTIME_FINGERPRINT_CHECK_UNAVAILABLE_OK")


def assert_candidate_discovery_uses_env() -> None:
    with patch.dict(
        os.environ,
        {
            "CODEX_GATEWAY_PUBLIC_URL": "http://192.168.0.48:9101",
            "CODEX_GATEWAY_URL": "http://127.0.0.1:9101",
        },
        clear=False,
    ):
        urls = check.candidate_base_urls("http://localhost:9101")
    if urls[:3] != ["http://localhost:9101", "http://127.0.0.1:9101", "http://192.168.0.48:9101"]:
        raise SystemExit(f"expected explicit/env gateway candidate priority, got {urls!r}")
    print("GATEWAY_RUNTIME_FINGERPRINT_CHECK_DISCOVERY_OK")


def main() -> int:
    assert_request_json_disables_proxy()
    assert_candidate_fallback()
    assert_unavailable_reports_tried()
    assert_candidate_discovery_uses_env()
    print("GATEWAY_RUNTIME_FINGERPRINT_CHECK_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
