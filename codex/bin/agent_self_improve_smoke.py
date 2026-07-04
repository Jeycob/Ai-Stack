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


def run_transcript_schema_variants() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-transcript-schema-") as tmp:
        tmp_path = Path(tmp)
        transcript = {
            "chat": {
                "id": "chat-output-schema",
                "history": {
                    "messages": {
                        "u1": {"role": "user", "content": "repo: Test2\nvytvor tam ssh klic", "created": 1},
                        "a1": {
                            "role": "assistant",
                            "content": "",
                            "created": 2,
                            "output": [
                                {
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "NEEDS_ATTENTION: TaskSpec requested unsupported capability workspace_ssh_key_create",
                                        }
                                    ]
                                }
                            ],
                        },
                    }
                },
            }
        }
        transcript_file = tmp_path / "transcript-output-schema.json"
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
        normalized = json.loads((Path(payload["artifact_dir"]) / "transcript.json").read_text(encoding="utf-8"))
        assistant_text = "\n".join(
            str(item.get("content") or "")
            for item in normalized.get("messages") or []
            if item.get("role") == "assistant"
        )
        if "workspace_ssh_key_create" not in assistant_text:
            raise SystemExit(f"expected assistant text from output[].content[].text, got {normalized!r}")

        empty_file = tmp_path / "empty-transcript.json"
        empty_file.write_text(json.dumps({"messages": []}, ensure_ascii=False), encoding="utf-8")
        failed = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--transcript-file",
                str(empty_file),
                "--mode",
                "diagnose",
                "--dry-run",
                "--audit-root",
                str(tmp_path / "audit-empty"),
            ],
            timeout=60,
            expect_ok=False,
        )
        if failed.get("status") != "TRANSCRIPT_EMPTY":
            raise SystemExit(f"expected TRANSCRIPT_EMPTY failure, got {failed!r}")
        print("AGENT_SELF_IMPROVE_TRANSCRIPT_SCHEMA_OK")


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
        )
        proposal_file = Path(payload["artifact_dir"]) / "cycle-01/patch-proposal.json"
        if not proposal_file.is_file():
            raise SystemExit(f"patch proposal missing: {proposal_file}")
        proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
        if not proposal.get("proposal"):
            raise SystemExit(f"expected proposal content, got {proposal!r}")
        proposed_file_changes = proposal.get("proposed_file_changes") or []
        if not any(item.get("path") == "codex/gateway/gateway.py" for item in proposed_file_changes if isinstance(item, dict)):
            raise SystemExit(f"expected non-capability proposal to include runtime gateway change plan, got {proposal!r}")
        if not proposal.get("offload_split", {}).get("codex_local"):
            raise SystemExit(f"expected offload split in proposal, got {proposal!r}")
        generated = payload.get("generated_diff") or {}
        if generated.get("source") != "failure_regression_template" or not generated.get("ok"):
            raise SystemExit(f"expected generated failure regression bundle for non-capability proposal, got {payload!r}")
        paths = generated.get("paths") or []
        if "docs/self-improve-cases/ssh_public_key_alias_test2.json" not in paths:
            raise SystemExit(f"expected self-improve regression case file in generated diff, got {generated!r}")
        if "docs/self-improve-cases/ssh_public_key_alias_test2.smoke.json" not in paths:
            raise SystemExit(f"expected self-improve smoke contract file in generated diff, got {generated!r}")
        if "docs/self-improve-cases/ssh_public_key_alias_test2.patch.md" not in paths:
            raise SystemExit(f"expected self-improve patch fragment file in generated diff, got {generated!r}")
        if "docs/self-improve-cases/ssh_public_key_alias_test2.runtime.patch.diff" not in paths:
            raise SystemExit(f"expected self-improve runtime patch candidate in generated diff, got {generated!r}")
        if "codex/bin/self_improve_cases/ssh_public_key_alias_test2_smoke.py" not in paths:
            raise SystemExit(f"expected self-improve smoke scaffold in generated diff, got {generated!r}")
        patch_file = Path(generated["patch_file"])
        patch_text = patch_file.read_text(encoding="utf-8")
        for marker in (
            '"kind": "codex-local-self-improve-case"',
            '"kind": "codex-local-self-improve-case-smoke"',
            "codex-local-self-improve-patch-fragment",
            "codex-local-self-improve-runtime-patch-candidate",
            "SELF_IMPROVE_CASE_SMOKE_SCAFFOLD",
        ):
            if marker not in patch_text:
                raise SystemExit(f"expected marker {marker!r} in non-capability generated diff:\n{patch_text}")
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
        proposed_file_changes = proposal.get("proposed_file_changes") or []
        if not any(item.get("path") == "docs/codex-local-capability-roadmap.json" for item in proposed_file_changes if isinstance(item, dict)):
            raise SystemExit(f"expected roadmap change plan in capability proposal, got {proposal!r}")
        if not any(item.get("path") == "docs/capability-drafts/workspace_profile.executor-contract.json" for item in proposed_file_changes if isinstance(item, dict)):
            raise SystemExit(f"expected executor contract change plan in capability proposal, got {proposal!r}")
        if not any(item.get("path") == "docs/capability-drafts/workspace_profile.executor-dispatch.json" for item in proposed_file_changes if isinstance(item, dict)):
            raise SystemExit(f"expected executor dispatch change plan in capability proposal, got {proposal!r}")
        if not any(item.get("path") == "docs/capability-drafts/workspace_profile.promotion.patch.diff" for item in proposed_file_changes if isinstance(item, dict)):
            raise SystemExit(f"expected promotion patch change plan in capability proposal, got {proposal!r}")
        if not any(item.get("path") == "docs/capability-drafts/workspace_profile.implementation-workorder.json" for item in proposed_file_changes if isinstance(item, dict)):
            raise SystemExit(f"expected implementation workorder change plan in capability proposal, got {proposal!r}")
        if not any(item.get("path") == "codex/bin/capability_drafts/workspace_profile_executor_stub.py" for item in proposed_file_changes if isinstance(item, dict)):
            raise SystemExit(f"expected executor scaffold change plan in capability proposal, got {proposal!r}")
        evidence_plan = proposal.get("acceptance_evidence_plan") or []
        if len(evidence_plan) < 4:
            raise SystemExit(f"expected capability acceptance evidence plan, got {proposal!r}")
        if not any("target_capability_name" in str(item.get("criterion") or "") for item in evidence_plan if isinstance(item, dict)):
            raise SystemExit(f"expected target_capability_name evidence criterion, got {proposal!r}")
        if not any("AUDIT:generated-unified.diff" in (item.get("expected_artifacts") or []) for item in evidence_plan if isinstance(item, dict)):
            raise SystemExit(f"expected unified diff audit evidence in capability proposal, got {proposal!r}")
        if proposal.get("target_capability_name") != "workspace_profile":
            raise SystemExit(f"expected target capability in proposal, got {proposal!r}")
        if proposal.get("unified_diff_expectations", {}).get("must_pass_git_apply_check") is not True:
            raise SystemExit(f"expected guarded diff expectations in proposal, got {proposal!r}")
        generated_file = Path(payload["artifact_dir"]) / "cycle-01/generated-unified.diff"
        generated_result_file = Path(payload["artifact_dir"]) / "cycle-01/generated-diff-result.json"
        report_file = Path(payload["artifact_dir"]) / "self-improve-report.json"
        manifest_file = Path(payload["artifact_dir"]) / "guarded-apply-manifest.json"
        packet_file = Path(payload["artifact_dir"]) / "execution-packet.json"
        if not generated_file.is_file() or not generated_result_file.is_file():
            raise SystemExit(f"expected generated unified diff artifacts under {payload['artifact_dir']}")
        if not report_file.is_file():
            raise SystemExit(f"expected final self-improve report artifact under {payload['artifact_dir']}")
        if not manifest_file.is_file():
            raise SystemExit(f"expected guarded apply manifest artifact under {payload['artifact_dir']}")
        if not packet_file.is_file():
            raise SystemExit(f"expected execution packet artifact under {payload['artifact_dir']}")
        generated = json.loads(generated_result_file.read_text(encoding="utf-8"))
        report = json.loads(report_file.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        packet = json.loads(packet_file.read_text(encoding="utf-8"))
        if not generated.get("ok") or generated.get("git_apply_check_exit_code") != 0:
            raise SystemExit(f"expected applicable generated diff, got {generated!r}")
        safe_apply_patch_file = Path(str(generated.get("safe_apply_candidate_patch_file") or ""))
        review_only_patch_file = Path(str(generated.get("review_only_patch_file") or ""))
        promotable_runtime_patch_file = Path(str(generated.get("promotable_runtime_patch_file") or ""))
        if not safe_apply_patch_file.is_file():
            raise SystemExit(f"expected safe apply candidate patch file, got {generated!r}")
        if not review_only_patch_file.is_file():
            raise SystemExit(f"expected review-only patch file, got {generated!r}")
        if not promotable_runtime_patch_file.is_file():
            raise SystemExit(f"expected promotable runtime patch file, got {generated!r}")
        if report.get("target_capability_name") != "workspace_profile":
            raise SystemExit(f"expected report to expose target capability, got {report!r}")
        readiness = report.get("capability_patch_readiness") or {}
        if readiness.get("target_capability_name") != "workspace_profile":
            raise SystemExit(f"expected capability readiness target in report, got {report!r}")
        if readiness.get("ready_for_review") is not True:
            raise SystemExit(f"expected capability readiness to be review-ready, got {report!r}")
        if readiness.get("ready_for_apply") is not False:
            raise SystemExit(f"expected capability readiness to remain non-apply in dry-run capability mode, got {report!r}")
        if readiness.get("missing_acceptance_evidence") not in ([], None):
            raise SystemExit(f"expected no missing acceptance evidence in readiness, got {report!r}")
        phase_status = report.get("phase_status") or {}
        if phase_status.get("generate_unified_diff") != "ok":
            raise SystemExit(f"expected generate_unified_diff phase status ok, got {report!r}")
        verify_summary = report.get("verify_summary") or {}
        if verify_summary.get("all_green") is not True:
            raise SystemExit(f"expected verify summary all_green in report, got {report!r}")
        evidence_status = report.get("acceptance_evidence_status") or []
        if len(evidence_status) < 4:
            raise SystemExit(f"expected acceptance evidence status in report, got {report!r}")
        if any(item.get("status") != "covered" for item in evidence_status if isinstance(item, dict)):
            raise SystemExit(f"expected covered acceptance evidence status, got {report!r}")
        if not any("AUDIT:generated-unified.diff" in (item.get("expected_artifacts") or []) for item in evidence_status if isinstance(item, dict)):
            raise SystemExit(f"expected unified diff audit evidence in report status, got {report!r}")
        if manifest.get("decision") != "safe_apply_candidate_with_runtime_review":
            raise SystemExit(f"expected runtime-review guarded apply decision, got {manifest!r}")
        if "docs/capability-drafts/workspace_profile.runtime.patch.diff" not in (manifest.get("review_only_runtime_artifacts") or []):
            raise SystemExit(f"expected runtime patch candidate in review-only list, got {manifest!r}")
        if "docs/capability-drafts/workspace_profile.runtime.patch.diff" not in (manifest.get("promotable_runtime_candidates") or []):
            raise SystemExit(f"expected runtime patch candidate in promotable list, got {manifest!r}")
        if "docs/codex-local-capability-roadmap.json" not in (manifest.get("safe_apply_candidate_paths") or []):
            raise SystemExit(f"expected roadmap diff in safe-apply candidate paths, got {manifest!r}")
        if str(manifest.get("safe_apply_candidate_patch_file") or "") != str(safe_apply_patch_file):
            raise SystemExit(f"expected safe apply patch file in manifest, got {manifest!r}")
        if str(manifest.get("review_only_patch_file") or "") != str(review_only_patch_file):
            raise SystemExit(f"expected review-only patch file in manifest, got {manifest!r}")
        if str(manifest.get("promotable_runtime_patch_file") or "") != str(promotable_runtime_patch_file):
            raise SystemExit(f"expected promotable runtime patch file in manifest, got {manifest!r}")
        if manifest.get("verify_all_green") is not True:
            raise SystemExit(f"expected manifest verify_all_green flag, got {manifest!r}")
        if manifest.get("missing_acceptance_evidence") not in ([], None):
            raise SystemExit(f"expected no missing acceptance evidence in manifest, got {manifest!r}")
        if manifest.get("promotion_ready") is not False:
            raise SystemExit(f"expected promotion_ready false while runtime review is required, got {manifest!r}")
        blockers = manifest.get("promotion_blockers") or []
        if not any("review-only" in str(item) for item in blockers):
            raise SystemExit(f"expected runtime review blocker explanation, got {manifest!r}")
        if "repository exploration" not in (report.get("safe_to_offload_to_codex_local") or []):
            raise SystemExit(f"expected codex-local offload report, got {report!r}")
        if "smoke command execution" not in (report.get("completed_by_codex_local_in_this_run") or []):
            raise SystemExit(f"expected completed codex-local smoke execution in report, got {report!r}")
        if "applying runtime patches" not in (report.get("codex_senior_review_required_for") or []):
            raise SystemExit(f"expected senior review report, got {report!r}")
        if packet.get("kind") != "codex-local-execution-packet":
            raise SystemExit(f"expected execution packet kind, got {packet!r}")
        if packet.get("target_capability_name") != "workspace_profile":
            raise SystemExit(f"expected execution packet target capability, got {packet!r}")
        decision = packet.get("decision") or {}
        if decision.get("execution_state") != "ready_for_review_then_apply":
            raise SystemExit(f"expected review-then-apply execution state, got {packet!r}")
        if decision.get("next_actor") != "senior_codex":
            raise SystemExit(f"expected senior_codex next actor for runtime review packet, got {packet!r}")
        if decision.get("next_step") != "review_runtime_patch_candidate":
            raise SystemExit(f"expected runtime review next step, got {packet!r}")
        if decision.get("ready_for_safe_apply") is not True:
            raise SystemExit(f"expected safe apply readiness in execution packet, got {packet!r}")
        if decision.get("ready_for_runtime_promotion_review") is not True:
            raise SystemExit(f"expected runtime promotion review readiness in execution packet, got {packet!r}")
        if "repository exploration" not in ((packet.get("offload") or {}).get("safe_to_codex_local") or []):
            raise SystemExit(f"expected codex-local offload packet, got {packet!r}")
        if "applying runtime patches" not in ((packet.get("offload") or {}).get("requires_senior_codex") or []):
            raise SystemExit(f"expected senior review packet, got {packet!r}")
        if not ((packet.get("apply_path") or {}).get("safe_apply_commands") or []):
            raise SystemExit(f"expected safe apply commands in execution packet, got {packet!r}")
        if "python3 codex/bin/gateway_runtime_fingerprint_check.py" != ((packet.get("runtime_gate") or {}).get("command") or ""):
            raise SystemExit(f"expected runtime gate command in execution packet, got {packet!r}")
        capability_artifacts = packet.get("capability_artifacts") or {}
        if capability_artifacts.get("implementation_workorder") != "docs/capability-drafts/workspace_profile.implementation-workorder.json":
            raise SystemExit(f"expected implementation workorder path in execution packet, got {packet!r}")
        if capability_artifacts.get("executor_contract") != "docs/capability-drafts/workspace_profile.executor-contract.json":
            raise SystemExit(f"expected executor contract path in execution packet, got {packet!r}")
        if str(report.get("safe_apply_candidate_patch_file") or "") != str(safe_apply_patch_file):
            raise SystemExit(f"expected safe apply patch file in report, got {report!r}")
        if str(report.get("review_only_patch_file") or "") != str(review_only_patch_file):
            raise SystemExit(f"expected review-only patch file in report, got {report!r}")
        if str(report.get("promotable_runtime_patch_file") or "") != str(promotable_runtime_patch_file):
            raise SystemExit(f"expected promotable runtime patch file in report, got {report!r}")
        patch_text = generated_file.read_text(encoding="utf-8")
        safe_apply_patch_text = safe_apply_patch_file.read_text(encoding="utf-8")
        review_only_patch_text = review_only_patch_file.read_text(encoding="utf-8")
        promotable_runtime_patch_text = promotable_runtime_patch_file.read_text(encoding="utf-8")
        if "docs/capability-drafts/workspace_profile.json" not in patch_text:
            raise SystemExit(f"expected capability draft file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.smoke.json" not in patch_text:
            raise SystemExit(f"expected capability smoke contract file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.gateway-integration.json" not in patch_text:
            raise SystemExit(f"expected capability gateway integration draft file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.gateway.patch.md" not in patch_text:
            raise SystemExit(f"expected capability gateway patch fragment file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.runtime.patch.diff" not in patch_text:
            raise SystemExit(f"expected capability runtime patch candidate file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.promotion.patch.diff" not in patch_text:
            raise SystemExit(f"expected capability promotion patch file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.wiring.json" not in patch_text:
            raise SystemExit(f"expected capability wiring blueprint file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.executor-contract.json" not in patch_text:
            raise SystemExit(f"expected capability executor contract file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.executor-dispatch.json" not in patch_text:
            raise SystemExit(f"expected capability executor dispatch file in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.implementation-workorder.json" not in patch_text:
            raise SystemExit(f"expected capability implementation workorder file in generated diff:\n{patch_text}")
        if "codex/bin/capability_drafts/workspace_profile_executor_stub.py" not in patch_text:
            raise SystemExit(f"expected capability executor scaffold in generated diff:\n{patch_text}")
        if "codex/bin/capability_drafts/workspace_profile_runtime_hook_stub.py" not in patch_text:
            raise SystemExit(f"expected capability runtime hook scaffold in generated diff:\n{patch_text}")
        if "codex/bin/capability_drafts/workspace_profile_smoke.py" not in patch_text:
            raise SystemExit(f"expected capability smoke scaffold in generated diff:\n{patch_text}")
        if "docs/codex-local-capability-roadmap.json" not in patch_text:
            raise SystemExit(f"expected roadmap entry in generated diff:\n{patch_text}")
        if '"planned_workflow": "autopilot"' not in patch_text:
            raise SystemExit(f"expected planned workflow metadata in generated diff:\n{patch_text}")
        if '"aliases": [' not in patch_text:
            raise SystemExit(f"expected aliases metadata in generated diff:\n{patch_text}")
        if '"kind": "codex-local-capability-draft-smoke"' not in patch_text:
            raise SystemExit(f"expected draft smoke contract metadata in generated diff:\n{patch_text}")
        if '"kind": "codex-local-capability-wiring-blueprint"' not in patch_text:
            raise SystemExit(f"expected wiring blueprint metadata in generated diff:\n{patch_text}")
        if '"kind": "codex-local-capability-executor-contract"' not in patch_text:
            raise SystemExit(f"expected executor contract metadata in generated diff:\n{patch_text}")
        if '"kind": "codex-local-capability-executor-dispatch-plan"' not in patch_text:
            raise SystemExit(f"expected executor dispatch metadata in generated diff:\n{patch_text}")
        if '"kind": "codex-local-capability-implementation-workorder"' not in patch_text:
            raise SystemExit(f"expected implementation workorder metadata in generated diff:\n{patch_text}")
        if '"kind": "codex-local-capability-gateway-integration-draft"' not in patch_text:
            raise SystemExit(f"expected gateway integration draft metadata in generated diff:\n{patch_text}")
        if "codex-local-capability-gateway-patch-fragment" not in patch_text:
            raise SystemExit(f"expected gateway patch fragment metadata in generated diff:\n{patch_text}")
        if "codex-local-capability-runtime-patch-candidate" not in patch_text:
            raise SystemExit(f"expected runtime patch candidate metadata in generated diff:\n{patch_text}")
        if "diff --git a/codex/gateway/gateway.py b/codex/gateway/gateway.py" not in patch_text:
            raise SystemExit(f"expected promotion patch to carry real gateway diff:\n{patch_text}")
        if "@@ AGENT_CAPABILITY_TO_WORKFLOW @@" not in patch_text:
            raise SystemExit(f"expected gateway workflow patch fragment in generated diff:\n{patch_text}")
        if '"integration_order": [' not in patch_text:
            raise SystemExit(f"expected gateway integration order metadata in generated diff:\n{patch_text}")
        if '"touchpoints": [' not in patch_text:
            raise SystemExit(f"expected touchpoints metadata in generated diff:\n{patch_text}")
        if "CAPABILITY_NAME = 'workspace_profile'" not in patch_text:
            raise SystemExit(f"expected executor stub capability constant in generated diff:\n{patch_text}")
        if "run_workspace_profile_capability" not in patch_text:
            raise SystemExit(f"expected concrete dispatch handler marker in generated diff:\n{patch_text}")
        if '"codex_local_steps": [' not in patch_text:
            raise SystemExit(f"expected implementation workorder steps in generated diff:\n{patch_text}")
        if "CAPABILITY_RUNTIME_HOOK_STUB" not in patch_text:
            raise SystemExit(f"expected runtime hook scaffold marker in generated diff:\n{patch_text}")
        if "CAPABILITY_DRAFT_SMOKE_SCAFFOLD" not in patch_text:
            raise SystemExit(f"expected smoke scaffold marker in generated diff:\n{patch_text}")
        if "docs/capability-drafts/workspace_profile.runtime.patch.diff" in safe_apply_patch_text:
            raise SystemExit(f"safe apply patch should not contain runtime review-only artifact:\n{safe_apply_patch_text}")
        if "docs/capability-drafts/workspace_profile.gateway.patch.md" in safe_apply_patch_text:
            raise SystemExit(f"safe apply patch should not contain gateway review-only artifact:\n{safe_apply_patch_text}")
        if "docs/codex-local-capability-roadmap.json" not in safe_apply_patch_text:
            raise SystemExit(f"safe apply patch should keep safe docs/stubs:\n{safe_apply_patch_text}")
        if "docs/capability-drafts/workspace_profile.runtime.patch.diff" not in review_only_patch_text:
            raise SystemExit(f"review-only patch should include runtime candidate artifact:\n{review_only_patch_text}")
        if "docs/capability-drafts/workspace_profile.promotion.patch.diff" not in review_only_patch_text:
            raise SystemExit(f"review-only patch should include promotion patch artifact:\n{review_only_patch_text}")
        if "--- a/codex/gateway/gateway.py" not in promotable_runtime_patch_text:
            raise SystemExit(f"expected promotable runtime patch to target gateway before path:\n{promotable_runtime_patch_text}")
        if "+++ b/codex/gateway/gateway.py" not in promotable_runtime_patch_text:
            raise SystemExit(f"expected promotable runtime patch to target gateway after path:\n{promotable_runtime_patch_text}")
        if '"workspace_profile": "clarify"' not in promotable_runtime_patch_text:
            raise SystemExit(f"expected capability workflow mapping in promotable runtime patch diff:\n{promotable_runtime_patch_text}")
        if '"implemented": False' not in promotable_runtime_patch_text:
            raise SystemExit(f"expected promotable runtime patch to stay in draft state:\n{promotable_runtime_patch_text}")
        if "def run_workspace_profile_capability(task_spec, workspace):" not in promotable_runtime_patch_text:
            raise SystemExit(f"expected promotable runtime patch to carry a concrete handler stub:\n{promotable_runtime_patch_text}")
        if '"status": "draft_only"' not in promotable_runtime_patch_text:
            raise SystemExit(f"expected promotable runtime patch to keep the handler in draft-only mode:\n{promotable_runtime_patch_text}")
        if '"target_capability_name"' not in promotable_runtime_patch_text:
            raise SystemExit(f"expected promotable runtime patch to preserve target_capability_name wiring:\n{promotable_runtime_patch_text}")
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
        if not generated.get("safe_apply_candidate_patch_file"):
            raise SystemExit(f"expected safe apply candidate patch file in generated diff result, got {generated!r}")
        if not generated.get("review_only_patch_file"):
            raise SystemExit(f"expected review-only patch file in generated diff result, got {generated!r}")
        if not generated.get("promotable_runtime_patch_file"):
            raise SystemExit(f"expected promotable runtime patch file in generated diff result, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.json" not in paths:
            raise SystemExit(f"expected capability draft path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.smoke.json" not in paths:
            raise SystemExit(f"expected capability smoke contract path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.gateway-integration.json" not in paths:
            raise SystemExit(f"expected capability gateway integration draft path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.gateway.patch.md" not in paths:
            raise SystemExit(f"expected capability gateway patch fragment path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.runtime.patch.diff" not in paths:
            raise SystemExit(f"expected capability runtime patch candidate path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.promotion.patch.diff" not in paths:
            raise SystemExit(f"expected capability promotion patch path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.wiring.json" not in paths:
            raise SystemExit(f"expected capability wiring blueprint path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.executor-contract.json" not in paths:
            raise SystemExit(f"expected capability executor contract path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.executor-dispatch.json" not in paths:
            raise SystemExit(f"expected capability executor dispatch path in generated diff, got {generated!r}")
        if "docs/capability-drafts/workspace_profile.implementation-workorder.json" not in paths:
            raise SystemExit(f"expected capability implementation workorder path in generated diff, got {generated!r}")
        if "codex/bin/capability_drafts/workspace_profile_executor_stub.py" not in paths:
            raise SystemExit(f"expected capability executor scaffold path in generated diff, got {generated!r}")
        if "codex/bin/capability_drafts/workspace_profile_runtime_hook_stub.py" not in paths:
            raise SystemExit(f"expected capability runtime hook scaffold path in generated diff, got {generated!r}")
        if "codex/bin/capability_drafts/workspace_profile_smoke.py" not in paths:
            raise SystemExit(f"expected capability smoke scaffold path in generated diff, got {generated!r}")
        if "docs/codex-local-capability-roadmap.json" not in paths:
            raise SystemExit(f"expected roadmap path in generated diff, got {generated!r}")
        print("AGENT_SELF_IMPROVE_GENERATE_UNIFIED_DIFF_OK")


def run_patch_mode_dry_run() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-patch-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--mode",
                "patch",
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
        patch = payload.get("patch") or {}
        if not generated.get("ok"):
            raise SystemExit(f"expected generated diff to be valid before patch phase, got {payload!r}")
        if patch.get("mode") != "dry_run":
            raise SystemExit(f"expected dry-run patch mode, got {payload!r}")
        if patch.get("applied"):
            raise SystemExit(f"patch should not apply during dry-run, got {payload!r}")
        if patch.get("git_apply_check_exit_code") != 0:
            raise SystemExit(f"expected git apply --check to pass in patch mode, got {payload!r}")
        if patch.get("patch_file") != generated.get("safe_apply_candidate_patch_file"):
            raise SystemExit(f"expected patch mode to use safe apply candidate bundle, got {payload!r}")
        if patch.get("patch_source") != "guarded_generated_patch":
            raise SystemExit(f"expected guarded generated patch source, got {payload!r}")
        print("AGENT_SELF_IMPROVE_PATCH_DRY_RUN_OK")


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
        artifact_dir = Path(payload["artifact_dir"])
        cycle1 = json.loads((artifact_dir / "cycle-01/cycle-summary.json").read_text(encoding="utf-8"))
        cycle2 = json.loads((artifact_dir / "cycle-02/cycle-summary.json").read_text(encoding="utf-8"))
        previous = cycle2.get("previous_cycle_context") or {}
        if previous.get("failed_cycle") != 1:
            raise SystemExit(f"expected cycle-02 to reference failed cycle 1, got {cycle2!r}")
        repair_context = ((cycle2.get("phases", {}).get("reason", {}) or {}).get("task_spec") or {}).get("repair_context") or {}
        if repair_context.get("failed_cycle") != 1:
            raise SystemExit(f"expected repair_context to carry failed cycle info, got {cycle2!r}")
        details = repair_context.get("failed_phase_details") or []
        if not details:
            raise SystemExit(f"expected failed phase details in repair_context, got {cycle2!r}")
        if not any(item.get("phase") == "reason" for item in details if isinstance(item, dict)):
            raise SystemExit(f"expected failed reason phase detail in repair_context, got {cycle2!r}")
        if not any(item.get("phase") == "reason" and item.get("missing_inputs") == ["feature_request_or_capability_name"] for item in details if isinstance(item, dict)):
            raise SystemExit(f"expected reason phase missing_inputs in repair_context, got {cycle2!r}")
        if not repair_context.get("cycle_dir"):
            raise SystemExit(f"expected repair_context cycle_dir, got {cycle2!r}")
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
        gate = e2e.get("gate") or {}
        if not gate or gate.get("ok") is not False:
            raise SystemExit(f"expected failing runtime gate payload, got {payload!r}")
        report = payload.get("report") or {}
        runtime_gate_status = report.get("runtime_gate_status") or {}
        e2e_gate = runtime_gate_status.get("e2e") or {}
        if e2e_gate.get("status") != "blocked":
            raise SystemExit(f"expected blocked e2e runtime gate status, got {payload!r}")
        if not e2e_gate.get("marker"):
            raise SystemExit(f"expected runtime gate marker in report, got {payload!r}")
        packet = json.loads((Path(payload["artifact_dir"]) / "execution-packet.json").read_text(encoding="utf-8"))
        decision = packet.get("decision") or {}
        if decision.get("execution_state") != "blocked_runtime_drift":
            raise SystemExit(f"expected blocked runtime drift execution state, got {packet!r}")
        if "e2e" not in (decision.get("blocked_runtime_gate_phases") or []):
            raise SystemExit(f"expected blocked e2e phase in execution packet, got {packet!r}")
        print("AGENT_SELF_IMPROVE_RUNTIME_GATE_BLOCKS_E2E_OK")


def run_runtime_drift_blocks_deploy() -> None:
    with tempfile.TemporaryDirectory(prefix="asi-gate-deploy-") as tmp:
        payload = run_self_improve(
            [
                "--workspace",
                "ai-stack",
                "--prompt",
                "repo: Test2\nkde ted jsi?",
                "--mode",
                "deploy",
                "--gateway-url",
                "http://127.0.0.1:1",
                "--audit-root",
                str(Path(tmp) / "audit"),
                "--command-timeout",
                "30",
            ],
            timeout=80,
            expect_ok=False,
        )
        deploy = payload.get("deploy") or {}
        if deploy.get("reason") != "runtime_fingerprint_gate_failed":
            raise SystemExit(f"expected deploy runtime gate failure, got {payload!r}")
        gate = deploy.get("gate") or {}
        if not gate or gate.get("ok") is not False:
            raise SystemExit(f"expected failing deploy runtime gate payload, got {payload!r}")
        report = payload.get("report") or {}
        runtime_gate_status = report.get("runtime_gate_status") or {}
        deploy_gate = runtime_gate_status.get("deploy") or {}
        if deploy_gate.get("status") != "blocked":
            raise SystemExit(f"expected blocked deploy runtime gate status, got {payload!r}")
        if not deploy_gate.get("marker"):
            raise SystemExit(f"expected deploy runtime gate marker in report, got {payload!r}")
        packet = json.loads((Path(payload["artifact_dir"]) / "execution-packet.json").read_text(encoding="utf-8"))
        decision = packet.get("decision") or {}
        if decision.get("execution_state") != "blocked_runtime_drift":
            raise SystemExit(f"expected blocked runtime drift execution state for deploy, got {packet!r}")
        if "deploy" not in (decision.get("blocked_runtime_gate_phases") or []):
            raise SystemExit(f"expected blocked deploy phase in execution packet, got {packet!r}")
        print("AGENT_SELF_IMPROVE_RUNTIME_GATE_BLOCKS_DEPLOY_OK")


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
        report = payload.get("report") or {}
        artifact_dir = Path(payload["artifact_dir"])
        manifest_file = artifact_dir / "guarded-apply-manifest.json"
        packet_file = artifact_dir / "execution-packet.json"
        if not manifest_file.is_file():
            raise SystemExit(f"expected guarded apply manifest in verify dry-run: {artifact_dir}")
        if not packet_file.is_file():
            raise SystemExit(f"expected execution packet in verify dry-run: {artifact_dir}")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        packet = json.loads(packet_file.read_text(encoding="utf-8"))
        if report.get("patch_application_decision") not in {"not_applied", "validated_only"}:
            raise SystemExit(f"expected report patch decision in verify dry-run, got {payload!r}")
        if (report.get("phase_status") or {}).get("verify") != "ok":
            raise SystemExit(f"expected verify phase status ok in verify dry-run, got {payload!r}")
        if (report.get("verify_summary") or {}).get("all_green") is not True:
            raise SystemExit(f"expected verify summary all_green in verify dry-run, got {payload!r}")
        runtime_gate_status = report.get("runtime_gate_status") or {}
        for phase in ("deploy", "e2e"):
            gate = runtime_gate_status.get(phase) or {}
            if gate.get("status") not in {"not_run", "not_recorded"}:
                raise SystemExit(f"expected untouched runtime gate status for {phase} in verify dry-run, got {payload!r}")
        if manifest.get("decision") not in {"safe_apply_candidate", "safe_apply_candidate_with_runtime_review", "no_apply_candidate"}:
            raise SystemExit(f"unexpected guarded apply decision in verify dry-run, got {manifest!r}")
        manifest_runtime_gate_status = manifest.get("runtime_gate_status") or {}
        if sorted(manifest_runtime_gate_status.keys()) != ["deploy", "e2e"]:
            raise SystemExit(f"expected runtime gate status in manifest, got {manifest!r}")
        if manifest.get("runtime_gate_command") != "python3 codex/bin/gateway_runtime_fingerprint_check.py":
            raise SystemExit(f"expected runtime gate command in manifest, got {manifest!r}")
        if manifest.get("safe_apply_candidate_patch_file"):
            commands = manifest.get("safe_apply_commands") or []
            if len(commands) < 2 or not commands[0].startswith("git apply --check "):
                raise SystemExit(f"expected guarded safe-apply commands in manifest, got {manifest!r}")
        if manifest.get("promotable_runtime_patch_file"):
            commands = manifest.get("runtime_promotion_commands") or []
            if len(commands) < 3 or "gateway_runtime_fingerprint_check.py" not in " ".join(commands):
                raise SystemExit(f"expected runtime promotion commands in manifest, got {manifest!r}")
        if packet.get("kind") != "codex-local-execution-packet":
            raise SystemExit(f"expected execution packet in verify dry-run, got {packet!r}")
        if packet.get("verify", {}).get("all_green") is not True:
            raise SystemExit(f"expected verify summary in execution packet, got {packet!r}")
        decision = packet.get("decision") or {}
        if decision.get("execution_state") not in {"ready_for_guarded_apply", "ready_for_review_then_apply", "verified_no_apply_candidate"}:
            raise SystemExit(f"expected actionable execution packet state in verify dry-run, got {packet!r}")
        print("AGENT_SELF_IMPROVE_VERIFY_DRY_RUN_OK")


def main() -> int:
    os.environ.setdefault("AGENT_SELF_IMPROVE_SMOKE_RUNNING", "1")
    run_transcript_schema_variants()
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
    run_patch_mode_dry_run()
    run_max_cycles_mode()
    run_parallel_artifact_uniqueness()
    run_runtime_drift_blocks_e2e()
    run_runtime_drift_blocks_deploy()
    run_verify_dry_run()
    print("AGENT_SELF_IMPROVE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
