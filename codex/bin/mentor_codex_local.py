#!/usr/bin/env python3
"""Small orchestrator that sends structured tasks to codex-local via the audit chat."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_MODEL = "codex-local-plan-qwen14b"
DEFAULT_TITLE = "Codex audit log - OpenWebUI visible history"


def write_temp(text: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="codex-local-", suffix=".txt")
    try:
        handle.write(text)
        return handle.name
    finally:
        handle.close()


def repo_prefix(repo: str) -> str:
    return f"repo: {repo.strip()}"


def build_prompts(args: argparse.Namespace) -> tuple[str, str]:
    if args.mode == "ask":
        visible = args.prompt.strip()
        technical = f"{repo_prefix(args.repo)}\n{args.prompt.strip()}"
        return visible, technical

    if args.mode == "scan":
        visible = f"Projdi workspace {args.workspace} a stručně popiš strukturu, jazyky, manifesty a doporučené příkazy. Nic nespouštěj."
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_SCAN {args.workspace}"
        return visible, technical

    if args.mode == "action":
        action_labels = {
            "install": "Nainstaluj závislosti v pracovním prostoru a vrať stručný výsledek.",
            "test": "Spusť testy v pracovním prostoru a vrať stručný výsledek.",
            "build": "Spusť build pracovního prostoru a vrať stručný výsledek.",
            "lint": "Spusť lint pracovního prostoru a vrať stručný výsledek.",
        }
        visible = f"repo: {args.workspace}\n{action_labels[args.action]}"
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_ACTION {args.workspace} {args.action} --timeout {args.timeout}"
        if args.dry_run_action:
            technical += " --dry-run"
        return visible, technical

    if args.mode == "run":
        command = shlex.join(args.command)
        visible = f"repo: {args.workspace}\nSpusť příkaz v pracovním prostoru a vrať stručný výsledek:\n{command}"
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_RUN_WORKSPACE {args.workspace} --timeout {args.timeout} -- {command}"
        return visible, technical

    if args.mode == "deploy":
        visible = "repo: ai-stack\nPullni ai-stack a nasaď poslední změny. Po dokončení napiš stručný stav."
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_DEPLOY_STACK"
        return visible, technical

    if args.mode == "deploy-status":
        visible = "repo: ai-stack\nUkaž stručný stav posledního nasazení."
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_DEPLOY_STATUS"
        return visible, technical

    if args.mode == "create-repo":
        visible = f"Vytvoř nové repository {args.name} a připrav workspace."
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_CREATE_LOCAL_REPO {args.name}"
        if args.github:
            technical += " --github"
        if args.restart:
            technical += " --restart"
        return visible, technical

    raise SystemExit(f"Unsupported mode: {args.mode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send structured mentor tasks to codex-local via the OpenWebUI audit chat.",
        allow_abbrev=False,
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--repo", default="ai-stack", help="Controller repository for gateway admin commands")
    parser.add_argument("--status-interval", type=float, default=3.0)
    parser.add_argument("--no-live-status", action="store_true")
    parser.add_argument("--send-history", action="store_true")

    sub = parser.add_subparsers(dest="mode", required=True)

    ask = sub.add_parser("ask", help="Send a free-form prompt through the audit chat")
    ask.add_argument("repo")
    ask.add_argument("prompt")
    ask.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    scan = sub.add_parser("scan", help="Ask codex-local to scan a workspace")
    scan.add_argument("workspace")
    scan.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    action = sub.add_parser("action", help="Ask codex-local to run a broad workspace action")
    action.add_argument("workspace")
    action.add_argument("action", choices=["install", "test", "build", "lint"])
    action.add_argument("--timeout", type=int, default=1800)
    action.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")
    action.add_argument("--dry-run-action", action="store_true")

    run = sub.add_parser("run", help="Ask codex-local to run an explicit command in a workspace")
    run.add_argument("workspace")
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")
    run.add_argument("command", nargs=argparse.REMAINDER)

    deploy = sub.add_parser("deploy", help="Ask codex-local to deploy ai-stack")
    deploy.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")
    deploy.set_defaults()

    deploy_status = sub.add_parser("deploy-status", help="Ask codex-local for deploy status")
    deploy_status.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")
    deploy_status.set_defaults()

    create_repo = sub.add_parser("create-repo", help="Ask codex-local to create a repository/workspace")
    create_repo.add_argument("name")
    create_repo.add_argument("--github", action="store_true")
    create_repo.add_argument("--restart", action="store_true")
    create_repo.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "run":
        if args.command and args.command[0] == "--dry-run":
            args.dry_run = True
            args.command = args.command[1:]
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        if not args.command:
            raise SystemExit("run mode requires a command after --")

    visible, technical = build_prompts(args)
    if args.dry_run:
        print("VISIBLE_PROMPT")
        print(visible)
        print("\nTECHNICAL_PROMPT")
        print(technical)
        return 0

    script = Path(__file__).resolve().parent / "owui_chat_turn.py"
    visible_file = write_temp(visible + "\n")
    technical_file = write_temp(technical + "\n")
    cmd = [
        sys.executable,
        str(script),
        "--model",
        args.model,
        "--title",
        args.title,
        "--visible-prompt-file",
        visible_file,
        "--prompt-file",
        technical_file,
        "--status-interval",
        str(args.status_interval),
        "--quiet",
    ]
    if args.no_live_status:
        cmd.append("--no-live-status")
    if args.send_history:
        cmd.append("--send-history")

    proc = subprocess.run(cmd, text=True)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
