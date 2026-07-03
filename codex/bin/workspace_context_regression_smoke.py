#!/usr/bin/env python3
"""Regression smoke for workspace resolution, SSH intent routing, and idempotence."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "codex/bin"
GATEWAY_PATH = ROOT / "codex/gateway/gateway.py"
FILTER_PATH = ROOT / "codex/bin/openwebui_codex_auto_tools_filter.py"
ADD_WORKSPACE = ROOT / "codex/bin/add_workspace.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def expect(condition: bool, label: str, detail: str) -> None:
    if not condition:
        raise SystemExit(f"WORKSPACE_CONTEXT_REGRESSION_SMOKE_FAILED\nlabel={label}\ndetail={detail}")


def build_registry(tmp: Path) -> Path:
    ai_stack = tmp / "ai-stack"
    testcode = tmp / "TestCode"
    ai_stack.mkdir(parents=True, exist_ok=True)
    testcode.mkdir(parents=True, exist_ok=True)
    (ai_stack / "README.md").write_text("# ai-stack\n", encoding="utf-8")
    (testcode / "README.md").write_text("# TestCode\n", encoding="utf-8")
    workspaces_file = tmp / "workspaces.json"
    workspaces_file.write_text(
        json.dumps(
            {
                "default": "smoke",
                "workspaces": {
                    "smoke": {"path": str(tmp / "smoke"), "port": 4096, "cpus": 8, "memory": "16g"},
                    "ai-stack": {"path": str(ai_stack), "port": 4098, "cpus": 8, "memory": "16g"},
                    "TestCode": {"path": str(testcode), "port": 4100, "cpus": 8, "memory": "16g"},
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return workspaces_file


def run_add_workspace_idempotence(tmp: Path) -> None:
    workspaces_file = build_registry(tmp)
    repo_path = tmp / "IdemRepo"
    repo_path.mkdir()
    env = dict(os.environ)
    env["CODEX_WORKSPACES_FILE"] = str(workspaces_file)
    first = subprocess.run(
        [sys.executable, str(ADD_WORKSPACE), "IdemRepo", str(repo_path)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    second = subprocess.run(
        [sys.executable, str(ADD_WORKSPACE), "IdemRepo", str(repo_path)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    data = json.loads(workspaces_file.read_text(encoding="utf-8"))
    workspace = data["workspaces"]["IdemRepo"]
    expect(workspace["port"] == 4101, "idempotent-port", json.dumps(workspace, ensure_ascii=False))
    expect("already registered" in second.stdout, "idempotent-second-run", second.stdout)
    expect("Added workspace" in first.stdout, "idempotent-first-run", first.stdout)


def main() -> int:
    workspace_context = load_module("workspace_context_regression", BIN / "workspace_context.py")
    gateway = load_module("gateway_workspace_context_regression", GATEWAY_PATH)
    filter_module = load_module("openwebui_codex_auto_tools_filter_regression", FILTER_PATH)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        workspaces_file = build_registry(tmp)

        messages = [
            {"role": "user", "content": "vytvor mi nove repository TestCode"},
            {"role": "assistant", "content": "AGENT_LOOP_OK\nrequested_workspace=ai-stack\ncontroller_workspace=ai-stack"},
            {"role": "user", "content": "v repozitart TestCode vytvor ssh klic pro github"},
            {"role": "assistant", "content": "AGENT_LOOP_OK\nrequested_workspace=TestCode\ncontroller_workspace=TestCode\nworkflow=ssh_key_create"},
        ]
        resolved = workspace_context.resolve_workspace_context("vrat mi public key", messages, workspaces_file, fallback_workspace="ai-stack")
        expect(resolved.workspace == "TestCode", "history-workspace", repr(resolved))

        payload = {
            "model": "codex-local-plan-qwen14b",
            "messages": messages + [{"role": "user", "content": "vrat mi public key"}],
        }
        with patch.object(gateway, "WORKSPACES_FILE", str(workspaces_file)):
            natural = gateway.codex_local_agent_loop_payload(payload)
            expect(natural is not None, "natural-loop-payload", repr(natural))
            expect(natural["workspace"] == "TestCode", "natural-loop-workspace", json.dumps(natural, ensure_ascii=False))

            plan = gateway.normalize_agent_plan(
                {"workflow": "review"},
                "TestCode",
                "TestCode",
                True,
                "TestCode Vrat mi public key SSH klice",
            )
            expect(plan["workflow"] == "ssh_key_show_public", "show-public-intent", json.dumps(plan, ensure_ascii=False))

            plan = gateway.normalize_agent_plan(
                {"workflow": "run", "command": ["sh", "-lc", "ssh-keygen -t ed25519 -C \"your_email@example.com\""]},
                "TestCode",
                "TestCode",
                True,
                "TestCode:\npust ssh-keygen -t ed25519 -C \"your_email@example.com\"",
            )
            expect(plan["workflow"] == "ssh_key_create", "ssh-keygen-capability", json.dumps(plan, ensure_ascii=False))
            expect(plan["ssh_comment"] == "your_email@example.com", "ssh-comment", json.dumps(plan, ensure_ascii=False))

            recommendation = gateway.workspace_autopilot_recommendation("TestCode")
            expect(isinstance(recommendation, dict), "load-workspace-path-object", repr(recommendation))

        filter_obj = filter_module.Filter()
        filter_obj._workspaces_file = lambda: workspaces_file
        filter_obj._workspaces = lambda: json.loads(workspaces_file.read_text(encoding="utf-8"))["workspaces"]
        body = {
            "model": "codex-local-plan-qwen14b",
            "messages": messages + [{"role": "user", "content": "vrat mi public key"}],
        }
        routed = filter_obj.inlet(body)
        content = str(routed["messages"][-1]["content"])
        expect("repo: TestCode" in content, "filter-history-workspace", content)
        expect("GATEWAY_ADMIN_AGENT_LOOP TestCode --" in content, "filter-history-loop", content)

        run_add_workspace_idempotence(tmp)

    print("WORKSPACE_CONTEXT_REGRESSION_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
