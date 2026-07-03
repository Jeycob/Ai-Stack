"""
title: Codex Auto Tools Filter
author: OpenAI Codex
version: 0.1.3
description: Dynamically attaches Codex toolsets and routes safe codex-local natural-language admin intents.
"""

from pydantic import BaseModel, Field
from typing import Optional
import re


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

        command = self._natural_create_repo_command(text)
        if not command and self._mentions_ai_stack(text):
            command = self._natural_ai_stack_command(text)
        if not command:
            return None

        self._set_message_text(latest, "repo: ai-stack\n" + command)
        body["stream"] = False
        return body

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
            return f"GATEWAY_ADMIN_CREATE_LOCAL_REPO {name} --restart"
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
