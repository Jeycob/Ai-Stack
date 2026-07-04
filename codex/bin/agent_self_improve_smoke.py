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


def run_self_improve(args: list[str], *, timeout: int = 240, expect_ok: bool = True) -> dict:
    proc = subprocess.run(
        ["python3", str(SCRIPT), *args, "--json"],
        cwd=ROOT,
        env={**os.environ, "AGENT_SELF_IMPROVE_SMOKE_RUNNING": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if expect_ok and proc.returncode != 0:
        raise SystemExit(f"expected success, got {proc.returncode}\n{proc.stdout}")
    if not expect_ok and proc.returncode == 0:
        raise SystemExit(f"expected failure, got success\n{proc.stdout}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"bad JSON output: {exc}\n{proc.stdout}") from exc


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

        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--transcript-file",
                str(transcript_file),
                "--mode",
                "diagnose",
                "--dry-run",
                "--audit-root",
                str(tmp_path / "audit"),
            ],
            timeout=60,
        )
        artifact_dir = Path(payload["artifact_dir"])
        diagnosis = json.loads((artifact_dir / "diagnosis.json").read_text(encoding="utf-8"))
        regression = json.loads((artifact_dir / "regression.json").read_text(encoding="utf-8"))
        if diagnosis.get("category") != "capability_alias_or_registry_bug":
            raise SystemExit(f"{name}: expected alias diagnosis, got {diagnosis!r}")
        cases = [case.get("name") for case in regression.get("cases") or []]
        if expected_case not in cases:
            raise SystemExit(f"{name}: expected regression case {expected_case!r}, got {cases!r}")
        print(f"AGENT_SELF_IMPROVE_CASE_OK name={name} case={expected_case}")


def run_reproduce_mode() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-repro-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--prompt",
                "repo: Test2\nkde ted jsi?",
                "--mode",
                "reproduce",
                "--dry-run",
                "--audit-root",
                str(Path(tmp) / "audit"),
            ],
            timeout=240,
        )
        artifact_dir = Path(payload["artifact_dir"])
        reproduce_file = artifact_dir / "cycle-01/reproduce-results.json"
        if not reproduce_file.is_file():
            raise SystemExit(f"reproduce results missing: {reproduce_file}")
        reproduce = json.loads(reproduce_file.read_text(encoding="utf-8"))
        if len(reproduce.get("commands") or []) < 2:
            raise SystemExit(f"expected real reproduce commands, got {reproduce!r}")
        print("AGENT_SELF_IMPROVE_REPRODUCE_MODE_OK")


def run_proposal_mode() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-propose-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--prompt",
                "repo: Test2\nvytvor tam ssh klic a vypis mi public",
                "--mode",
                "propose_patch",
                "--dry-run",
                "--audit-root",
                str(Path(tmp) / "audit"),
            ],
            timeout=240,
            expect_ok=False,
        )
        proposal_file = Path(payload["artifact_dir"]) / "cycle-01/patch-proposal.json"
        if not proposal_file.is_file():
            raise SystemExit(f"patch proposal missing: {proposal_file}")
        proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
        if not proposal.get("proposal"):
            raise SystemExit(f"expected proposal content, got {proposal!r}")
        generated = payload.get("generated_diff") or {}
        if generated.get("source") != "no_safe_generator" or not generated.get("patch_file") in {"", None}:
            raise SystemExit(f"expected explicit no-generator recovery for non-capability proposal, got {payload!r}")
        print("AGENT_SELF_IMPROVE_PATCH_PROPOSAL_OK")


def run_capability_develop_mode() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-cap-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--mode",
                "capability_develop",
                "--capability-name",
                "workspace_profile",
                "--target-capability-name",
                "workspace_profile",
                "--feature-request",
                "Add bounded workspace profiling capability.",
                "--dry-run",
                "--audit-root",
                str(Path(tmp) / "audit"),
            ],
            timeout=240,
        )
        proposal_file = Path(payload["artifact_dir"]) / "cycle-01/patch-proposal.json"
        proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
        dev = proposal.get("capability_development") or {}
        if dev.get("capability_name") != "workspace_profile":
            raise SystemExit(f"expected capability development plan, got {proposal!r}")
        generated_file = Path(payload["artifact_dir"]) / "cycle-01/generated-unified.diff"
        generated_result_file = Path(payload["artifact_dir"]) / "cycle-01/generated-diff-result.json"
        if not generated_file.is_file() or not generated_result_file.is_file():
            raise SystemExit(f"expected generated unified diff artifacts under {payload['artifact_dir']}")
        generated = json.loads(generated_result_file.read_text(encoding="utf-8"))
        if not generated.get("ok") or generated.get("git_apply_check_exit_code") != 0:
            raise SystemExit(f"expected applicable generated diff, got {generated!r}")
        patch_text = generated_file.read_text(encoding="utf-8")
        if "docs/capability-drafts/workspace_profile.json" not in patch_text:
            raise SystemExit(f"expected capability draft file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.smoke.json" not in patch_text:
            raise SystemExit(f"expected capability smoke contract file in generated diff:\n{patch_text}")
        if "docs/codex-local-capability-roadmap.json" not in patch_text:
            raise SystemExit(f"expected roadmap entry in generated diff:\n{patch_text}")
        if '"planned_workflow": "autopilot"' not in patch_text:
            raise SystemExit(f"expected planned workflow metadata in generated diff:\n{patch_text}")
        if '"aliases": [' not in patch_text:
            raise SystemExit(f"expected aliases metadata in generated diff:\n{patch_text}")
        if '"kind": "codex-local-capability-draft-smoke"' not in patch_text:
            raise SystemExit(f"expected draft smoke contract metadata in generated diff:\n{patch_text}")
        print("AGENT_SELF_IMPROVE_CAPABILITY_DEVELOP_OK")


def run_generate_unified_diff_mode() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-gendiff-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--mode",
                "generate_unified_diff",
                "--target-capability-name",
                "workspace_profile",
                "--feature-request",
                "Add bounded workspace profiling capability.",
                "--dry-run",
                "--audit-root",
                str(Path(tmp) / "audit"),
            ],
            timeout=240,
        )
        generated = payload.get("generated_diff") or {}
        if not generated.get("ok") or generated.get("source") != "capability_development_template":
            raise SystemExit(f"expected generated diff mode to produce a valid draft diff, got {payload!r}")
        paths = generated.get("paths") or []
        if "docs/capability-drafts/workspace_profile.json" not in paths:
            raise SystemExit(f"expected capability draft path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.smoke.json" not in paths:
            raise SystemExit(f"expected capability smoke contract path in generated diff, got {generated!r}")
        if "docs/codex-local-capability-roadmap.json" not in paths:
            raise SystemExit(f"expected roadmap path in generated diff, got {generated!r}")
        print("AGENT_SELF_IMPROVE_GENERATE_UNIFIED_DIFF_OK")


def run_max_cycles_mode() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-cycles-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--mode",
                "capability_develop",
                "--max-cycles",
                "2",
                "--dry-run",
                "--audit-root",
                str(Path(tmp) / "audit"),
            ],
            timeout=240,
            expect_ok=False,
        )
        if payload.get("cycles_completed") != 2:
            raise SystemExit(f"expected two cycles on persistent blocker, got {payload!r}")
        print("AGENT_SELF_IMPROVE_MAX_CYCLES_OK")


def run_parallel_artifact_uniqueness() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-unique-") as tmp:
        common = [
            "--workspace",
            "ai-stack",
            "--prompt",
            "repo: Test2\nkde ted jsi?",
            "--mode",
            "diagnose",
            "--dry-run",
            "--audit-root",
            str(Path(tmp) / "audit"),
        ]
        first = run_self_improve(common, timeout=60)
        second = run_self_improve(common, timeout=60)
        if first.get("artifact_dir") == second.get("artifact_dir"):
            raise SystemExit(f"artifact dirs collided: {first['artifact_dir']}")
        print("AGENT_SELF_IMPROVE_ARTIFACT_UNIQUE_OK")


def run_runtime_drift_blocks_e2e() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-gate-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--prompt",
                "repo: Test2\nkde ted jsi?",
                "--mode",
                "e2e",
                "--gateway-url",
                "http://127.0.0.1:1",
                "--e2e-prompt",
                "repo: Test2\nkde ted jsi?",
                "--audit-root",
                str(Path(tmp) / "audit"),
                "--command-timeout",
                "30",
            ],
            timeout=80,
            expect_ok=False,
        )
        e2e = payload.get("e2e") or {}
        if e2e.get("reason") != "runtime_fingerprint_gate_failed":
            raise SystemExit(f"expected runtime gate failure, got {payload!r}")
        print("AGENT_SELF_IMPROVE_RUNTIME_GATE_BLOCKS_E2E_OK")


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
            env={**os.environ, "AGENT_SELF_IMPROVE_SMOKE_RUNNING": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=240,
        )
        if proc.returncode != 0:
            raise SystemExit(f"verify dry-run should pass:\n{proc.stdout}")
        payload = json.loads(proc.stdout)
        if payload.get("verify", {}).get("command_count", 0) < 6:
            raise SystemExit(f"expected smoke commands in verify result, got {payload!r}")
        print("AGENT_SELF_IMPROVE_VERIFY_DRY_RUN_OK")


def main() -> int:
    os.environ.setdefault("AGENT_SELF_IMPROVE_SMOKE_RUNNING", "1")
    run_case("context-status", "repo: Test2\nkde ted jsi?", "meta_workspace_status_test2")
    run_case("capability-catalog", "repo: Test2\njake mas capability?", "meta_capability_catalog_test2")
    run_case("ssh-public", "repo: Test2\nvytvor tam ssh klic a vypis mi public", "ssh_public_key_alias_test2")
    run_case(
        "workspace-search",
        "repo: Test2\nprohledej repo a hledej zminky o capability implementaci",
        "workspace_search_capability_test2",
    )
    run_reproduce_mode()
    run_proposal_mode()
    run_capability_develop_mode()
    run_generate_unified_diff_mode()
    run_max_cycles_mode()
    run_parallel_artifact_uniqueness()
    run_runtime_drift_blocks_e2e()
    run_verify_dry_run()
    print("AGENT_SELF_IMPROVE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
