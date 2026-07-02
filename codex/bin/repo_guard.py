#!/usr/bin/env python3
"""Read-only guardrail checks for registered codex-local workspaces."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_WORKSPACES_FILE = "codex/workspaces.json"
SENSITIVE_NAMES = {".env", "id_rsa", "id_ed25519", "known_hosts"}
SENSITIVE_NAME_PREFIXES = (".env",)
SENSITIVE_NAME_MARKERS = ("id_rsa", "id_ed25519", "private_key")
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
RUNTIME_PREFIXES = ("codex/state/", "codex/audit/", "logs/")


class GuardError(RuntimeError):
    pass


def run_git(root: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def resolve_workspace_path(path: Path) -> Path:
    if path.exists():
        return path
    raw = path.as_posix()
    mappings = (
        ("/mnt/c/Repositories/", "/data/repositories/"),
        ("C:/Repositories/", "/data/repositories/"),
        ("C:\\Repositories\\", "/data/repositories/"),
    )
    for prefix, replacement in mappings:
        if raw.startswith(prefix):
            candidate = Path(replacement + raw[len(prefix):].replace("\\", "/"))
            if candidate.exists():
                return candidate
    return path


def load_workspace(workspaces_file: Path, workspace: str) -> Path:
    data = json.loads(workspaces_file.read_text(encoding="utf-8"))
    workspaces = data.get("workspaces") or {}
    if workspace not in workspaces:
        allowed = ", ".join(sorted(workspaces))
        raise GuardError(f"Unknown workspace {workspace!r}. Allowed: {allowed}")
    path = Path(workspaces[workspace].get("path", ""))
    if not path:
        raise GuardError(f"Workspace {workspace!r} has no path")
    return resolve_workspace_path(path)


def status_paths(status_lines: list[str]) -> list[str]:
    paths: list[str] = []
    for line in status_lines:
        raw = line[3:] if len(line) > 3 else line
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        paths.append(raw.strip())
    return paths


def is_sensitive_path(rel: str) -> bool:
    lower = rel.lower()
    name = lower.rsplit("/", 1)[-1]
    return (
        lower in SENSITIVE_NAMES
        or name in SENSITIVE_NAMES
        or any(name.startswith(prefix) for prefix in SENSITIVE_NAME_PREFIXES)
        or any(marker in name for marker in SENSITIVE_NAME_MARKERS)
        or any(lower.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)
        or lower.endswith("_rsa")
        or lower.endswith("_ed25519")
        or lower.startswith("codex/state/")
        or lower.startswith("codex/audit/")
    )


def is_runtime_path(rel: str) -> bool:
    lower = rel.lower()
    return (
        lower.startswith(RUNTIME_PREFIXES)
        or "__pycache__/" in lower
        or lower.endswith(".pyc")
        or ".bak-" in lower
        or rel == ".env"
    )


def short_list(items: list[str], max_items: int) -> tuple[list[str], int]:
    if len(items) <= max_items:
        return items, 0
    return items[:max_items], len(items) - max_items


def collect(root: Path, desired_branch: str | None, require_clean: bool, max_paths: int) -> dict[str, Any]:
    if not root.exists():
        raise GuardError(f"Repository path does not exist: {root}")
    if not (root / ".git").exists():
        raise GuardError(f"Path is not a git repository: {root}")

    branch_proc = run_git(root, ["branch", "--show-current"], timeout=20)
    if branch_proc.returncode != 0:
        raise GuardError("git branch failed:\n" + branch_proc.stdout)
    branch = branch_proc.stdout.strip() or "(detached)"

    status_proc = run_git(root, ["status", "--porcelain=v1", "-uall"], timeout=30)
    if status_proc.returncode != 0:
        raise GuardError("git status failed:\n" + status_proc.stdout)
    status_lines = [line for line in status_proc.stdout.splitlines() if line.strip()]
    paths = status_paths(status_lines)

    sensitive = sorted({p for p in paths if is_sensitive_path(p)})
    runtime = sorted({p for p in paths if is_runtime_path(p)})
    regular_dirty = sorted({p for p in paths if p not in sensitive and p not in runtime})

    remote_proc = run_git(root, ["remote", "-v"], timeout=20)
    remotes = remote_proc.stdout.strip().splitlines() if remote_proc.returncode == 0 else []

    failures: list[str] = []
    warnings: list[str] = []
    if desired_branch and branch != desired_branch:
        failures.append(f"branch_mismatch expected={desired_branch} actual={branch}")
    if sensitive:
        failures.append("sensitive_paths_present")
    if require_clean and status_lines:
        failures.append("working_tree_not_clean")
    elif status_lines:
        warnings.append("working_tree_dirty")
    if runtime:
        warnings.append("runtime_or_ignored_paths_present")

    shown_status, hidden_status = short_list(status_lines, max_paths)
    shown_sensitive, hidden_sensitive = short_list(sensitive, max_paths)
    shown_runtime, hidden_runtime = short_list(runtime, max_paths)
    shown_regular, hidden_regular = short_list(regular_dirty, max_paths)

    return {
        "ok": not failures,
        "root": str(root),
        "branch": branch,
        "desired_branch": desired_branch,
        "dirty_count": len(status_lines),
        "remote_count": len(remotes),
        "failures": failures,
        "warnings": warnings,
        "status": shown_status,
        "status_truncated": hidden_status,
        "sensitive_paths": shown_sensitive,
        "sensitive_truncated": hidden_sensitive,
        "runtime_paths": shown_runtime,
        "runtime_truncated": hidden_runtime,
        "regular_dirty_paths": shown_regular,
        "regular_dirty_truncated": hidden_regular,
    }


def text_report(result: dict[str, Any]) -> str:
    status = "REPO_GUARD_OK" if result["ok"] else "REPO_GUARD_FAILED"
    lines = [
        status,
        f"root={result['root']}",
        f"branch={result['branch']}",
        f"desired_branch={result['desired_branch'] or '(not checked)'}",
        f"dirty_count={result['dirty_count']}",
        f"remote_count={result['remote_count']}",
        "failures:",
        *(result["failures"] or ["(none)"]),
        "warnings:",
        *(result["warnings"] or ["(none)"]),
        "sensitive_paths:",
        *(result["sensitive_paths"] or ["(none)"]),
        "runtime_paths:",
        *(result["runtime_paths"] or ["(none)"]),
        "regular_dirty_paths:",
        *(result["regular_dirty_paths"] or ["(none)"]),
        "status:",
        *(result["status"] or ["(clean)"]),
    ]
    for key, label in [
        ("sensitive_truncated", "sensitive_paths_truncated"),
        ("runtime_truncated", "runtime_paths_truncated"),
        ("regular_dirty_truncated", "regular_dirty_paths_truncated"),
        ("status_truncated", "status_truncated"),
    ]:
        if result[key]:
            lines.append(f"{label}={result[key]}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only guardrail checks for a git workspace.")
    parser.add_argument("--root", help="Repository root to check")
    parser.add_argument("--workspace", help="Workspace name from codex/workspaces.json")
    parser.add_argument("--workspaces-file", default=DEFAULT_WORKSPACES_FILE)
    parser.add_argument("--branch", default="main", help="Expected branch; use empty string to skip")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--max-paths", type=int, default=80)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if bool(args.root) == bool(args.workspace):
        raise SystemExit("Use exactly one of --root or --workspace")
    root = Path(args.root) if args.root else load_workspace(Path(args.workspaces_file), args.workspace)
    desired_branch = args.branch or None

    try:
        result = collect(root, desired_branch, args.require_clean, max(1, args.max_paths))
    except (GuardError, subprocess.TimeoutExpired) as exc:
        result = {
            "ok": False,
            "root": str(root),
            "branch": "(unknown)",
            "desired_branch": desired_branch,
            "dirty_count": 0,
            "remote_count": 0,
            "failures": [str(exc)],
            "warnings": [],
            "status": [],
            "status_truncated": 0,
            "sensitive_paths": [],
            "sensitive_truncated": 0,
            "runtime_paths": [],
            "runtime_truncated": 0,
            "regular_dirty_paths": [],
            "regular_dirty_truncated": 0,
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(text_report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
