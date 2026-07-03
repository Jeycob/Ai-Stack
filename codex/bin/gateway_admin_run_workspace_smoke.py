#!/usr/bin/env python3
"""Cheap offline checks for gateway admin workspace-run helper normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILTER_PATH = ROOT / "codex/bin/openwebui_gateway_admin_filter.py"


def load_filter_class():
    spec = importlib.util.spec_from_file_location("openwebui_gateway_admin_filter", FILTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load gateway admin filter from {FILTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Filter


def assert_contains(sequence: list[str], needle: str, label: str) -> None:
    if needle not in sequence:
        raise AssertionError(f"{label}: missing {needle!r} in {sequence!r}")


def main() -> int:
    filt = load_filter_class()()

    mentor = [
        "python3",
        "codex/bin/mentor_codex_local.py",
        "delegate",
        "ai-stack",
        "repo: ai-stack\nNic needituj.",
        "--timeout",
        "1200",
    ]
    mentor_fixed = filt._canonicalize_nested_helper_command(mentor)
    assert_contains(mentor_fixed, "--stateless-turns", "mentor")
    if mentor_fixed.index("--stateless-turns") <= mentor_fixed.index("delegate"):
        raise AssertionError(f"mentor: stateless flag should be inserted after mode: {mentor_fixed!r}")

    owui = [
        "python3",
        "codex/bin/owui_chat_turn.py",
        "--model",
        "codex-local-plan-qwen14b",
        "--chat-id",
        "abc",
    ]
    owui_fixed = filt._canonicalize_nested_helper_command(owui)
    assert_contains(owui_fixed, "--stateless", "owui")
    if owui_fixed[2] != "--stateless":
        raise AssertionError(f"owui: stateless flag should be inserted right after script path: {owui_fixed!r}")

    mentor_existing = [
        "python3",
        "codex/bin/mentor_codex_local.py",
        "delegate",
        "--stateless-turns",
        "ai-stack",
        "task",
    ]
    mentor_existing_fixed = filt._canonicalize_nested_helper_command(mentor_existing)
    if mentor_existing_fixed.count("--stateless-turns") != 1:
        raise AssertionError(f"mentor-existing: duplicate stateless flag: {mentor_existing_fixed!r}")

    read_only_prompt = (
        "repo: ai-stack\n"
        "Prohlédni architekturu gateway/filter/helper vrstvy. Nic needituj. "
        "Řekni 3 největší blockery autonomie a navrhni další bezpečný krok."
    )
    routed = filt._natural_admin_command(
        {"model": "codex-local-plan-qwen14b", "messages": [{"role": "user", "content": read_only_prompt}]},
        read_only_prompt,
    )
    if routed is not None:
        raise AssertionError(f"read-only-analysis: expected direct model path, got {routed!r}")

    bootstrap_prompt = (
        "repo Test3\n"
        "vytvor workspace a initni git a vygeneruj ssh klic"
    )
    routed = filt._natural_admin_command(
        {"model": "codex-local-plan-qwen14b", "messages": [{"role": "user", "content": bootstrap_prompt}]},
        bootstrap_prompt,
    )
    if routed != "GATEWAY_ADMIN_CREATE_LOCAL_REPO Test3":
        raise AssertionError(f"create-workspace-git-ssh: expected create-local-repo, got {routed!r}")

    print("GATEWAY_ADMIN_RUN_WORKSPACE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
