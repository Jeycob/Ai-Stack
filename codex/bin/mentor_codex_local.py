#!/usr/bin/env python3
"""Small orchestrator that sends structured tasks to codex-local via the audit chat."""

from __future__ import annotations

import argparse
import re
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
            "verify": "Ověř pracovní prostor jako senior developer: pokud dává smysl, proveď lint, test a build a vrať stručný audit výsledků.",
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

    if args.mode == "audit":
        visible = (
            f"repo: {args.workspace}\n"
            "Proveď technický audit workspace a potom navrhni jeden nejlepší další bezpečný krok. "
            "V téhle fázi nic needituj."
        )
        technical = (
            f"{repo_prefix(args.workspace)}\n"
            "Proveď technický audit na základě předchozích výsledků. "
            "Stručně shrň architekturu, rizika a navrhni právě jeden další krok. "
            "Nic nespouštěj a nic needituj."
        )
        return visible, technical

    raise SystemExit(f"Unsupported mode: {args.mode}")


def parse_next_action(text: str, allowed_actions: set[str]) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    action = ""
    reason = ""
    for line in lines:
        m = re.match(r"(?i)^NEXT_ACTION:\s*([A-Za-z_-]+)\s*$", line)
        if m:
            action = m.group(1).strip().lower()
            continue
        m = re.match(r"(?i)^REASON:\s*(.+?)\s*$", line)
        if m:
            reason = m.group(1).strip()
    if not action:
        raise ValueError("NEXT_ACTION line was not found in codex-local response")
    if action not in allowed_actions | {"none"}:
        raise ValueError(f"NEXT_ACTION {action!r} is not in the allowed action set")
    return action, reason


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


def invoke_turn(
    args: argparse.Namespace,
    visible: str,
    technical: str,
    send_history: bool = False,
    capture_output: bool = False,
) -> tuple[int, str]:
    if args.dry_run:
        print("VISIBLE_PROMPT")
        print(visible)
        print("\nTECHNICAL_PROMPT")
        print(technical)
        return 0, ""

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
    if args.send_history or send_history:
        cmd.append("--send-history")

    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE if capture_output else None, stderr=subprocess.STDOUT if capture_output else None)
    return proc.returncode, proc.stdout or ""


def run_audit_sequence(args: argparse.Namespace) -> int:
    scan_visible = f"repo: {args.workspace}\nNejdřív si technicky zmapuj workspace. Nic nespouštěj."
    scan_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_SCAN {args.workspace}"

    verify_visible = (
        f"repo: {args.workspace}\n"
        "Teď si připrav ověřovací plán projektu jako senior developer. Nic ještě nespouštěj, jen vyhodnoť verify kroky."
    )
    verify_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_ACTION {args.workspace} verify --timeout {args.timeout} --dry-run"

    summary_visible, summary_technical = build_prompts(args)

    steps = [
        (scan_visible, scan_technical, False),
        (verify_visible, verify_technical, True),
        (summary_visible, summary_technical, True),
    ]
    for visible, technical, send_history in steps:
        rc, _ = invoke_turn(args, visible, technical, send_history=send_history)
        if rc != 0:
            return rc
    return 0


def run_autopilot_sequence(args: argparse.Namespace) -> int:
    allowed_actions = {x.strip().lower() for x in args.allow_actions.split(",") if x.strip()}
    if not allowed_actions:
        raise SystemExit("--allow-actions must contain at least one action")
    invalid = sorted(allowed_actions - {"install", "test", "build", "lint"})
    if invalid:
        raise SystemExit(f"Unsupported actions in --allow-actions: {', '.join(invalid)}")

    scan_visible = f"repo: {args.workspace}\nNejdřív si technicky zmapuj workspace. Nic nespouštěj."
    scan_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_SCAN {args.workspace}"

    verify_visible = (
        f"repo: {args.workspace}\n"
        "Teď si připrav ověřovací plán projektu jako senior developer. Nic ještě nespouštěj, jen vyhodnoť verify kroky."
    )
    verify_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_ACTION {args.workspace} verify --timeout {args.timeout} --dry-run"

    chooser_visible = (
        f"repo: {args.workspace}\n"
        "Vyber právě jeden další bezpečný krok na základě předchozího auditu. "
        "Zůstaň v povolených akcích a zatím nic nespouštěj. "
        "Jde jen o doporučení dalšího capability kroku."
    )
    chooser_technical = (
        f"{repo_prefix(args.workspace)}\n"
        "Na základě celé dosavadní historie vyber právě jednu další bezpečnou capability akci. "
        f"Povolené akce: {', '.join(sorted(allowed_actions))}, none. "
        "Odpověz přesně ve dvou řádcích a ničím navíc:\n"
        "NEXT_ACTION: <action-or-none>\n"
        "REASON: <one sentence>"
    )

    execute_template = {
        "install": "Na základě auditu teď proveď instalaci závislostí a vrať stručný výsledek.",
        "test": "Na základě auditu teď spusť testy a vrať stručný výsledek.",
        "build": "Na základě auditu teď spusť build a vrať stručný výsledek.",
        "lint": "Na základě auditu teď spusť lint a vrať stručný výsledek.",
    }

    if args.dry_run:
        for visible, technical in [
            (scan_visible, scan_technical),
            (verify_visible, verify_technical),
            (chooser_visible, chooser_technical),
        ]:
            print("VISIBLE_PROMPT")
            print(visible)
            print("\nTECHNICAL_PROMPT")
            print(technical)
            print()
        return 0

    for visible, technical, send_history in [
        (scan_visible, scan_technical, False),
        (verify_visible, verify_technical, True),
    ]:
        rc, _ = invoke_turn(args, visible, technical, send_history=send_history)
        if rc != 0:
            return rc

    rc, chooser_output = invoke_turn(args, chooser_visible, chooser_technical, send_history=True, capture_output=True)
    if rc != 0:
        return rc
    action, reason = parse_next_action(chooser_output, allowed_actions)
    if action == "none" or args.recommend_only:
        print(f"NEXT_ACTION: {action}")
        if reason:
            print(f"REASON: {reason}")
        return 0

    execute_visible = f"repo: {args.workspace}\n{execute_template[action]}"
    execute_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_ACTION {args.workspace} {action} --timeout {args.timeout}"
    rc, _ = invoke_turn(args, execute_visible, execute_technical, send_history=True)
    if rc != 0:
        return rc
    print(f"NEXT_ACTION: {action}")
    if reason:
        print(f"REASON: {reason}")
    return 0


def run_patch_plan_sequence(args: argparse.Namespace) -> int:
    visible = (
        f"repo: {args.workspace}\n"
        "Ověř workspace, zjisti nejbližší bezpečný další směr a pokud nic nepůjde rovnou spustit, "
        "připrav podklad pro další patch. Zatím nic needituj."
    )
    technical = (
        f"{repo_prefix(args.repo)}\n"
        f"GATEWAY_ADMIN_WORKSPACE_AUTOPILOT {args.workspace} --recommend-only --timeout {args.timeout}"
    )

    if args.dry_run:
        print("VISIBLE_PROMPT")
        print(visible)
        print("\nTECHNICAL_PROMPT")
        print(technical)
        print()
        print("FOLLOW_UP")
        print("If the response contains read_command, the helper will execute it and then ask codex-local for a minimal patch plan.")
        return 0

    rc, first_output = invoke_turn(args, visible, technical, send_history=False, capture_output=True)
    if rc != 0:
        return rc
    meta = parse_key_values(first_output)
    read_command = meta.get("read_command", "").strip()
    if not read_command:
        if first_output.strip():
            print(first_output.strip())
        return 0

    read_visible = (
        f"repo: {args.workspace}\n"
        "Přečti teď nejrelevantnější soubor pro další patch směr a vrať ho s čísly řádků. Nic needituj."
    )
    read_technical = f"{repo_prefix(args.repo)}\n{read_command}"
    rc, _ = invoke_turn(args, read_visible, read_technical, send_history=True)
    if rc != 0:
        return rc

    plan_visible = (
        f"repo: {args.workspace}\n"
        "Na základě předchozího auditu a přečteného souboru navrhni minimální patch plan. "
        "Zůstaň u malého bezpečného zásahu a nic ještě needituj."
    )
    plan_technical = (
        f"{repo_prefix(args.workspace)}\n"
        "Na základě celé dosavadní historie navrhni minimální další patch plan. "
        "Odpověz stručně a strukturovaně v těchto řádcích:\n"
        "PATCH_TARGET: <path or none>\n"
        "PATCH_SUMMARY: <one sentence>\n"
        "PATCH_HINT: <one sentence>\n"
        "NEXT_ADMIN_COMMAND: <GATEWAY_ADMIN_READ_NUMBERED ... or GATEWAY_ADMIN_APPLY_NOW or none>"
    )
    rc, plan_output = invoke_turn(args, plan_visible, plan_technical, send_history=True, capture_output=True)
    if rc != 0:
        return rc
    if plan_output.strip():
        print(plan_output.strip())
    return 0


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
    action.add_argument("action", choices=["install", "test", "build", "lint", "verify"])
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

    audit = sub.add_parser("audit", help="Run scan + verify plan + next-step recommendation for a workspace")
    audit.add_argument("workspace")
    audit.add_argument("--timeout", type=int, default=2400)
    audit.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    autopilot = sub.add_parser("autopilot", help="Run scan + verify + choose and optionally execute one safe next action")
    autopilot.add_argument("workspace")
    autopilot.add_argument("--timeout", type=int, default=2400)
    autopilot.add_argument("--allow-actions", default="install,test,build,lint")
    autopilot.add_argument("--recommend-only", action="store_true", help="Only print the chosen next action, do not execute it")
    autopilot.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    patch_plan = sub.add_parser("patch-plan", help="Use autopilot recommendation, follow read_command, and ask codex-local for a minimal patch plan")
    patch_plan.add_argument("workspace")
    patch_plan.add_argument("--timeout", type=int, default=2400)
    patch_plan.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

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

    if args.mode == "audit":
        return run_audit_sequence(args)
    if args.mode == "autopilot":
        return run_autopilot_sequence(args)
    if args.mode == "patch-plan":
        return run_patch_plan_sequence(args)

    visible, technical = build_prompts(args)
    rc, _ = invoke_turn(args, visible, technical)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
