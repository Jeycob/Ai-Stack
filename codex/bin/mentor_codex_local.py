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
    "deploy": 90,
    "publish-plan": 89,
    "release-prep": 89,
    "push-check": 89,
    "push": 88,
    "bootstrap-improve": 86,
    "autopilot": 85,
    "create-repo": 80,
    "apply-safe": 75,
    "action": 70,
    "run": 65,
    "review": 55,
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


def prefixed_block(prefix: str, body: str) -> str:
    body = body.strip()
    if not body:
        return ""
    return f"{prefix.rstrip()}\n{body}"


def apply_mentor_context(args: argparse.Namespace, visible: str, technical: str) -> tuple[str, str]:
    visible_context = getattr(args, "mentor_visible_context", "").strip()
    technical_context = getattr(args, "mentor_technical_context", "").strip()
    if visible_context:
        visible = prefixed_block(visible_context, visible)
    if technical_context:
        technical = prefixed_block(technical_context, technical)
    return visible, technical


def print_prompt_preview(args: argparse.Namespace, visible: str, technical: str) -> None:
    print("VISIBLE_PROMPT")
    print(visible)
    print("\nTECHNICAL_PROMPT")
    print(technical)


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
        if getattr(args, "dry_run_action", False):
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

    if args.mode == "release-prep":
        visible = (
            f"repo: {args.workspace}\n"
            "Zkontroluj release readiness, shrň blokery a navrhni další krok před publikací. Nic neměň."
        )
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_REPO_GUARD {args.workspace} main"
        return visible, technical

    if args.mode == "publish-plan":
        visible = (
            f"repo: {args.workspace}\n"
            "Připrav krátký publish plán pro release/publikaci. Nejprve si ověř release readiness a pak navrhni další auditované kroky. Nic neměň."
        )
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_REPO_GUARD {args.workspace} main"
        return visible, technical

    if args.mode == "push-check":
        visible = "repo: ai-stack\nZkontroluj, jestli jsou změny připravené na push, a stručně řekni co případně blokuje publish."
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_GIT_STATUS"
        return visible, technical

    if args.mode == "push":
        visible = "repo: ai-stack\nCommitni povolené změny a pushni je do GitHubu. Po dokončení napiš stručný stav."
        technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_GIT_PUSH {args.branch} {shlex.quote(args.message)}"
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

    if args.mode == "review":
        visible = (
            f"repo: {args.workspace}\n"
            "Proveď senior code review nebo architektonické review z dostupného kontextu. "
            "Zaměř se na rizika, možné regrese, test gaps a potom navrhni jeden nejlepší další krok. "
            "Nic nespouštěj a nic needituj."
        )
        technical = (
            f"{repo_prefix(args.workspace)}\n"
            "Na základě předchozí historie a dostupného snapshotu proveď review ve stylu senior developera. "
            "Prioritizuj bugy, rizika, behavioral regressions a missing tests. "
            "Na konci navrhni právě jeden další auditovaný krok. "
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


def extract_create_repo_name(task: str) -> str:
    patterns = [
        r"(?i)\b(?:vytvor|vytvoř|zaloz|založ|create)\b\s+(?:mi\s+)?(?:nove|nové|new\s+)?(?:repository|repo|repozitar|repozitář)\s+([A-Za-z0-9_.-]{1,80})\b",
        r"(?i)\b(?:repository|repo|repozitar|repozitář)\s+([A-Za-z0-9_.-]{1,80})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, task)
        if match:
            name = match.group(1).strip()
            if name.lower() not in {"ai-stack", "smoke"}:
                return name
    return ""


def wants_github_repo(task: str) -> bool:
    lower = task.lower()
    return "github" in lower or "git@github.com" in lower


def wants_repo_followthrough(task: str) -> bool:
    lower = task.lower()
    cues = (
        "doinstaluj",
        "nainstaluj",
        "install",
        "zavislost",
        "závislost",
        "napis kod",
        "napiš kód",
        "implementuj",
        "vytvor aplikaci",
        "vytvoř aplikaci",
        "udelej appku",
        "udělej appku",
        "udelej projekt",
        "udělej projekt",
        "rozbehni",
        "rozběhni",
        "spust to",
        "spusť to",
        "pust to",
        "postav to",
        "build",
        "otestuj",
        "testy",
        "run it",
        "shipni",
        "dotahni",
        "dotáhni",
        "co je treba",
        "co je třeba",
        "react",
        "next.js",
        "nextjs",
        "vue",
        "svelte",
        "fastapi",
        "flask",
        "django",
        "express",
        "nestjs",
        "three.js",
        "threejs",
        "opengl",
        "webgl",
        "electron",
    )
    return any(cue in lower for cue in cues)


def infer_solution_profile(task: str) -> tuple[str, str]:
    lower = task.lower()
    profiles = [
        (
            "nextjs-app",
            (
                "next.js",
                "nextjs",
                "next app",
            ),
            "Prefer a modern Next.js app scaffold with a small production-ready baseline.",
        ),
        (
            "react-app",
            (
                "react",
                "vite",
                "spa",
            ),
            "Prefer a lightweight React app scaffold, likely Vite-based unless the repo context suggests otherwise.",
        ),
        (
            "fastapi-service",
            (
                "fastapi",
                "python api",
                "rest api v pythonu",
                "rest api v python",
            ),
            "Prefer a small FastAPI service scaffold with straightforward local run and test commands.",
        ),
        (
            "flask-service",
            (
                "flask",
            ),
            "Prefer a minimal Flask service baseline only if FastAPI is not explicitly requested.",
        ),
        (
            "django-app",
            (
                "django",
            ),
            "Prefer a conventional Django project layout with explicit app/runtime commands.",
        ),
        (
            "node-service",
            (
                "express",
                "node api",
                "node service",
                "nestjs",
            ),
            "Prefer a simple Node service baseline with explicit scripts and minimal runtime wiring.",
        ),
        (
            "threejs-app",
            (
                "three.js",
                "threejs",
                "3d web",
                "webgl",
            ),
            "Prefer a browser 3D scaffold using Three.js and a small dev/build loop.",
        ),
        (
            "opengl-native",
            (
                "opengl",
            ),
            "Prefer a native OpenGL starter with clear build instructions and a tiny runnable example.",
        ),
        (
            "electron-app",
            (
                "electron",
                "desktop app",
                "desktop aplikaci",
                "desktop aplikaci",
            ),
            "Prefer a minimal Electron starter with explicit run/build commands.",
        ),
    ]
    for profile_id, needles, hint in profiles:
        if any(needle in lower for needle in needles):
            return profile_id, hint
    return "", ""


def public_stack_hint_for_profile(solution_profile: str) -> tuple[str, str]:
    hints = {
        "nextjs-app": (
            "next, react, typescript, eslint, playwright",
            "Prefer established Next.js defaults and keep custom scaffolding thin.",
        ),
        "react-app": (
            "vite, react, typescript, vitest, @testing-library/react",
            "Prefer Vite and common React testing/build defaults before inventing local scaffolding.",
        ),
        "fastapi-service": (
            "fastapi, uvicorn, pydantic-settings, pytest, httpx",
            "Prefer small proven FastAPI ecosystem packages instead of hand-rolled config or HTTP helpers.",
        ),
        "flask-service": (
            "flask, pytest",
            "Keep the Flask baseline minimal and add third-party pieces only where they reduce obvious boilerplate.",
        ),
        "django-app": (
            "django, pytest-django",
            "Prefer conventional Django layout and ecosystem defaults over custom runtime structure.",
        ),
        "node-service": (
            "express or nestjs, typescript, vitest or jest",
            "Prefer the smallest established Node service stack that still fits the requested shape.",
        ),
        "threejs-app": (
            "three, vite, typescript",
            "Prefer Three.js plus a small web scaffold instead of custom low-level rendering setup.",
        ),
        "opengl-native": (
            "glfw or sdl2, glad or glew, cmake",
            "Prefer standard OpenGL bootstrap libraries and a simple CMake build over ad-hoc native setup.",
        ),
        "electron-app": (
            "electron, vite, typescript",
            "Prefer a minimal Electron starter with well-known tooling rather than custom packaging glue.",
        ),
    }
    return hints.get(solution_profile, ("", ""))


def scaffold_recipe_for_profile(solution_profile: str) -> tuple[str, str, str]:
    recipes = {
        "nextjs-app": (
            "npx create-next-app@latest . --ts --eslint --app --src-dir --import-alias '@/*'",
            "package.json, src/app/page.tsx, src/app/layout.tsx, eslint.config.*, tsconfig.json",
            "install -> dev server smoke -> lint -> build",
        ),
        "react-app": (
            "npm create vite@latest . -- --template react-ts",
            "package.json, src/main.tsx, src/App.tsx, vite.config.ts, tsconfig.json",
            "install -> dev server smoke -> test or lint -> build",
        ),
        "fastapi-service": (
            "python -m venv .venv && . .venv/bin/activate && python -m pip install fastapi uvicorn pydantic-settings pytest httpx",
            "app/main.py, app/config.py, tests/test_health.py, pyproject.toml or requirements.txt",
            "venv setup -> import smoke -> pytest -> uvicorn run smoke",
        ),
        "flask-service": (
            "python -m venv .venv && . .venv/bin/activate && python -m pip install flask pytest",
            "app.py or app/__init__.py, tests/test_app.py, requirements.txt",
            "venv setup -> import smoke -> pytest -> flask run smoke",
        ),
        "django-app": (
            "python -m venv .venv && . .venv/bin/activate && python -m pip install django pytest-django && django-admin startproject app .",
            "manage.py, app/settings.py, app/urls.py, pytest.ini",
            "venv setup -> migrate smoke -> pytest -> runserver smoke",
        ),
        "node-service": (
            "npm init -y && npm install express && npm install -D typescript tsx @types/node @types/express vitest",
            "package.json, src/index.ts, tsconfig.json, vitest.config.*",
            "install -> typecheck or test -> run smoke",
        ),
        "threejs-app": (
            "npm create vite@latest . -- --template vanilla-ts && npm install three",
            "package.json, src/main.ts, index.html, vite.config.ts",
            "install -> dev server smoke -> build",
        ),
        "opengl-native": (
            "use CMake scaffold plus glfw and glad packages available on the target system",
            "CMakeLists.txt, src/main.cpp, include/ or third_party/glad as needed, README build notes",
            "configure -> build -> run sample window smoke",
        ),
        "electron-app": (
            "npm init -y && npm install electron && npm install -D vite typescript",
            "package.json, main.ts or main.js, preload.ts, renderer entry, vite.config.ts",
            "install -> electron run smoke -> build",
        ),
    }
    return recipes.get(solution_profile, ("", "", ""))


def infer_workspace_action(task: str) -> str:
    lower = task.lower()
    action_map = [
        ("install", ("nainstaluj zavislosti", "nainstaluj závislosti", "install dependencies", "prepare environment")),
        ("test", ("spust testy", "spusť testy", "run tests", "otestuj projekt")),
        ("build", ("postav projekt", "build project", "udělej build", "udelej build", "spust build", "spusť build")),
        ("lint", ("spust lint", "spusť lint", "run lint", "zkontroluj lint", "lint projekt")),
        ("verify", ("over projekt", "ověř projekt", "zkontroluj projekt", "verify project", "proveď ověření", "proveď overeni")),
    ]
    for action, needles in action_map:
        if any(needle in lower for needle in needles):
            return action
    return ""


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
        action_name: str = "",
        repo_name: str = "",
        repo_github: str = "",
        solution_profile: str = "",
        starter_hint: str = "",
        public_stack: str = "",
        public_stack_rationale: str = "",
        scaffold_recipe: str = "",
        scaffold_files: str = "",
        scaffold_loop: str = "",
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
            "action_name": action_name,
            "repo_name": repo_name,
            "repo_github": repo_github,
            "solution_profile": solution_profile,
            "starter_hint": starter_hint,
            "public_stack": public_stack,
            "public_stack_rationale": public_stack_rationale,
            "scaffold_recipe": scaffold_recipe,
            "scaffold_files": scaffold_files,
            "scaffold_loop": scaffold_loop,
        }

    repo_name = extract_create_repo_name(task)
    solution_profile, starter_hint = infer_solution_profile(task)
    public_stack, public_stack_rationale = public_stack_hint_for_profile(solution_profile)
    scaffold_recipe, scaffold_files, scaffold_loop = scaffold_recipe_for_profile(solution_profile)
    if repo_name and wants_repo_followthrough(task):
        return result(
            "capability",
            "bootstrap-improve",
            "The task asks not only for repository bootstrap, but also for follow-through work such as setup, implementation, or running the project.",
            "high",
            "This should stay inside audited bootstrap plus workspace improvement flow rather than falling back to a generic unrestricted agent.",
            "workspace_repo_bootstrap",
            "If bootstrap plus improve still cannot finish the task, add the next named workspace capability instead of widening shell access blindly.",
            repo_name=repo_name,
            repo_github="yes" if wants_github_repo(task) else "no",
            solution_profile=solution_profile,
            starter_hint=starter_hint,
            public_stack=public_stack,
            public_stack_rationale=public_stack_rationale,
            scaffold_recipe=scaffold_recipe,
            scaffold_files=scaffold_files,
            scaffold_loop=scaffold_loop,
        )
    if repo_name:
        return result(
            "capability",
            "create-repo",
            "The task directly asks for repository bootstrap, which already matches an audited create-repo capability flow.",
            "high",
            "Repository bootstrap is broader than a tiny patch, but it is still a named audited workflow and does not need a generic unrestricted runtime.",
            "workspace_repo_bootstrap",
            "If repo bootstrap later grows into package install, service wiring, or GitHub release automation, split that into the next audited capability instead of widening create-repo.",
            repo_name=repo_name,
            repo_github="yes" if wants_github_repo(task) else "no",
            solution_profile=solution_profile,
            starter_hint=starter_hint,
            public_stack=public_stack,
            public_stack_rationale=public_stack_rationale,
            scaffold_recipe=scaffold_recipe,
            scaffold_files=scaffold_files,
            scaffold_loop=scaffold_loop,
        )
    if any(
        token in lower
        for token in (
            "pullni ai-stack",
            "pullnout ai-stack",
            "nasad ai-stack",
            "nasaď ai-stack",
            "aktualizuj stack",
            "update stack",
            "deploy ai-stack",
            "restartni stack",
            "restartni openwebui",
            "restartuj openwebui",
            "restart gateway",
            "self-deploy",
            "self deploy",
        )
    ):
        return result(
            "capability",
            "deploy",
            "The task is an ai-stack deployment/restart request and fits the dedicated audited deploy workflow.",
            "high",
            "Stack restart is broader than repo-safe editing, but we already have a named deploy flow with preflight, restart, and smoke checks.",
            "stack_deploy",
            "If deployment later needs host package changes or wider infra orchestration, add a dedicated stack-runtime capability instead of widening deploy blindly.",
        )
    if any(
        token in lower
        for token in (
            "publish plan",
            "release plan",
            "plan publikace",
            "plán publikace",
            "jak publikovat",
            "jak udelat release",
            "jak udělat release",
            "navrhni publish plan",
            "navrhni release plan",
            "co delat pred releasem",
            "co dělat před releasem",
            "co mam delat pred releasem",
            "co mám dělat před releasem",
            "co dal pred releasem",
            "co dál před releasem",
            "dalsi release krok",
            "další release krok",
            "what next before release",
            "next release step",
        )
    ):
        return result(
            "capability",
            "publish-plan",
            "The task asks for a concrete publish or release plan, which fits a read-only orchestration step built on release-prep evidence.",
            "high",
            "Before tags or publication, we should first inspect readiness and then return a short audited publish sequence instead of pretending release execution already exists.",
            "",
            "If the resulting plan ends with remote publication only, the remaining gap is the GitHub/release capability boundary rather than a missing local check.",
        )
    if any(
        token in lower
        for token in (
            "release readiness",
            "release ready",
            "priprav release",
            "připrav release",
            "zkontroluj release",
            "zkontroluj jestli je release ready",
            "zkontroluj, jestli je release ready",
            "co blokuje release",
            "what blocks release",
            "prepare release",
            "release prep",
        )
    ) and not any(
        token in lower
        for token in (
            "vytvor release",
            "vytvoř release",
            "create release",
            "publish package",
            "github actions",
            "tag release",
        )
    ):
        return result(
            "capability",
            "release-prep",
            "The task asks for release readiness or preparation, which fits a read-only preflight workflow before any remote mutation.",
            "high",
            "Release preparation should inspect status, remote, recent commits, and workspace metadata before escalating into tags or release publication.",
            "",
            "If release-prep finds only remote publication work left, switch to the GitHub/release capability boundary instead of pretending a full release already exists.",
        )
    if any(
        token in lower
        for token in (
            "ready na push",
            "pripravené na push",
            "pripravene na push",
            "před pushem",
            "pred pushem",
            "before push",
            "push readiness",
            "zkontroluj push",
            "co blokuje push",
            "what blocks push",
        )
    ):
        return result(
            "capability",
            "push-check",
            "The task asks whether ai-stack changes are safe to publish, which fits an audited pre-push status review.",
            "high",
            "Pre-push readiness should use the named git-status safety check instead of jumping straight to a remote mutation.",
            "",
            "If push-check shows only allowed source paths, the next audited step is the named push workflow rather than ad-hoc git commands.",
        )
    if any(
        token in lower
        for token in (
            "pushni zmeny",
            "pushni změny",
            "commitni a pushni",
            "commitni zmeny",
            "commitni změny",
            "commit and push",
            "push changes",
            "pushni ai-stack",
            "publish zmeny",
            "publish změny",
            "pushni to do githubu",
        )
    ) and not any(
        token in lower
        for token in (
            "release",
            "publish package",
            "github actions",
            "tag release",
            "vytvor release",
            "vytvoř release",
        )
    ):
        return result(
            "capability",
            "push",
            "The task asks for a straightforward audited push of allowed ai-stack changes, which already matches the named git-push capability.",
            "high",
            "Simple publish of allowed ai-stack source files should use the named push capability instead of falling back to broad GitHub/release ambiguity.",
            "",
            "If the request grows into tags, releases, package publish, or GitHub Actions automation, switch to the dedicated GitHub/release capability boundary instead of widening push implicitly.",
        )
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

    action_name = infer_workspace_action(task)
    if action_name:
        return result(
            "capability",
            "action",
            "The task explicitly requests a standard workspace capability step, so we can route it directly instead of stopping at audit-only review.",
            "high",
            "Install/test/build/lint/verify are already bounded workspace capabilities, so broader runtime access is unnecessary here.",
            "next_workspace_capability",
            "If the requested step needs more than the standard workspace capability set, expose that next capability explicitly instead of widening action flow.",
            action_name=action_name,
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
    if any(
        token in lower
        for token in (
            "code review",
            "udělej review",
            "udelej review",
            "review kodu",
            "review kódu",
            "zkontroluj rizika",
            "najdi rizika",
            "najdi regres",
            "architektonicke review",
            "architektonické review",
            "kritika navrhu",
            "kritika návrhu",
        )
    ):
        return result(
            "review",
            "review",
            "The task explicitly asks for a review-style pass focused on risks, regressions, and missing tests.",
            "high",
            "This is a read-only senior review task, so we should stay in a narrow review scope and avoid execution until findings are clear.",
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
    visible, technical = apply_mentor_context(args, visible, technical)

    if args.dry_run:
        print_prompt_preview(args, visible, technical)
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
            print_prompt_preview(args, *apply_mentor_context(args, visible, technical))
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
        print_prompt_preview(args, *apply_mentor_context(args, visible, technical))
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
        print_prompt_preview(args, *apply_mentor_context(args, visible, technical))
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
        print_prompt_preview(args, *apply_mentor_context(args, visible, technical))
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
        print_prompt_preview(args, *apply_mentor_context(args, visible, technical))
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
    print_execution_brief("DELEGATE", decision, args.workspace, args.task)

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

    if workflow == "bootstrap-improve":
        bootstrap_args = argparse.Namespace(**vars(args))
        bootstrap_args.mode = "bootstrap-improve"
        return build_and_invoke_mode(bootstrap_args)

    routed = argparse.Namespace(**vars(args))
    if workflow == "action":
        routed.mode = "action"
        routed.action = decision["action_name"]
    elif workflow == "create-repo":
        routed.mode = "create-repo"
        routed.name = decision["repo_name"]
        routed.github = decision["repo_github"] == "yes"
        routed.restart = True
    else:
        routed.mode = workflow
    routed.mentor_visible_context = visible_brief_block(decision, args.workspace, args.task)
    routed.mentor_technical_context = "MENTOR_EXECUTION_BRIEF\n" + execution_brief_block(decision, args.workspace, args.task)
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
    print(f"SOLUTION_PROFILE={decision['solution_profile']}")
    print(f"STARTER_HINT={decision['starter_hint']}")
    print(f"PUBLIC_STACK={decision['public_stack']}")
    print(f"PUBLIC_STACK_RATIONALE={decision['public_stack_rationale']}")
    print(f"SCAFFOLD_RECIPE={decision['scaffold_recipe']}")
    print(f"SCAFFOLD_FILES={decision['scaffold_files']}")
    print(f"SCAFFOLD_LOOP={decision['scaffold_loop']}")
    return 0


def recommended_next_step(decision: dict[str, str], workspace: str, task: str) -> str:
    workflow = decision["workflow"]
    task = task.strip()
    if workflow == "run":
        command = extract_run_command(task)
        return f"python3 codex/bin/mentor_codex_local.py delegate {workspace} $'repo: {workspace}\\nspusť příkaz: {command}'" if command else f"python3 codex/bin/mentor_codex_local.py audit {workspace}"
    if workflow == "action":
        action_name = decision.get("action_name", "")
        return f"python3 codex/bin/mentor_codex_local.py action {workspace} {action_name}" if action_name else f"python3 codex/bin/mentor_codex_local.py audit {workspace}"
    if workflow == "create-repo":
        repo_name = decision.get("repo_name", "")
        github_flag = " --github" if decision.get("repo_github") == "yes" else ""
        return f"python3 codex/bin/mentor_codex_local.py create-repo {repo_name}{github_flag} --restart" if repo_name else f"python3 codex/bin/mentor_codex_local.py audit {workspace}"
    if workflow == "bootstrap-improve":
        return f"python3 codex/bin/mentor_codex_local.py bootstrap-improve {workspace} {shlex.quote(task)}"
    if workflow == "deploy":
        return "python3 codex/bin/mentor_codex_local.py deploy"
    if workflow == "publish-plan":
        return f"python3 codex/bin/mentor_codex_local.py publish-plan {workspace}"
    if workflow == "release-prep":
        return f"python3 codex/bin/mentor_codex_local.py release-prep {workspace}"
    if workflow == "push-check":
        return "python3 codex/bin/mentor_codex_local.py push-check"
    if workflow == "push":
        return "python3 codex/bin/mentor_codex_local.py push"
    if workflow == "review":
        return f"python3 codex/bin/mentor_codex_local.py review {workspace}"
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
    if workflow == "action":
        action_name = decision.get("action_name", "")
        action_prompts = {
            "install": "Nainstaluj závislosti a vrať stručný výsledek.",
            "test": "Spusť testy a vrať stručný výsledek.",
            "build": "Spusť build a vrať stručný výsledek.",
            "lint": "Spusť lint a vrať stručný výsledek.",
            "verify": "Ověř projekt a vrať stručný audit výsledků.",
        }
        return f"repo: {workspace}\n{action_prompts.get(action_name, task)}"
    if workflow == "create-repo":
        repo_name = decision.get("repo_name", "")
        if repo_name:
            suffix = " a rovnou připrav i GitHub remote." if decision.get("repo_github") == "yes" else "."
            return f"repo: ai-stack\nVytvoř nové repository {repo_name}{suffix}"
    if workflow == "bootstrap-improve":
        repo_name = decision.get("repo_name", "")
        if repo_name:
            profile = decision.get("solution_profile", "")
            suffix = f" se starter profilem {profile}" if profile else ""
            return f"repo: ai-stack\nVytvoř repository {repo_name}{suffix}, připrav workspace a pokračuj s bootstrapem co nejdál."
    if workflow == "deploy":
        return "repo: ai-stack\nPullni ai-stack a nasaď poslední změny. Po dokončení napiš stručný stav."
    if workflow == "publish-plan":
        return f"repo: {workspace}\nPřiprav krátký publish plán pro release/publikaci a navrhni další auditované kroky."
    if workflow == "release-prep":
        return f"repo: {workspace}\nZkontroluj release readiness, shrň blokery a navrhni další krok před publikací."
    if workflow == "push-check":
        return "repo: ai-stack\nZkontroluj, jestli jsou změny připravené na push, a stručně řekni co případně blokuje publish."
    if workflow == "push":
        return "repo: ai-stack\nCommitni povolené změny a pushni je do GitHubu. Po dokončení napiš stručný stav."
    if workflow == "review":
        return f"repo: {workspace}\nProveď review, najdi hlavní rizika a navrhni další krok. Nic needituj."
    if workflow == "apply-safe":
        return f"repo: {workspace}\nUprav malý bezpečný patch a auditovaně ho aplikuj."
    if workflow == "improve":
        return f"repo: {workspace}\nFixni to a dotáhni co zvládneš."
    if workflow == "autopilot":
        return f"repo: {workspace}\nOvěř projekt a pokračuj sám."
    return f"repo: {workspace}\nAnalyzuj projekt a navrhni další krok. Nic needituj."


def execution_brief_lines(decision: dict[str, str], workspace: str, task: str) -> list[str]:
    workflow = decision["workflow"]
    lines = [
        f"workspace={workspace}",
        f"task={task}",
        f"workflow={workflow}",
        f"runtime_profile={decision['runtime_profile']}",
        f"confidence={decision['confidence']}",
    ]
    capability_id = decision.get("capability_id", "")
    if capability_id:
        lines.append(f"capability_id={capability_id}")
    capability_scope = decision.get("capability_scope", "")
    if capability_scope:
        lines.append(f"capability_scope={capability_scope}")
    if decision.get("solution_profile"):
        lines.append(f"solution_profile={decision['solution_profile']}")
    if decision.get("starter_hint"):
        lines.append(f"starter_hint={decision['starter_hint']}")
    if decision.get("public_stack"):
        lines.append(f"public_stack={decision['public_stack']}")
    if decision.get("public_stack_rationale"):
        lines.append(f"public_stack_rationale={decision['public_stack_rationale']}")
    if decision.get("scaffold_recipe"):
        lines.append(f"scaffold_recipe={decision['scaffold_recipe']}")
    if decision.get("scaffold_files"):
        lines.append(f"scaffold_files={decision['scaffold_files']}")
    if decision.get("scaffold_loop"):
        lines.append(f"scaffold_loop={decision['scaffold_loop']}")

    if workflow == "run":
        command = extract_run_command(task)
        lines.append("goal=run audited workspace command and summarize output")
        if command:
            lines.append(f"command_hint={command}")
    elif workflow == "action":
        action_name = decision.get("action_name", "")
        lines.append(f"goal=execute audited workspace action {action_name or 'capability'} and summarize the outcome")
        lines.append("guardrail=stay inside the standard workspace capability set and stop before broader runtime changes")
    elif workflow == "create-repo":
        repo_name = decision.get("repo_name", "")
        lines.append(f"goal=bootstrap repository {repo_name or '(unspecified)'} through the audited create-repo workflow")
        lines.append("guardrail=use repo bootstrap capability instead of broad host/runtime mutation")
    elif workflow == "bootstrap-improve":
        repo_name = decision.get("repo_name", "")
        lines.append(f"goal=bootstrap repository {repo_name or '(unspecified)'}, register the workspace, and then continue with audited improve flow")
        lines.append("guardrail=use named bootstrap plus workspace-improve capabilities instead of generic unrestricted repo setup")
    elif workflow == "deploy":
        lines.append("goal=run the audited ai-stack deploy flow with pull, restart, and smoke checks")
        lines.append("guardrail=prefer named deploy flow over ad-hoc docker or root commands")
    elif workflow == "publish-plan":
        lines.append("goal=derive a short audited publish sequence from release readiness rather than improvising a full release")
        lines.append("guardrail=stay read-only, build the plan from release-prep evidence, and leave remote publication behind the release capability boundary")
    elif workflow == "release-prep":
        lines.append("goal=inspect release readiness from repo status, remotes, recent commits, and workspace metadata before any publish step")
        lines.append("guardrail=stay read-only and escalate to the release capability boundary only if publication or tag mutation is the remaining step")
    elif workflow == "push-check":
        lines.append("goal=inspect ai-stack git safety before publish and summarize blocked or allowed paths")
        lines.append("guardrail=stay read-only and use the named git-status safety check before any remote mutation")
    elif workflow == "push":
        lines.append("goal=commit only allowed ai-stack source files and push them through the audited GitHub flow")
        lines.append("guardrail=stop if blocked_paths or sensitive paths appear and prefer the named push capability over ad-hoc git commands")
    elif workflow == "review":
        lines.append("goal=perform a senior review pass and surface the highest-risk findings")
        lines.append("guardrail=stay read-only, prioritize regressions and missing tests, and recommend one next audited step")
    elif workflow == "apply-safe":
        lines.append("goal=prepare and apply a minimal safe patch inside the guarded ai-stack scope")
        lines.append("guardrail=stay inside safe files and validate the diff before apply")
    elif workflow == "improve":
        lines.append("goal=push the project forward agentically with capability steps first")
        lines.append("guardrail=prefer install/test/build/lint or autopilot-style steps before widening patch scope")
    elif workflow == "autopilot":
        lines.append("goal=verify the workspace and continue with the next safe audited step")
        lines.append("guardrail=stop when the next useful step exceeds standard workspace capability scope")
    else:
        lines.append("goal=analyze the repository and decide the best next audited step")
        lines.append("guardrail=do not mutate files until the task shape is clearer")

    missing = decision.get("missing_capability_hint", "")
    if missing:
        lines.append(f"next_scope_hint={missing}")
    lines.append(f"next_helper={recommended_next_step(decision, workspace, task)}")
    lines.append(f"audit_prompt={audit_chat_prompt_suggestion(decision, workspace, task)}")
    return lines


def print_execution_brief(prefix: str, decision: dict[str, str], workspace: str, task: str) -> None:
    print(f"{prefix}_EXECUTION_BRIEF<<EOF")
    for line in execution_brief_lines(decision, workspace, task):
        print(line)
    print("EOF")


def execution_brief_block(decision: dict[str, str], workspace: str, task: str) -> str:
    return "\n".join(execution_brief_lines(decision, workspace, task))


def visible_brief_block(decision: dict[str, str], workspace: str, task: str) -> str:
    next_helper = recommended_next_step(decision, workspace, task)
    goal = ""
    for line in execution_brief_lines(decision, workspace, task):
        if line.startswith("goal="):
            goal = line.split("=", 1)[1]
            break
    lines = [
        "Mentor brief:",
        f"- task: {task}",
        f"- workflow: {decision['workflow']}",
        f"- confidence: {decision['confidence']}",
        f"- goal: {goal or decision['reason']}",
        f"- next helper: {next_helper}",
    ]
    if decision.get("guardrail_summary"):
        lines.append(f"- guardrail: {decision['guardrail_summary']}")
    return "\n".join(lines)


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
        if isinstance(args.task, str):
            tasks.append(args.task)
        else:
            tasks.extend(args.task)
    if getattr(args, "tasks", None):
        tasks.extend(args.tasks)
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


def print_backlog(entries: list[BacklogEntry], workspace: str) -> None:
    print(f"MENTOR_BACKLOG_WORKSPACE={workspace}")
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
        print(f"BACKLOG_ITEM_{idx}_PLAN_CMD=python3 codex/bin/mentor_codex_local.py plan {workspace} {shlex.quote(entry.task)}")
        print(f"BACKLOG_ITEM_{idx}_REPORT_CMD=python3 codex/bin/mentor_codex_local.py report {workspace} {shlex.quote(entry.task)}")
        print(f"BACKLOG_ITEM_{idx}_AUDIT_CHAT_PROMPT<<EOF")
        print(entry.audit_prompt)
        print("EOF")


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
    print(f"MENTOR_REPORT_SOLUTION_PROFILE={decision['solution_profile']}")
    print(f"MENTOR_REPORT_STARTER_HINT={decision['starter_hint']}")
    print(f"MENTOR_REPORT_PUBLIC_STACK={decision['public_stack']}")
    print(f"MENTOR_REPORT_PUBLIC_STACK_RATIONALE={decision['public_stack_rationale']}")
    print(f"MENTOR_REPORT_SCAFFOLD_RECIPE={decision['scaffold_recipe']}")
    print(f"MENTOR_REPORT_SCAFFOLD_FILES={decision['scaffold_files']}")
    print(f"MENTOR_REPORT_SCAFFOLD_LOOP={decision['scaffold_loop']}")
    print(f"MENTOR_REPORT_NEXT_HELPER={recommended_next_step(decision, args.workspace, args.task)}")
    print("MENTOR_REPORT_AUDIT_CHAT_PROMPT<<EOF")
    print(audit_chat_prompt_suggestion(decision, args.workspace, args.task))
    print("EOF")
    print_execution_brief("MENTOR_REPORT", decision, args.workspace, args.task)
    return 0


def mentor_plan_steps(decision: dict[str, str], workspace: str, task: str) -> list[tuple[str, str]]:
    workflow = decision["workflow"]
    capability_id = decision["capability_id"]
    steps: list[tuple[str, str]] = []

    if workflow != "review":
        steps.append(("report", f"python3 codex/bin/mentor_codex_local.py report {workspace} {shlex.quote(task)}"))

    if workflow == "run":
        steps.append(("delegate", recommended_next_step(decision, workspace, task)))
        return steps

    if workflow == "action":
        steps.append(("action", recommended_next_step(decision, workspace, task)))
        if capability_id:
            steps.append(("capability-watch", f"if the standard action scope is not enough, consider capability: {capability_id}"))
        return steps

    if workflow == "create-repo":
        steps.append(("create-repo", recommended_next_step(decision, workspace, task)))
        if decision.get("repo_github") == "yes":
            steps.append(("post-create", "verify GitHub remote, deploy key, and workspace registration"))
        else:
            steps.append(("post-create", "verify workspace registration and initial git status"))
        return steps

    if workflow == "bootstrap-improve":
        repo_name = decision.get("repo_name", "")
        github_hint = " with GitHub remote" if decision.get("repo_github") == "yes" else ""
        profile_hint = f" using starter profile {decision.get('solution_profile')}" if decision.get("solution_profile") else ""
        steps.append(("bootstrap", f"create and register repository {repo_name or '(unspecified)'}{github_hint}{profile_hint}"))
        steps.append(("improve", recommended_next_step(decision, workspace, task)))
        steps.append(("post-bootstrap-check", f"verify new workspace {repo_name or '(unspecified)'} status, then continue with install/test/build or a minimal patch"))
        return steps

    if workflow == "deploy":
        steps.append(("deploy", "python3 codex/bin/mentor_codex_local.py deploy"))
        steps.append(("deploy-status", "python3 codex/bin/mentor_codex_local.py deploy-status"))
        return steps

    if workflow == "publish-plan":
        steps.append(("publish-plan", f"python3 codex/bin/mentor_codex_local.py publish-plan {workspace}"))
        steps.append(("if-publication-remains", "if the final step still needs remote mutation, review the GitHub/release capability boundary"))
        return steps

    if workflow == "release-prep":
        steps.append(("release-prep", f"python3 codex/bin/mentor_codex_local.py release-prep {workspace}"))
        steps.append(("if-publish-remains", "if only remote publication remains, review the GitHub/release capability boundary"))
        return steps

    if workflow == "push-check":
        steps.append(("push-check", "python3 codex/bin/mentor_codex_local.py push-check"))
        steps.append(("if-clean", "if only allowed source paths remain, continue with python3 codex/bin/mentor_codex_local.py push"))
        return steps

    if workflow == "push":
        steps.append(("push", "python3 codex/bin/mentor_codex_local.py push"))
        steps.append(("post-push", "verify clean git status and remote head"))
        return steps

    if workflow == "review":
        steps.append(("review", f"python3 codex/bin/mentor_codex_local.py review {workspace}"))
        steps.append(("report", f"python3 codex/bin/mentor_codex_local.py report {workspace} {shlex.quote(task)}"))
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
    print_execution_brief("MENTOR_PLAN", decision, args.workspace, args.task)
    return 0


def run_backlog_sequence(args: argparse.Namespace) -> int:
    tasks = collect_backlog_tasks(args)
    if not tasks:
        raise SystemExit("backlog mode requires at least one task via --task, --task-file, or stdin")

    entries = build_backlog_entries(args.workspace, tasks)
    print_backlog(entries, args.workspace)
    return 0


def run_dispatch_sequence(args: argparse.Namespace) -> int:
    tasks = collect_backlog_tasks(args)
    if not tasks:
        raise SystemExit("dispatch mode requires at least one task via --task, --task-file, or stdin")

    entries = build_backlog_entries(args.workspace, tasks)
    top = entries[0]
    print_backlog(entries, args.workspace)
    print(f"MENTOR_DISPATCH_SELECTED_TASK={top.task}")
    print(f"MENTOR_DISPATCH_SELECTED_WORKFLOW={top.decision['workflow']}")
    print(f"MENTOR_DISPATCH_SELECTED_PRIORITY={top.priority}")
    print(f"MENTOR_DISPATCH_SELECTED_NEXT_HELPER={top.next_helper}")
    print_execution_brief("MENTOR_DISPATCH_SELECTED", top.decision, args.workspace, top.task)

    if args.recommend_only:
        print("MENTOR_DISPATCH_MODE=recommend-only")
        return 0

    delegate_args = argparse.Namespace(**vars(args))
    delegate_args.mode = "delegate"
    delegate_args.task = top.task
    print("MENTOR_DISPATCH_MODE=execute")
    return run_delegate_sequence(delegate_args)


def run_top_sequence(args: argparse.Namespace) -> int:
    tasks = collect_backlog_tasks(args)
    if not tasks:
        raise SystemExit("top mode requires at least one task via --task, --task-file, or stdin")

    entries = build_backlog_entries(args.workspace, tasks)
    top = entries[0]
    decision = top.decision
    print(f"MENTOR_TOP_WORKSPACE={args.workspace}")
    print(f"MENTOR_TOP_COUNT={len(entries)}")
    print(f"MENTOR_TOP_TASK={top.task}")
    print(f"MENTOR_TOP_PRIORITY={top.priority}")
    print(f"MENTOR_TOP_WORKFLOW={decision['workflow']}")
    print(f"MENTOR_TOP_RUNTIME_PROFILE={decision['runtime_profile']}")
    print(f"MENTOR_TOP_CONFIDENCE={decision['confidence']}")
    print(f"MENTOR_TOP_REASON={decision['reason']}")
    print(f"MENTOR_TOP_GUARDRAIL_SUMMARY={decision['guardrail_summary']}")
    print(f"MENTOR_TOP_NEXT_HELPER={top.next_helper}")
    print(f"MENTOR_TOP_AUDIT_CHAT_PROMPT<<EOF")
    print(top.audit_prompt)
    print("EOF")
    print_execution_brief("MENTOR_TOP", decision, args.workspace, top.task)
    return 0


def run_next_helper_sequence(args: argparse.Namespace) -> int:
    tasks = collect_backlog_tasks(args)
    if tasks:
        entries = build_backlog_entries(args.workspace, tasks)
        top = entries[0]
        decision = top.decision
        task = top.task
        next_helper = top.next_helper
        source = "top-task"
    else:
        if not getattr(args, "task", None):
            raise SystemExit("next-helper mode requires either a positional task or --tasks/--task-file")
        task = args.task
        decision = classify_task(task)
        next_helper = recommended_next_step(decision, args.workspace, task)
        source = "single-task"

    print(f"MENTOR_NEXT_HELPER_WORKSPACE={args.workspace}")
    print(f"MENTOR_NEXT_HELPER_SOURCE={source}")
    print(f"MENTOR_NEXT_HELPER_TASK={task}")
    print(f"MENTOR_NEXT_HELPER_WORKFLOW={decision['workflow']}")
    print(f"MENTOR_NEXT_HELPER_CONFIDENCE={decision['confidence']}")
    print(f"MENTOR_NEXT_HELPER_REASON={decision['reason']}")
    print(f"MENTOR_NEXT_HELPER_COMMAND={next_helper}")
    print_execution_brief("MENTOR_NEXT_HELPER", decision, args.workspace, task)
    return 0


def run_release_prep_sequence(args: argparse.Namespace) -> int:
    repo_guard_visible = (
        f"repo: {args.workspace}\n"
        "Nejdřív udělej read-only repo guard pro release přípravu. Nic neměň."
    )
    repo_guard_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_REPO_GUARD {args.workspace} main"

    scan_visible = (
        f"repo: {args.workspace}\n"
        "Teď si technicky zmapuj workspace kvůli release přípravě. Nic nespouštěj ani neměň."
    )
    scan_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_WORKSPACE_SCAN {args.workspace}"

    remote_visible = (
        f"repo: {args.workspace}\n"
        "Zkontroluj git remote konfiguraci kvůli release přípravě. Jen read-only výstup."
    )
    remote_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_RUN_WORKSPACE {args.workspace} --timeout 120 -- git remote -v"

    log_visible = (
        f"repo: {args.workspace}\n"
        "Zkontroluj poslední commity kvůli release přípravě. Jen read-only výstup."
    )
    log_technical = f"{repo_prefix(args.repo)}\nGATEWAY_ADMIN_RUN_WORKSPACE {args.workspace} --timeout 120 -- git log -5 --oneline"

    summary_visible = (
        f"repo: {args.workspace}\n"
        "Na základě celé dosavadní historie zhodnoť release readiness jako senior engineer. "
        "Řekni, jestli je workspace připravený na další release krok, co ho blokuje a jaký je nejlepší další auditovaný krok. "
        "Nic neměň."
    )
    summary_technical = (
        f"{repo_prefix(args.workspace)}\n"
        "Na základě celé dosavadní historie proveď release-prep verdict. "
        "Odpověz stručně a strukturovaně v těchto řádcích:\n"
        "RELEASE_READY: <yes-or-no>\n"
        "BLOCKERS: <one sentence or none>\n"
        "NEXT_STEP: <one audited helper or capability step>\n"
        "NOTES: <short sentence>"
    )

    if args.dry_run:
        for visible, technical in [
            (repo_guard_visible, repo_guard_technical),
            (scan_visible, scan_technical),
            (remote_visible, remote_technical),
            (log_visible, log_technical),
            (summary_visible, summary_technical),
        ]:
            print_prompt_preview(args, *apply_mentor_context(args, visible, technical))
            print()
        return 0

    for visible, technical, send_history in [
        (repo_guard_visible, repo_guard_technical, False),
        (scan_visible, scan_technical, True),
        (remote_visible, remote_technical, True),
        (log_visible, log_technical, True),
    ]:
        rc, _ = invoke_turn(args, visible, technical, send_history=send_history)
        if rc != 0:
            return rc

    rc, verdict = invoke_turn(args, summary_visible, summary_technical, send_history=True, capture_output=True)
    if rc != 0:
        return rc
    if verdict.strip():
        print(verdict.strip())
    return 0


def run_publish_plan_sequence(args: argparse.Namespace) -> int:
    prep_args = argparse.Namespace(**vars(args))
    prep_args.mode = "release-prep"

    if args.dry_run:
        run_release_prep_sequence(prep_args)
        plan_visible = (
            f"repo: {args.workspace}\n"
            "Na základě celé dosavadní historie teď navrhni krátký publish plán. "
            "Vrať 2-4 auditované kroky a jasně napiš, jestli poslední krok už naráží na release capability boundary."
        )
        plan_technical = (
            f"{repo_prefix(args.workspace)}\n"
            "Na základě celé dosavadní historie navrhni stručný publish plan. "
            "Odpověz strukturovaně v těchto řádcích:\n"
            "PUBLISH_STEP_1: <step>\n"
            "PUBLISH_STEP_2: <step>\n"
            "PUBLISH_STEP_3: <step or none>\n"
            "PUBLISH_STEP_4: <step or none>\n"
            "BOUNDARY: <none or capability boundary summary>"
        )
        print_prompt_preview(args, *apply_mentor_context(args, plan_visible, plan_technical))
        return 0

    rc = run_release_prep_sequence(prep_args)
    if rc != 0:
        return rc

    plan_visible = (
        f"repo: {args.workspace}\n"
        "Na základě celé dosavadní historie teď navrhni krátký publish plán. "
        "Vrať 2-4 auditované kroky a jasně napiš, jestli poslední krok už naráží na release capability boundary."
    )
    plan_technical = (
        f"{repo_prefix(args.workspace)}\n"
        "Na základě celé dosavadní historie navrhni stručný publish plan. "
        "Odpověz strukturovaně v těchto řádcích:\n"
        "PUBLISH_STEP_1: <step>\n"
        "PUBLISH_STEP_2: <step>\n"
        "PUBLISH_STEP_3: <step or none>\n"
        "PUBLISH_STEP_4: <step or none>\n"
        "BOUNDARY: <none or capability boundary summary>"
    )
    rc, output = invoke_turn(args, plan_visible, plan_technical, send_history=True, capture_output=True)
    if rc != 0:
        return rc
    if output.strip():
        print(output.strip())
    return 0


def run_bootstrap_improve_sequence(args: argparse.Namespace) -> int:
    decision = classify_task(args.task)
    repo_name = decision.get("repo_name", "").strip()
    if not repo_name:
        print("BOOTSTRAP_IMPROVE_BLOCKED\nreason=repo name could not be inferred from task")
        return 0

    create_args = argparse.Namespace(**vars(args))
    create_args.mode = "create-repo"
    create_args.name = repo_name
    create_args.github = decision.get("repo_github") == "yes"
    create_args.restart = True
    rc = build_and_invoke_mode(create_args)
    if rc != 0:
        return rc

    improve_args = argparse.Namespace(**vars(args))
    improve_args.mode = "improve"
    improve_args.workspace = repo_name
    improve_args.mentor_visible_context = prefixed_block(
        "Mentor brief:",
        (
            f"- bootstrap source task: {args.task}\n"
            f"- bootstrap repo: {repo_name}\n"
            + (f"- starter profile: {decision.get('solution_profile')}\n" if decision.get("solution_profile") else "")
            + (f"- starter hint: {decision.get('starter_hint')}\n" if decision.get("starter_hint") else "")
            + (f"- public stack: {decision.get('public_stack')}\n" if decision.get("public_stack") else "")
            + (f"- stack rationale: {decision.get('public_stack_rationale')}\n" if decision.get("public_stack_rationale") else "")
            + (f"- scaffold recipe: {decision.get('scaffold_recipe')}\n" if decision.get("scaffold_recipe") else "")
            + (f"- scaffold files: {decision.get('scaffold_files')}\n" if decision.get("scaffold_files") else "")
            + (f"- scaffold loop: {decision.get('scaffold_loop')}\n" if decision.get("scaffold_loop") else "")
            +
            "- follow-through: continue with audited workspace setup and improvement after repository creation"
        ),
    )
    improve_args.mentor_technical_context = prefixed_block(
        "MENTOR_EXECUTION_BRIEF",
        (
            f"workspace={repo_name}\n"
            f"bootstrap_source_task={args.task}\n"
            "workflow=bootstrap-improve\n"
            + (f"solution_profile={decision.get('solution_profile')}\n" if decision.get("solution_profile") else "")
            + (f"starter_hint={decision.get('starter_hint')}\n" if decision.get("starter_hint") else "")
            + (f"public_stack={decision.get('public_stack')}\n" if decision.get("public_stack") else "")
            + (f"public_stack_rationale={decision.get('public_stack_rationale')}\n" if decision.get("public_stack_rationale") else "")
            + (f"scaffold_recipe={decision.get('scaffold_recipe')}\n" if decision.get("scaffold_recipe") else "")
            + (f"scaffold_files={decision.get('scaffold_files')}\n" if decision.get("scaffold_files") else "")
            + (f"scaffold_loop={decision.get('scaffold_loop')}\n" if decision.get("scaffold_loop") else "")
            +
            "goal=continue from repository bootstrap into audited install/test/build or safe patch progression"
        ),
    )
    return run_improve_sequence(improve_args)


def run_boundary_sequence(args: argparse.Namespace) -> int:
    decision = classify_task(args.task)
    print(f"MENTOR_BOUNDARY_WORKSPACE={args.workspace}")
    print(f"MENTOR_BOUNDARY_TASK={args.task}")
    print(f"MENTOR_BOUNDARY_WORKFLOW={decision['workflow']}")
    print(f"MENTOR_BOUNDARY_RUNTIME_PROFILE={decision['runtime_profile']}")
    print(f"MENTOR_BOUNDARY_CONFIDENCE={decision['confidence']}")
    print(f"MENTOR_BOUNDARY_REASON={decision['reason']}")
    print(f"MENTOR_BOUNDARY_GUARDRAIL_SUMMARY={decision['guardrail_summary']}")
    print(f"MENTOR_BOUNDARY_CAPABILITY_ID={decision['capability_id']}")
    print(f"MENTOR_BOUNDARY_CAPABILITY_SCOPE={decision['capability_scope']}")
    print(f"MENTOR_BOUNDARY_CAPABILITY_SUMMARY={decision['capability_summary']}")
    print(f"MENTOR_BOUNDARY_MISSING_CAPABILITY_HINT={decision['missing_capability_hint']}")
    print(f"MENTOR_BOUNDARY_NEXT_HELPER={recommended_next_step(decision, args.workspace, args.task)}")
    print_execution_brief("MENTOR_BOUNDARY", decision, args.workspace, args.task)
    return 0


def run_brief_sequence(args: argparse.Namespace) -> int:
    decision = classify_task(args.task)
    print(f"MENTOR_BRIEF_WORKSPACE={args.workspace}")
    print(f"MENTOR_BRIEF_TASK={args.task}")
    print(f"MENTOR_BRIEF_WORKFLOW={decision['workflow']}")
    print(f"MENTOR_BRIEF_NEXT_HELPER={recommended_next_step(decision, args.workspace, args.task)}")
    print_execution_brief("MENTOR_BRIEF", decision, args.workspace, args.task)
    return 0


def build_and_invoke_mode(args: argparse.Namespace) -> int:
    if args.mode == "audit":
        return run_audit_sequence(args)
    if args.mode == "review":
        visible, technical = build_prompts(args)
        rc, _ = invoke_turn(args, visible, technical)
        return rc
    if args.mode == "autopilot":
        return run_autopilot_sequence(args)
    if args.mode == "publish-plan":
        return run_publish_plan_sequence(args)
    if args.mode == "release-prep":
        return run_release_prep_sequence(args)
    if args.mode == "bootstrap-improve":
        return run_bootstrap_improve_sequence(args)
    if args.mode == "push-check":
        visible, technical = build_prompts(args)
        rc, _ = invoke_turn(args, visible, technical)
        return rc
    if args.mode == "push":
        visible, technical = build_prompts(args)
        rc, _ = invoke_turn(args, visible, technical)
        return rc
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
    if args.mode == "next-helper":
        return run_next_helper_sequence(args)
    if args.mode == "boundary":
        return run_boundary_sequence(args)
    if args.mode == "brief":
        return run_brief_sequence(args)
    if args.mode == "top":
        return run_top_sequence(args)
    if args.mode == "backlog":
        return run_backlog_sequence(args)
    if args.mode == "dispatch":
        return run_dispatch_sequence(args)

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

    publish_plan = sub.add_parser("publish-plan", help="Ask codex-local for a short audited publish plan built on release-prep evidence")
    publish_plan.add_argument("workspace")
    publish_plan.add_argument("--timeout", type=int, default=120)
    publish_plan.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    release_prep = sub.add_parser("release-prep", help="Ask codex-local for a read-only release readiness preflight over a workspace")
    release_prep.add_argument("workspace")
    release_prep.add_argument("--timeout", type=int, default=120)
    release_prep.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    push_check = sub.add_parser("push-check", help="Ask codex-local for an audited ai-stack pre-push readiness summary")
    push_check.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")
    push_check.set_defaults()

    push = sub.add_parser("push", help="Ask codex-local to commit allowed ai-stack changes and push them to GitHub")
    push.add_argument("--branch", default="main")
    push.add_argument("--message", default="Update ai-stack via codex-local")
    push.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")
    push.set_defaults()

    audit = sub.add_parser("audit", help="Run scan + verify plan + next-step recommendation for a workspace")
    audit.add_argument("workspace")
    audit.add_argument("--timeout", type=int, default=2400)
    audit.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    review = sub.add_parser("review", help="Ask codex-local for a senior read-only review over a workspace")
    review.add_argument("workspace")
    review.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

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

    next_helper = sub.add_parser("next-helper", help="Return only the best next helper command for a task or top-priority task set")
    next_helper.add_argument("workspace")
    next_helper.add_argument("task", nargs="?", help="Single task text; omit when using --tasks or --task-file")
    next_helper.add_argument("--tasks", action="append", default=[], help="Task text; can be repeated")
    next_helper.add_argument("--task-file", help="Path to a newline-delimited task file")
    next_helper.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; next-helper mode never calls OpenWebUI")

    boundary = sub.add_parser("boundary", help="Explain current guardrails, capability scope, and what blocks a wider action for a task")
    boundary.add_argument("workspace")
    boundary.add_argument("task")
    boundary.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; boundary mode never calls OpenWebUI")

    brief = sub.add_parser("brief", help="Produce a minimal execution brief for a task: tiny mentor context, next helper, and guardrails")
    brief.add_argument("workspace")
    brief.add_argument("task")
    brief.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; brief mode never calls OpenWebUI")

    top = sub.add_parser("top", help="Return only the current top-priority task from a multi-task set, with reason and execution brief")
    top.add_argument("workspace")
    top.add_argument("--tasks", action="append", default=[], help="Task text; can be repeated")
    top.add_argument("--task-file", help="Path to a newline-delimited task file")
    top.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; top mode never calls OpenWebUI")

    backlog = sub.add_parser("backlog", help="Classify and prioritize multiple tasks into a mentor-ready queue with next helper commands")
    backlog.add_argument("workspace")
    backlog.add_argument("--task", action="append", default=[], help="Task text; can be repeated")
    backlog.add_argument("--task-file", help="Path to a newline-delimited task file")
    backlog.add_argument("--dry-run", action="store_true", help="Accepted for CLI symmetry; backlog mode never calls OpenWebUI")

    dispatch = sub.add_parser("dispatch", help="Prioritize multiple tasks, choose the best next one, and optionally execute the matching mentor workflow")
    dispatch.add_argument("workspace")
    dispatch.add_argument("--tasks", action="append", default=[], help="Task text; can be repeated")
    dispatch.add_argument("--task-file", help="Path to a newline-delimited task file")
    dispatch.add_argument("--timeout", type=int, default=2400)
    dispatch.add_argument("--max-steps", type=int, default=2)
    dispatch.add_argument("--allow-actions", default="install,test,build,lint")
    dispatch.add_argument("--recommend-only", action="store_true", help="Only select and print the top task/workflow, do not execute it")
    dispatch.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI when execution is reached")

    create_repo = sub.add_parser("create-repo", help="Ask codex-local to create a repository/workspace")
    create_repo.add_argument("name")
    create_repo.add_argument("--github", action="store_true")
    create_repo.add_argument("--restart", action="store_true")
    create_repo.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

    bootstrap_improve = sub.add_parser("bootstrap-improve", help="Bootstrap a new repository/workspace and then continue with audited improve flow")
    bootstrap_improve.add_argument("workspace", help="Controller workspace, typically ai-stack")
    bootstrap_improve.add_argument("task")
    bootstrap_improve.add_argument("--timeout", type=int, default=2400)
    bootstrap_improve.add_argument("--max-steps", type=int, default=2)
    bootstrap_improve.add_argument("--allow-actions", default="install,test,build,lint")
    bootstrap_improve.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling OpenWebUI")

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
