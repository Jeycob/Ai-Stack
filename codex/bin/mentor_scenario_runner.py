#!/usr/bin/env python3
"""Run a cheap local end-to-end mentor scenario over codex-local helper flows."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MENTOR = [sys.executable, "codex/bin/mentor_codex_local.py"]


@dataclass
class StepResult:
    name: str
    command: list[str]
    exit_code: int
    duration_ms: int
    output: str


def parse_key_values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            result[key] = value
    return result


def run_step(name: str, command: list[str]) -> StepResult:
    started = time.time()
    proc = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return StepResult(
        name=name,
        command=command,
        exit_code=proc.returncode,
        duration_ms=int((time.time() - started) * 1000),
        output=proc.stdout or "",
    )


def summarize_output(text: str, max_lines: int = 10) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    head = lines[:max_lines]
    head.append(f"... ({len(lines) - max_lines} more lines)")
    return "\n".join(head)


def scenario_steps(workspace: str, task: str, followup_steps: int) -> list[tuple[str, list[str]]]:
    steps: list[tuple[str, list[str]]] = [
        ("profile", [*MENTOR, "profile", workspace, task]),
        ("brief", [*MENTOR, "brief", workspace, task]),
        ("next-helper", [*MENTOR, "next-helper", workspace, task]),
        ("plan", [*MENTOR, "plan", workspace, task]),
    ]
    return steps


def workflow_specific_steps(workspace: str, task: str, workflow: str, followup_steps: int) -> list[tuple[str, list[str]]]:
    if workflow == "bootstrap-improve":
        return [
            (
                "bootstrap-dispatch",
                [*MENTOR, "bootstrap-dispatch", workspace, task, "--followup-steps", str(followup_steps)],
            )
        ]
    if workflow in {"improve", "autopilot"}:
        return [
            (
                "delegate",
                [*MENTOR, "delegate", workspace, task, "--dry-run"],
            )
        ]
    if workflow in {"push", "push-check", "publish-plan", "release-prep", "deploy"}:
        return [
            (
                "boundary",
                [*MENTOR, "boundary", workspace, task],
            )
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a cheap local end-to-end mentor scenario across codex-local helper flows.")
    parser.add_argument("workspace")
    parser.add_argument("task")
    parser.add_argument("--followup-steps", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-full-output", action="store_true")
    args = parser.parse_args()

    results: list[StepResult] = []
    for name, command in scenario_steps(args.workspace, args.task, args.followup_steps):
        result = run_step(name, command)
        results.append(result)
        if result.exit_code != 0:
            break

    profile_result = next((item for item in results if item.name == "profile"), None)
    workflow = ""
    profile_meta: dict[str, str] = {}
    if profile_result:
        profile_meta = parse_key_values(profile_result.output)
        workflow = profile_meta.get("workflow", "")

    if results and results[-1].exit_code == 0 and workflow:
        for name, command in workflow_specific_steps(args.workspace, args.task, workflow, args.followup_steps):
            result = run_step(name, command)
            results.append(result)
            if result.exit_code != 0:
                break

    payload = {
        "workspace": args.workspace,
        "task": args.task,
        "workflow": workflow or "(unknown)",
        "runtime_profile": profile_meta.get("runtime_profile", ""),
        "confidence": profile_meta.get("confidence", ""),
        "step_count": len(results),
        "ok": all(item.exit_code == 0 for item in results),
        "steps": [
            {
                "name": item.name,
                "command": item.command,
                "exit_code": item.exit_code,
                "duration_ms": item.duration_ms,
                "output": item.output,
            }
            for item in results
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    print("MENTOR_SCENARIO")
    print(f"workspace={payload['workspace']}")
    print(f"task={payload['task']}")
    print(f"workflow={payload['workflow']}")
    print(f"runtime_profile={payload['runtime_profile']}")
    print(f"confidence={payload['confidence']}")
    print(f"step_count={payload['step_count']}")
    print(f"ok={payload['ok']}")
    for idx, item in enumerate(results, start=1):
        print(f"SCENARIO_STEP_{idx}_NAME={item.name}")
        print(f"SCENARIO_STEP_{idx}_EXIT_CODE={item.exit_code}")
        print(f"SCENARIO_STEP_{idx}_DURATION_MS={item.duration_ms}")
        print(f"SCENARIO_STEP_{idx}_COMMAND={' '.join(item.command)}")
        print(f"SCENARIO_STEP_{idx}_OUTPUT<<EOF")
        print(item.output.rstrip() if args.show_full_output else summarize_output(item.output))
        print("EOF")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
