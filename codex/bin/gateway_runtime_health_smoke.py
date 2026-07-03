#!/usr/bin/env python3
"""Offline smoke for gateway runtime health readiness payload."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.gateway import gateway


def assert_ready_payload() -> None:
    root_probe = {"ok": True, "url": "http://127.0.0.1:9090/", "status": 200}
    loader_probe = {"ok": True, "url": "http://127.0.0.1:9090/static/loader.js", "status": 200}

    with patch.object(gateway, "probe_openwebui_runtime", return_value={
        "ok": True,
        "base_url": "http://127.0.0.1:9090",
        "root": root_probe,
        "loader": loader_probe,
        "tried": [{"base_url": "http://127.0.0.1:9090", "root_ok": True, "loader_ok": True}],
    }), patch.object(
        gateway, "run_ro", return_value="abc1234"
    ), patch.object(gateway, "WORKSPACES_FILE", str(ROOT / "codex/workspaces.json")), patch.object(
        gateway, "CAPABILITY_ROADMAP_FILE", ROOT / "docs/codex-local-capability-roadmap.json"
    ), patch.object(
        gateway, "REPO_ROOT", ROOT
    ), patch.object(gateway, "ADMIN_TOKEN", "token-present"), patch.object(gateway.Path, "is_file", return_value=True):
        payload = gateway.runtime_health()

    if payload.get("codex_local_ready") is not True:
        raise SystemExit(f"expected codex_local_ready=true, got {payload!r}")
    if payload.get("capability_mode") != "agent-first":
        raise SystemExit(f"expected capability_mode=agent-first, got {payload!r}")
    if payload.get("natural_codex_local_route") != "agent_loop":
        raise SystemExit(f"expected natural route agent_loop, got {payload!r}")
    if str(payload.get("runtime_repo_root") or "").rstrip("/") != str(ROOT).rstrip("/"):
        raise SystemExit(f"expected runtime_repo_root={ROOT}, got {payload!r}")
    if payload.get("runtime_commit") != "abc1234":
        raise SystemExit(f"expected runtime_commit=abc1234, got {payload!r}")
    if not payload.get("runtime_fingerprint"):
        raise SystemExit(f"expected runtime_fingerprint in ready payload, got {payload!r}")
    if payload.get("readiness_issues") != []:
        raise SystemExit(f"expected no readiness issues, got {payload!r}")
    if (payload.get("openwebui") or {}).get("base_url") != "http://127.0.0.1:9090":
        raise SystemExit(f"expected base_url in health payload, got {payload!r}")
    admin = payload.get("gateway_admin") or {}
    if admin.get("lan_admin_ready") is not True or admin.get("token_present") is not True:
        raise SystemExit(f"expected admin token readiness, got {payload!r}")
    print("GATEWAY_RUNTIME_HEALTH_READY_OK")


def assert_not_ready_payload() -> None:
    root_probe = {"ok": False, "url": "http://127.0.0.1:9090/", "error": "ConnectionRefused"}
    loader_probe = {"ok": False, "url": "http://127.0.0.1:9090/static/loader.js", "error": "ConnectionRefused"}

    path_exists = {
        str(ROOT / "codex/workspaces.json"): False,
        str(ROOT / "docs/codex-local-capability-roadmap.json"): False,
    }

    def fake_is_file(path_self):
        return path_exists.get(str(path_self), False)

    with patch.object(gateway, "probe_openwebui_runtime", return_value={
        "ok": False,
        "base_url": "",
        "root": root_probe,
        "loader": loader_probe,
        "tried": [{"base_url": "http://127.0.0.1:9090", "root_ok": False, "loader_ok": False}],
    }), patch.object(
        gateway, "run_ro", return_value="[FileNotFoundError: git]"
    ), patch.object(gateway, "WORKSPACES_FILE", str(ROOT / "codex/workspaces.json")), patch.object(
        gateway, "CAPABILITY_ROADMAP_FILE", ROOT / "docs/codex-local-capability-roadmap.json"
    ), patch.object(
        gateway, "REPO_ROOT", ROOT
    ), patch.object(gateway, "ADMIN_TOKEN", ""), patch.object(gateway.Path, "is_file", fake_is_file):
        payload = gateway.runtime_health()

    if payload.get("codex_local_ready") is not False:
        raise SystemExit(f"expected codex_local_ready=false, got {payload!r}")
    issues = set(payload.get("readiness_issues") or [])
    expected = {
        "WORKSPACES_FILE_MISSING",
        "CAPABILITY_ROADMAP_MISSING",
        "OPENWEBUI_ROOT_UNAVAILABLE",
        "OPENWEBUI_LOADER_UNAVAILABLE",
        "GATEWAY_ADMIN_TOKEN_MISSING",
    }
    if issues != expected:
        raise SystemExit(f"expected readiness issues {sorted(expected)!r}, got {sorted(issues)!r}")
    admin = payload.get("gateway_admin") or {}
    if admin.get("lan_admin_ready") is not False or admin.get("token_present") is not False:
        raise SystemExit(f"expected admin token missing readiness, got {payload!r}")
    if not payload.get("runtime_fingerprint"):
        raise SystemExit(f"expected runtime_fingerprint even in degraded payload, got {payload!r}")
    print("GATEWAY_RUNTIME_HEALTH_NOT_READY_OK")


def assert_runtime_uses_public_fallback() -> None:
    probes = {
        "http://192.168.0.48:9090/": {"ok": True, "url": "http://192.168.0.48:9090/", "status": 200},
        "http://192.168.0.48:9090/static/loader.js": {"ok": True, "url": "http://192.168.0.48:9090/static/loader.js", "status": 200},
        "http://127.0.0.1:9090/": {"ok": False, "url": "http://127.0.0.1:9090/", "error": "Timeout"},
        "http://127.0.0.1:9090/static/loader.js": {"ok": False, "url": "http://127.0.0.1:9090/static/loader.js", "error": "Timeout"},
    }

    def fake_probe(url, timeout=2, max_bytes=8192):
        del timeout, max_bytes
        return probes[url]

    with patch.object(gateway, "http_probe", side_effect=fake_probe), patch.object(
        gateway, "openwebui_probe_candidates", return_value=["http://127.0.0.1:9090", "http://192.168.0.48:9090"]
    ):
        payload = gateway.probe_openwebui_runtime()

    if payload.get("ok") is not True or payload.get("base_url") != "http://192.168.0.48:9090":
        raise SystemExit(f"expected runtime probe to use public fallback, got {payload!r}")
    tried = payload.get("tried") or []
    if len(tried) != 2 or tried[-1].get("loader_ok") is not True:
        raise SystemExit(f"expected tried history with successful fallback candidate, got {payload!r}")
    print("GATEWAY_RUNTIME_HEALTH_PUBLIC_FALLBACK_OK")


def main() -> int:
    assert_ready_payload()
    assert_not_ready_payload()
    assert_runtime_uses_public_fallback()
    print("GATEWAY_RUNTIME_HEALTH_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
