#!/usr/bin/env python3
"""Offline regression smoke for ai-stack repo-root discovery in auto tools filter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILTER_PATH = ROOT / "codex/bin/openwebui_codex_auto_tools_filter.py"


def load_module():
    spec = importlib.util.spec_from_file_location("codex_auto_tools_filter_repo_root_smoke", FILTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load filter from {FILTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    mod = load_module()
    filt = mod.Filter()
    filt.valves.repo_root = "auto"
    filt.valves.candidate_roots = f"/tmp/does-not-exist,{ROOT}"

    repo_root = filt._repo_root()
    workspaces_file = filt._workspaces_file()
    roadmap_path = filt._roadmap_path()

    expected_workspaces = ROOT / "codex/workspaces.json"
    expected_roadmap = ROOT / "docs/codex-local-capability-roadmap.json"
    if repo_root != ROOT:
        raise SystemExit(
            "CODEX_AUTO_TOOLS_FILTER_REPO_ROOT_SMOKE_FAILED\n"
            f"reason=expected repo root {ROOT}, got {repo_root}"
        )
    if workspaces_file != expected_workspaces:
        raise SystemExit(
            "CODEX_AUTO_TOOLS_FILTER_REPO_ROOT_SMOKE_FAILED\n"
            f"reason=expected workspaces file {expected_workspaces}, got {workspaces_file}"
        )
    if roadmap_path != expected_roadmap:
        raise SystemExit(
            "CODEX_AUTO_TOOLS_FILTER_REPO_ROOT_SMOKE_FAILED\n"
            f"reason=expected roadmap path {expected_roadmap}, got {roadmap_path}"
        )

    body = {
        "model": "codex-local-plan-qwen14b",
        "messages": [{"role": "user", "content": "v repozitart Test2 vytvor ssh klic pro github"}],
    }
    routed = filt.inlet(body)
    content = routed["messages"][-1]["content"]
    if "GATEWAY_ADMIN_AGENT_LOOP Test2 --" not in content:
        raise SystemExit(
            "CODEX_AUTO_TOOLS_FILTER_REPO_ROOT_SMOKE_FAILED\n"
            f"reason=expected Test2 routing, got {content!r}"
        )

    print("CODEX_AUTO_TOOLS_FILTER_REPO_ROOT_SMOKE_OK")
    print(f"repo_root={repo_root}")
    print(f"workspaces_file={workspaces_file}")
    print(f"roadmap_path={roadmap_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
