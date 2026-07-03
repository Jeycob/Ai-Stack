#!/usr/bin/env python3
"""Offline regression smoke for background workspace-run env forwarding.

This guards the nested OpenWebUI -> gateway -> run_check flow. Background
workspace jobs must inherit OWUI_STATELESS (and other forwarded env vars),
otherwise child helpers can fall back to visible chat mutation and recurse into
the same OpenWebUI chat request.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_PATH = ROOT / "codex/gateway/gateway.py"


def load_gateway_module():
    spec = importlib.util.spec_from_file_location("codex_gateway_test", GATEWAY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load gateway module from {GATEWAY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    gateway = load_gateway_module()

    captured: dict[str, object] = {}

    class FakePopen:
        def __init__(self, cmd, cwd=None, stdout=None, stderr=None, env=None, start_new_session=False):
            captured["cmd"] = list(cmd)
            captured["cwd"] = cwd
            captured["env"] = dict(env or {})
            captured["start_new_session"] = start_new_session
            self.pid = 424242

    with tempfile.TemporaryDirectory(prefix="gateway-bg-smoke-") as tmp:
        repo_root = Path(tmp)
        (repo_root / "codex/bin").mkdir(parents=True, exist_ok=True)
        (repo_root / "codex/audit").mkdir(parents=True, exist_ok=True)
        (repo_root / "codex/bin/run_check.py").write_text("# smoke placeholder\n", encoding="utf-8")

        original_repo_root = gateway.REPO_ROOT
        original_popen = gateway.subprocess.Popen
        original_writer = gateway.workspace_run_write
        original_workspace_root = gateway.workspace_root
        try:
            gateway.REPO_ROOT = repo_root
            gateway.subprocess.Popen = FakePopen
            gateway.workspace_run_write = lambda *args, **kwargs: None
            gateway.workspace_root = lambda workspace: repo_root

            result = gateway.admin_run_workspace(
                {
                    "workspace": "ai-stack",
                    "runner": "host",
                    "timeout": 30,
                    "background": True,
                    "env": {
                        "OWUI_STATELESS": "1",
                        "EXTRA_TEST_FLAG": "smoke",
                    },
                    "command": [
                        "python3",
                        "codex/bin/mentor_codex_local.py",
                        "delegate",
                        "ai-stack",
                        "Nic needituj.",
                    ],
                }
            )
        finally:
            gateway.REPO_ROOT = original_repo_root
            gateway.subprocess.Popen = original_popen
            gateway.workspace_run_write = original_writer
            gateway.workspace_root = original_workspace_root

    env = captured.get("env")
    if not isinstance(env, dict):
        raise SystemExit("GATEWAY_BACKGROUND_ENV_SMOKE_FAILED\nreason=no env captured from subprocess.Popen")
    if env.get("OWUI_STATELESS") != "1":
        raise SystemExit(
            "GATEWAY_BACKGROUND_ENV_SMOKE_FAILED\n"
            f"reason=OWUI_STATELESS missing from child env\nchild_env={env!r}"
        )
    if env.get("EXTRA_TEST_FLAG") != "smoke":
        raise SystemExit(
            "GATEWAY_BACKGROUND_ENV_SMOKE_FAILED\n"
            f"reason=generic forwarded env missing from child env\nchild_env={env!r}"
        )
    if not result.get("background"):
        raise SystemExit("GATEWAY_BACKGROUND_ENV_SMOKE_FAILED\nreason=admin_run_workspace did not schedule background job")

    print("GATEWAY_BACKGROUND_ENV_SMOKE_OK")
    print(f"pid={result.get('pid')}")
    print(f"workspace={result.get('workspace')}")
    print(f"child_env_OWUI_STATELESS={env.get('OWUI_STATELESS')}")
    print(f"child_env_EXTRA_TEST_FLAG={env.get('EXTRA_TEST_FLAG')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
