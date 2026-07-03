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
SAFE_PATCH_ROOT_FILES = {
    "README.md",
    "docker-compose.yml",
    "start_docker.bat",
    ".gitignore",
}
SAFE_PATCH_PREFIX_RULES = (
    ("docs/", (".md",)),
    ("codex/bin/", (".py", ".sh")),
    ("codex/gateway/", (".py",)),
    ("openwebui/", (".js", ".css")),
)
UNSAFE_PATCH_SEGMENTS = (
    "/dev/null",
    "codex/state/",
    "codex/audit/",
    "logs/",
    "__pycache__/",
    ".env",
    ".bak-",
)


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


def extract_diff_block(text: str) -> str:
    blocks = re.findall(r"```(?:diff|patch)?\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if len(blocks) != 1:
        raise ValueError(f"Expected exactly one fenced diff block, got {len(blocks)}")
    diff = blocks[0].strip("\n")
    if not diff:
        raise ValueError("Diff block is empty")
    return diff + "\n"


def touched_patch_files(diff: str) -> list[str]:
    files: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+++ "):
            continue
        path = line[4:].strip().split("\t", 1)[0].strip('"')
        if path.startswith("b/"):
            path = path[2:]
        files.append(path)
    if not files:
        raise ValueError("Diff did not contain any +++ file headers")
    return files


def is_safe_patch_target(rel: str) -> bool:
    if rel in SAFE_PATCH_ROOT_FILES:
        return True
    for prefix, suffixes in SAFE_PATCH_PREFIX_RULES:
        if rel.startswith(prefix) and rel.endswith(suffixes):
            return True
    return False


def validate_safe_diff(diff: str) -> tuple[list[str], str]:
    if any(token in diff for token in UNSAFE_PATCH_SEGMENTS):
        raise ValueError("Diff touches runtime, backup, secret, or generated paths")
    if diff.count("\n@@ ") > 12:
        raise ValueError("Diff is too large for safe auto-apply")
    if len(diff.splitlines()) > 240:
        raise ValueError("Diff has too many lines for safe auto-apply")

    files = touched_patch_files(diff)
    if len(set(files)) > 3:
        raise ValueError("Safe auto-apply is limited to at most 3 files")

    cleaned: list[str] = []
    for rel in files:
        rel = rel.lstrip("/")
        if rel.startswith("a/") or rel.startswith("b/"):
            rel = rel[2:]
        if ".." in Path(rel).parts:
            raise ValueError(f"Unsafe path traversal in diff: {rel}")
        if not is_safe_patch_target(rel):
            raise ValueError(f"Diff touches path outside safe auto-apply scope: {rel}")
        cleaned.append(rel)

    summary = ", ".join(cleaned)
    return cleaned, summary


def follow_read_command(args: argparse.Namespace, workspace: str, read_command: str) -> int:
    if not read_command:
        return 0
    read_visible = (
        f"repo: {workspace}\n"
        "Přečti nejrelevantnější soubor pro další bezpečný patch a vrať ho s čísly řádků. Nic needituj."
    )
    read_technical = f"{repo_prefix(args.repo)}\n{read_command}"
    rc, _ = invoke_turn(args, read_visible, read_technical, send_history=True)
    return rc


def request_patch_plan(args: argparse.Namespace, workspace: str) -> tuple[int, str]:
    plan_visible = (
        f"repo: {workspace}\n"
        "Navrhni minimální patch plan. Zůstaň u malého bezpečného zásahu a nic ještě needituj."
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
    return invoke_turn(args, plan_visible, plan_technical, send_history=True, capture_output=True)


def request_diff_only(args: argparse.Namespace, workspace: str, target: str, summary: str, hint: str) -> tuple[int, str]:
    diff_visible = (
        f"repo: {workspace}\n"
        "Teď připrav přesně jeden malý unified diff pro tenhle zásah. Změň jen nutné soubory a nic zatím neaplikuj."
    )
    diff_technical = (
        f"{repo_prefix(args.workspace)}\n"
        f"Na základě celé dosavadní historie navrhni malý unified diff související s {target}. "
        "Odpověz pouze jedním fenced ```diff blokem bez dalšího textu. "
        "Nepřidávej vysvětlení mimo diff. "
        f"Záměr změny: {summary or '(unspecified)'}. "
        f"Hint: {hint or '(none)'}"
    )
    return invoke_turn(args, diff_visible, diff_technical, send_history=True, capture_output=True)


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


def run_apply_ready_sequence(args: argparse.Namespace) -> int:
    visible = (
        f"repo: {args.workspace}\n"
        "Najdi nejbližší bezpečný patch směr, přečti potřebný kontext a připrav návrh malého dif­fu. "
        "Diff zatím jen navrhni, nic neaplikuj."
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
        print("If the response contains read_command, the helper will execute it, ask for a patch plan, and then ask codex-local for a unified diff proposal only.")
        return 0

    rc, first_output = invoke_turn(args, visible, technical, send_history=False, capture_output=True)
    if rc != 0:
        return rc
    meta = parse_key_values(first_output)
    read_command = meta.get("read_command", "").strip()
    if read_command:
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
        "Na základě dosavadní historie navrhni minimální patch plan. "
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
    plan_meta = parse_key_values(plan_output)

    target = plan_meta.get("patch_target", "").strip()
    summary = plan_meta.get("patch_summary", "").strip()
    hint = plan_meta.get("patch_hint", "").strip()
    if not target or target.lower() == "none":
        if plan_output.strip():
            print(plan_output.strip())
        return 0

    diff_visible = (
        f"repo: {args.workspace}\n"
        "Na základě dosavadního auditu teď navrhni malý unified diff pro daný soubor. "
        "Diff pouze navrhni, nic neaplikuj."
    )
    diff_technical = (
        f"{repo_prefix(args.workspace)}\n"
        f"Na základě celé dosavadní historie navrhni malý unified diff jen pro {target}. "
        "Neměň jiné soubory. Odpověz pouze jedním fenced ```diff blokem bez dalšího textu. "
        f"Záměr změny: {summary or '(unspecified)'} . "
        f"Hint: {hint or '(none)'}"
    )
    rc, diff_output = invoke_turn(args, diff_visible, diff_technical, send_history=True, capture_output=True)
    if rc != 0:
        return rc

    final_parts = []
    if plan_output.strip():
        final_parts.append(plan_output.strip())
    if diff_output.strip():
        final_parts.append(diff_output.strip())
    if final_parts:
        print("\n\n".join(final_parts))
    return 0


def run_apply_safe_sequence(args: argparse.Namespace) -> int:
    visible = (
        f"repo: {args.workspace}\n"
        "Najdi nejbližší bezpečný patch směr, načti potřebný kontext, navrhni malý diff "
        "a pokud zůstane v bezpečném rozsahu, rovnou ho auditovaně aplikuj."
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
        print("The helper will fetch read_command if available, ask for a minimal patch plan, request exactly one diff block, validate it locally, and only then call GATEWAY_ADMIN_APPLY_NOW.")
        return 0

    rc, first_output = invoke_turn(args, visible, technical, send_history=False, capture_output=True)
    if rc != 0:
        return rc
    meta = parse_key_values(first_output)
    read_command = meta.get("read_command", "").strip()
    rc = follow_read_command(args, args.workspace, read_command)
    if rc != 0:
        return rc

    rc, plan_output = request_patch_plan(args, args.workspace)
    if rc != 0:
        return rc
    plan_meta = parse_key_values(plan_output)

    target = plan_meta.get("patch_target", "").strip()
    summary = plan_meta.get("patch_summary", "").strip()
    hint = plan_meta.get("patch_hint", "").strip()
    if not target or target.lower() == "none":
        if plan_output.strip():
            print(plan_output.strip())
        return 0

    rc, diff_output = request_diff_only(args, args.workspace, target, summary, hint)
    if rc != 0:
        return rc

    try:
        diff = extract_diff_block(diff_output)
        files, safe_summary = validate_safe_diff(diff)
    except ValueError as exc:
        final_parts = []
        if plan_output.strip():
            final_parts.append(plan_output.strip())
        if diff_output.strip():
            final_parts.append(diff_output.strip())
        final_parts.append(f"APPLY_SAFE_BLOCKED\nreason={exc}")
        print("\n\n".join(final_parts))
        return 0

    apply_visible = (
        f"repo: {args.workspace}\n"
        f"Patch prošel lokální kontrolou ({len(files)} souborů). Teď ho auditovaně aplikuj a vrať stručný výsledek."
    )
    apply_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_APPLY_NOW\n\n```diff\n{diff}```"
    rc, apply_output = invoke_turn(args, apply_visible, apply_technical, send_history=True, capture_output=True)
    if rc != 0:
        return rc

    final_parts = []
    if plan_output.strip():
        final_parts.append(plan_output.strip())
    final_parts.append(f"APPLY_SAFE_READY\nfiles={safe_summary}")
    if apply_output.strip():
        final_parts.append(apply_output.strip())
    print("\n\n".join(final_parts))
    return 0


def run_improve_sequence(args: argparse.Namespace) -> int:
    visible = (
        f"repo: {args.workspace}\n"
        "Nejdřív projekt agenticky ověř a proveď, co bezpečně dává smysl. "
        "Když capability kroky nestačí, přepni do malého bezpečného patch workflow a dotáhni to co nejdál."
    )
    technical = (
        f"{repo_prefix(args.repo)}\n"
        f"GATEWAY_ADMIN_WORKSPACE_AUTOPILOT {args.workspace} --timeout {args.timeout} "
        f"--max-steps {args.max_steps} --allow-actions {args.allow_actions}"
    )

    if args.dry_run:
        print("VISIBLE_PROMPT")
        print(visible)
        print("\nTECHNICAL_PROMPT")
        print(technical)
        print()
        print("FOLLOW_UP")
        print("If autopilot returns read_command or patch guidance, the helper will continue into the apply-safe flow automatically.")
        return 0

    rc, autopilot_output = invoke_turn(args, visible, technical, send_history=False, capture_output=True)
    if rc != 0:
        return rc
    meta = parse_key_values(autopilot_output)
    read_command = meta.get("read_command", "").strip()
    patch_target = meta.get("patch_target", "").strip().lower()
    recommendation = meta.get("recommendation", "").strip()

    if not read_command and (not patch_target or patch_target == "none"):
        if autopilot_output.strip():
            print(autopilot_output.strip())
        return 0

    rc = follow_read_command(args, args.workspace, read_command)
    if rc != 0:
        return rc

    rc, plan_output = request_patch_plan(args, args.workspace)
    if rc != 0:
        return rc
    plan_meta = parse_key_values(plan_output)
    target = plan_meta.get("patch_target", "").strip()
    summary = plan_meta.get("patch_summary", "").strip() or recommendation
    hint = plan_meta.get("patch_hint", "").strip()
    if not target or target.lower() == "none":
        final_parts = []
        if autopilot_output.strip():
            final_parts.append(autopilot_output.strip())
        if plan_output.strip():
            final_parts.append(plan_output.strip())
        print("\n\n".join(final_parts))
        return 0

    rc, diff_output = request_diff_only(args, args.workspace, target, summary, hint)
    if rc != 0:
        return rc

    try:
        diff = extract_diff_block(diff_output)
        files, safe_summary = validate_safe_diff(diff)
    except ValueError as exc:
        final_parts = []
        if autopilot_output.strip():
            final_parts.append(autopilot_output.strip())
        if plan_output.strip():
            final_parts.append(plan_output.strip())
        if diff_output.strip():
            final_parts.append(diff_output.strip())
        final_parts.append(f"IMPROVE_BLOCKED\nreason={exc}")
        print("\n\n".join(final_parts))
        return 0

    apply_visible = (
        f"repo: {args.workspace}\n"
        f"Capability kroky doběhly a malý patch prošel guardraily ({len(files)} souborů). "
        "Teď ho auditovaně aplikuj a vrať stručný výsledek."
    )
    apply_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_APPLY_NOW\n\n```diff\n{diff}```"
    rc, apply_output = invoke_turn(args, apply_visible, apply_technical, send_history=True, capture_output=True)
    if rc != 0:
        return rc

    final_parts = []
    if autopilot_output.strip():
        final_parts.append(autopilot_output.strip())
    if plan_output.strip():
        final_parts.append(plan_output.strip())
    final_parts.append(f"IMPROVE_READY\nfiles={safe_summary}")
    if apply_output.strip():
        final_parts.append(apply_output.strip())
    print("\n\n".join(final_parts))
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

    apply_ready = sub.add_parser("apply-ready", help="Use autopilot recommendation, follow read guidance, and ask codex-local for a diff proposal only")
    apply_ready.add_argument("workspace")
    apply_ready.add_argument("--timeout", type=int, default=2400)
    apply_ready.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    apply_safe = sub.add_parser("apply-safe", help="Prepare a small diff through codex-local, validate it locally, and apply it through the gateway when it stays in a safe scope")
    apply_safe.add_argument("workspace")
    apply_safe.add_argument("--timeout", type=int, default=2400)
    apply_safe.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    improve = sub.add_parser("improve", help="Run broader agentic workspace improvement: capability steps first, then safe patch workflow if needed")
    improve.add_argument("workspace")
    improve.add_argument("--timeout", type=int, default=2400)
    improve.add_argument("--max-steps", type=int, default=2)
    improve.add_argument("--allow-actions", default="install,test,build,lint")
    improve.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

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
    if args.mode == "apply-ready":
        return run_apply_ready_sequence(args)
    if args.mode == "apply-safe":
        return run_apply_safe_sequence(args)
    if args.mode == "improve":
        return run_improve_sequence(args)

    visible, technical = build_prompts(args)
    rc, _ = invoke_turn(args, visible, technical)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
