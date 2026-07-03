#!/usr/bin/env python3
"""Offline smoke tests for agent_self_improve.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "codex/bin/agent_self_improve.py"


def run_case(name: str, prompt: str, expected_case: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"asi-{name}-") as tmp:
        tmp_path = Path(tmp)
        transcript = {
            "id": f"chat-{name}",
            "title": name,
            "messages": [
                {"role": "user", "content": prompt, "created": 1},
                {
                    "role": "assistant",
                    "content": "NEEDS_ATTENTION: TaskSpec requested unsupported capability workspace_ssh_key_create",
                    "created": 2,
                },
            ],
        }
        transcript_file = tmp_path / "transcript.json"
        transcript_file.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")

        proc = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--workspace",
                "ai-stack",
                "--transcript-file",
                str(transcript_file),
                "--mode",
                "diagnose",
                "--dry-run",
                "--audit-root",
                str(tmp_path / "audit"),
                "--json",
            ],
            cwd=ROOT,
            env={**os.environ, "AGENT_SELF_IMPROVE_SMOKE_RUNNING": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        if proc.returncode != 0:
            raise SystemExit(f"{name}: expected success, got {proc.returncode}\n{proc.stdout}")
        payload = json.loads(proc.stdout)
        artifact_dir = Path(payload["artifact_dir"])
        diagnosis = json.loads((artifact_dir / "diagnosis.json").read_text(encoding="utf-8"))
        regression = json.loads((artifact_dir / "regression.json").read_text(encoding="utf-8"))
        if diagnosis.get("category") != "capability_alias_or_registry_bug":
            raise SystemExit(f"{name}: expected alias diagnosis, got {diagnosis!r}")
        cases = [case.get("name") for case in regression.get("cases") or []]
        if expected_case not in cases:
            raise SystemExit(f"{name}: expected regression case {expected_case!r}, got {cases!r}")
        print(f"AGENT_SELF_IMPROVE_CASE_OK name={name} case={expected_case}")


def run_verify_dry_run() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-verify-") as tmp:
        proc = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--workspace",
                "ai-stack",
                "--prompt",
                "repo: Test2\nkde ted jsi?",
                "--mode",
                "verify",
                "--dry-run",
                "--audit-root",
                str(Path(tmp) / "audit"),
                "--json",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=240,
        )
        if proc.returncode != 0:
            raise SystemExit(f"verify dry-run should pass:\n{proc.stdout}")
        payload = json.loads(proc.stdout)
        if payload.get("verify", {}).get("command_count", 0) < 4:
            raise SystemExit(f"expected smoke commands in verify result, got {payload!r}")
        print("AGENT_SELF_IMPROVE_VERIFY_DRY_RUN_OK")


def main() -> int:
    run_case("context-status", "repo: Test2\nkde ted jsi?", "meta_workspace_status_test2")
    run_case("capability-catalog", "repo: Test2\njake mas capability?", "meta_capability_catalog_test2")
    run_case("ssh-public", "repo: Test2\nvytvor tam ssh klic a vypis mi public", "ssh_public_key_alias_test2")
    run_case(
        "workspace-search",
        "repo: Test2\nprohledej repo a hledej zminky o capability implementaci",
        "workspace_search_capability_test2",
    )
    run_verify_dry_run()
    print("AGENT_SELF_IMPROVE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
