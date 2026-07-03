#!/usr/bin/env python3
"""Offline smoke tests for the OpenWebUI codex-local auto-tools filter.

This helper imports the filter directly and verifies that natural user prompts
are rewritten to the intended gateway/admin workflow. It does not call
OpenWebUI, Ollama, or the gateway, so it is cheap enough to run in self-checks
and local clones without runtime secrets.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FILTER_PATH = ROOT / "codex/bin/openwebui_codex_auto_tools_filter.py"


@dataclass(frozen=True)
class RouteScenario:
    name: str
    prompt: str
    expected: tuple[str, ...]
    unexpected: tuple[str, ...] = ()
    model: str = "codex-local-plan-qwen14b"


SCENARIOS: tuple[RouteScenario, ...] = (
    RouteScenario(
        name="agent-review-read-only",
        prompt="repo: ai-stack\nProhlédni architekturu gateway/filter/helper vrstvy. Nic needituj. Řekni 3 největší blockery autonomie.",
        expected=("repo: ai-stack", "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "Nic needituj"),
        unexpected=("mentor_codex_local.py", "GATEWAY_ADMIN_WORKSPACE_AUTOPILOT"),
    ),
    RouteScenario(
        name="agent-action-verify",
        prompt="repo: ai-stack\nOvěř projekt a vrať stručný audit výsledku.",
        expected=("repo: ai-stack", "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "Ověř projekt"),
        unexpected=("GATEWAY_ADMIN_WORKSPACE_ACTION", "mentor_codex_local.py"),
    ),
    RouteScenario(
        name="agent-safe-edit-run-after",
        prompt="repo: ai-stack\nPřidej do README krátký příklad a pak ověř projekt.",
        expected=("repo: ai-stack", "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "README", "ověř projekt"),
        unexpected=("GATEWAY_ADMIN_WORKSPACE_EDIT", "GATEWAY_ADMIN_WORKSPACE_ACTION"),
    ),
    RouteScenario(
        name="agent-bootstrap-workspace",
        prompt="repo Test3\nvytvor workspace a initni git a vygeneruj ssh klic",
        expected=("repo: Test3", "GATEWAY_ADMIN_AGENT_LOOP Test3 --", "vytvor workspace"),
        unexpected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO", "GATEWAY_ADMIN_SSH_KEYGEN"),
    ),
    RouteScenario(
        name="agent-natural-create-repo-colon",
        prompt="vytvor repozitar: svatektest",
        expected=("repo: ai-stack", "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "vytvor repozitar: svatektest"),
        unexpected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO", "mkdir", "git init"),
    ),
    RouteScenario(
        name="agent-web-answer",
        prompt="kdo ma dneska svatek? stahni mi to z seznam.cz",
        expected=("repo: ai-stack", "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "seznam.cz"),
        unexpected=("GATEWAY_ADMIN_WEB_ANSWER", "nemám přístup"),
    ),
    RouteScenario(
        name="agent-file-explain",
        prompt="repozitar: ai-stack\nsoubor : docker-compose.yml\n\nprecti docker compose a vysvetli co dela radek po radku",
        expected=("repo: ai-stack", "GATEWAY_ADMIN_AGENT_LOOP ai-stack --", "docker-compose.yml"),
        unexpected=("GATEWAY_ADMIN_EXPLAIN_FILE", "nemohl najít"),
    ),
)

LEGACY_SCENARIOS: tuple[RouteScenario, ...] = (
    RouteScenario(
        name="legacy-next-step-recommend-only",
        prompt="repo: ai-stack\nNavrhni dalsi krok.",
        expected=("GATEWAY_ADMIN_WORKSPACE_AUTOPILOT ai-stack", "--recommend-only"),
        unexpected=("mentor_codex_local.py delegate",),
    ),
    RouteScenario(
        name="follow-through-delegate",
        prompt="repo: ai-stack\nNavrhni dalsi krok a dotahni co pujde.",
        expected=("GATEWAY_ADMIN_RUN_WORKSPACE ai-stack", "mentor_codex_local.py delegate --stateless-turns ai-stack"),
        unexpected=("GATEWAY_ADMIN_WORKSPACE_AUTOPILOT ai-stack --recommend-only",),
    ),
    RouteScenario(
        name="autonomous-delegate",
        prompt="repo: ai-stack\nFixni to a udelej maximum, co pujde.",
        expected=("GATEWAY_ADMIN_RUN_WORKSPACE ai-stack", "mentor_codex_local.py delegate --stateless-turns ai-stack"),
        unexpected=("GATEWAY_ADMIN_WORKSPACE_AUTOPILOT ai-stack",),
    ),
    RouteScenario(
        name="simple-create-repo",
        prompt="Vytvor nove repository Test2 a vygeneruj ssh klic.",
        expected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2",),
        unexpected=("bootstrap-improve", "--restart", "GATEWAY_ADMIN_GIT_PUSH"),
    ),
    RouteScenario(
        name="simple-create-repo-explicit-restart",
        prompt="Vytvor nove repository Test2 a vygeneruj ssh klic. Pak restartni workspace.",
        expected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2 --restart",),
        unexpected=("bootstrap-improve", "GATEWAY_ADMIN_GIT_PUSH"),
    ),
    RouteScenario(
        name="github-create-is-not-push",
        prompt="Vytvor GitHub repository Test2 a vygeneruj ssh klic.",
        expected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2 --github",),
        unexpected=("GATEWAY_ADMIN_GIT_PUSH", "commit"),
    ),
    RouteScenario(
        name="create-repo-negated-github-restart",
        prompt="Vytvor nove repository Test2 a vygeneruj ssh klic, bez GitHubu a bez restartu.",
        expected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2",),
        unexpected=("--github", "--restart", "GATEWAY_ADMIN_GIT_PUSH"),
    ),
    RouteScenario(
        name="github-word-in-negation-is-not-github-request",
        prompt="Vytvor nove repository Test2, ale zatim bez github remote.",
        expected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2",),
        unexpected=("--github", "GATEWAY_ADMIN_GIT_PUSH"),
    ),
    RouteScenario(
        name="bootstrap-improve",
        prompt="Vytvor nove repository Test2 jako React appku, doinstaluj co chybi a zkus to rozbehnout.",
        expected=("mentor_codex_local.py bootstrap-improve --stateless-turns ai-stack", "React appku"),
        unexpected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2 --restart",),
    ),
    RouteScenario(
        name="read-only-architecture-analysis-stays-direct",
        prompt="repo: ai-stack\nProhlédni architekturu gateway/filter/helper vrstvy. Nic needituj. Řekni 3 největší blockery autonomie a navrhni další bezpečný krok.",
        expected=("Prohlédni architekturu gateway/filter/helper vrstvy.", "Nic needituj."),
        unexpected=("GATEWAY_ADMIN_RUN_WORKSPACE", "mentor_codex_local.py", "GATEWAY_ADMIN_WORKSPACE_AUTOPILOT", "GATEWAY_ADMIN_EXPLAIN_FILE"),
    ),
    RouteScenario(
        name="deploy-stack",
        prompt="repo: ai-stack\nPullni ai-stack a nasad.",
        expected=("GATEWAY_ADMIN_DEPLOY_STACK",),
    ),
    RouteScenario(
        name="web-answer-seznam-svatek",
        prompt="kdo ma dneska svatek? stahni mi to z seznam.cz",
        expected=("GATEWAY_ADMIN_WEB_ANSWER https://www.seznam.cz/", "kdo ma dneska svatek"),
        unexpected=("nemám přístup", "read-only"),
    ),
    RouteScenario(
        name="web-answer-seznam-svatek-reversed",
        prompt="stahni z seznam.cz kdo ma dneska svatek",
        expected=("GATEWAY_ADMIN_WEB_ANSWER https://www.seznam.cz/", "kdo ma dneska svatek"),
        unexpected=("nemám přístup", "read-only"),
    ),
    RouteScenario(
        name="web-fetch-url",
        prompt="Podivej se na https://example.com a stahni mi text.",
        expected=("GATEWAY_ADMIN_WEB_FETCH https://example.com", "--max-bytes 300000"),
        unexpected=("GATEWAY_ADMIN_WEB_ANSWER",),
    ),
    RouteScenario(
        name="czech-file-explain-docker-compose",
        prompt="repozitar: ai-stack\nsoubor : docker-compose.yml\n\nprecti docker compose a vysvetli co dela radek po radku",
        expected=("GATEWAY_ADMIN_EXPLAIN_FILE ai-stack docker-compose.yml 1 400", "vysvetli co dela radek po radku"),
        unexpected=("GATEWAY_ADMIN_WORKSPACE_SCAN", "nemohl najít", "read-only"),
    ),
    RouteScenario(
        name="project-file-readme-explain",
        prompt="projekt: ai-stack\nfile: README.md\nVysvětli stručně tento soubor.",
        expected=("GATEWAY_ADMIN_EXPLAIN_FILE ai-stack README.md 1 400",),
        unexpected=("GATEWAY_ADMIN_READ_NUMBERED", "nemohl najít"),
    ),
    RouteScenario(
        name="labelled-create-workspace",
        prompt="repozitar: Test3\nvytvor workspace",
        expected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test3",),
        unexpected=("GATEWAY_ADMIN_WORKSPACE_EDIT", "mentor_codex_local.py delegate"),
    ),
    RouteScenario(
        name="labelled-ssh-key-is-not-create-repo",
        prompt="repozitar: Test2\nvytvor mi ssh klic pro github",
        expected=("GATEWAY_ADMIN_SSH_KEYGEN github-Test2 Test2@local",),
        unexpected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2", "GATEWAY_ADMIN_WORKSPACE_EDIT"),
    ),
    RouteScenario(
        name="labelled-create-workspace-git-ssh",
        prompt="repo Test3\nvytvor workspace a initni git a vygeneruj ssh klic",
        expected=("GATEWAY_ADMIN_CREATE_LOCAL_REPO Test3",),
        unexpected=("GATEWAY_ADMIN_SSH_KEYGEN", "mentor_codex_local.py delegate", "Tuhle akci jsem sam"),
    ),
    RouteScenario(
        name="workspace-direct-edit-webgl",
        prompt="repozitar: Test2\npridej webgl soubor s kouli",
        expected=("GATEWAY_ADMIN_WORKSPACE_EDIT Test2 --timeout 900", "webgl soubor s kouli"),
        unexpected=("mentor_codex_local.py delegate", "Tuhle akci jsem sam"),
    ),
    RouteScenario(
        name="workspace-direct-edit-and-run",
        prompt="repozitar: Test2\npridej webgl soubor s kouli a spust to",
        expected=("GATEWAY_ADMIN_WORKSPACE_EDIT Test2 --timeout 900 --run-after smoke", "webgl soubor s kouli"),
        unexpected=("mentor_codex_local.py delegate", "GATEWAY_ADMIN_WORKSPACE_ACTION Test2 smoke"),
    ),
    RouteScenario(
        name="workspace-install-natural",
        prompt="repo: Test2\nstahni co je potreba a priprav prostredi",
        expected=("GATEWAY_ADMIN_WORKSPACE_ACTION Test2 install --runner container --timeout 1800",),
    ),
    RouteScenario(
        name="workspace-smoke-natural",
        prompt="repo: Test2\npusť to a zkus to rozběhnout",
        expected=("GATEWAY_ADMIN_WORKSPACE_ACTION Test2 smoke --runner container --timeout 900",),
    ),
)


def load_filter_class():
    spec = importlib.util.spec_from_file_location("openwebui_codex_auto_tools_filter", FILTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load filter module from {FILTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Filter


def route_prompt(filter_obj: Any, scenario: RouteScenario) -> str:
    body = {
        "model": scenario.model,
        "messages": [{"role": "user", "content": scenario.prompt}],
    }
    routed = filter_obj.inlet(body)
    return str(routed["messages"][-1]["content"])


def run_scenario(filter_obj: Any, scenario: RouteScenario) -> dict[str, Any]:
    routed = route_prompt(filter_obj, scenario)
    missing = [needle for needle in scenario.expected if needle not in routed]
    unexpected = [needle for needle in scenario.unexpected if needle in routed]
    return {
        "name": scenario.name,
        "ok": not missing and not unexpected,
        "prompt": scenario.prompt,
        "routed": routed,
        "expected": list(scenario.expected),
        "missing": missing,
        "unexpected": unexpected,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline codex-local auto-tools filter route smoke tests.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--scenario", action="append", default=[], help="Scenario name; can be repeated.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    by_name = {scenario.name: scenario for scenario in SCENARIOS}
    if args.list:
        for scenario in SCENARIOS:
            print(scenario.name)
        return 0

    names = args.scenario or list(by_name)
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise SystemExit("Unknown scenario(s): " + ", ".join(unknown))

    filter_obj = load_filter_class()()
    results = [run_scenario(filter_obj, by_name[name]) for name in names]
    payload = {
        "ok": all(item["ok"] for item in results),
        "scenario_count": len(results),
        "results": results,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    print("FILTER_ROUTE_SMOKE")
    print(f"ok={payload['ok']}")
    print(f"scenario_count={payload['scenario_count']}")
    for result in results:
        print(f"SCENARIO={result['name']}")
        print(f"OK={result['ok']}")
        print("ROUTED:")
        print(result["routed"])
        if result["missing"]:
            print("MISSING:")
            print("\n".join(result["missing"]))
        if result["unexpected"]:
            print("UNEXPECTED:")
            print("\n".join(result["unexpected"]))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
