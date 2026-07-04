#!/usr/bin/env python3
"""Offline smoke for gateway_admin.py self-improve CLI payload wiring."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "codex/bin/gateway_admin.py"


def main() -> int:
    proc = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "self-improve",
            "--dry-run",
            "--json",
            "--workspace",
            "ai-stack",
            "--mode",
            "patch",
            "--prompt",
            "repo: ai-stack\nprepare guarded patch apply",
            "--patch-file",
            "/tmp/reviewed.patch",
            "--e2e-prompt",
            "repo: ai-stack\nkde ted jsi?",
            "--capability-name",
            "workspace_profile",
            "--target-capability-name",
            "workspace_profile",
            "--feature-request",
            "Promote bounded workspace profile capability.",
            "--apply",
            "--max-cycles",
            "2",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"gateway_admin self-improve dry-run failed:\n{proc.stdout}")
    payload = json.loads(proc.stdout)
    if payload.get("method") != "POST":
        raise SystemExit(f"expected POST preview, got {payload!r}")
    if not str(payload.get("url") or "").endswith("/v1/admin/agent/self-improve"):
        raise SystemExit(f"expected self-improve endpoint preview, got {payload!r}")
    body = payload.get("payload") or {}
    expected_pairs = {
        "workspace": "ai-stack",
        "mode": "patch",
        "prompt": "repo: ai-stack\nprepare guarded patch apply",
        "patch_file": "/tmp/reviewed.patch",
        "e2e_prompt": "repo: ai-stack\nkde ted jsi?",
        "capability_name": "workspace_profile",
        "target_capability_name": "workspace_profile",
        "feature_request": "Promote bounded workspace profile capability.",
        "max_cycles": 2,
    }
    for key, expected in expected_pairs.items():
        if body.get(key) != expected:
            raise SystemExit(f"expected payload[{key!r}]={expected!r}, got {body.get(key)!r} in {body!r}")
    if body.get("dry_run") is not False:
        raise SystemExit(f"expected --apply to flip payload dry_run=false, got {body!r}")
    print("GATEWAY_ADMIN_SELF_IMPROVE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
