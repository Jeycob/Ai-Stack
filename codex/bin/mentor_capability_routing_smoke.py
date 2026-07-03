#!/usr/bin/env python3
"""Offline smoke for mentor capability routing decisions.

This keeps the mentor-side intent classification honest for the user-facing
prompts that most strongly affect perceived autonomy: repository bootstrap,
bootstrap-plus-followthrough, and public-web questions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MENTOR_PATH = ROOT / "codex/bin/mentor_codex_local.py"


def load_module():
    spec = importlib.util.spec_from_file_location("mentor_codex_local_capability_smoke", MENTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load mentor helper from {MENTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def expect(value: object, expected: object, label: str) -> None:
    if value != expected:
        raise SystemExit(
            "MENTOR_CAPABILITY_ROUTING_SMOKE_FAILED\n"
            f"label={label}\nexpected={expected!r}\nactual={value!r}"
        )


def expect_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(
            "MENTOR_CAPABILITY_ROUTING_SMOKE_FAILED\n"
            f"label={label}\nmissing={needle!r}\ntext={text!r}"
        )


def expect_not_contains(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise SystemExit(
            "MENTOR_CAPABILITY_ROUTING_SMOKE_FAILED\n"
            f"label={label}\nunexpected={needle!r}\ntext={text!r}"
        )


def main() -> int:
    mentor = load_module()

    create_repo_task = "Vytvor nove repository Test2 a vygeneruj ssh klic pro nej."
    create_repo = mentor.classify_task(create_repo_task)
    expect(create_repo["workflow"], "create-repo", "create-repo-workflow")
    expect(create_repo["repo_name"], "Test2", "create-repo-name")
    expect(create_repo["repo_github"], "no", "create-repo-github-default")
    create_repo_next = mentor.recommended_next_step(create_repo, "ai-stack", create_repo_task)
    expect_contains(create_repo_next, "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "create-repo-next-helper")
    expect_contains(create_repo_next, "Vytvoř nové repository Test2", "create-repo-next-helper-task")
    expect_not_contains(create_repo_next, "GIT_PUSH", "create-repo-next-helper-no-push")
    expect_not_contains(create_repo_next, " push", "create-repo-next-helper-no-push-word")
    expect_not_contains(create_repo_next, "mentor_codex_local.py", "create-repo-next-helper-no-nested-helper")

    bootstrap_task = "Vytvor nove repository Test2 jako React appku, stahni co je treba a pust to."
    bootstrap = mentor.classify_task(bootstrap_task)
    expect(bootstrap["workflow"], "bootstrap-improve", "bootstrap-improve-workflow")
    expect(bootstrap["repo_name"], "Test2", "bootstrap-improve-name")
    expect(bootstrap["solution_profile"], "react-app", "bootstrap-improve-profile")
    expect_contains(bootstrap["guardrail_summary"], "bootstrap plus workspace improvement flow", "bootstrap-improve-guardrail")
    bootstrap_next = mentor.recommended_next_step(bootstrap, "ai-stack", bootstrap_task)
    expect_contains(bootstrap_next, "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "bootstrap-improve-next-helper")
    expect_contains(bootstrap_next, "Pokračuj bootstrapem", "bootstrap-improve-next-helper-execute")
    expect_not_contains(bootstrap_next, "GIT_PUSH", "bootstrap-improve-next-helper-no-push")
    expect_not_contains(bootstrap_next, "mentor_codex_local.py", "bootstrap-improve-next-helper-no-nested-helper")

    web_task = "kdo ma dneska svatek? stahni mi to z seznam.cz"
    web = mentor.classify_task(web_task)
    expect(web["workflow"], "web-answer", "web-answer-workflow")
    expect(web["runtime_profile"], "capability", "web-answer-runtime-profile")
    expect_contains(web["guardrail_summary"], "Public web fetch and answer", "web-answer-guardrail")
    web_next = mentor.recommended_next_step(web, "ai-stack", web_task)
    expect_contains(web_next, "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "web-answer-next-helper")
    expect_contains(web_next, "seznam.cz", "web-answer-next-helper-url")
    expect_not_contains(web_next, "audit", "web-answer-next-helper-no-audit-fallback")
    expect_not_contains(web_next, "mentor_codex_local.py", "web-answer-next-helper-no-nested-helper")

    print("MENTOR_CAPABILITY_ROUTING_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
