#!/usr/bin/env python3
"""Resolve and run common developer actions for a registered workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from workspace_scan import collect, load_workspace


DEFAULT_WORKSPACES_FILE = "codex/workspaces.json"
ALLOWED_ACTIONS = {"install", "test", "build", "lint", "verify", "smoke"}
SMOKE_READY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"http://127\.0\.0\.1",
        r"http://localhost",
        r"listening on",
        r"running on",
        r"running at",
        r"ready in",
        r"ready on",
        r"compiled successfully",
        r"development server at",
        r"uvicorn running on",
    )
]


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


def smoke_package_script_command(package_scripts: list[str]) -> list[str] | None:
    if "smoke" in package_scripts:
        return ["npm", "run", "smoke"]
    if "dev" in package_scripts:
        return ["npm", "run", "dev", "--", "--host", "127.0.0.1"]
    if "start" in package_scripts:
        return ["npm", "run", "start", "--", "--host", "127.0.0.1"]
    return None


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


def read_small_text(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 512_000:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def fastapi_smoke_command(root: Path) -> list[str] | None:
    for stem in ("main", "app"):
        candidate = root / f"{stem}.py"
        text = read_small_text(candidate)
        if "FastAPI(" in text and re.search(r"(?m)^\s*app\s*=", text):
            return [sys.executable, "-m", "uvicorn", f"{stem}:app", "--host", "127.0.0.1", "--port", "8000"]
    nested = root / "app/main.py"
    text = read_small_text(nested)
    if "FastAPI(" in text and re.search(r"(?m)^\s*app\s*=", text):
        return [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
    return None


def flask_smoke_command(root: Path) -> list[str] | None:
    for stem in ("app", "main"):
        candidate = root / f"{stem}.py"
        text = read_small_text(candidate)
        if "Flask(" in text:
            return [sys.executable, "-m", "flask", "--app", stem, "run", "--host", "127.0.0.1", "--port", "5000"]
    return None


def django_smoke_command(root: Path) -> list[str] | None:
    manage = root / "manage.py"
    if manage.is_file():
        return [sys.executable, "manage.py", "runserver", "127.0.0.1:8000"]
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


def resolve_smoke(root: Path, manifest_set: set[str], package_scripts: list[str]) -> tuple[list[str], str]:
    checks = [
        ("node-smoke-script", smoke_package_script_command(package_scripts) if "package.json" in manifest_set and command_available(["npm"], root) else None),
        ("django-runserver", django_smoke_command(root)),
        ("fastapi-uvicorn", fastapi_smoke_command(root)),
        ("flask-run", flask_smoke_command(root)),
    ]
    for label, command in checks:
        if command and command_available(command, root):
            return command, label
    raise ActionError(
        "No supported smoke command found. "
        "Expected one of: package.json script smoke/dev/start, manage.py for Django, or main.py/app.py with FastAPI or Flask app."
    )


def smoke_ready(output: str) -> bool:
    return any(pattern.search(output) for pattern in SMOKE_READY_PATTERNS)


def normalize_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


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
    elif action == "smoke":
        return resolve_smoke(root, manifest_set, package_scripts)
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


def verify_plan(root: Path) -> list[dict[str, object]]:
    steps = []
    for action in ["lint", "test", "build"]:
        try:
            command, resolved_from = resolve_action(root, action)
            steps.append(
                {
                    "action": action,
                    "command": command,
                    "resolved_from": resolved_from,
                    "supported": True,
                }
            )
        except ActionError as exc:
            steps.append(
                {
                    "action": action,
                    "supported": False,
                    "reason": str(exc),
                }
            )
    return steps


def run_verify(root: Path, timeout: int, env: dict[str, str], dry_run: bool) -> dict[str, object]:
    started = time.time()
    steps = verify_plan(root)
    runnable = [step for step in steps if step.get("supported")]
    if not runnable:
        return {
            "ok": False,
            "planned_only": True,
            "action": "verify",
            "root": str(root),
            "duration_ms": int((time.time() - started) * 1000),
            "error": "unsupported",
            "verify_steps": steps,
            "output": "No supported verify steps were found for this workspace.",
        }

    if dry_run:
        return {
            "ok": True,
            "planned_only": True,
            "action": "verify",
            "root": str(root),
            "duration_ms": int((time.time() - started) * 1000),
            "verify_steps": steps,
            "output": "",
        }

    deadline = time.time() + timeout
    executed_steps = []
    ok = True
    for step in steps:
        if not step.get("supported"):
            executed_steps.append(step)
            continue
        remaining = max(1, int(deadline - time.time()))
        if remaining <= 0:
            executed_steps.append(
                {
                    **step,
                    "ok": False,
                    "exit_code": None,
                    "error": "timeout_budget_exhausted",
                    "output": "",
                }
            )
            ok = False
            break
        try:
            proc = subprocess.run(
                step["command"],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=remaining,
                env=env,
            )
            step_result = {
                **step,
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "output": proc.stdout,
            }
            executed_steps.append(step_result)
            if proc.returncode != 0:
                ok = False
                break
        except subprocess.TimeoutExpired as exc:
            executed_steps.append(
                {
                    **step,
                    "ok": False,
                    "exit_code": None,
                    "error": "timeout",
                    "output": (exc.stdout or "") + (exc.stderr or ""),
                }
            )
            ok = False
            break

    summary_lines = []
    for step in executed_steps:
        if not step.get("supported"):
            summary_lines.append(f"{step['action']}: skipped")
        elif step.get("ok"):
            summary_lines.append(f"{step['action']}: ok")
        else:
            reason = step.get("error") or f"exit {step.get('exit_code')}"
            summary_lines.append(f"{step['action']}: failed ({reason})")

    return {
        "ok": ok,
        "planned_only": False,
        "action": "verify",
        "root": str(root),
        "duration_ms": int((time.time() - started) * 1000),
        "verify_steps": executed_steps,
        "output": "\n".join(summary_lines),
    }


def run_smoke(root: Path, timeout: int, env: dict[str, str], dry_run: bool) -> dict[str, object]:
    started = time.time()
    result = collect(root, 80)
    manifest_set = {Path(rel).name for rel in result["manifests"]}
    package_scripts = result["package_scripts"]
    command, resolved_from = resolve_smoke(root, manifest_set, package_scripts)
    smoke_window = max(8, min(timeout, 25))

    if dry_run:
        return {
            "ok": True,
            "planned_only": True,
            "action": "smoke",
            "root": str(root),
            "command": command,
            "resolved_from": resolved_from,
            "smoke_window_s": smoke_window,
            "duration_ms": int((time.time() - started) * 1000),
            "output": "",
        }

    smoke_env = env.copy()
    smoke_env.setdefault("HOST", "127.0.0.1")
    smoke_env.setdefault("CI", "1")
    smoke_env.setdefault("PYTHONUNBUFFERED", "1")

    wrapped_command = ["timeout", "--signal=TERM", f"{smoke_window}s", *command]
    proc = subprocess.run(
        wrapped_command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(timeout + 5, smoke_window + 5),
        env=smoke_env,
    )
    output = normalize_output(proc.stdout)
    startup_detected = smoke_ready(output)
    ok = startup_detected and proc.returncode in {0, 124, 143}

    return {
        "ok": ok,
        "planned_only": False,
        "action": "smoke",
        "root": str(root),
        "command": command,
        "resolved_from": resolved_from,
        "wrapped_command": wrapped_command,
        "smoke_window_s": smoke_window,
        "startup_detected": startup_detected,
        "timed_window_exit": proc.returncode == 124,
        "duration_ms": int((time.time() - started) * 1000),
        "exit_code": proc.returncode,
        "output": output,
    }


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
        if args.action == "verify":
            result = run_verify(root, args.timeout, env, args.dry_run)
        elif args.action == "smoke":
            result = run_smoke(root, args.timeout, env, args.dry_run)
        else:
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
            "output": normalize_output(exc.stdout) + normalize_output(exc.stderr),
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
        if "wrapped_command" in result:
            print(f"wrapped_command={' '.join(result['wrapped_command'])}")
        if "verify_steps" in result:
            print("verify_steps:")
            for step in result["verify_steps"]:
                if not step.get("supported"):
                    print(f"- {step['action']}: skipped")
                    continue
                line = f"- {step['action']}: "
                if step.get("ok") is True:
                    line += "ok"
                elif step.get("ok") is False:
                    line += f"failed ({step.get('error') or step.get('exit_code')})"
                else:
                    line += "planned"
                if step.get("command"):
                    line += f" command={' '.join(step['command'])}"
                print(line)
        print(f"planned_only={result.get('planned_only', False)}")
        print(f"duration_ms={result['duration_ms']}")
        if "smoke_window_s" in result:
            print(f"smoke_window_s={result['smoke_window_s']}")
        if "startup_detected" in result:
            print(f"startup_detected={result['startup_detected']}")
        if "timed_window_exit" in result:
            print(f"timed_window_exit={result['timed_window_exit']}")
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
