#!/usr/bin/env python3
"""Resolve and run common developer actions for a registered workspace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from workspace_scan import collect, load_workspace


DEFAULT_WORKSPACES_FILE = "codex/workspaces.json"
ALLOWED_ACTIONS = {"install", "test", "build", "lint"}


class ActionError(RuntimeError):
    pass


def command_available(command: list[str], root: Path) -> bool:
    if not command:
        return False
    head = command[0]
    if head.startswith("./"):
        return (root / head[2:]).exists()
    return shutil.which(head) is not None


def node_install_command(root: Path, manifest_set: set[str]) -> list[str] | None:
    if "pnpm-lock.yaml" in manifest_set and command_available(["pnpm"], root):
        return ["pnpm", "install", "--frozen-lockfile"]
    if "yarn.lock" in manifest_set and command_available(["yarn"], root):
        return ["yarn", "install", "--frozen-lockfile"]
    if "package.json" in manifest_set and command_available(["npm"], root):
        return ["npm", "install"]
    return None


def python_install_command(root: Path, manifest_set: set[str]) -> list[str] | None:
    python = [sys.executable, "-m", "pip"]
    if "requirements.txt" in manifest_set:
        return python + ["install", "-r", "requirements.txt"]
    if "pyproject.toml" in manifest_set:
        return python + ["install", "-e", "."]
    return None


def package_script_command(script_name: str, package_scripts: list[str]) -> list[str] | None:
    if script_name not in package_scripts:
        return None
    if script_name == "test":
        return ["npm", "test"]
    return ["npm", "run", script_name]


def python_test_command(root: Path, manifest_set: set[str]) -> list[str] | None:
    if not {"pyproject.toml", "requirements.txt"} & manifest_set:
        return None
    if not ((root / "tests").is_dir() or (root / "test").is_dir()):
        return None
    return [sys.executable, "-m", "pytest"]


def python_lint_command(root: Path) -> list[str] | None:
    if (root / "ruff.toml").is_file():
        return [sys.executable, "-m", "ruff", "check", "."]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        if "[tool.ruff" in text:
            return [sys.executable, "-m", "ruff", "check", "."]
    return None


def gradle_command(root: Path, task: str) -> list[str] | None:
    wrapper = root / "gradlew"
    if wrapper.is_file():
        return ["./gradlew", task]
    if command_available(["gradle"], root):
        return ["gradle", task]
    return None


def mvn_command(task: str) -> list[str] | None:
    if shutil.which("mvn") is None:
        return None
    return ["mvn", task]


def resolve_action(root: Path, action: str) -> tuple[list[str], str]:
    result = collect(root, 80)
    manifest_set = {Path(rel).name for rel in result["manifests"]}
    package_scripts = result["package_scripts"]
    checks: list[tuple[str, list[str] | None]]

    if action == "install":
        checks = [
            ("node", node_install_command(root, manifest_set)),
            ("python", python_install_command(root, manifest_set)),
            ("rust", ["cargo", "fetch"] if "Cargo.toml" in manifest_set and command_available(["cargo"], root) else None),
            ("go", ["go", "mod", "download"] if "go.mod" in manifest_set and command_available(["go"], root) else None),
            ("maven", mvn_command("dependency:resolve") if "pom.xml" in manifest_set else None),
            ("gradle", gradle_command(root, "dependencies") if {"build.gradle", "settings.gradle"} & manifest_set else None),
        ]
    elif action == "test":
        checks = [
            ("node", package_script_command("test", package_scripts)),
            ("python", python_test_command(root, manifest_set)),
            ("rust", ["cargo", "test"] if "Cargo.toml" in manifest_set and command_available(["cargo"], root) else None),
            ("go", ["go", "test", "./..."] if "go.mod" in manifest_set and command_available(["go"], root) else None),
            ("maven", mvn_command("test") if "pom.xml" in manifest_set else None),
            ("gradle", gradle_command(root, "test") if {"build.gradle", "settings.gradle"} & manifest_set else None),
            ("make", ["make", "test"] if "Makefile" in manifest_set and command_available(["make"], root) else None),
        ]
    elif action == "build":
        checks = [
            ("node", package_script_command("build", package_scripts)),
            ("rust", ["cargo", "build"] if "Cargo.toml" in manifest_set and command_available(["cargo"], root) else None),
            ("go", ["go", "build", "./..."] if "go.mod" in manifest_set and command_available(["go"], root) else None),
            ("maven", mvn_command("package") if "pom.xml" in manifest_set else None),
            ("gradle", gradle_command(root, "build") if {"build.gradle", "settings.gradle"} & manifest_set else None),
            ("cmake", ["cmake", "-S", ".", "-B", "build"] if "CMakeLists.txt" in manifest_set and command_available(["cmake"], root) else None),
            ("make", ["make"] if "Makefile" in manifest_set and command_available(["make"], root) else None),
        ]
    elif action == "lint":
        checks = [
            ("node", package_script_command("lint", package_scripts)),
            ("python", python_lint_command(root)),
        ]
    else:
        raise ActionError(f"Unsupported action: {action}")

    for label, command in checks:
        if command and command_available(command, root):
            return command, label
    raise ActionError(
        f"No supported command found for action {action!r}. "
        f"Detected manifests: {', '.join(sorted(manifest_set)) or '(none)'}; "
        f"package scripts: {', '.join(package_scripts) or '(none)'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve and run a common developer action in a workspace.")
    parser.add_argument("action", choices=sorted(ALLOWED_ACTIONS))
    parser.add_argument("--root", help="Repository root to act in")
    parser.add_argument("--workspace", help="Workspace name from codex/workspaces.json")
    parser.add_argument("--workspaces-file", default=DEFAULT_WORKSPACES_FILE)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env", action="append", default=[])
    args = parser.parse_args()

    if bool(args.root) == bool(args.workspace):
        raise SystemExit("Use exactly one of --root or --workspace")

    root = Path(args.root) if args.root else load_workspace(Path(args.workspaces_file), args.workspace)
    env = os.environ.copy()
    for item in args.env:
        if "=" not in item:
            raise SystemExit(f"Invalid --env value: {item!r}")
        key, value = item.split("=", 1)
        env[key] = value

    started = time.time()
    try:
        command, resolved_from = resolve_action(root, args.action)
        if args.dry_run:
            result = {
                "ok": True,
                "planned_only": True,
                "action": args.action,
                "root": str(root),
                "command": command,
                "resolved_from": resolved_from,
                "duration_ms": int((time.time() - started) * 1000),
                "output": "",
            }
        else:
            proc = subprocess.run(
                command,
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
                env=env,
            )
            result = {
                "ok": proc.returncode == 0,
                "planned_only": False,
                "action": args.action,
                "root": str(root),
                "command": command,
                "resolved_from": resolved_from,
                "duration_ms": int((time.time() - started) * 1000),
                "exit_code": proc.returncode,
                "output": proc.stdout,
            }
    except subprocess.TimeoutExpired as exc:
        result = {
            "ok": False,
            "planned_only": False,
            "action": args.action,
            "root": str(root),
            "command": exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)],
            "duration_ms": int((time.time() - started) * 1000),
            "timeout": args.timeout,
            "output": (exc.stdout or "") + (exc.stderr or ""),
            "error": "timeout",
        }
    except ActionError as exc:
        result = {
            "ok": False,
            "planned_only": True,
            "action": args.action,
            "root": str(root),
            "duration_ms": int((time.time() - started) * 1000),
            "error": "unsupported",
            "output": str(exc),
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "WORKSPACE_ACTION_OK" if result["ok"] else "WORKSPACE_ACTION_FAILED"
        print(status)
        print(f"action={result['action']}")
        print(f"root={result['root']}")
        if "resolved_from" in result:
            print(f"resolved_from={result['resolved_from']}")
        if "command" in result:
            print(f"command={' '.join(result['command'])}")
        print(f"planned_only={result.get('planned_only', False)}")
        print(f"duration_ms={result['duration_ms']}")
        if "exit_code" in result:
            print(f"exit_code={result['exit_code']}")
        if "timeout" in result:
            print(f"timeout={result['timeout']}")
        if "error" in result:
            print(f"error={result['error']}")
        print("output:")
        print(str(result.get("output", "")).rstrip())

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
