#!/usr/bin/env python3
"""Run cheap user-like OpenWebUI audit chat scenarios against codex-local.

Each scenario sends a natural-language prompt through the visible audit chat and
asserts that the resulting assistant reply contains a few expected markers.
The goal is not deep semantic verification; it is a lightweight E2E proof that
the chat path, filters, routing, and gateway/admin capability chain still work
for common codex-local mentoring flows.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import owui_chat_turn as turn


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "codex/bin/owui_chat_smoke.py"


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    prompt_template: str
    expected_substrings: tuple[str, ...]
    total_timeout: float = 240.0
    status_interval: float = 3.0
    mutating: bool = False


SCENARIOS: dict[str, Scenario] = {
    "agent-review": Scenario(
        name="agent-review",
        description="Read-only engineering review routed through the intent-first agent loop.",
        prompt_template="repo: {workspace}\nProhlédni architekturu gateway/filter/helper vrstvy. Nic needituj. Řekni 3 největší blockery autonomie a navrhni další bezpečný krok.",
        expected_substrings=("AGENT_LOOP_OK", "workflow=review", "read_only=True"),
    ),
    "explicit-agent-loop": Scenario(
        name="explicit-agent-loop",
        description="Explicit GATEWAY_ADMIN_AGENT_LOOP marker is intercepted by the admin/capability layer, not answered by a plain model.",
        prompt_template="repo: {workspace}\nGATEWAY_ADMIN_AGENT_LOOP {workspace} -- Prohlédni workspace. Nic needituj. Odpověz stručně.",
        expected_substrings=("AGENT_LOOP", "workflow=review"),
    ),
    "verify-project": Scenario(
        name="verify-project",
        description="Natural project verification request routed through the intent-first agent loop into audited verify action.",
        prompt_template="repo: {workspace}\nOver projekt a vrat strucny audit vysledku.",
        expected_substrings=("workflow=action", "\"action\": \"verify\""),
        total_timeout=360.0,
    ),
    "deploy-status": Scenario(
        name="deploy-status",
        description="Natural ai-stack deploy status query routed through the intent-first agent loop.",
        prompt_template="repo: ai-stack\nUkaz deploy status.",
        expected_substrings=("workflow=deploy", "AGENT_LOOP"),
    ),
    "web-answer": Scenario(
        name="web-answer",
        description="Natural public web question routed through the intent-first agent loop into audited web-answer capability.",
        prompt_template="kdo ma dneska svatek? stahni mi to z seznam.cz",
        expected_substrings=("workflow=web_answer", "AGENT_LOOP"),
        total_timeout=360.0,
    ),
    "next-step": Scenario(
        name="next-step",
        description="Natural recommendation request routed to the intent-first agent loop.",
        prompt_template="repo: {workspace}\nNavrhni dalsi krok.",
        expected_substrings=("AGENT_LOOP", "workflow=autopilot"),
    ),
    "bootstrap-followthrough": Scenario(
        name="bootstrap-followthrough",
        description="Natural repository bootstrap plus follow-through request routed into bootstrap capability flow.",
        prompt_template="vytvor repozitar: svatektest a pak stahni co je treba a pust to",
        expected_substrings=("AGENT_LOOP", "workflow=bootstrap"),
        total_timeout=480.0,
        mutating=True,
    ),
    "safe-edit-verify": Scenario(
        name="safe-edit-verify",
        description="Natural coding change plus verification request routed into edit capability flow.",
        prompt_template="repo: {workspace}\nPridej do README kratky priklad a pak over projekt.",
        expected_substrings=("AGENT_LOOP", "workflow=edit"),
        total_timeout=480.0,
        mutating=True,
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run user-like codex-local audit chat scenarios via OpenWebUI.")
    parser.add_argument("--base-url", default=turn.DEFAULT_BASE_URL)
    parser.add_argument("--chat-id", default=turn.DEFAULT_CHAT_ID)
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--api-key-file", default=str(turn.DEFAULT_API_KEY_FILE))
    parser.add_argument("--model", default=turn.DEFAULT_MODEL)
    parser.add_argument("--title", default="Codex audit log - OpenWebUI visible history")
    parser.add_argument("--workspace", default="ai-stack")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIOS.keys()) + ["all"],
        default=[],
        help="Scenario name; can be repeated. Default runs agent-review and verify-project.",
    )
    parser.add_argument(
        "--include-mutating",
        action="store_true",
        help="Allow scenarios that may create/edit state through the live capability chain.",
    )
    parser.add_argument("--list", action="store_true", help="List available scenarios and exit")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable results")
    parser.add_argument("--dry-run", action="store_true", help="Print planned prompts/commands without calling OpenWebUI")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--send-history", action="store_true")
    parser.add_argument("--no-live-status", action="store_true")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=4.0)
    parser.add_argument("--total-timeout", type=float, default=240.0)
    parser.add_argument("--status-interval", type=float, default=3.0)
    return parser.parse_args()


def selected_scenarios(args: argparse.Namespace) -> list[Scenario]:
    if args.list:
        return []
    names = args.scenario or ["agent-review", "verify-project"]
    if "all" in names:
        names = [
            name
            for name, scenario in SCENARIOS.items()
            if args.include_mutating or not scenario.mutating
        ]
    deduped: list[Scenario] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        scenario = SCENARIOS[name]
        if scenario.mutating and not args.include_mutating:
            raise SystemExit(
                "OWUI_CHAT_SCENARIOS_BLOCKED\n"
                f"reason=scenario {name!r} is mutating\n"
                "recovery=rerun with --include-mutating when you intentionally want live repo/container changes"
            )
        deduped.append(scenario)
        seen.add(name)
    return deduped


def scenario_prompt(scenario: Scenario, workspace: str) -> str:
    return scenario.prompt_template.format(workspace=workspace)


def scenario_command(args: argparse.Namespace, scenario: Scenario, prompt_path: str) -> list[str]:
    expected = scenario.expected_substrings[0] if scenario.expected_substrings else ""
    cmd = [
        sys.executable,
        str(SMOKE),
        "--base-url",
        args.base_url,
        "--chat-id",
        args.chat_id,
        "--api-key-env",
        args.api_key_env,
        "--api-key-file",
        args.api_key_file,
        "--model",
        args.model,
        "--title",
        args.title,
        "--prompt-file",
        prompt_path,
        "--visible-prompt-file",
        prompt_path,
        "--attempts",
        str(args.attempts),
        "--timeout",
        str(args.timeout),
        "--initial-delay",
        str(args.initial_delay),
        "--max-delay",
        str(args.max_delay),
        "--total-timeout",
        str(min(args.total_timeout, scenario.total_timeout)),
        "--status-interval",
        str(args.status_interval or scenario.status_interval),
        "--turn-key",
        f"scenario:{scenario.name}:{args.workspace}",
        "--quiet",
    ]
    if expected:
        cmd.extend(["--expected-substring", expected])
    if args.send_history:
        cmd.append("--send-history")
    if args.no_live_status:
        cmd.append("--no-live-status")
    return cmd


def output_contains_all(output: str, needles: tuple[str, ...]) -> tuple[bool, list[str]]:
    missing = [needle for needle in needles if needle not in output]
    return not missing, missing


def summarize(text: str, max_lines: int = 18) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"])


def run_scenario(args: argparse.Namespace, scenario: Scenario) -> dict[str, object]:
    prompt = scenario_prompt(scenario, args.workspace)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(prompt)
        prompt_path = handle.name
    cmd = scenario_command(args, scenario, prompt_path)
    started = time.time()
    try:
        if args.dry_run:
            return {
                "name": scenario.name,
                "ok": True,
                "dry_run": True,
                "description": scenario.description,
                "prompt": prompt,
                "command": cmd,
            }

        try:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(30.0, min(args.total_timeout, scenario.total_timeout) + 90.0),
            )
            output = proc.stdout or ""
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", "replace")
            output += "\nOWUI_CHAT_SCENARIO_TIMEOUT\n"
            returncode = 124
        ok, missing = output_contains_all(output, scenario.expected_substrings)
        return {
            "name": scenario.name,
            "ok": returncode == 0 and ok,
            "runner_exit_code": returncode,
            "duration_ms": int((time.time() - started) * 1000),
            "description": scenario.description,
            "prompt": prompt,
            "expected_substrings": list(scenario.expected_substrings),
            "missing_substrings": missing,
            "output": output,
        }
    finally:
        Path(prompt_path).unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    if args.list:
        for scenario in SCENARIOS.values():
            marker = " [mutating]" if scenario.mutating else ""
            print(f"{scenario.name}{marker}: {scenario.description}")
        return 0

    scenarios = selected_scenarios(args)
    results = [run_scenario(args, scenario) for scenario in scenarios]
    payload = {
        "workspace": args.workspace,
        "chat_id": args.chat_id,
        "model": args.model,
        "scenario_count": len(results),
        "ok": all(bool(item.get("ok")) for item in results),
        "results": results,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    print("OWUI_CHAT_SCENARIOS")
    print(f"workspace={args.workspace}")
    print(f"chat_id={args.chat_id}")
    print(f"model={args.model}")
    print(f"scenario_count={len(results)}")
    print(f"ok={payload['ok']}")
    for item in results:
        print(f"SCENARIO={item['name']}")
        print(f"OK={item['ok']}")
        if item.get("dry_run"):
            print("PROMPT:")
            print(item["prompt"])
            print("COMMAND:")
            print(" ".join(str(x) for x in item["command"]))
            continue
        print(f"RUNNER_EXIT_CODE={item['runner_exit_code']}")
        print(f"DURATION_MS={item['duration_ms']}")
        print("PROMPT:")
        print(item["prompt"])
        print("OUTPUT:")
        print(summarize(str(item.get("output", ""))))
        if item.get("missing_substrings"):
            print("MISSING_SUBSTRINGS:")
            for value in item["missing_substrings"]:
                print(value)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
