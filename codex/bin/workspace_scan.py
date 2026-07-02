#!/usr/bin/env python3
"""Read-only project shape scanner for registered codex-local workspaces."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_WORKSPACES_FILE = "codex/workspaces.json"
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "target", ".next", "__pycache__", "logs"}
IGNORE_NAMES = {".env"}
MANIFESTS = [
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "CMakeLists.txt",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
]


class ScanError(RuntimeError):
    pass


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
        raise ScanError(f"Unknown workspace {workspace!r}. Allowed: {allowed}")
    path = Path(workspaces[workspace].get("path", ""))
    if not path:
        raise ScanError(f"Workspace {workspace!r} has no path")
    return resolve_workspace_path(path)


def run_git(root: Path, args: list[str], timeout: int = 15) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def first_existing(root: Path, names: list[str]) -> list[str]:
    return [name for name in names if (root / name).exists()]


def find_manifests(root: Path, max_depth: int = 3) -> list[str]:
    names = set(MANIFESTS)
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name not in names:
            continue
        rel = path.relative_to(root)
        if len(rel.parts) > max_depth:
            continue
        if any(part in IGNORE_DIRS for part in rel.parts[:-1]):
            continue
        found.append(rel.as_posix())
    return sorted(found)


def manifest_names(manifests: list[str]) -> set[str]:
    return {Path(rel).name for rel in manifests}


def is_ignored_top_level(name: str) -> bool:
    return name in IGNORE_DIRS or name in IGNORE_NAMES or ".bak-" in name


def top_level(root: Path, limit: int) -> list[str]:
    items = []
    for path in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if is_ignored_top_level(path.name):
            continue
        prefix = "[d]" if path.is_dir() else "[f]"
        items.append(f"{prefix} {path.name}")
        if len(items) >= limit:
            break
    return items


def safe_package_script_names(root: Path) -> list[str]:
    package = root / "package.json"
    if not package.is_file() or package.stat().st_size > 512_000:
        return []
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except Exception:
        return []
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        return []
    return sorted(str(name) for name in scripts if isinstance(name, str))[:80]


def detect_languages(root: Path, manifests: list[str]) -> list[str]:
    languages = set()
    names = manifest_names(manifests)
    suffixes = {p.suffix.lower() for p in root.rglob("*") if p.is_file() and len(p.parts) < 8}
    if "package.json" in names or {".js", ".jsx", ".ts", ".tsx"} & suffixes:
        languages.add("javascript/typescript")
    if {"pyproject.toml", "requirements.txt"} & names or ".py" in suffixes:
        languages.add("python")
    if "Cargo.toml" in names or ".rs" in suffixes:
        languages.add("rust")
    if "go.mod" in names or ".go" in suffixes:
        languages.add("go")
    if {"pom.xml", "build.gradle", "settings.gradle"} & names or {".java", ".kt"} & suffixes:
        languages.add("jvm")
    if "CMakeLists.txt" in names or {".c", ".cc", ".cpp", ".h", ".hpp"} & suffixes:
        languages.add("c/cpp")
    if {"Dockerfile", "docker-compose.yml", "compose.yml"} & names:
        languages.add("container/docker")
    return sorted(languages)


def candidate_commands(root: Path, manifests: list[str], package_scripts: list[str]) -> list[str]:
    commands = []
    manifest_set = manifest_names(manifests)
    if "package.json" in manifest_set:
        for name in ["test", "build", "lint", "typecheck", "dev"]:
            if name in package_scripts:
                commands.append(f"npm run {name}" if name not in {"test"} else "npm test")
    if {"pyproject.toml", "requirements.txt"} & manifest_set:
        if (root / "tests").is_dir() or (root / "test").is_dir():
            commands.append("python -m pytest")
    if "Cargo.toml" in manifest_set:
        commands += ["cargo test", "cargo build"]
    if "go.mod" in manifest_set:
        commands += ["go test ./...", "go build ./..."]
    if "pom.xml" in manifest_set:
        commands += ["mvn test", "mvn package"]
    if "build.gradle" in manifest_set or "settings.gradle" in manifest_set:
        commands += ["./gradlew test", "./gradlew build"]
    if "CMakeLists.txt" in manifest_set:
        commands += ["cmake -S . -B build", "cmake --build build"]
    if "Makefile" in manifest_set:
        commands += ["make", "make test"]
    dockerfiles = [rel for rel in manifests if Path(rel).name == "Dockerfile"]
    for rel in dockerfiles[:3]:
        context = Path(rel).parent.as_posix()
        commands.append("docker build ." if context == "." else f"docker build {context}")
    compose_files = [rel for rel in manifests if Path(rel).name in {"docker-compose.yml", "compose.yml"}]
    for rel in compose_files[:3]:
        commands.append("docker compose config" if "/" not in rel else f"docker compose -f {rel} config")
    seen = set()
    unique = []
    for command in commands:
        if command not in seen:
            seen.add(command)
            unique.append(command)
    return unique[:80]


def collect(root: Path, max_items: int) -> dict[str, Any]:
    if not root.exists():
        raise ScanError(f"Workspace path does not exist: {root}")
    manifests = find_manifests(root)
    package_scripts = safe_package_script_names(root)
    branch = run_git(root, ["branch", "--show-current"]) if (root / ".git").exists() else ""
    status = run_git(root, ["status", "--short", "--branch"]) if (root / ".git").exists() else "not a git repo"
    return {
        "root": str(root),
        "branch": branch or "(unknown)",
        "manifests": manifests,
        "languages": detect_languages(root, manifests),
        "package_scripts": package_scripts,
        "candidate_commands": candidate_commands(root, manifests, package_scripts),
        "top_level": top_level(root, max_items),
        "git_status": status,
    }


def text_report(result: dict[str, Any]) -> str:
    lines = [
        "WORKSPACE_SCAN",
        f"root={result['root']}",
        f"branch={result['branch']}",
        "languages:",
        *(result["languages"] or ["(unknown)"]),
        "manifests:",
        *(result["manifests"] or ["(none)"]),
        "package_script_names:",
        *(result["package_scripts"] or ["(none)"]),
        "candidate_commands_not_executed:",
        *(result["candidate_commands"] or ["(none)"]),
        "top_level:",
        *(result["top_level"] or ["(empty)"]),
        "git_status:",
        result["git_status"],
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only project shape scanner for a codex-local workspace.")
    parser.add_argument("--root", help="Repository root to scan")
    parser.add_argument("--workspace", help="Workspace name from codex/workspaces.json")
    parser.add_argument("--workspaces-file", default=DEFAULT_WORKSPACES_FILE)
    parser.add_argument("--max-items", type=int, default=80)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if bool(args.root) == bool(args.workspace):
        raise SystemExit("Use exactly one of --root or --workspace")
    root = Path(args.root) if args.root else load_workspace(Path(args.workspaces_file), args.workspace)
    try:
        result = collect(root, max(1, args.max_items))
    except (ScanError, OSError, subprocess.TimeoutExpired) as exc:
        result = {
            "root": str(root),
            "branch": "(unknown)",
            "manifests": [],
            "languages": [],
            "package_scripts": [],
            "candidate_commands": [],
            "top_level": [],
            "git_status": f"SCAN_FAILED {type(exc).__name__}: {exc}",
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(text_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
