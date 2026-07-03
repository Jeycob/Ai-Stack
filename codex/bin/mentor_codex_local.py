#!/usr/bin/env python3
"""Small orchestrator that sends structured tasks to codex-local via the audit chat."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "codex-local-plan-qwen14b"
DEFAULT_TITLE = "Codex audit log - OpenWebUI visible history"
CAPABILITY_ROADMAP = Path(__file__).resolve().parents[2] / "docs/codex-local-capability-roadmap.json"
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

WORKFLOW_PRIORITY = {
    "improve": 95,
    "autopilot": 85,
    "apply-safe": 75,
    "run": 65,
    "audit": 45,
}

CONFIDENCE_PRIORITY = {
    "high": 8,
    "medium": 4,
    "low": 0,
}


@dataclass
class BacklogEntry:
    task: str
    decision: dict[str, str]
    priority: int
    next_helper: str
    audit_prompt: str


def write_temp(text: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="codex-local-", suffix=".txt")
    try:
        handle.write(text)
        return handle.name
    finally:
        handle.close()


def repo_prefix(repo: str) -> str:
    return f"repo: {repo.strip()}"


def load_capability_roadmap() -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(CAPABILITY_ROADMAP.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    capabilities = payload.get("capabilities")
    return capabilities if isinstance(capabilities, dict) else {}


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
        f"{repo_prefix(args.repo)}\n"
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
        f"{repo_prefix(args.repo)}\n"
        f"Na základě celé dosavadní historie navrhni malý unified diff související s {target}. "
        "Odpověz pouze jedním fenced ```diff blokem bez dalšího textu. "
        "Nepřidávej vysvětlení mimo diff. "
        f"Záměr změny: {summary or '(unspecified)'}. "
        f"Hint: {hint or '(none)'}"
    )
    return invoke_turn(args, diff_visible, diff_technical, send_history=True, capture_output=True)


def choose_workflow(task: str) -> str:
    return classify_task(task)["workflow"]


def classify_task(task: str) -> dict[str, str]:
    lower = task.lower()
    roadmap = load_capability_roadmap()

    def result(
        profile: str,
        workflow: str,
        reason: str,
        confidence: str,
        guardrail_summary: str,
        capability_id: str = "",
        missing_capability_hint: str = "",
    ) -> dict[str, str]:
        capability = roadmap.get(capability_id, {}) if capability_id else {}
        return {
            "runtime_profile": profile,
            "workflow": workflow,
            "reason": reason,
            "confidence": confidence,
            "guardrail_summary": guardrail_summary,
            "capability_id": capability_id,
            "capability_scope": str(capability.get("scope", "")),
            "capability_summary": str(capability.get("summary", "")),
            "missing_capability_hint": missing_capability_hint,
        }

    if any(token in lower for token in ("github actions", "create github repo", "vytvor github", "pushni do githubu", "release", "publish package")):
        return result(
            "capability",
            "audit",
            "The task mentions remote repository or release operations that may exceed the currently delegated safe runtime scope.",
            "medium",
            "We should inspect the workspace and existing deployment/push capabilities first instead of assuming direct remote write access.",
            "github_release",
            "If the task truly needs remote repository or release mutations, add or use a dedicated audited GitHub/release capability rather than widening generic runtime access.",
        )
    if any(token in lower for token in ("nainstaluj systemovy balik", "nainstaluj systémový balík", "apt install", "sudo ", "docker compose", "restartni service", "restartuj service")):
        return result(
            "runtime",
            "audit",
            "The task likely needs host-level runtime or package-management privileges, which should not be inferred from a normal repo task.",
            "medium",
            "Current repo-safe and workspace-safe flows are not enough for host-level package/service changes without an explicit audited runtime capability.",
            "host_runtime_package_install",
            "Add or invoke a dedicated host-runtime capability for package installs or service restarts instead of broadening workspace execution implicitly.",
        )

    if any(token in lower for token in ("git status", "git remote", "git log", "spusť příkaz:", "spust prikaz:", "run command")):
        return result(
            "capability",
            "run",
            "Explicit command inside a registered workspace is best handled by the audited workspace runner.",
            "high",
            "Command stays inside a registered workspace, so audited workspace-run is sufficient and broader patch/runtime scope is unnecessary.",
            "",
        )
    if any(token in lower for token in ("navrhni další krok", "navrhni dalsi krok", "co dál", "co dal", "audit", "analyzuj")):
        return result(
            "review",
            "audit",
            "The task asks for analysis or next-step reasoning without an explicit execution request.",
            "high",
            "No execution intent is visible, so we stay in the narrowest read-only mentoring scope.",
            "",
        )
    if any(token in lower for token in ("apply patch", "aplikuj patch", "malý patch", "maly patch", "uprav readme", "uprav dokumentaci")):
        return result(
            "safe_patch",
            "apply-safe",
            "The task points to a small documentation/config/helper change that fits the guarded safe-patch scope.",
            "medium",
            "The requested change looks small enough for guarded ai-stack patching, but only inside the safe file scope and after diff validation.",
            "",
            "If the change grows beyond the safe ai-stack file scope, switch to a broader audited workspace capability instead of forcing apply-safe.",
        )
    if any(token in lower for token in ("fixni to", "rozběhni to", "rozbehni to", "dotáhni to", "dotahni to", "dokonči to", "dokonci to")):
        return result(
            "runtime",
            "improve",
            "The task asks to push the project forward agentically and may need both capability steps and a follow-up patch.",
            "medium",
            "The task may require multiple audited steps; start with capability execution and only escalate into safe patching if capability progress runs out.",
            "wider_workspace_runtime",
            "If capability execution and safe patching still do not unblock progress, request a dedicated wider runtime capability rather than falling back to generic unrestricted shell.",
        )
    if any(token in lower for token in ("ověř projekt", "over projekt", "pokračuj sám", "pokracuj sam", "udělej co je potřeba", "udelej co je potreba")):
        return result(
            "capability",
            "autopilot",
            "The task is primarily about audited install/test/build/lint progression inside the workspace.",
            "medium",
            "Execution is requested, but standard capability steps should be tried before any patch-oriented or broader runtime action.",
            "next_workspace_capability",
            "If the next useful step is outside install/test/build/lint, expose that next step as a named audited capability instead of widening autopilot blindly.",
        )
    return result(
        "review",
        "audit",
        "Defaulting to the narrowest safe mentoring workflow because the task does not clearly require execution.",
        "low",
        "Intent is ambiguous, so we avoid widening permissions until the task shape is clearer from the audit context.",
        "clarify_or_infer_capability",
        "Clarify or infer the next audited capability from the repository context before expanding runtime scope.",
    )


def extract_run_command(task: str) -> str:
    patterns = [
        r"(?im)^\s*(?:spust|spusť|run)\s+(?:příkaz|prikaz|command)\s*:\s*(.+?)\s*$",
        r"(?im)^\s*(?:spust|spusť|run command)\s*:\s*(.+?)\s*$",
    ]
    for pattern in patterns:
        m = re.search(pattern, task)
        if m:
            return m.group(1).strip()
    for line in task.splitlines():
        lowered = line.lower()
        if "příkaz:" in lowered or "prikaz:" in lowered or "command:" in lowered:
            return line.split(":", 1)[1].strip()
    return ""


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


def run_delegate_sequence(args: argparse.Namespace) -> int:
    decision = classify_task(args.task)
    workflow = decision["workflow"]
    print(f"DELEGATE_RUNTIME_PROFILE={decision['runtime_profile']}")
    print(f"DELEGATE_WORKFLOW={workflow}")
    print(f"DELEGATE_CONFIDENCE={decision['confidence']}")
    print(f"DELEGATE_REASON={decision['reason']}")
    print(f"DELEGATE_GUARDRAIL_SUMMARY={decision['guardrail_summary']}")
    print(f"DELEGATE_CAPABILITY_ID={decision['capability_id']}")
    print(f"DELEGATE_CAPABILITY_SCOPE={decision['capability_scope']}")
    print(f"DELEGATE_CAPABILITY_SUMMARY={decision['capability_summary']}")
    print(f"DELEGATE_MISSING_CAPABILITY_HINT={decision['missing_capability_hint']}")

    if workflow == "run":
        command_text = extract_run_command(args.task)
        if not command_text:
            print("DELEGATE_BLOCKED\nreason=run workflow was selected but no explicit command was found")
            return 0
        command = shlex.split(command_text)
        run_args = argparse.Namespace(**vars(args))
        run_args.mode = "run"
        run_args.command = command
        return build_and_invoke_mode(run_args)

    routed = argparse.Namespace(**vars(args))
    routed.mode = workflow
    return build_and_invoke_mode(routed)


def run_profile_sequence(args: argparse.Namespace) -> int:
    decision = classify_task(args.task)
    print(f"RUNTIME_PROFILE={decision['runtime_profile']}")
    print(f"WORKFLOW={decision['workflow']}")
    print(f"CONFIDENCE={decision['confidence']}")
    print(f"REASON={decision['reason']}")
    print(f"GUARDRAIL_SUMMARY={decision['guardrail_summary']}")
    print(f"CAPABILITY_ID={decision['capability_id']}")
    print(f"CAPABILITY_SCOPE={decision['capability_scope']}")
    print(f"CAPABILITY_SUMMARY={decision['capability_summary']}")
    print(f"MISSING_CAPABILITY_HINT={decision['missing_capability_hint']}")
    return 0


def recommended_next_step(decision: dict[str, str], workspace: str, task: str) -> str:
    workflow = decision["workflow"]
    task = task.strip()
    if workflow == "run":
        command = extract_run_command(task)
        return f"python3 codex/bin/mentor_codex_local.py delegate {workspace} $'repo: {workspace}\\nspusť příkaz: {command}'" if command else f"python3 codex/bin/mentor_codex_local.py audit {workspace}"
    if workflow == "apply-safe":
        return f"python3 codex/bin/mentor_codex_local.py apply-safe {workspace}"
    if workflow == "improve":
        return f"python3 codex/bin/mentor_codex_local.py improve {workspace}"
    if workflow == "autopilot":
        return f"python3 codex/bin/mentor_codex_local.py autopilot {workspace}"
    return f"python3 codex/bin/mentor_codex_local.py audit {workspace}"


def audit_chat_prompt_suggestion(decision: dict[str, str], workspace: str, task: str) -> str:
    workflow = decision["workflow"]
    if workflow == "run":
        return task if task.lower().startswith("repo: ") else f"repo: {workspace}\n{task}"
    if workflow == "apply-safe":
        return f"repo: {workspace}\nUprav malý bezpečný patch a auditovaně ho aplikuj."
    if workflow == "improve":
        return f"repo: {workspace}\nFixni to a dotáhni co zvládneš."
    if workflow == "autopilot":
        return f"repo: {workspace}\nOvěř projekt a pokračuj sám."
    return f"repo: {workspace}\nAnalyzuj projekt a navrhni další krok. Nic needituj."


def backlog_priority(decision: dict[str, str], task: str) -> int:
    score = WORKFLOW_PRIORITY.get(decision["workflow"], 40)
    score += CONFIDENCE_PRIORITY.get(decision["confidence"], 0)

    lower = task.lower()
    if any(token in lower for token in ("hned", "urgent", "priorita", "co nejdřív", "co nejdriv")):
        score += 6
    if decision.get("capability_scope") in {"remote_repo", "host_runtime"}:
        score -= 8
    if decision["workflow"] == "audit" and decision.get("capability_id") == "clarify_or_infer_capability":
        score -= 4
    return score


def collect_backlog_tasks(args: argparse.Namespace) -> list[str]:
    tasks: list[str] = []
    if getattr(args, "task", None):
        tasks.extend(args.task)
    task_file = getattr(args, "task_file", None)
    if task_file:
        for line in Path(task_file).read_text(encoding="utf-8").splitlines():
            item = line.strip()
            if item and not item.startswith("#"):
                tasks.append(item)
    if not tasks and not sys.stdin.isatty():
        for line in sys.stdin.read().splitlines():
            item = line.strip()
            if item and not item.startswith("#"):
                tasks.append(item)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in tasks:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def build_backlog_entries(workspace: str, tasks: list[str]) -> list[BacklogEntry]:
    entries: list[BacklogEntry] = []
    for task in tasks:
        decision = classify_task(task)
        entries.append(
            BacklogEntry(
                task=task,
                decision=decision,
                priority=backlog_priority(decision, task),
                next_helper=recommended_next_step(decision, workspace, task),
                audit_prompt=audit_chat_prompt_suggestion(decision, workspace, task),
            )
        )
    return sorted(
        entries,
        key=lambda item: (
            -item.priority,
            -WORKFLOW_PRIORITY.get(item.decision["workflow"], 0),
            item.task.lower(),
        ),
    )


def run_report_sequence(args: argparse.Namespace) -> int:
    decision = classify_task(args.task)
    print(f"MENTOR_REPORT_WORKSPACE={args.workspace}")
    print(f"MENTOR_REPORT_TASK={args.task}")
    print(f"MENTOR_REPORT_RUNTIME_PROFILE={decision['runtime_profile']}")
    print(f"MENTOR_REPORT_WORKFLOW={decision['workflow']}")
    print(f"MENTOR_REPORT_CONFIDENCE={decision['confidence']}")
    print(f"MENTOR_REPORT_REASON={decision['reason']}")
    print(f"MENTOR_REPORT_GUARDRAIL_SUMMARY={decision['guardrail_summary']}")
    print(f"MENTOR_REPORT_CAPABILITY_ID={decision['capability_id']}")
    print(f"MENTOR_REPORT_CAPABILITY_SCOPE={decision['capability_scope']}")
    print(f"MENTOR_REPORT_CAPABILITY_SUMMARY={decision['capability_summary']}")
    print(f"MENTOR_REPORT_MISSING_CAPABILITY_HINT={decision['missing_capability_hint']}")
    print(f"MENTOR_REPORT_NEXT_HELPER={recommended_next_step(decision, args.workspace, args.task)}")
    print("MENTOR_REPORT_AUDIT_CHAT_PROMPT<<EOF")
    print(audit_chat_prompt_suggestion(decision, args.workspace, args.task))
    print("EOF")
    return 0


def mentor_plan_steps(decision: dict[str, str], workspace: str, task: str) -> list[tuple[str, str]]:
    workflow = decision["workflow"]
    capability_id = decision["capability_id"]
    steps: list[tuple[str, str]] = []

    steps.append(("report", f"python3 codex/bin/mentor_codex_local.py report {workspace} {shlex.quote(task)}"))

    if workflow == "run":
        steps.append(("delegate", recommended_next_step(decision, workspace, task)))
        return steps

    if workflow == "apply-safe":
        steps.append(("apply-safe", f"python3 codex/bin/mentor_codex_local.py apply-safe {workspace}"))
        steps.append(("deploy-status", "python3 codex/bin/mentor_codex_local.py deploy-status"))
        return steps

    if workflow == "improve":
        steps.append(("improve", f"python3 codex/bin/mentor_codex_local.py improve {workspace}"))
        steps.append(("deploy-status", "python3 codex/bin/mentor_codex_local.py deploy-status"))
        if capability_id:
            steps.append(("capability-watch", f"watch for capability boundary: {capability_id}"))
        return steps

    if workflow == "autopilot":
        steps.append(("autopilot", f"python3 codex/bin/mentor_codex_local.py autopilot {workspace}"))
        if capability_id:
            steps.append(("capability-watch", f"if autopilot stalls, consider capability: {capability_id}"))
        return steps

    # audit/review path
    steps.append(("audit", f"python3 codex/bin/mentor_codex_local.py audit {workspace}"))
    if capability_id:
        steps.append(("capability-review", f"review capability roadmap item: {capability_id}"))
    if decision.get("missing_capability_hint"):
        steps.append(("next-scope", decision["missing_capability_hint"]))
    return steps


def run_plan_sequence(args: argparse.Namespace) -> int:
    decision = classify_task(args.task)
    steps = mentor_plan_steps(decision, args.workspace, args.task)
    print(f"MENTOR_PLAN_WORKSPACE={args.workspace}")
    print(f"MENTOR_PLAN_TASK={args.task}")
    print(f"MENTOR_PLAN_RUNTIME_PROFILE={decision['runtime_profile']}")
    print(f"MENTOR_PLAN_WORKFLOW={decision['workflow']}")
    print(f"MENTOR_PLAN_CAPABILITY_ID={decision['capability_id']}")
    print(f"MENTOR_PLAN_CONFIDENCE={decision['confidence']}")
    print(f"MENTOR_PLAN_GUARDRAIL_SUMMARY={decision['guardrail_summary']}")
    print(f"MENTOR_PLAN_MISSING_CAPABILITY_HINT={decision['missing_capability_hint']}")
    for idx, (label, value) in enumerate(steps, start=1):
        print(f"PLAN_STEP_{idx}_LABEL={label}")
        print(f"PLAN_STEP_{idx}_VALUE={value}")
    print(f"PLAN_STEP_COUNT={len(steps)}")
    return 0


def run_backlog_sequence(args: argparse.Namespace) -> int:
    tasks = collect_backlog_tasks(args)
    if not tasks:
        raise SystemExit("backlog mode requires at least one task via --task, --task-file, or stdin")

    entries = build_backlog_entries(args.workspace, tasks)
    print(f"MENTOR_BACKLOG_WORKSPACE={args.workspace}")
    print(f"MENTOR_BACKLOG_COUNT={len(entries)}")
    if entries:
        print(f"MENTOR_BACKLOG_TOP_WORKFLOW={entries[0].decision['workflow']}")
        print(f"MENTOR_BACKLOG_TOP_TASK={entries[0].task}")

    for idx, entry in enumerate(entries, start=1):
        decision = entry.decision
        print(f"BACKLOG_ITEM_{idx}_TASK={entry.task}")
        print(f"BACKLOG_ITEM_{idx}_PRIORITY={entry.priority}")
        print(f"BACKLOG_ITEM_{idx}_WORKFLOW={decision['workflow']}")
        print(f"BACKLOG_ITEM_{idx}_RUNTIME_PROFILE={decision['runtime_profile']}")
        print(f"BACKLOG_ITEM_{idx}_CONFIDENCE={decision['confidence']}")
        print(f"BACKLOG_ITEM_{idx}_CAPABILITY_ID={decision['capability_id']}")
        print(f"BACKLOG_ITEM_{idx}_CAPABILITY_SCOPE={decision['capability_scope']}")
        print(f"BACKLOG_ITEM_{idx}_CAPABILITY_SUMMARY={decision['capability_summary']}")
        print(f"BACKLOG_ITEM_{idx}_GUARDRAIL_SUMMARY={decision['guardrail_summary']}")
        print(f"BACKLOG_ITEM_{idx}_MISSING_CAPABILITY_HINT={decision['missing_capability_hint']}")
        print(f"BACKLOG_ITEM_{idx}_NEXT_HELPER={entry.next_helper}")
        print(f"BACKLOG_ITEM_{idx}_REASON={decision['reason']}")
        print(f"BACKLOG_ITEM_{idx}_PLAN_CMD=python3 codex/bin/mentor_codex_local.py plan {args.workspace} {shlex.quote(entry.task)}")
        print(f"BACKLOG_ITEM_{idx}_REPORT_CMD=python3 codex/bin/mentor_codex_local.py report {args.workspace} {shlex.quote(entry.task)}")
        print(f"BACKLOG_ITEM_{idx}_AUDIT_CHAT_PROMPT<<EOF")
        print(entry.audit_prompt)
        print("EOF")
    return 0


def build_and_invoke_mode(args: argparse.Namespace) -> int:
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
    if args.mode == "delegate":
        return run_delegate_sequence(args)
    if args.mode == "profile":
        return run_profile_sequence(args)
    if args.mode == "report":
        return run_report_sequence(args)
    if args.mode == "plan":
        return run_plan_sequence(args)
    if args.mode == "backlog":
        return run_backlog_sequence(args)

    visible, technical = build_prompts(args)
    rc, _ = invoke_turn(args, visible, technical)
    return rc


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

    delegate = sub.add_parser("delegate", help="Choose the most suitable orchestration workflow for a workspace task and run it")
    delegate.add_argument("workspace")
    delegate.add_argument("task")
    delegate.add_argument("--timeout", type=int, default=2400)
    delegate.add_argument("--max-steps", type=int, default=2)
    delegate.add_argument("--allow-actions", default="install,test,build,lint")
    delegate.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    profile = sub.add_parser("profile", help="Classify a workspace task into a runtime profile and recommended workflow without executing it")
    profile.add_argument("workspace")
    profile.add_argument("task")
    profile.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; profile mode never calls OpenWebUI")

    report = sub.add_parser("report", help="Produce a compact mentor report for a task: workflow, runtime profile, capability metadata, guardrails, and recommended next step")
    report.add_argument("workspace")
    report.add_argument("task")
    report.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; report mode never calls OpenWebUI")

    plan = sub.add_parser("plan", help="Produce a short sequenced mentor plan for a task: report plus the next 2-4 helper/capability steps")
    plan.add_argument("workspace")
    plan.add_argument("task")
    plan.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; plan mode never calls OpenWebUI")

    backlog = sub.add_parser("backlog", help="Classify and prioritize multiple tasks into a mentor-ready queue with next helper commands")
    backlog.add_argument("workspace")
    backlog.add_argument("--task", action="append", default=[], help="Task text; can be repeated")
    backlog.add_argument("--task-file", help="Path to a newline-delimited task file")
    backlog.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; backlog mode never calls OpenWebUI")

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

    return build_and_invoke_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
