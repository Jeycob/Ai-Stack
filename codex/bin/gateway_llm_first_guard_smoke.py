#!/usr/bin/env python3
"""Offline guard that keeps gateway core LLM-first.

The gateway may validate structured TaskSpec fields, canonicalize capability
names, apply safety guards, and execute bounded structural fallbacks such as a
literal URL or a backticked shell command. It must not re-grow a natural
language keyword router for Czech/English user prose.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_PATH = ROOT / "codex/gateway/gateway.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.gateway import gateway


def fail(message: str) -> None:
    raise SystemExit(f"GATEWAY_LLM_FIRST_GUARD_FAILED\n{message}")


def assert_no_keyword_router_artifacts() -> None:
    text = GATEWAY_PATH.read_text(encoding="utf-8")
    forbidden_fragments = (
        "_legacy",
        "heuristic_cues",
        "legacy_heuristic",
        "stahni co je treba",
        "stáhni co je třeba",
        "stahni co je potreba",
        "stáhni co je potřeba",
        "vytvor repo",
        "vytvoř repo",
        "vytvor repository",
        "vytvoř repository",
        "zaloz repo",
        "založ repo",
        "public klic",
        "public klíč",
        "ssh klic",
        "ssh klíč",
        "vypis public",
        "vypiš public",
        "vypis vysledky",
        "vypiš výsledky",
        "vyhledej to",
        "hledej to",
        "spust testy",
        "spusť testy",
        "rozbehni to",
        "rozběhni to",
        "create repo",
        "create repository",
        "run tests",
        "search it",
        "show results",
    )
    lowered = text.lower()
    found = [fragment for fragment in forbidden_fragments if fragment.lower() in lowered]
    if found:
        fail(f"natural-language keyword router fragments found in gateway core: {found!r}")
    print("NO_KEYWORD_ROUTER_ARTIFACTS_OK")


def assert_normal_chat_structural_only() -> None:
    source = inspect.getsource(gateway.normal_chat_requires_tool).lower()
    forbidden = ("github", "ssh", "seznam.cz", "git push", "repo")
    found = [item for item in forbidden if item in source]
    if found:
        fail(f"normal_chat_requires_tool must stay structural-only; found {found!r}")

    def payload(text: str, model: str = "mistral-small") -> dict:
        return {"model": model, "messages": [{"role": "user", "content": text}]}

    if gateway.normal_chat_requires_tool(payload("stahni mi to ze seznam.cz")):
        fail("domain-like prose without a concrete URL must not force tool routing")
    if gateway.normal_chat_requires_tool(payload("vygeneruj ssh klic pro github")):
        fail("ssh/github prose must not force tool routing")
    if not gateway.normal_chat_requires_tool(payload("https://www.seznam.cz/")):
        fail("concrete URL should still route through the tool/gateway path")
    print("NORMAL_CHAT_STRUCTURAL_ONLY_OK")


def assert_agent_fallback_structural_only() -> None:
    natural_prompts = (
        "vytvor repozitar: smoke",
        "vytvor workspace a vygeneruj ssh klic",
        "stahni co je treba a pust to",
        "kdo ma dneska svatek? stahni mi to z seznam.cz",
        "pullni ai-stack a nasad",
        "vypis vysledky",
    )
    for prompt in natural_prompts:
        plan = gateway.agent_fallback_plan(prompt, "ai-stack", "ai-stack", True)
        if plan:
            fail(f"natural-language prompt unexpectedly produced fallback plan: {prompt!r} -> {plan!r}")

    structural_cases = (
        ("https://www.seznam.cz/", "web_fetch"),
        ("`pwd`", "run"),
        ("repo: TestCode\npush git@github.com:owner/repo.git", "workspace_git_publish"),
    )
    for prompt, workflow in structural_cases:
        plan = gateway.agent_fallback_plan(prompt, "TestCode", "ai-stack", True)
        if not plan or plan[0].get("workflow") != workflow:
            fail(f"structural fallback failed for {prompt!r}; expected {workflow!r}, got {plan!r}")
    print("AGENT_FALLBACK_STRUCTURAL_ONLY_OK")


def main() -> int:
    assert_no_keyword_router_artifacts()
    assert_normal_chat_structural_only()
    assert_agent_fallback_structural_only()
    print("GATEWAY_LLM_FIRST_GUARD_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
