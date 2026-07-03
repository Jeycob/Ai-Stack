#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from container_runner_guard import inspect_container_state


DEFAULT_ROOT = Path(os.getenv("AI_STACK_ROOT", "/mnt/c/Repositories/ai-stack"))
WORKSPACES_FILE = Path(os.getenv("CODEX_WORKSPACES_FILE", str(DEFAULT_ROOT / "codex/workspaces.json")))


def load_registry():
    data = json.loads(WORKSPACES_FILE.read_text(encoding="utf-8"))
    return data.get("workspaces", {})


def parse_env(items):
    env = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --env value: {item!r}")
        key, value = item.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
            raise SystemExit(f"Invalid env key: {key!r}")
        env[key] = value
    return env

def container_name(workspace):
    return f"codex-opencode-{workspace}"


def docker_exec_command(workspace, command, env):
    cmd = ["docker", "exec", "--workdir", "/workspace"]
    for key, value in sorted(env.items()):
        cmd.extend(["-e", f"{key}={value}"])
    cmd.append(container_name(workspace))
    cmd.extend(command)
    return cmd


def run_checked_command(workspace, cwd, command, timeout, env, runner):
    started = time.time()
    if runner == "host":
        proc = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env={**os.environ.copy(), **env},
        )
        executed = command
        display_cwd = str(cwd)
    elif runner == "container":
        state = inspect_container_state(workspace)
        if not state.get("ok"):
            return {
                "ok": False,
                "workspace": workspace,
                "runner": runner,
                "container": container_name(workspace),
                "cwd": "/workspace",
                "host_cwd": str(cwd),
                "command": command,
                "executed_command": docker_exec_command(workspace, command, env),
                "exit_code": None,
                "duration_ms": int((time.time() - started) * 1000),
                "output": str(state.get("diagnostic_output") or ""),
                "error": state.get("error"),
                "marker": state.get("marker"),
                "recovery": state.get("recovery"),
            }
        executed = docker_exec_command(workspace, command, env)
        proc = subprocess.run(
            executed,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        display_cwd = "/workspace"
    else:
        raise ValueError(f"Unsupported runner: {runner}")

    return {
        "ok": proc.returncode == 0,
        "workspace": workspace,
        "runner": runner,
        "container": container_name(workspace) if runner == "container" else "",
        "cwd": display_cwd,
        "host_cwd": str(cwd),
        "command": command,
        "executed_command": executed,
        "exit_code": proc.returncode,
        "duration_ms": int((time.time() - started) * 1000),
        "output": proc.stdout,
    }


def main():
    parser = argparse.ArgumentParser(description="Run a checked command inside a registered workspace.")
    parser.add_argument("workspace")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE environment override")
    parser.add_argument(
        "--runner",
        choices=["container", "host"],
        default=os.getenv("CODEX_WORKSPACE_RUNNER", "container"),
        help="Execution boundary. Default is container so commands run inside codex-opencode-<workspace>.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", args.workspace):
        raise SystemExit("Invalid workspace name")

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("Usage: run_check.py <workspace> -- <command> [args...]")

    workspaces = load_registry()
    if args.workspace not in workspaces:
        raise SystemExit(f"Unknown workspace: {args.workspace}")

    cwd = Path(workspaces[args.workspace]["path"])
    if not cwd.is_dir():
        raise SystemExit(f"Workspace path does not exist: {cwd}")

    env = parse_env(args.env)

    started = time.time()
    try:
        result = run_checked_command(args.workspace, cwd, command, args.timeout, env, args.runner)
    except subprocess.TimeoutExpired as exc:
        result = {
            "ok": False,
            "workspace": args.workspace,
            "runner": args.runner,
            "container": container_name(args.workspace) if args.runner == "container" else "",
            "cwd": "/workspace" if args.runner == "container" else str(cwd),
            "host_cwd": str(cwd),
            "command": command,
            "executed_command": exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)],
            "exit_code": None,
            "duration_ms": int((time.time() - started) * 1000),
            "timeout": args.timeout,
            "output": (exc.stdout or "") + (exc.stderr or ""),
            "error": "timeout",
        }
    except FileNotFoundError as exc:
        result = {
            "ok": False,
            "workspace": args.workspace,
            "runner": args.runner,
            "container": container_name(args.workspace) if args.runner == "container" else "",
            "cwd": "/workspace" if args.runner == "container" else str(cwd),
            "host_cwd": str(cwd),
            "command": command,
            "executed_command": docker_exec_command(args.workspace, command, env) if args.runner == "container" else command,
            "exit_code": None,
            "duration_ms": int((time.time() - started) * 1000),
            "output": str(exc),
            "error": "runner_unavailable",
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "RUN_CHECK_OK" if result["ok"] else "RUN_CHECK_FAILED"
        print(status)
        print(f"workspace={result['workspace']}")
        print(f"runner={result['runner']}")
        if result.get("container"):
            print(f"container={result['container']}")
        if result.get("marker"):
            print(f"marker={result['marker']}")
        print(f"cwd={result['cwd']}")
        print(f"command={' '.join(result['command'])}")
        print(f"executed_command={' '.join(result['executed_command'])}")
        print(f"duration_ms={result['duration_ms']}")
        if result["exit_code"] is not None:
            print(f"exit_code={result['exit_code']}")
        if "timeout" in result:
            print(f"timeout={result['timeout']}")
        if "error" in result:
            print(f"error={result['error']}")
        if result.get("recovery"):
            print(f"recovery={result['recovery']}")
        print("output:")
        print(result["output"].rstrip())

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
