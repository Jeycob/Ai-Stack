"""
title: Codex Auto Tools Filter
author: OpenAI Codex
version: 0.1.8
description: Dynamically attaches Codex toolsets and routes broader codex-local natural-language admin intents.
"""

import json
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional
import re
import shlex


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=-10,
            description="Run early so Codex tool_ids or admin intents are prepared before later filters/model calls.",
        )
        enable_codex_local_intent_router: bool = Field(
            default=True,
            description="Translate narrow natural-language codex-local ai-stack admin intents into explicit gateway admin commands.",
        )
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.lite = ["codex_lite_workspace_tools"]
        self.extra = ["codex_extra_workspace_tools"]
        self.ssh = ["codex_ssh_key_tools"]
        self.aider = ["aider_container_access"]
        self.capability_roadmap = self._load_capability_roadmap()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        model = body.get("model")
        if not model:
            return body

        if self.valves.enable_codex_local_intent_router and str(model).startswith("codex-local-"):
            routed = self._route_codex_local_admin_intent(body)
            if routed:
                return routed

        # Respect explicit UI/API tool selection. The old version merged in every
        # Codex tool, which made complex prompts harder for small local models.
        if body.get("tool_ids"):
            return body

        tool_ids = self._default_tool_ids(model, self._conversation_text(body))
        if tool_ids:
            body["tool_ids"] = tool_ids
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body

    def _conversation_text(self, body: dict) -> str:
        messages = body.get("messages") or []
        parts = []
        for msg in messages[-8:]:
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
        return "\n".join(parts).lower()

    def _last_user_message(self, body: dict) -> dict | None:
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") == "user":
                return msg
        return None

    def _message_text(self, msg: dict | None) -> str:
        if not msg:
            return ""
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        return str(content)

    def _set_message_text(self, msg: dict, text: str) -> None:
        content = msg.get("content", "")
        if isinstance(content, str):
            msg["content"] = text
            return
        msg["content"] = text

    def _route_codex_local_admin_intent(self, body: dict) -> dict | None:
        latest = self._last_user_message(body)
        text = self._message_text(latest)
        if not latest or not text:
            return None
        if "GATEWAY_ADMIN_" in text:
            return None

        command = self._natural_workspace_backlog_command(text)
        if not command:
            command = self._natural_workspace_dispatch_command(text)
        if not command:
            command = self._natural_capability_roadmap_command(text)
        if not command:
            command = self._natural_workspace_run_command(text)
        if not command:
            command = self._natural_workspace_common_command(text)
        if not command:
            command = self._natural_workspace_autopilot_command(text)
        if not command:
            command = self._natural_workspace_action_command(text)
        if not command:
            command = self._natural_create_repo_command(text)
        if not command and self._mentions_ai_stack(text):
            command = self._natural_ai_stack_command(text)
        if not command:
            return None

        self._set_message_text(latest, "repo: ai-stack\n" + command)
        body["stream"] = False
        return body

    def _extract_task_list(self, text: str) -> list[str]:
        tasks = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"(?i)^(repo|workspace|project)\s*:\s*[A-Za-z0-9_.-]{1,80}\s*$", stripped):
                continue
            for pattern in (r"^[-*]\s+(.+)$", r"^\d+[.)]\s+(.+)$"):
                match = re.match(pattern, stripped)
                if match:
                    item = match.group(1).strip()
                    if item:
                        tasks.append(item)
                    break
        deduped = []
        seen = set()
        for item in tasks:
            if item in seen:
                continue
            deduped.append(item)
            seen.add(item)
        return deduped

    def _backlog_helper_command(self, mode: str, workspace: str, tasks: list[str], recommend_only: bool = False) -> str:
        command = [
            "python3",
            "codex/bin/mentor_codex_local.py",
            mode,
            workspace,
        ]
        if recommend_only:
            command.append("--recommend-only")
        task_flag = "--tasks" if mode == "dispatch" else "--task"
        for task in tasks:
            command.extend([task_flag, task])
        return f"GATEWAY_ADMIN_RUN_WORKSPACE {workspace} --timeout 180 -- {shlex.join(command)}"

    def _natural_workspace_backlog_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        tasks = self._extract_task_list(text)
        if len(tasks) < 2:
            return None
        if not any(
            needle in lower
            for needle in (
                "backlog",
                "fronta",
                "queue",
                "prioritiz",
                "serad",
                "seřaď",
                "srovnej",
                "roztrid",
                "roztřiď",
                "co driv",
                "co dřív",
                "co prvni",
                "co první",
            )
        ):
            return None
        return self._backlog_helper_command("backlog", workspace, tasks)

    def _natural_workspace_dispatch_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        tasks = self._extract_task_list(text)
        if len(tasks) < 2:
            return None
        if not any(
            needle in lower
            for needle in (
                "vyber dalsi krok",
                "vyber další krok",
                "zacni prvnim",
                "začni prvním",
                "vezmi prvni",
                "vezmi první",
                "udelaj prvni",
                "udělej první",
                "spust prvni",
                "spusť první",
                "udelej z toho plan a pokracuj",
                "udělej z toho plán a pokračuj",
            )
        ):
            return None
        recommend_only = not any(
            needle in lower
            for needle in (
                "spust",
                "spusť",
                "zacni",
                "začni",
                "proved",
                "proveď",
                "pokracuj",
                "pokračuj",
            )
        )
        return self._backlog_helper_command("dispatch", workspace, tasks, recommend_only=recommend_only)

    def _load_capability_roadmap(self) -> dict[str, dict]:
        module_file = globals().get("__file__")
        if module_file:
            path = Path(module_file).resolve().parents[2] / "docs" / "codex-local-capability-roadmap.json"
        else:
            path = Path.cwd() / "docs" / "codex-local-capability-roadmap.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        capabilities = payload.get("capabilities")
        return capabilities if isinstance(capabilities, dict) else {}

    def _capability_block(self, capability_id: str) -> str:
        item = self.capability_roadmap.get(capability_id) or {}
        scope = str(item.get("scope", ""))
        summary = str(item.get("summary", ""))
        parts = [f"CAPABILITY_ROADMAP_ID {capability_id}"]
        if scope:
            parts.append(f"CAPABILITY_ROADMAP_SCOPE {scope}")
        if summary:
            parts.append(f"CAPABILITY_ROADMAP_SUMMARY {summary}")
        return "\n".join(parts)

    def _natural_capability_roadmap_command(self, text: str) -> str | None:
        lower = text.lower()
        if any(token in lower for token in ("github actions", "create github repo", "vytvor github", "pushni do githubu", "release", "publish package")):
            return (
                "GATEWAY_ADMIN_WORKSPACE_SCAN ai-stack\n"
                + self._capability_block("github_release")
            )
        if any(token in lower for token in ("nainstaluj systemovy balik", "nainstaluj systémový balík", "apt install", "sudo ", "docker compose", "restartni service", "restartuj service")):
            return (
                "GATEWAY_ADMIN_WORKSPACE_SCAN ai-stack\n"
                + self._capability_block("host_runtime_package_install")
            )
        return None

    def _mentions_ai_stack(self, text: str) -> bool:
        return re.search(r"(?im)^\s*(?:repo|workspace|project)\s*:\s*ai-stack\s*$", text) is not None or "ai-stack" in text.lower()

    def _natural_ai_stack_command(self, text: str) -> str | None:
        lower = text.lower()
        deploy_words = [
            "deploy",
            "nasad",
            "nasaď",
            "restart",
            "self-deploy",
            "self deploy",
            "pullni",
            "pullnout",
            "git pull",
            "stahni z gitu",
            "stáhni z gitu",
            "aktualizuj stack",
            "update stack",
        ]
        status_words = [
            "deploy status",
            "status deploy",
            "stav deploy",
            "stav nasazeni",
            "stav nasazení",
            "deploy log",
            "log deploy",
            "log nasazeni",
            "log nasazení",
        ]

        if any(word in lower for word in status_words):
            return "GATEWAY_ADMIN_DEPLOY_STATUS"
        if any(word in lower for word in deploy_words):
            return "GATEWAY_ADMIN_DEPLOY_STACK"
        return None

    def _natural_create_repo_command(self, text: str) -> str | None:
        lower = text.lower()
        has_create = any(word in lower for word in ["vytvor", "vytvoř", "zaloz", "založ", "create"])
        has_repo = any(word in lower for word in ["repository", "repozitar", "repozitář", "repo "])
        if not (has_create and has_repo):
            return None

        patterns = [
            r"(?i)\b(?:vytvor|vytvoř|zaloz|založ|create)\b\s+(?:mi\s+)?(?:nove|nové|new\s+)?(?:repository|repo|repozitar|repozitář)\s+([A-Za-z0-9_.-]{1,80})\b",
            r"(?i)\b(?:repository|repo|repozitar|repozitář)\s+([A-Za-z0-9_.-]{1,80})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            name = match.group(1)
            if name.lower() in {"ai-stack", "smoke"}:
                return None
            github = " --github" if "github" in lower else ""
            return f"GATEWAY_ADMIN_CREATE_LOCAL_REPO {name}{github} --restart"
        return None

    def _natural_workspace_run_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        patterns = [
            r"(?im)^\s*(?:spust|spusť|run|command|prikaz|příkaz)\s*:\s*(.+?)\s*$",
            r"(?im)^\s*(?:spust|spusť)\s+(?:prikaz|příkaz)\s*:\s*(.+?)\s*$",
        ]
        command = ""
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                command = match.group(1).strip()
                break
        if not command:
            return None
        if "\n" in command or len(command) > 400:
            return None
        timeout_match = re.search(r"(?im)^\s*timeout\s*:\s*(\d{1,4})\s*$", text)
        timeout = int(timeout_match.group(1)) if timeout_match else 300
        timeout = max(1, min(timeout, 1800))
        return f"GATEWAY_ADMIN_RUN_WORKSPACE {workspace} --timeout {timeout} -- {command}"

    def _natural_workspace_common_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        checks = [
            (["git status", "stav gitu", "stav git", "status repa", "status repo"], "git status --short --branch"),
            (["git remote", "remote repa", "remote repo", "jak je nastaveny origin", "jak je nastavený origin"], "git remote -v"),
            (["posledni commity", "poslední commity", "last commits", "git log"], "git log -5 --oneline"),
        ]
        for needles, command in checks:
            if any(needle in lower for needle in needles):
                return f"GATEWAY_ADMIN_RUN_WORKSPACE {workspace} --timeout 120 -- {command}"
        return None

    def _natural_workspace_action_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        actions = [
            (["nainstaluj zavislosti", "nainstaluj závislosti", "install dependencies", "prepare environment"], "install", 1800),
            (["spust testy", "spusť testy", "run tests", "otestuj projekt"], "test", 1800),
            (["postav projekt", "build project", "udělej build", "udelej build", "spust build", "spusť build"], "build", 1800),
            (["spust lint", "spusť lint", "run lint", "zkontroluj lint", "lint projekt"], "lint", 1200),
            (["over projekt", "ověř projekt", "zkontroluj projekt", "verify project", "proveď ověření", "proveď overeni"], "verify", 2400),
        ]
        for needles, action, timeout in actions:
            if any(needle in lower for needle in needles):
                return f"GATEWAY_ADMIN_WORKSPACE_ACTION {workspace} {action} --timeout {timeout}"
        return None

    def _natural_workspace_autopilot_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        recommend_only = [
            "co bys udelal dal",
            "co bys udělal dál",
            "co doporucujes dal",
            "co doporučuješ dál",
            "navrhni dalsi krok",
            "navrhni další krok",
            "doporuč další krok",
            "recommend next step",
        ]
        autopilot = [
            "pokracuj sam",
            "pokračuj sám",
            "pokračuj sam",
            "udelej co je potreba",
            "udělej co je potřeba",
            "zkus to rozbehat",
            "zkus to rozběhat",
            "over a pokracuj",
            "ověř a pokračuj",
            "autonomne",
            "autonomně",
            "sam vyber dalsi krok",
            "sám vyber další krok",
            "sam pokracuj",
            "sám pokračuj",
            "dotahni to",
            "dotáhni to",
            "dokonci co zvladnes",
            "dokonči co zvládneš",
            "oprav to sam",
            "oprav to sám",
            "udelej zmenu sam",
            "udělej změnu sám",
            "aplikuj maly patch",
            "aplikuj malý patch",
            "fixni to",
            "rozbehni to",
            "rozběhni to",
            "dodelej to",
            "dokonci to",
            "dokonči to",
        ]
        if any(needle in lower for needle in recommend_only):
            return f"GATEWAY_ADMIN_WORKSPACE_AUTOPILOT {workspace} --recommend-only --timeout 2400"
        if any(needle in lower for needle in autopilot):
            return f"GATEWAY_ADMIN_WORKSPACE_AUTOPILOT {workspace} --timeout 2400 --max-steps 2"
        return None

    def _workspace_from_text(self, text: str) -> str | None:
        match = re.search(r"(?im)^\s*(?:repo|workspace|project)\s*:\s*([A-Za-z0-9_.-]{1,80})\s*$", text)
        if match:
            return match.group(1)
        return None

    def _default_tool_ids(self, model: str, text: str) -> list[str]:
        if model == "codex-lite-coding-agent":
            return self.lite

        if model == "codex-lite-tool-agent":
            if self._mentions_ssh(text):
                return self.ssh
            return self.lite + self.extra

        if model != "codex-hybrid-aider-agent":
            return []

        if self._mentions_ssh(text):
            return self.ssh

        # Repository/config/admin automation works better when the model first
        # resolves paths and uses the smaller workspace toolset. The large Aider
        # bridge remains available when explicitly selected in the UI/API.
        if self._mentions_repo_or_config_work(text):
            return self.lite + self.extra

        if self._mentions_aider_or_explicit_code_heavy(text):
            return self.lite + self.extra + self.aider

        return self.lite + self.extra

    def _mentions_ssh(self, text: str) -> bool:
        return any(
            phrase in text
            for phrase in [
                "ssh key",
                "ssh klic",
                "ssh klíč",
                "public key",
                "public klic",
                "public klíč",
                "ssh-ed25519",
                "ssh-rsa",
            ]
        )

    def _mentions_repo_or_config_work(self, text: str) -> bool:
        return any(
            phrase in text
            for phrase in [
                "repo je ",
                "repo ",
                "reposit",
                "repository",
                "git",
                "github",
                "openwebui",
                "config",
                "configuration",
                "konfigur",
                "nastaveni",
                "nastavení",
                "push",
                "commit",
                "track",
                "sled",
                "uklad",
                "uklád",
                "cyklus",
                "pravidel",
            ]
        )

    def _mentions_aider_or_explicit_code_heavy(self, text: str) -> bool:
        has_explicit_path = "/mnt/c/repositories/" in text or "/mnt/c/newrepos/" in text
        has_code_work = any(
            phrase in text
            for phrase in [
                "aider",
                "implement",
                "refactor",
                "bugfix",
                "oprav",
                "migrac",
                "test repair",
            ]
        )
        return has_explicit_path and has_code_work
