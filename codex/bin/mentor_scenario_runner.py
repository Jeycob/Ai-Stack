#!/usr/bin/env python3
"""Run a cheap local end-to-end mentor scenario over codex-local helper flows."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Iterable
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


def read_task_file(path: str) -> list[str]:
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def read_stdin_tasks() -> list[str]:
    if sys.stdin.isatty():
        return []
    return [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]


def collect_tasks(args: argparse.Namespace) -> list[str]:
    tasks: list[str] = []
    if args.primary_task:
        tasks.append(args.primary_task.strip())
    for task in args.tasks:
        if task and task.strip():
            tasks.append(task.strip())
    for path in args.task_files:
        tasks.extend(read_task_file(path))
    tasks.extend(read_stdin_tasks())
    return tasks


def task_args(tasks: Iterable[str], flag: str = "--task") -> list[str]:
    result: list[str] = []
    for task in tasks:
        result.extend([flag, task])
    return result


def single_task_steps(workspace: str, task: str, followup_steps: int) -> list[tuple[str, list[str]]]:
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


def multi_task_steps(workspace: str, tasks: list[str]) -> list[tuple[str, list[str]]]:
    backlog_args = task_args(tasks, "--task")
    shared = task_args(tasks, "--tasks")
    return [
        ("backlog", [*MENTOR, "backlog", workspace, *backlog_args]),
        ("top", [*MENTOR, "top", workspace, *shared]),
        ("dispatch", [*MENTOR, "dispatch", workspace, *shared, "--recommend-only"]),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a cheap local end-to-end mentor scenario across codex-local helper flows.")
    parser.add_argument("workspace")
    parser.add_argument("primary_task", nargs="?")
    parser.add_argument("--task", dest="tasks", action="append", default=[])
    parser.add_argument("--task-file", dest="task_files", action="append", default=[])
    parser.add_argument("--followup-steps", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-full-output", action="store_true")
    args = parser.parse_args()

    tasks = collect_tasks(args)
    if not tasks:
        raise SystemExit("mentor_scenario_runner requires at least one task via positional task, --task, --task-file, or stdin")

    results: list[StepResult] = []
    workflow = ""
    profile_meta: dict[str, str] = {}
    scenario_kind = "multi-task" if len(tasks) > 1 else "single-task"

    if len(tasks) == 1:
        task = tasks[0]
        for name, command in single_task_steps(args.workspace, task, args.followup_steps):
            result = run_step(name, command)
            results.append(result)
            if result.exit_code != 0:
                break
        profile_result = next((item for item in results if item.name == "profile"), None)
        if profile_result:
            profile_meta = parse_key_values(profile_result.output)
            workflow = profile_meta.get("workflow", "")

        if results and results[-1].exit_code == 0 and workflow:
            for name, command in workflow_specific_steps(args.workspace, task, workflow, args.followup_steps):
                result = run_step(name, command)
                results.append(result)
                if result.exit_code != 0:
                    break
    else:
        for name, command in multi_task_steps(args.workspace, tasks):
            result = run_step(name, command)
            results.append(result)
            if result.exit_code != 0:
                break
        top_result = next((item for item in results if item.name == "top"), None)
        if top_result:
            profile_meta = parse_key_values(top_result.output)
            workflow = profile_meta.get("mentor_top_workflow", "")

    payload = {
        "workspace": args.workspace,
        "task": tasks[0],
        "tasks": tasks,
        "task_count": len(tasks),
        "scenario_kind": scenario_kind,
        "workflow": workflow or "(unknown)",
        "runtime_profile": profile_meta.get("runtime_profile", profile_meta.get("mentor_top_runtime_profile", "")),
        "confidence": profile_meta.get("confidence", profile_meta.get("mentor_top_confidence", "")),
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
    print(f"task_count={payload['task_count']}")
    print(f"scenario_kind={payload['scenario_kind']}")
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
