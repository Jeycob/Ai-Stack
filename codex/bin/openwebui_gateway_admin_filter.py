"""
title: Codex Gateway Admin Filter
author: OpenAI Codex
version: 0.1.0
description: Applies explicitly marked, guarded ai-stack gateway patches from Open WebUI conversations.
"""

from pathlib import Path
from typing import Optional
import json
import os
import py_compile
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - used by lightweight local smoke tests
    def Field(default=None, **_: object):
        return default

    class BaseModel:
        def __init__(self, **kwargs):
            for name, value in self.__class__.__dict__.items():
                if name.startswith("_") or callable(value):
                    continue
                setattr(self, name, kwargs.get(name, value))

WORKSPACE_LABEL_PATTERN = r"(?:repo|repository|repositar|repozitar|repozitář|projekt|project|workspace)"
FILE_LABEL_PATTERN = r"(?:soubor|file|path|cesta)"
EMBEDDED_CAPABILITY_ROADMAP = None


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=-5,
            description="Run after tool auto-selection and before/after model calls.",
        )
        repo_root: str = Field(
            default="auto",
            description="ai-stack path inside the Open WebUI container, or auto.",
        )
        candidate_roots: str = Field(
            default="/data/repositories/ai-stack,/app/backend/data/repositories/ai-stack,/Repositories/ai-stack,/mnt/c/Repositories/ai-stack",
            description="Comma-separated fallback ai-stack paths.",
        )
        marker: str = Field(
            default="GATEWAY_ADMIN_APPLY",
            description="Only patches following this marker are applied.",
        )
        inject_instructions: bool = Field(
            default=True,
            description="Teach build model how to request gateway patch application.",
        )
        gateway_url: str = Field(
            default="http://192.168.0.48:9101",
            description="Codex gateway base URL for admin endpoint calls.",
        )
        gateway_admin_token_file: str = Field(
            default="auto",
            description="Admin token file path, or auto to resolve from ai-stack codex/state.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.allowed_exact = {
            ".gitignore",
            "README.md",
            "codex/gateway/gateway.py",
            "codex/bin/start_codex_stack.sh",
            "codex/bin/watch_gateway.sh",
            "codex/bin/add_workspace.py",
            "codex/workspaces.json",
            "codex/opencode-default.json",
            "start_docker.bat",
            "docker-compose.yml",
        }
        roadmap = self._load_capability_roadmap_payload()
        self.workspace_actions = self._extract_workspace_actions(roadmap)
        self.command_handlers = {
            "GATEWAY_ADMIN_DIAG": self._diag,
            "GATEWAY_ADMIN_PROBE": self._probe_paths,
            "GATEWAY_ADMIN_READ_NUMBERED": self._read_numbered_requested_file,
            "GATEWAY_ADMIN_READ": self._read_requested_file,
            "GATEWAY_ADMIN_EXPLAIN_FILE": self._explain_file_admin,
            "GATEWAY_ADMIN_SSH_KEYGEN": self._ssh_keygen,
            "GATEWAY_ADMIN_INSTALL_SSH_CLIENT": self._install_ssh_client,
            "GATEWAY_ADMIN_GIT_STATUS": self._git_status,
            "GATEWAY_ADMIN_GIT_DIFF": self._git_diff,
            "GATEWAY_ADMIN_REPO_GUARD": self._repo_guard,
            "GATEWAY_ADMIN_WORKSPACE_SCAN": self._workspace_scan,
            "GATEWAY_ADMIN_GIT_UNTRACK_IGNORED": self._git_untrack_ignored,
            "GATEWAY_ADMIN_SMOKE": self._gateway_smoke,
            "GATEWAY_ADMIN_CHECK_STACK": self._check_ai_stack,
            "GATEWAY_ADMIN_WEB_ANSWER": self._web_answer_admin,
            "GATEWAY_ADMIN_WEB_FETCH": self._web_fetch_admin,
            "GATEWAY_ADMIN_AGENT_LOOP": self._agent_loop_admin,
            "GATEWAY_ADMIN_WORKSPACE_ACTION": self._workspace_action_admin,
            "GATEWAY_ADMIN_WORKSPACE_AUTOPILOT": self._workspace_autopilot_admin,
            "GATEWAY_ADMIN_WORKSPACE_EDIT": self._workspace_edit_admin,
            "GATEWAY_ADMIN_RUN_WORKSPACE": self._run_workspace_admin,
            "GATEWAY_ADMIN_RUN_WORKSPACE_STATUS": self._run_workspace_status_admin,
            "GATEWAY_ADMIN_ADD_WORKSPACE": self._add_workspace_admin,
            "GATEWAY_ADMIN_CREATE_LOCAL_REPO": self._create_local_repo_admin,
            "GATEWAY_ADMIN_DEPLOY_STACK": self._deploy_stack_admin,
            "GATEWAY_ADMIN_DEPLOY_STATUS": self._deploy_status_admin,
            "GATEWAY_ADMIN_GIT_PUSH": self._git_push,
        }

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        if self.valves.inject_instructions and self._is_ai_stack_request(body):
            body.setdefault("messages", []).insert(0, {"role": "system", "content": self._instructions()})

        latest_user = self._last_message_text(body, "user")
        natural_command = self._natural_admin_command(body, latest_user)
        if natural_command:
            return self._direct_response(body, self._dispatch_admin_command(natural_command))

        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_APPLY_NOW"):
            normalized = re.sub(
                r"(?im)^(\s*)GATEWAY_ADMIN_APPLY_NOW(\s*)$",
                rf"\1{self.valves.marker}\2",
                latest_user,
                count=1,
            )
            if not self._extract_patches(normalized):
                raise RuntimeError("NO_PATCH_FOUND: GATEWAY_ADMIN_APPLY_NOW requires a fenced unified diff block.")
            result = self._apply_from_text(normalized)
            return self._direct_response(body, result)
        command, handler = self._match_admin_handler(latest_user)
        if command and handler:
            return self._direct_response(body, self._invoke_admin_handler(command, handler, latest_user))
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_APPLY_NOW"):
            normalized = re.sub(
                r"(?im)^(\s*)GATEWAY_ADMIN_APPLY_NOW(\s*)$",
                rf"\1{self.valves.marker}\2",
                latest_user,
                count=1,
            )
            if not self._extract_patches(normalized):
                raise RuntimeError("NO_PATCH_FOUND: GATEWAY_ADMIN_APPLY_NOW requires a fenced unified diff block.")
            result = self._apply_from_text(normalized)
            body.setdefault("messages", []).append(
                {"role": "system", "content": "Gateway admin filter result:\n" + result}
            )
            return body
        if self._admin_command_requested(latest_user, self.valves.marker):
            if not self._extract_patches(latest_user):
                body["stream"] = False
                self._replace_last_user_text(
                    body,
                    latest_user,
                    latest_user.replace(
                        self.valves.marker,
                        "[Gateway admin patch requested, but no diff was provided.]",
                    ),
                )
                body.setdefault("messages", []).append(
                    {
                        "role": "system",
                        "content": (
                            "The user requested a gateway admin patch but did not provide a diff. "
                            "Do not say it was applied. If the requested edit is safe, reply with "
                            "GATEWAY_ADMIN_APPLY followed by one fenced unified diff block for a "
                            "whitelisted ai-stack file."
                        ),
                    }
                )
                return body
            result = self._schedule_apply(latest_user)
            body.setdefault("messages", []).append(
                {
                    "role": "system",
                    "content": (
                        "Gateway admin filter result:\n"
                        + result
                        + "\nReply exactly: GATEWAY_PATCH_SCHEDULED"
                    ),
                }
            )
        return body

    def _dispatch_admin_command(self, command: str) -> str:
        matched, handler = self._match_admin_handler(command)
        if matched and handler:
            return self._invoke_admin_handler(matched, handler, command)
        raise ValueError(f"Unsupported natural admin command: {command.splitlines()[0]}")

    def _match_admin_handler(self, text: str) -> tuple[str | None, object | None]:
        for command, handler in self.command_handlers.items():
            if self._admin_command_requested(text, command):
                return command, handler
        return None, None

    def _invoke_admin_handler(self, command: str, handler: object, text: str) -> str:
        if command in {
            "GATEWAY_ADMIN_DIAG",
            "GATEWAY_ADMIN_PROBE",
            "GATEWAY_ADMIN_INSTALL_SSH_CLIENT",
            "GATEWAY_ADMIN_GIT_STATUS",
            "GATEWAY_ADMIN_GIT_UNTRACK_IGNORED",
            "GATEWAY_ADMIN_DEPLOY_STATUS",
        }:
            return handler()
        return handler(text)

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body

    def _instructions(self) -> str:
        return (
            "You may propose safe edits as a normal unified diff when asked, but do not include "
            "internal GATEWAY_ADMIN_* markers in ordinary assistant replies. The Open WebUI "
            "Gateway Admin Filter applies patches only from explicit user-side technical payloads. "
            "Do not claim files were edited unless an admin filter result is shown."
        )

    def _is_ai_stack_request(self, body: dict) -> bool:
        model = str(body.get("model") or "")
        text = "\n".join(self._message_texts(body)[-8:]).lower()
        return "codex-local-" in model and (
            re.search(rf"(?im)^\s*{WORKSPACE_LABEL_PATTERN}\s*:\s*ai-stack\s*$", text) is not None
            or "ai-stack" in text
        )

    def _message_texts(self, body: dict) -> list[str]:
        out = []
        for msg in body.get("messages") or []:
            content = msg.get("content", "")
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                out.append("\n".join(parts))
        return out

    def _last_message_text(self, body: dict, role: str) -> str:
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") == role:
                content = msg.get("content", "")
                return content if isinstance(content, str) else str(content)
        return ""

    def _workspace_from_text(self, text: str) -> str | None:
        match = re.search(rf"(?im)^\s*{WORKSPACE_LABEL_PATTERN}\s*:\s*([A-Za-z0-9_.-]{{1,80}})\s*$", text)
        if match:
            return match.group(1)
        match = re.search(rf"(?im)^\s*{WORKSPACE_LABEL_PATTERN}\s+([A-Za-z0-9_.-]{{1,80}})\s*$", text)
        return match.group(1) if match else None

    def _line_value(self, text: str, label_pattern: str) -> str | None:
        match = re.search(rf"(?im)^\s*{label_pattern}\s*:\s*(.+?)\s*$", text)
        if not match:
            return None
        value = match.group(1).strip().strip("`").strip().strip("\"'")
        return value or None

    def _non_route_lines(self, text: str) -> list[str]:
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(rf"(?i)^{WORKSPACE_LABEL_PATTERN}(?:\s*:\s*|\s+)[A-Za-z0-9_.-]{{1,80}}\s*$", stripped):
                continue
            if re.match(rf"(?i)^{FILE_LABEL_PATTERN}\s*:\s*.+$", stripped):
                continue
            lines.append(stripped)
        return lines

    def _file_from_text(self, text: str) -> str | None:
        explicit = self._line_value(text, FILE_LABEL_PATTERN)
        if explicit:
            return explicit
        lower = text.lower()
        known = [
            ("docker compose", "docker-compose.yml"),
            ("docker-compose", "docker-compose.yml"),
            ("compose.yml", "compose.yml"),
            ("compose.yaml", "compose.yaml"),
            ("readme", "README.md"),
            ("gateway.py", "codex/gateway/gateway.py"),
            ("start_codex_stack.sh", "codex/bin/start_codex_stack.sh"),
            ("workspaces.json", "codex/workspaces.json"),
        ]
        for needle, rel in known:
            if needle in lower:
                return rel
        return None

    def _looks_like_file_read_or_explain(self, text: str) -> bool:
        lower = text.lower()
        return any(
            cue in lower
            for cue in (
                "precti",
                "přečti",
                "read ",
                "ukaz",
                "ukaž",
                "vypis",
                "vypiš",
                "vysvetli",
                "vysvětli",
                "explain",
                "popis",
                "co dela",
                "co dělá",
                "radek po radku",
                "řádek po řádku",
                "line by line",
            )
        )

    def _looks_like_read_only_repo_analysis(self, text: str) -> bool:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return False
        lower = text.lower()
        read_only_cues = (
            "nic needituj",
            "bez editace",
            "jen analyzuj",
            "jen analysis",
            "jen popis",
            "jen vysvetli",
            "jen vysvětli",
            "jen rekni",
            "jen řekni",
            "jen navrhni",
            "jen navrh",
            "read-only",
        )
        analysis_cues = (
            "architekt",
            "blocker",
            "blokery",
            "rizika",
            "vrstvy",
            "jak je zapojena",
            "jak je zapojená",
            "jak je postaven",
            "jak je postaveny",
            "jak je postavený",
            "popis strukturu",
            "prohledni strukturu",
            "prohlédni strukturu",
            "navrhni dalsi bezpecny krok",
            "navrhni další bezpečný krok",
            "safe next step",
            "autonomie",
            "analyzuj projekt",
            "analyzuj architekturu",
        )
        return any(cue in lower for cue in read_only_cues) and any(cue in lower for cue in analysis_cues)

    def _looks_like_workspace_action(self, text: str) -> str | None:
        lower = text.lower()
        for action, spec in self.workspace_actions.items():
            if not isinstance(spec, dict):
                continue
            cues = spec.get("cues") or []
            if any(isinstance(cue, str) and cue.lower() in lower for cue in cues):
                return action
        return None

    def _roadmap_path(self) -> Path:
        module_file = globals().get("__file__")
        if module_file:
            return Path(module_file).resolve().parents[2] / "docs" / "codex-local-capability-roadmap.json"
        return Path.cwd() / "docs" / "codex-local-capability-roadmap.json"

    def _load_capability_roadmap_payload(self) -> dict:
        if isinstance(EMBEDDED_CAPABILITY_ROADMAP, dict):
            return EMBEDDED_CAPABILITY_ROADMAP
        try:
            payload = json.loads(self._roadmap_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _extract_workspace_actions(self, payload: dict) -> dict[str, dict]:
        actions = payload.get("workspace_actions")
        return actions if isinstance(actions, dict) else {}

    def _workspace_action_spec(self, action: str) -> dict:
        spec = self.workspace_actions.get(action)
        return spec if isinstance(spec, dict) else {}

    def _looks_like_edit_request(self, text: str) -> bool:
        lower = text.lower()
        return any(
            cue in lower
            for cue in (
                "pridej",
                "přidej",
                "vytvor",
                "vytvoř",
                "uprav",
                "edituj",
                "napis",
                "napiš",
                "implementuj",
                "dopln",
                "doplň",
                "add ",
                "create ",
                "modify ",
                "implement ",
                "webgl",
                "kouli",
                "sphere",
            )
        )

    def _looks_like_create_workspace_request(self, text: str) -> bool:
        lower = text.lower()
        create_cues = ("vytvor", "vytvoř", "zaloz", "založ", "create", "bootstrap", "priprav", "připrav")
        target_cues = ("workspace", "repository", "repozitar", "repozitář", "repo ", "projekt")
        key_cues = ("ssh key", "ssh klic", "ssh klíč", "vygeneruj klic", "vygeneruj klíč")
        create_hit = any(cue in lower for cue in create_cues)
        target_hit = any(cue in lower for cue in target_cues)
        key_hit = any(cue in lower for cue in key_cues)
        return (
            create_hit
            and target_hit
            and (not key_hit or (create_hit and target_hit))
        )

    def _natural_web_url(self, text: str) -> str | None:
        match = re.search(r"https?://[^\s<>'\")]+", text)
        if match:
            return match.group(0).rstrip(".,;:!?)]}")
        lower = text.lower()
        known_domains = {
            "seznam.cz": "https://www.seznam.cz/",
            "novinky.cz": "https://www.novinky.cz/",
            "idnes.cz": "https://www.idnes.cz/",
            "github.com": "https://github.com/",
            "example.com": "https://example.com/",
        }
        for domain, url in known_domains.items():
            if domain in lower:
                return url
        return None

    def _extract_web_question(self, text: str) -> str:
        question = " ".join(self._non_route_lines(text)).strip() or text.strip()
        question = re.sub(r"https?://[^\s<>'\")]+", " ", question, flags=re.I)
        question = re.sub(
            r"(?i)\b(?:www\.)?(seznam\.cz|novinky\.cz|idnes\.cz|example\.com|github\.com)\b/?",
            " ",
            question,
        )
        cleanup_patterns = (
            r"(?i)\bst[aá]hni(?:\s+mi)?(?:\s+to)?(?:\s+z)?\b",
            r"(?i)\bst[aá]hnout(?:\s+mi)?(?:\s+to)?(?:\s+z)?\b",
            r"(?i)\bnacti(?:\s+mi)?(?:\s+to)?(?:\s+z)?\b",
            r"(?i)\bna[cč]ti(?:\s+mi)?(?:\s+to)?(?:\s+z)?\b",
            r"(?i)\bpod[ií]vej\s+se(?:\s+na)?\b",
            r"(?i)\bfetch\b",
            r"(?i)\bdownload\b",
            r"(?i)\bz\s+webu\b",
            r"(?i)\bz\s+internetu\b",
        )
        for pattern in cleanup_patterns:
            question = re.sub(pattern, " ", question)
        question = re.sub(r"(?i)\b(url|str[aá]nku|web|website|site)\b", " ", question)
        question = re.sub(r"\s+", " ", question).strip(" ,.;:-")
        return question or (" ".join(self._non_route_lines(text)).strip() or text.strip())[:1200]

    def _natural_admin_command(self, body: dict, text: str) -> str | None:
        model = str(body.get("model") or "")
        if not model.startswith("codex-local-") or not text or "GATEWAY_ADMIN_" in text:
            return None
        return self._agent_loop_command_for_text(text)

    def _agent_loop_command_for_text(self, text: str) -> str | None:
        task_text = self._agent_loop_task_text(text)
        if not task_text:
            return None
        workspace = self._workspace_from_text(text) or "ai-stack"
        return f"GATEWAY_ADMIN_AGENT_LOOP {shlex.quote(workspace)} -- {shlex.quote(task_text[:3000])}"

    def _agent_loop_task_text(self, text: str) -> str:
        lines = []
        for line in str(text or "").splitlines():
            if re.search(rf"(?im)^\s*{WORKSPACE_LABEL_PATTERN}\s*:?\s*[A-Za-z0-9_.-]{{1,80}}\s*$", line):
                continue
            if line.strip():
                lines.append(line.strip())
        return " ".join(lines).strip() or str(text or "").strip()

    def _assistant_text(self, body: dict) -> str:
        if isinstance(body.get("choices"), list) and body["choices"]:
            msg = body["choices"][0].get("message") or {}
            content = msg.get("content")
            return content if isinstance(content, str) else ""
        return self._last_message_text(body, "assistant")

    def _append_assistant_text(self, body: dict, suffix: str) -> None:
        if isinstance(body.get("choices"), list) and body["choices"]:
            msg = body["choices"][0].setdefault("message", {})
            msg["content"] = (msg.get("content") or "") + suffix
            return
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") == "assistant":
                msg["content"] = (msg.get("content") or "") + suffix
                return

    def _replace_last_user_text(self, body: dict, old: str, new: str) -> None:
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str) and content == old:
                msg["content"] = new
            return

    def _admin_command_requested(self, text: str, command: str) -> bool:
        return re.search(rf"(?im)^\s*{re.escape(command)}(?:\s|$)", text) is not None

    def _direct_response(self, body: dict, text: str) -> dict:
        body["stream"] = False
        body["messages"] = [{"role": "user", "content": "GATEWAY_ADMIN_DIRECT_RESPONSE\n" + text}]
        return body

    def _apply_from_text(self, text: str) -> str:
        patches = self._extract_patches(text)
        if not patches:
            return "NO_PATCH_FOUND: marker was present but no fenced diff block was found."

        results = []
        changed = []
        for patch in patches:
            files = self._parse_unified_diff(patch)
            if not files:
                return "NO_PATCH_FILES: unified diff did not contain file hunks."
            for rel, hunks in files:
                changed.append(rel)
                results.append(self._apply_file(rel, hunks))

        for rel in sorted(set(changed)):
            if rel.endswith(".py"):
                py_compile.compile(str(self._target(rel)), doraise=True)

        return "PATCH_APPLIED\n" + "\n".join(results)

    def _schedule_apply(self, text: str) -> str:
        delay = 30.0

        def worker():
            time.sleep(delay)
            try:
                result = self._apply_from_text(text)
            except Exception as exc:
                result = f"PATCH_FAILED {type(exc).__name__}: {exc}"
            self._admin_log(result)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return f"PATCH_SCHEDULED delay_seconds={int(delay)} log=codex/audit/openwebui-gateway-admin.log"

    def _admin_log(self, text: str) -> None:
        try:
            root = self._repo_root()
            log = root / "codex/audit/openwebui-gateway-admin.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] "))
                f.write(text.replace("\n", " | "))
                f.write("\n")
        except Exception:
            pass

    def _extract_patches(self, text: str) -> list[str]:
        marker_pos = text.find(self.valves.marker)
        if marker_pos < 0:
            return []
        tail = text[marker_pos + len(self.valves.marker):]
        blocks = re.findall(r"```(?:diff|patch)?\s*\n(.*?)```", tail, flags=re.DOTALL | re.IGNORECASE)
        if blocks:
            return [b.strip("\n") + "\n" for b in blocks if b.strip()]
        tail = tail.strip()
        return [tail + "\n"] if tail.startswith(("diff --git ", "--- ")) else []

    def _parse_unified_diff(self, patch: str):
        lines = patch.splitlines(keepends=True)
        files = []
        i = 0
        while i < len(lines):
            if lines[i].startswith("diff --git "):
                i += 1
                continue
            if not lines[i].startswith("--- "):
                i += 1
                continue
            old_path = lines[i][4:].strip().split("\t", 1)[0]
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                raise ValueError("Malformed patch: expected +++ after ---")
            new_path = lines[i][4:].strip().split("\t", 1)[0]
            rel = self._clean_patch_path(new_path if new_path != "/dev/null" else old_path)
            self._assert_allowed(rel)
            i += 1
            hunks = []
            while i < len(lines):
                if lines[i].startswith(("diff --git ", "--- ")):
                    break
                if not lines[i].startswith("@@ "):
                    i += 1
                    continue
                header = lines[i]
                i += 1
                hunk_lines = []
                while i < len(lines) and not lines[i].startswith(("@@ ", "diff --git ", "--- ")):
                    if not lines[i].startswith("\\ No newline"):
                        hunk_lines.append(lines[i])
                    i += 1
                hunks.append((header, hunk_lines))
            files.append((rel, hunks))
        return files

    def _apply_file(self, rel: str, hunks) -> str:
        target = self._target(rel)
        existed = target.exists()
        original = target.read_text(encoding="utf-8").splitlines(keepends=True) if existed else []
        output = []
        pos = 0
        for header, hunk_lines in hunks:
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
            if not m:
                raise ValueError(f"Malformed hunk header for {rel}: {header.strip()}")
            old_start = max(0, int(m.group(1)) - 1)
            if old_start < pos:
                raise ValueError(f"Overlapping hunks in {rel}")
            output.extend(original[pos:old_start])
            cur = old_start
            for line in hunk_lines:
                if not line:
                    continue
                tag = line[0]
                value = line[1:]
                if tag == " ":
                    if cur >= len(original) or not self._same_line(original[cur], value):
                        raise ValueError(f"Context mismatch in {rel} near line {cur + 1}")
                    output.append(original[cur])
                    cur += 1
                elif tag == "-":
                    if cur >= len(original) or not self._same_line(original[cur], value):
                        raise ValueError(f"Delete mismatch in {rel} near line {cur + 1}")
                    cur += 1
                elif tag == "+":
                    output.append(value)
                else:
                    # OpenWebUI/chat copy-paste sometimes strips the leading
                    # space from unified-diff context lines. Treat such bare
                    # lines as context, but still require an exact match.
                    value = line
                    if cur >= len(original) or not self._same_line(original[cur], value):
                        raise ValueError(f"Context mismatch in {rel} near line {cur + 1}")
                    output.append(original[cur])
                    cur += 1
            pos = cur
        output.extend(original[pos:])
        stamp = time.strftime("%Y%m%d%H%M%S")
        target.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            shutil.copy2(target, target.with_name(target.name + ".bak-" + stamp))
        target.write_text("".join(output), encoding="utf-8")
        action = "WROTE" if existed else "CREATED"
        return f"{action} {rel} hunks={len(hunks)}"

    def _same_line(self, left: str, right: str) -> bool:
        return left.rstrip("\r\n") == right.rstrip("\r\n")

    def _clean_patch_path(self, path: str) -> str:
        path = path.strip().strip('"')
        if path.startswith(("a/", "b/")):
            path = path[2:]
        path = path.lstrip("/")
        if not path or ".." in Path(path).parts:
            raise ValueError(f"Unsafe patch path: {path!r}")
        return path

    def _assert_allowed(self, rel: str) -> None:
        if rel in self.allowed_exact:
            return
        if rel.startswith("docs/") and rel.endswith(".md"):
            return
        if rel.startswith("codex/bin/") and rel.endswith((".py", ".sh")):
            return
        if rel.startswith("codex/gateway/") and rel.endswith(".py"):
            return
        if rel.startswith("openwebui/") and rel.endswith((".js", ".css")):
            return
        if rel.startswith("codex/bin/") and rel.endswith(".md"):
            return
        raise PermissionError(f"Path is not whitelisted for gateway admin edits: {rel}")

    def _read_requested_file(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_READ\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_READ <whitelisted-relative-path>")
        rel = self._clean_patch_path(m.group(1))
        self._assert_allowed(rel)
        target = self._target(rel)
        if not target.is_file():
            raise FileNotFoundError(f"Whitelisted file does not exist: {rel}")
        data = target.read_text(encoding="utf-8", errors="replace")
        max_chars = 30000
        suffix = "" if len(data) <= max_chars else f"\n\n[truncated at {max_chars} chars]"
        return f"FILE {rel}\n--- BEGIN ---\n{data[:max_chars]}\n--- END ---{suffix}"

    def _read_numbered_requested_file(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_READ_NUMBERED\s+(\S+)(?:\s+(\d+))?(?:\s+(\d+))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_READ_NUMBERED <whitelisted-relative-path> [start-line] [end-line]")
        rel = self._clean_patch_path(m.group(1))
        self._assert_allowed(rel)
        start = int(m.group(2) or "1")
        end = int(m.group(3) or str(start + 199))
        if start < 1 or end < start:
            raise ValueError("Line range must be positive and end must be >= start")
        if end - start > 399:
            raise ValueError("Read range is limited to 400 lines")
        target = self._target(rel)
        if not target.is_file():
            raise FileNotFoundError(f"Whitelisted file does not exist: {rel}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        end = min(end, len(lines))
        width = max(4, len(str(end)))
        body = "\n".join(f"{idx:0{width}d}: {lines[idx - 1]}" for idx in range(start, end + 1))
        return f"FILE {rel} lines={start}-{end} total={len(lines)}\n--- BEGIN NUMBERED ---\n{body}\n--- END NUMBERED ---"

    def _explain_file_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_EXPLAIN_FILE\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_EXPLAIN_FILE <workspace> <relative-path> [start-line] [end-line] [-- question]")
        parts = shlex.split(m.group(1))
        if len(parts) < 2:
            raise ValueError("Usage: GATEWAY_ADMIN_EXPLAIN_FILE <workspace> <relative-path> [start-line] [end-line] [-- question]")
        workspace = parts[0]
        rel = parts[1]
        start = 1
        end = None
        question_parts: list[str] = []
        rest = parts[2:]
        if "--" in rest:
            idx = rest.index("--")
            numeric = rest[:idx]
            question_parts = rest[idx + 1 :]
        else:
            numeric = rest
        if numeric:
            start = int(numeric[0])
        if len(numeric) > 1:
            end = int(numeric[1])
        if len(numeric) > 2:
            raise ValueError("GATEWAY_ADMIN_EXPLAIN_FILE accepts at most start and end before --")

        payload: dict[str, object] = {
            "workspace": workspace,
            "path": rel,
            "start": start,
        }
        if end is not None:
            payload["end"] = end
        if question_parts:
            payload["question"] = " ".join(question_parts)

        result = self._gateway_admin_request("/v1/admin/file/explain", payload, timeout=360)
        status = "FILE_EXPLAIN_OK" if result.get("ok") else "FILE_EXPLAIN_FAILED"
        answer = str(result.get("answer", "")).strip()
        return (
            f"{status}\n"
            f"workspace={result.get('workspace') or workspace}\n"
            f"path={result.get('path') or rel}\n"
            f"lines={result.get('start')}-{result.get('end')} total={result.get('total_lines')}\n"
            f"truncated={result.get('truncated')}\n"
            "answer:\n"
            f"{answer}"
            + self._details("source_numbered", self._trim(str(result.get("numbered", "")), 12000))
        ).rstrip()

    def _ssh_keygen(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_SSH_KEYGEN(?:\s+([A-Za-z0-9_.-]+))?(?:\s+(.+?))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_SSH_KEYGEN [safe-name] [comment]")

        name = m.group(1) or "github-ai-stack"
        comment = (m.group(2) or f"{name}@ai-stack-local").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError("SSH key name must match [A-Za-z0-9_.-]{1,64}")
        if "\n" in comment or len(comment) > 160:
            raise ValueError("SSH key comment must be a single line up to 160 chars")

        root = self._repo_root()
        key_dir = root / "codex/state/ssh"
        key_path = key_dir / f"{name}_ed25519"
        pub_path = Path(str(key_path) + ".pub")
        key_rel = key_path.relative_to(root).as_posix()
        pub_rel = pub_path.relative_to(root).as_posix()

        key_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(key_dir, 0o700)
        except OSError:
            pass

        ssh_keygen = shutil.which("ssh-keygen")
        if not ssh_keygen:
            try:
                status = self._ssh_keygen_with_cryptography(key_path, pub_path, comment)
            except ImportError:
                return (
                    "SSH_KEYGEN_MISSING\n"
                    "Neither ssh-keygen nor Python cryptography is available in the environment that runs OpenWebUI.\n"
                    "Install openssh-client in that container/environment, or generate the key manually in WSL:\n"
                    f"mkdir -p {key_dir}\n"
                    f"ssh-keygen -t ed25519 -N '' -C '{comment}' -f '{key_path}'\n"
                    f"chmod 700 '{key_dir}' && chmod 600 '{key_path}' && chmod 644 '{pub_path}'"
                )
        else:
            if key_path.exists():
                if not pub_path.exists():
                    proc = subprocess.run(
                        [ssh_keygen, "-y", "-f", str(key_path)],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        timeout=20,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError("Existing private key found, but public key could not be derived:\n" + proc.stdout)
                    pub_path.write_text(proc.stdout.strip() + "\n", encoding="utf-8")
                status = "SSH_KEY_EXISTS"
            else:
                if pub_path.exists():
                    raise FileExistsError(f"Public key already exists without private key: {pub_rel}")
                proc = subprocess.run(
                    [ssh_keygen, "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key_path)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                )
                if proc.returncode != 0:
                    raise RuntimeError("ssh-keygen failed:\n" + proc.stdout)
                status = "SSH_KEY_READY"

        for path, mode in [(key_path, 0o600), (pub_path, 0o644)]:
            try:
                os.chmod(path, mode)
            except OSError:
                pass

        ignore_status = self._git_ignore_status(root, key_rel)
        public_key = pub_path.read_text(encoding="utf-8").strip()
        private_mode = oct(key_path.stat().st_mode & 0o777)
        public_mode = oct(pub_path.stat().st_mode & 0o777)
        permission_status = "strict" if (key_path.stat().st_mode & 0o077) == 0 else "WARNING_private_key_permissions_are_too_open_on_this_mount"
        return (
            f"{status}\n"
            f"private_key_path={key_rel}\n"
            f"public_key_path={pub_rel}\n"
            f"private_key_mode={private_mode}\n"
            f"public_key_mode={public_mode}\n"
            f"permission_status={permission_status}\n"
            f"git_ignore_status={ignore_status}\n"
            "private_key_value=NOT_PRINTED\n"
            "public_key_value:\n"
            f"{public_key}"
        )

    def _ssh_keygen_with_cryptography(self, key_path: Path, pub_path: Path, comment: str) -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        if key_path.exists():
            if not pub_path.exists():
                private_key = serialization.load_ssh_private_key(key_path.read_bytes(), password=None)
                public_key = private_key.public_key()
                public_text = public_key.public_bytes(
                    serialization.Encoding.OpenSSH,
                    serialization.PublicFormat.OpenSSH,
                ).decode("utf-8")
                pub_path.write_text(f"{public_text} {comment}\n", encoding="utf-8")
            return "SSH_KEY_EXISTS"

        if pub_path.exists():
            raise FileExistsError(f"Public key already exists without private key: {pub_path}")

        private_key = ed25519.Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
        key_path.write_bytes(private_bytes)
        pub_path.write_text(public_bytes.decode("utf-8") + f" {comment}\n", encoding="utf-8")
        return "SSH_KEY_READY"

    def _install_ssh_client(self) -> str:
        if shutil.which("ssh"):
            return "SSH_CLIENT_READY\nssh_path=" + str(shutil.which("ssh"))
        if os.geteuid() != 0:
            return (
                "SSH_CLIENT_INSTALL_BLOCKED\n"
                f"current_uid={os.geteuid()}\n"
                "OpenWebUI process is not root, so it cannot install packages itself.\n"
                "Run manually in WSL:\n"
                "sudo docker exec -u root open-webui sh -lc 'apt-get update && apt-get install -y --no-install-recommends openssh-client || apk add --no-cache openssh-client'\n"
            )

        commands = []
        if shutil.which("apt-get"):
            commands.append(["sh", "-lc", "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client"])
        if shutil.which("apk"):
            commands.append(["apk", "add", "--no-cache", "openssh-client"])
        if shutil.which("dnf"):
            commands.append(["dnf", "install", "-y", "openssh-clients"])
        if shutil.which("yum"):
            commands.append(["yum", "install", "-y", "openssh-clients"])
        if not commands:
            return (
                "SSH_CLIENT_INSTALL_BLOCKED\n"
                "No supported package manager found in OpenWebUI environment.\n"
                "Install openssh-client in the OpenWebUI image/container manually."
            )

        outputs = []
        for cmd in commands:
            proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
            outputs.append("$ " + " ".join(cmd) + "\n" + proc.stdout[-4000:])
            if proc.returncode == 0 and shutil.which("ssh"):
                return "SSH_CLIENT_INSTALLED\nssh_path=" + str(shutil.which("ssh")) + "\ninstall_output_tail:\n" + outputs[-1]
        return "SSH_CLIENT_INSTALL_FAILED\n" + "\n\n".join(outputs)

    def _git_ignore_status(self, root: Path, rel: str) -> str:
        git = shutil.which("git")
        if git:
            proc = subprocess.run([git, "-C", str(root), "check-ignore", "--no-index", "-q", rel], timeout=10)
            if proc.returncode == 0:
                return "ignored"
        gitignore = root / ".gitignore"
        if gitignore.is_file():
            text = gitignore.read_text(encoding="utf-8", errors="replace")
            if rel.startswith("codex/state/") and "codex/state/" in text:
                return "git_check_failed_but_codex_state_is_listed_in_gitignore"
        return "unknown_git_not_available"

    def _run_git(self, root: Path, args: list[str], timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
        git = shutil.which("git")
        if not git:
            raise RuntimeError("git is not available in the OpenWebUI environment")
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            [git, "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=merged_env,
        )

    def _ensure_safe_git_repo(self, root: Path) -> str:
        proc = self._run_git(root, ["config", "--global", "--add", "safe.directory", str(root)], timeout=20)
        return proc.stdout.strip()

    def _git_status_lines(self, root: Path) -> list[str]:
        proc = self._run_git(root, ["status", "--porcelain=v1", "-uall"], timeout=30)
        if proc.returncode != 0:
            raise RuntimeError("git status failed:\n" + proc.stdout)
        return [line for line in proc.stdout.splitlines() if line.strip()]

    def _git_status(self) -> str:
        root = self._repo_root()
        safe_out = self._ensure_safe_git_repo(root)
        branch = self._run_git(root, ["branch", "--show-current"], timeout=20).stdout.strip() or "(detached)"
        remote = self._run_git(root, ["remote", "-v"], timeout=20).stdout.strip()
        status = self._git_status_lines(root)
        sensitive = [p for p in self._status_paths(status) if self._is_sensitive_path(p)]
        allowed, blocked = self._classify_status_paths(status)
        return (
            "GIT_STATUS\n"
            f"repo={root}\n"
            f"safe_directory_configured=yes\n"
            f"safe_directory_output={safe_out}\n"
            f"branch={branch}\n"
            "remote:\n"
            f"{remote or '(none)'}\n"
            "changed_paths:\n"
            + ("\n".join(status) if status else "(clean)")
            + "\nallowed_for_auto_commit:\n"
            + ("\n".join(allowed) if allowed else "(none)")
            + "\nblocked_paths:\n"
            + ("\n".join(blocked) if blocked else "(none)")
            + "\nsensitive_paths_seen:\n"
            + ("\n".join(sensitive) if sensitive else "(none)")
        )

    def _git_diff(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_GIT_DIFF(?:\s+(\S+))?\s*$", text)
        requested = m.group(1).strip() if m and m.group(1) else None
        root = self._repo_root()
        self._ensure_safe_git_repo(root)
        status = self._git_status_lines(root)
        allowed, blocked = self._classify_status_paths(status)
        paths = allowed
        if requested:
            rel = self._clean_patch_path(requested)
            if not self._is_commit_allowed_path(rel):
                raise PermissionError(f"Path is not allowed for git diff: {rel}")
            paths = [rel]
        if not paths:
            return "GIT_DIFF\nchanged_allowed_paths=(none)\nblocked_paths:\n" + ("\n".join(blocked) if blocked else "(none)")

        sections = []
        for rel in sorted(set(paths)):
            status_line = next((line for line in status if self._status_paths([line])[0] == rel), "")
            if status_line.startswith("??"):
                sections.append(f"--- {rel} ---\n[untracked allowed file; content not shown by git diff. Use GATEWAY_ADMIN_READ_NUMBERED for review.]")
                continue
            worktree = self._run_git(root, ["diff", "--", rel], timeout=30)
            cached = self._run_git(root, ["diff", "--cached", "--", rel], timeout=30)
            if worktree.returncode != 0:
                raise RuntimeError(f"git diff failed for {rel}:\n{worktree.stdout}")
            if cached.returncode != 0:
                raise RuntimeError(f"git diff --cached failed for {rel}:\n{cached.stdout}")
            text_parts = []
            if cached.stdout.strip():
                text_parts.append("[cached]\n" + cached.stdout.strip())
            if worktree.stdout.strip():
                text_parts.append("[worktree]\n" + worktree.stdout.strip())
            sections.append(f"--- {rel} ---\n" + ("\n".join(text_parts) if text_parts else "[no textual diff]"))

        body = "\n\n".join(sections)
        if len(body) > 30000:
            body = body[:30000] + "\n[truncated at 30000 chars]"
        return (
            "GIT_DIFF\n"
            "allowed_paths:\n"
            + "\n".join(sorted(set(paths)))
            + "\nblocked_paths:\n"
            + ("\n".join(blocked) if blocked else "(none)")
            + "\ndiff:\n"
            + body
        )

    def _repo_guard(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_REPO_GUARD(?:\s+([A-Za-z0-9_.-]+))?(?:\s+([A-Za-z0-9_.\/-]+))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_REPO_GUARD [workspace] [branch]")
        workspace = (m.group(1) or "ai-stack").strip()
        branch = (m.group(2) or "main").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")
        if not re.fullmatch(r"[A-Za-z0-9_.\/-]{1,120}", branch):
            raise ValueError("Unsafe branch name")

        root = self._repo_root()
        script = root / "codex/bin/repo_guard.py"
        workspaces_file = root / "codex/workspaces.json"
        if not script.is_file():
            raise FileNotFoundError("repo guard script is missing")
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--workspace",
                workspace,
                "--workspaces-file",
                str(workspaces_file),
                "--branch",
                branch,
                "--max-paths",
                "120",
            ],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
        return (
            "REPO_GUARD_RESULT\n"
            f"workspace={workspace}\n"
            f"branch={branch}\n"
            f"exit_code={proc.returncode}\n"
            "output:\n"
            + proc.stdout.strip()
        )

    def _workspace_scan(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_WORKSPACE_SCAN(?:\s+([A-Za-z0-9_.-]+))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_WORKSPACE_SCAN [workspace]")
        workspace = (m.group(1) or "ai-stack").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")

        root = self._repo_root()
        script = root / "codex/bin/workspace_scan.py"
        workspaces_file = root / "codex/workspaces.json"
        if not script.is_file():
            raise FileNotFoundError("workspace scan script is missing")
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--workspace",
                workspace,
                "--workspaces-file",
                str(workspaces_file),
                "--max-items",
                "80",
            ],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
        return (
            "WORKSPACE_SCAN_RESULT\n"
            f"workspace={workspace}\n"
            f"exit_code={proc.returncode}\n"
            "output:\n"
            + proc.stdout.strip()
        )

    def _status_paths(self, status_lines: list[str]) -> list[str]:
        paths = []
        for line in status_lines:
            raw = line[3:] if len(line) > 3 else line
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            paths.append(raw.strip())
        return paths

    def _is_sensitive_path(self, rel: str) -> bool:
        lower = rel.lower()
        sensitive_names = {".env", "id_rsa", "id_ed25519", "known_hosts"}
        if rel in sensitive_names or Path(rel).name in sensitive_names:
            return True
        return lower.startswith("codex/state/") or lower.startswith("codex/audit/") or lower.endswith(".pem") or lower.endswith(".key")

    def _is_runtime_ignored_path(self, rel: str) -> bool:
        lower = rel.lower()
        return (
            lower.startswith("logs/")
            or lower.startswith("codex/state/")
            or lower.startswith("codex/audit/")
            or lower.startswith("codex/workspaces/")
            or "__pycache__/" in lower
            or lower.endswith(".pyc")
            or ".bak-" in lower
            or rel == ".env"
        )

    def _is_commit_allowed_path(self, rel: str) -> bool:
        if self._is_sensitive_path(rel):
            return False
        if rel in self.allowed_exact:
            return True
        if rel.startswith("docs/") and rel.endswith(".md"):
            return True
        if rel.startswith("codex/bin/") and rel.endswith((".py", ".sh")):
            return True
        return False

    def _classify_status_paths(self, status_lines: list[str]) -> tuple[list[str], list[str]]:
        allowed = []
        blocked = []
        for line in status_lines:
            rel = self._status_paths([line])[0]
            staged_deletion = line.startswith("D ")
            if self._is_commit_allowed_path(rel) or (staged_deletion and self._is_runtime_ignored_path(rel)):
                allowed.append(rel)
            else:
                blocked.append(rel)
        return sorted(set(allowed)), sorted(set(blocked))

    def _runtime_ssh_env(self, root: Path) -> tuple[dict[str, str], str]:
        source_key = root / "codex/state/ssh/github-ai-stack_ed25519"
        if not source_key.is_file():
            raise FileNotFoundError("SSH private key not found. Run GATEWAY_ADMIN_SSH_KEYGEN first.")
        state_root = Path(os.getenv("DATA_DIR", "/app/backend/data")) / "codex-runtime/ssh"
        state_root.mkdir(parents=True, exist_ok=True)
        runtime_key = state_root / "github-ai-stack_ed25519"
        known_hosts = state_root / "known_hosts"
        runtime_key.write_bytes(source_key.read_bytes())
        try:
            os.chmod(state_root, 0o700)
            os.chmod(runtime_key, 0o600)
        except OSError:
            pass
        mode = runtime_key.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError(f"Runtime SSH key permissions are too open: {oct(mode)}")
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("ssh client is not available in the OpenWebUI environment")
        env = {
            "GIT_SSH_COMMAND": (
                f"{ssh} -i {runtime_key} -o IdentitiesOnly=yes "
                f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={known_hosts}"
            )
        }
        return env, runtime_key.as_posix()

    def _git_untrack_ignored(self) -> str:
        root = self._repo_root()
        self._ensure_safe_git_repo(root)
        status = self._git_status_lines(root)
        candidates = [rel for rel in self._status_paths(status) if self._is_runtime_ignored_path(rel)]
        if not candidates:
            return "GIT_UNTRACK_IGNORED_OK\nremoved_from_index=(none)"

        removed = []
        skipped = []
        for rel in sorted(set(candidates)):
            proc = self._run_git(root, ["rm", "--cached", "--ignore-unmatch", "--", rel], timeout=30)
            if proc.returncode == 0:
                removed.append(rel)
            else:
                skipped.append(f"{rel}: {proc.stdout.strip()}")

        return (
            "GIT_UNTRACK_IGNORED_OK\nremoved_from_index:\n"
            + ("\n".join(removed) if removed else "(none)")
            + "\nskipped:\n"
            + ("\n".join(skipped) if skipped else "(none)")
        )

    def _gateway_smoke(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_SMOKE(?:\s+(\S+))?(?:\s+([A-Za-z0-9_.-]+))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_SMOKE [base-url|workspace] [workspace]")
        base_url = os.getenv("CODEX_GATEWAY_PUBLIC_URL", "http://192.168.0.48:9101")
        workspace = "ai-stack"
        if m.group(1):
            first = m.group(1).strip()
            if first.startswith(("http://", "https://")):
                base_url = first
                if m.group(2):
                    workspace = m.group(2).strip()
            else:
                workspace = first
                if m.group(2):
                    workspace = m.group(2).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")

        root = self._repo_root()
        script = root / "codex/bin/codex_gateway_smoke.py"
        if not script.is_file():
            raise FileNotFoundError("codex gateway smoke runner is missing")
        proc = subprocess.run(
            [sys.executable, str(script), "--base-url", base_url, "--workspace", workspace, "--timeout", "90"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        status = "GATEWAY_SMOKE_OK" if proc.returncode == 0 else "GATEWAY_SMOKE_FAILED"
        return (
            f"{status}\n"
            f"base_url={base_url}\n"
            f"workspace={workspace}\n"
            f"exit_code={proc.returncode}\n"
            "output:\n"
            + proc.stdout.strip()
        )

    def _check_ai_stack(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_CHECK_STACK(?:\s+([A-Za-z0-9_.-]+))?(?:\s+([A-Za-z0-9_.:-]+))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_CHECK_STACK [workspace] [model]")
        workspace = (m.group(1) or "ai-stack").strip()
        model = (m.group(2) or "codex-local-plan-qwen14b").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", model):
            raise ValueError("Unsafe model name")

        root = self._repo_root()
        script = root / "codex/bin/check_ai_stack.sh"
        if not script.is_file():
            raise FileNotFoundError("AI stack healthcheck script is missing")
        bash = shutil.which("bash")
        if not bash:
            raise FileNotFoundError("bash is required to run check_ai_stack.sh")

        env = os.environ.copy()
        env.update({
            "OPENWEBUI_URL": os.getenv("OPENWEBUI_INTERNAL_URL", os.getenv("OPENWEBUI_PUBLIC_URL", "http://127.0.0.1:8080")),
            "CODEX_GATEWAY_URL": os.getenv("CODEX_GATEWAY_PUBLIC_URL", "http://192.168.0.48:9101"),
            "OLLAMA_URL": os.getenv("OLLAMA_BASE_URL", "http://192.168.0.48:11434"),
            "WORKSPACE": workspace,
            "MODEL": model,
            "CHECK_AI_STACK_SUMMARY_ONLY": "1",
            "SKIP_OPENWEBUI": "1",
        })
        proc = subprocess.run(
            [bash, str(script)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            env=env,
        )
        status = "AI_STACK_CHECK_OK" if proc.returncode == 0 else "AI_STACK_CHECK_FAILED"
        parsed = self._parse_key_value_block(proc.stdout)
        summary_text = self._normalize_summary_lines(parsed.get("summary", ""))
        return (
            f"{status}\n"
            f"workspace={parsed.get('workspace', workspace)}\n"
            f"model={parsed.get('model', model)}\n"
            f"status={parsed.get('status', 'UNKNOWN')}\n"
            f"checks_total={parsed.get('checks_total', '(unknown)')}\n"
            f"checks_passed={parsed.get('checks_passed', '(unknown)')}\n"
            f"checks_failed={parsed.get('checks_failed', '(unknown)')}\n"
            f"checks_skipped={parsed.get('checks_skipped', '(unknown)')}\n"
            f"exit_code={proc.returncode}\n"
            + self._details("summary", summary_text)
        )

    def _run_workspace_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_RUN_WORKSPACE\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_RUN_WORKSPACE <workspace> [--timeout seconds] [--runner container|host] [--env KEY=VALUE] -- <command> [args...]")
        parts = shlex.split(m.group(1))
        if not parts:
            raise ValueError("Usage: GATEWAY_ADMIN_RUN_WORKSPACE <workspace> [--timeout seconds] [--runner container|host] [--env KEY=VALUE] -- <command> [args...]")

        workspace = parts.pop(0)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")

        timeout = 300
        env_map = {}
        runner = "container"
        background = False
        while parts and parts[0] != "--":
            opt = parts.pop(0)
            if opt == "--timeout" and parts:
                timeout = int(parts.pop(0))
                continue
            if opt == "--runner" and parts:
                runner = parts.pop(0)
                if runner not in {"container", "host"}:
                    raise ValueError("--runner must be container or host")
                continue
            if opt == "--env" and parts:
                item = parts.pop(0)
                if "=" not in item:
                    raise ValueError("--env expects KEY=VALUE")
                key, value = item.split("=", 1)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
                    raise ValueError(f"Unsafe env key: {key}")
                env_map[key] = value
                continue
            if opt == "--background":
                background = True
                continue
            if opt == "--foreground":
                background = False
                continue
            raise ValueError(f"Unknown GATEWAY_ADMIN_RUN_WORKSPACE option before --: {opt}")

        if not parts or parts[0] != "--":
            raise ValueError("GATEWAY_ADMIN_RUN_WORKSPACE requires -- before the command")
        command = parts[1:]
        if not command:
            raise ValueError("GATEWAY_ADMIN_RUN_WORKSPACE command is empty")
        if timeout < 1 or timeout > 1800:
            raise ValueError("Timeout must be between 1 and 1800 seconds")
        command = self._canonicalize_nested_helper_command(command)
        if any("mentor_codex_local.py" in item or "owui_chat_turn.py" in item for item in command):
            background = True
            env_map.setdefault("OWUI_STATELESS", "1")

        rescue = self._workspace_run_rescue_command(workspace, command)
        if rescue:
            return self._dispatch_admin_command(rescue)

        payload = {"workspace": workspace, "timeout": timeout, "runner": runner, "command": command}
        if runner == "host":
            payload["allow_host"] = True
        if background:
            payload["background"] = True
        if env_map:
            payload["env"] = env_map
        result = self._gateway_admin_request("/v1/admin/workspace/run", payload, timeout=max(timeout + 45, 90))
        output = str(result.get("output", ""))
        output = self._trim(output, 24000)
        if result.get("background"):
            status = "WORKSPACE_RUN_SCHEDULED" if result.get("ok") else "WORKSPACE_RUN_SCHEDULE_FAILED"
            return (
                f"{status}\n"
                f"workspace={result.get('workspace', workspace)}\n"
                f"runner={result.get('runner', runner)}\n"
                f"job_id={result.get('job_id', '')}\n"
                f"pid={result.get('pid')}\n"
                f"log={result.get('log')}\n"
                f"command={self._shell_join(result.get('command', command))}\n"
                f"executed_command={self._shell_join(result.get('executed_command', []))}\n"
            ).rstrip()
        status = "WORKSPACE_RUN_OK" if result.get("ok") else "WORKSPACE_RUN_FAILED"
        return (
            f"{status}\n"
            f"workspace={result.get('workspace', workspace)}\n"
            f"runner={result.get('runner', runner)}\n"
            f"container={result.get('container', '')}\n"
            f"cwd={result.get('cwd', '(unknown)')}\n"
            f"command={self._shell_join(result.get('command', command))}\n"
            f"executed_command={self._shell_join(result.get('executed_command', []))}\n"
            f"exit_code={result.get('exit_code')}\n"
            f"runner_exit_code={result.get('runner_exit_code')}\n"
            f"duration_ms={result.get('duration_ms', '(unknown)')}\n"
            + self._details("output", output)
        )

    def _run_workspace_status_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_RUN_WORKSPACE_STATUS\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_RUN_WORKSPACE_STATUS <job-id|workspace>")
        token = shlex.split(m.group(1))
        if not token:
            raise ValueError("Usage: GATEWAY_ADMIN_RUN_WORKSPACE_STATUS <job-id|workspace>")
        value = token[0]
        payload = {"job_id": value} if "-" in value else {"workspace": value}
        result = self._gateway_admin_request("/v1/admin/workspace/run/status", payload, timeout=60)
        status = "WORKSPACE_RUN_STATUS_OK" if result.get("ok") else "WORKSPACE_RUN_STATUS_FAILED"
        parsed = result.get("result") or {}
        return (
            f"{status}\n"
            f"job_id={result.get('job_id', value)}\n"
            f"workspace={result.get('workspace', '')}\n"
            f"running={result.get('running')}\n"
            f"pid={result.get('pid')}\n"
            f"log={result.get('log', '')}\n"
            f"exit_code={result.get('exit_code')}\n"
            f"runner_exit_code={result.get('runner_exit_code')}\n"
            f"duration_ms={result.get('duration_ms', '')}\n"
            + (self._details("result", self._trim(json.dumps(parsed, ensure_ascii=False, indent=2), 12000)) if parsed else "")
            + (self._details("tail", self._trim(str(result.get('tail', '')), 12000)) if result.get("tail") else "")
            + (self._details("error", str(result.get("error", ""))) if result.get("error") else "")
        )

    def _workspace_run_rescue_command(self, workspace: str, command: list[str]) -> str | None:
        parsed = self._parse_mentor_helper_command(command)
        if not parsed:
            return None
        mode = parsed["mode"]
        helper_workspace = parsed["workspace"]
        task = parsed["task"]
        task_lower = task.lower()
        if mode == "delegate" and helper_workspace and helper_workspace.lower() not in {"ai-stack", "smoke"}:
            if self._looks_like_create_workspace_request(task):
                return f"GATEWAY_ADMIN_CREATE_LOCAL_REPO {shlex.quote(helper_workspace)}"
            if any(cue in task_lower for cue in ("ssh key", "ssh klic", "ssh klíč", "github key")):
                key_name = re.sub(r"[^A-Za-z0-9_.-]", "-", f"github-{helper_workspace}")[:64].strip("-") or "github-workspace"
                return f"GATEWAY_ADMIN_SSH_KEYGEN {shlex.quote(key_name)} {shlex.quote(helper_workspace + '@local')}"
        agent_loop_task = self._mentor_helper_agent_loop_task(parsed)
        if helper_workspace and agent_loop_task:
            return f"GATEWAY_ADMIN_AGENT_LOOP {shlex.quote(helper_workspace)} -- {shlex.quote(agent_loop_task[:3000])}"
        return None

    def _canonicalize_nested_helper_command(self, command: list[str]) -> list[str]:
        normalized = list(command)
        if len(normalized) >= 2 and normalized[0] == "python3":
            script = normalized[1]
            if script == "codex/bin/mentor_codex_local.py":
                if "--stateless-turns" not in normalized:
                    mode_idx = None
                    for idx in range(2, len(normalized)):
                        if not normalized[idx].startswith("--"):
                            mode_idx = idx
                            break
                    if mode_idx is not None:
                        normalized.insert(mode_idx + 1, "--stateless-turns")
            elif script == "codex/bin/owui_chat_turn.py":
                if "--stateless" not in normalized:
                    normalized.insert(2, "--stateless")
        return normalized

    def _parse_mentor_helper_command(self, command: list[str]) -> dict[str, str] | None:
        if len(command) < 4:
            return None
        if command[0] != "python3" or command[1] != "codex/bin/mentor_codex_local.py":
            return None
        idx = 2
        while idx < len(command) and command[idx].startswith("--"):
            idx += 1
        if idx >= len(command):
            return None
        mode = command[idx]
        if mode not in {
            "delegate",
            "profile",
            "report",
            "plan",
            "next-helper",
            "bootstrap-improve",
            "audit",
            "review",
            "improve",
            "autopilot",
            "apply-safe",
        }:
            return None
        idx += 1
        while idx < len(command) and command[idx].startswith("--"):
            idx += 1
        if idx >= len(command):
            return None
        workspace = command[idx]
        idx += 1
        task = command[idx] if idx < len(command) else ""
        return {"mode": mode, "workspace": workspace, "task": task}

    def _mentor_helper_agent_loop_task(self, parsed: dict[str, str]) -> str:
        mode = str(parsed.get("mode") or "")
        workspace = str(parsed.get("workspace") or "").strip()
        task = str(parsed.get("task") or "").strip()
        if mode == "delegate":
            return task
        if mode == "review":
            return task or f"Proveď senior review workspace {workspace}. Nic needituj. Najdi hlavní rizika a navrhni další bezpečný krok."
        if mode == "audit":
            return task or f"Proveď technický audit workspace {workspace}. Nic needituj, pokud to není nutné. Navrhni jeden nejlepší další capability krok."
        if mode == "improve":
            return task or f"Pokračuj autonomně ve workspace {workspace}. Nejprve proveď nejbližší bezpečný capability krok, pak případný malý patch a vrať konkrétní výsledek."
        if mode == "autopilot":
            return task or f"Ověř workspace {workspace}, pokračuj nejbližším bezpečným capability krokem a vrať stručný průběh."
        if mode == "bootstrap-improve":
            return task or f"Bootstrapuj workspace {workspace}, připrav repozitář a pokračuj nejbližším bezpečným capability krokem."
        if mode == "apply-safe":
            return task or f"Připrav a auditovaně aplikuj malý bezpečný patch ve workspace {workspace}."
        return ""

    def _workspace_edit_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_WORKSPACE_EDIT\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_WORKSPACE_EDIT <workspace> [--timeout seconds] [--model qwen2.5-coder:14b|qwen2.5-coder:32b] [--max-files N] -- <task>")
        parts = shlex.split(m.group(1))
        if not parts:
            raise ValueError("Usage: GATEWAY_ADMIN_WORKSPACE_EDIT <workspace> [--timeout seconds] [--model qwen2.5-coder:14b|qwen2.5-coder:32b] [--max-files N] -- <task>")

        workspace = parts.pop(0)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")

        timeout = 900
        model = "qwen2.5-coder:14b"
        max_files = 6
        run_after = ""
        run_timeout = 900
        runner = "container"
        while parts and parts[0] != "--":
            opt = parts.pop(0)
            if opt == "--timeout" and parts:
                timeout = int(parts.pop(0))
                continue
            if opt == "--model" and parts:
                model = parts.pop(0)
                if model not in {"qwen2.5-coder:14b", "qwen2.5-coder:32b"}:
                    raise ValueError("Unsupported edit model")
                continue
            if opt == "--max-files" and parts:
                max_files = int(parts.pop(0))
                continue
            if opt == "--run-after" and parts:
                run_after = parts.pop(0).strip().lower()
                if run_after not in {"install", "verify", "smoke", "test", "build", "lint"}:
                    raise ValueError("run-after must be one of install, verify, smoke, test, build, lint")
                continue
            if opt == "--run-timeout" and parts:
                run_timeout = int(parts.pop(0))
                continue
            if opt == "--runner" and parts:
                runner = parts.pop(0)
                if runner not in {"container", "host"}:
                    raise ValueError("--runner must be container or host")
                continue
            raise ValueError(f"Unknown GATEWAY_ADMIN_WORKSPACE_EDIT option before --: {opt}")

        if not parts or parts[0] != "--":
            raise ValueError("GATEWAY_ADMIN_WORKSPACE_EDIT requires -- before the task")
        task = " ".join(parts[1:]).strip()
        if not task:
            raise ValueError("GATEWAY_ADMIN_WORKSPACE_EDIT task is empty")
        if timeout < 30 or timeout > 1800:
            raise ValueError("Timeout must be between 30 and 1800 seconds")
        if max_files < 1 or max_files > 20:
            raise ValueError("max-files must be between 1 and 20")

        payload = {
            "workspace": workspace,
            "task": task,
            "timeout": timeout,
            "model": model,
            "max_files": max_files,
        }
        if run_after:
            payload["run_after"] = run_after
            payload["run_timeout"] = run_timeout
            payload["runner"] = runner
        result = self._gateway_admin_request("/v1/admin/workspace/edit", payload, timeout=max(timeout + 90, 180))
        status = "WORKSPACE_EDIT_OK" if result.get("ok") else "WORKSPACE_EDIT_FAILED"
        files = result.get("files") or []
        files_text = "\n".join(f"- {item}" for item in files) if isinstance(files, list) else str(files)
        git_status = str(result.get("git_status", ""))
        diff = str(result.get("diff", ""))
        model_text = str(result.get("model_text", ""))
        error = str(result.get("error", "") or result.get("apply_output", ""))
        run_result = result.get("run_result") or {}
        run_output = ""
        if isinstance(run_result, dict) and run_result:
            run_output = str(run_result.get("output", ""))
        return (
            f"{status}\n"
            f"workspace={result.get('workspace', workspace)}\n"
            f"status={result.get('status', '(unknown)')}\n"
            f"model={result.get('model', model)}\n"
            f"run_after={result.get('run_after', '')}\n"
            f"duration_ms={result.get('duration_ms', '(unknown)')}\n"
            + (self._details("files", files_text) if files_text else "")
            + (self._details("git_status", git_status) if git_status else "")
            + (self._details("error", self._trim(error, 8000)) if error else "")
            + (self._details("run_result", self._trim(json.dumps(run_result, ensure_ascii=False, indent=2), 12000)) if run_result else "")
            + (self._details("run_output", self._trim(run_output, 12000)) if run_output else "")
            + self._details("diff", self._trim(diff, 24000))
            + (self._details("model_text", self._trim(model_text, 12000)) if not result.get("ok") and model_text else "")
        )

    def _workspace_action_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_WORKSPACE_ACTION\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_WORKSPACE_ACTION <workspace> <install|test|build|lint|verify|smoke> [--timeout seconds] [--runner container|host] [--env KEY=VALUE] [--dry-run]")
        parts = shlex.split(m.group(1))
        if len(parts) < 2:
            raise ValueError("Usage: GATEWAY_ADMIN_WORKSPACE_ACTION <workspace> <install|test|build|lint|verify|smoke> [--timeout seconds] [--runner container|host] [--env KEY=VALUE] [--dry-run]")

        workspace = parts.pop(0)
        action = parts.pop(0)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")
        spec = self._workspace_action_spec(action)
        if not spec:
            allowed = ", ".join(sorted(self.workspace_actions)) or "install, test, build, lint, verify, smoke"
            raise ValueError(f"Action must be one of {allowed}")

        timeout = int(spec.get("timeout", 900))
        env_map = {}
        dry_run = False
        runner = str(spec.get("runner", "container"))
        while parts:
            opt = parts.pop(0)
            if opt == "--timeout" and parts:
                timeout = int(parts.pop(0))
                continue
            if opt == "--runner" and parts:
                runner = parts.pop(0)
                if runner not in {"container", "host"}:
                    raise ValueError("--runner must be container or host")
                continue
            if opt == "--env" and parts:
                item = parts.pop(0)
                if "=" not in item:
                    raise ValueError("--env expects KEY=VALUE")
                key, value = item.split("=", 1)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
                    raise ValueError(f"Unsafe env key: {key}")
                env_map[key] = value
                continue
            if opt == "--dry-run":
                dry_run = True
                continue
            raise ValueError(f"Unknown GATEWAY_ADMIN_WORKSPACE_ACTION option: {opt}")

        if timeout < 1 or timeout > 3600:
            raise ValueError("Timeout must be between 1 and 3600 seconds")

        payload = {"workspace": workspace, "action": action, "timeout": timeout, "dry_run": dry_run, "runner": runner}
        if runner == "host":
            payload["allow_host"] = True
        if env_map:
            payload["env"] = env_map
        result = self._gateway_admin_request("/v1/admin/workspace/action", payload, timeout=max(timeout + 45, 90))
        output = self._trim(str(result.get("output", "")), 24000)
        steps = result.get("verify_steps") or []
        step_lines = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                action_name = str(step.get("action", "(unknown)"))
                if not step.get("supported", True):
                    step_lines.append(f"- {action_name}: skipped")
                    continue
                if step.get("ok") is True:
                    state = "ok"
                elif step.get("ok") is False:
                    state = f"failed ({step.get('error') or step.get('exit_code')})"
                else:
                    state = "planned"
                command = step.get("command") or []
                command_text = self._shell_join(command) if command else ""
                if command_text:
                    step_lines.append(f"- {action_name}: {state} command={command_text}")
                else:
                    step_lines.append(f"- {action_name}: {state}")
        status = "WORKSPACE_ACTION_OK" if result.get("ok") else "WORKSPACE_ACTION_FAILED"
        return (
            f"{status}\n"
            f"workspace={result.get('workspace', workspace)}\n"
            f"action={result.get('action', action)}\n"
            f"runner={result.get('runner', runner)}\n"
            f"container={result.get('container', '')}\n"
            f"resolved_from={result.get('resolved_from', '(unknown)')}\n"
            f"command={self._shell_join(result.get('command', []))}\n"
            f"executed_command={self._shell_join(result.get('executed_command', []))}\n"
            f"planned_only={result.get('planned_only', False)}\n"
            f"exit_code={result.get('exit_code')}\n"
            f"runner_exit_code={result.get('runner_exit_code')}\n"
            f"duration_ms={result.get('duration_ms', '(unknown)')}\n"
            + ("verify_steps:\n" + "\n".join(step_lines) + "\n" if step_lines else "")
            + self._details("output", output)
        )

    def _workspace_autopilot_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_WORKSPACE_AUTOPILOT\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_WORKSPACE_AUTOPILOT <workspace> [--timeout seconds] [--allow-actions install,verify,smoke,test,build,lint] [--max-steps N] [--recommend-only] [--env KEY=VALUE]")
        parts = shlex.split(m.group(1))
        if not parts:
            raise ValueError("Usage: GATEWAY_ADMIN_WORKSPACE_AUTOPILOT <workspace> [--timeout seconds] [--allow-actions install,verify,smoke,test,build,lint] [--max-steps N] [--recommend-only] [--env KEY=VALUE]")

        workspace = parts.pop(0)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")

        timeout = 1800
        allow_actions = ["install", "verify", "smoke", "test", "build", "lint"]
        max_steps = 1
        recommend_only = False
        env_map = {}
        while parts:
            opt = parts.pop(0)
            if opt == "--timeout" and parts:
                timeout = int(parts.pop(0))
                continue
            if opt == "--allow-actions" and parts:
                allow_actions = [x.strip().lower() for x in parts.pop(0).split(",") if x.strip()]
                continue
            if opt == "--max-steps" and parts:
                max_steps = int(parts.pop(0))
                continue
            if opt == "--recommend-only":
                recommend_only = True
                continue
            if opt == "--env" and parts:
                item = parts.pop(0)
                if "=" not in item:
                    raise ValueError("--env expects KEY=VALUE")
                key, value = item.split("=", 1)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
                    raise ValueError(f"Unsafe env key: {key}")
                env_map[key] = value
                continue
            raise ValueError(f"Unknown GATEWAY_ADMIN_WORKSPACE_AUTOPILOT option: {opt}")

        if timeout < 1 or timeout > 3600:
            raise ValueError("Timeout must be between 1 and 3600 seconds")

        payload = {
            "workspace": workspace,
            "timeout": timeout,
            "allow_actions": allow_actions,
            "max_steps": max_steps,
            "recommend_only": recommend_only,
        }
        if env_map:
            payload["env"] = env_map
        result = self._gateway_admin_request("/v1/admin/workspace/autopilot", payload, timeout=max(timeout + 45, 90))

        steps = result.get("verify_steps") or []
        step_lines = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                action_name = str(step.get("action", "(unknown)"))
                if not step.get("supported", True):
                    step_lines.append(f"- {action_name}: skipped")
                    continue
                if step.get("ok") is True:
                    state = "ok"
                elif step.get("ok") is False:
                    state = f"failed ({step.get('error') or step.get('exit_code')})"
                else:
                    state = "planned"
                command = step.get("command") or []
                command_text = self._shell_join(command) if command else ""
                if command_text:
                    step_lines.append(f"- {action_name}: {state} command={command_text}")
                else:
                    step_lines.append(f"- {action_name}: {state}")

        executed = result.get("executed_actions") or []
        executed_lines = []
        if isinstance(executed, list):
            for step in executed:
                if not isinstance(step, dict):
                    continue
                action_name = str(step.get("action", "(unknown)"))
                state = "ok" if step.get("ok") else f"failed ({step.get('error') or step.get('exit_code')})"
                command = step.get("command") or []
                command_text = self._shell_join(command) if command else ""
                if command_text:
                    executed_lines.append(f"- {action_name}: {state} command={command_text}")
                else:
                    executed_lines.append(f"- {action_name}: {state}")

        action_probes = result.get("action_probes") or {}
        probe_lines = []
        if isinstance(action_probes, dict):
            for action_name in sorted(action_probes):
                probe = action_probes.get(action_name)
                if not isinstance(probe, dict):
                    continue
                state = "supported" if probe.get("ok") else f"blocked ({probe.get('error') or 'unsupported'})"
                command = probe.get("command") or []
                command_text = self._shell_join(command) if command else ""
                if command_text:
                    probe_lines.append(f"- {action_name}: {state} command={command_text}")
                else:
                    probe_lines.append(f"- {action_name}: {state}")

        output = self._trim(str(result.get("output", "")), 24000)
        status = "WORKSPACE_AUTOPILOT_OK" if result.get("ok") else "WORKSPACE_AUTOPILOT_FAILED"
        return (
            f"{status}\n"
            f"workspace={result.get('workspace', workspace)}\n"
            f"action=autopilot\n"
            f"chosen_action={result.get('chosen_action', 'none')}\n"
            f"recommend_only={result.get('recommend_only', False)}\n"
            f"allow_actions={','.join(result.get('allow_actions', allow_actions))}\n"
            f"max_steps={result.get('max_steps', max_steps)}\n"
            f"reason={result.get('reason', '')}\n"
            f"recommendation={result.get('recommendation', '')}\n"
            f"patch_target={result.get('patch_target', '')}\n"
            f"patch_hint={result.get('patch_hint', '')}\n"
            f"patch_summary={result.get('patch_summary', '')}\n"
            f"read_command={result.get('read_command', '')}\n"
            f"stop_reason={result.get('stop_reason', '')}\n"
            f"exit_code={result.get('exit_code')}\n"
            f"runner_exit_code={result.get('runner_exit_code')}\n"
            f"duration_ms={result.get('duration_ms', '(unknown)')}\n"
            + ("verify_steps:\n" + "\n".join(step_lines) + "\n" if step_lines else "")
            + ("action_probes:\n" + "\n".join(probe_lines) + "\n" if probe_lines else "")
            + ("executed_actions:\n" + "\n".join(executed_lines) + "\n" if executed_lines else "")
            + self._details("output", output)
        )

    def _add_workspace_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_ADD_WORKSPACE\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_ADD_WORKSPACE <name> <path> [--port N] [--cpus N] [--memory 16g] [--default] [--restart]")
        parts = shlex.split(m.group(1))
        if len(parts) < 2:
            raise ValueError("Usage: GATEWAY_ADMIN_ADD_WORKSPACE <name> <path> [--port N] [--cpus N] [--memory 16g] [--default] [--restart]")

        name = parts.pop(0)
        path = parts.pop(0)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
            raise ValueError("Unsafe workspace name")
        payload = {"name": name, "path": path}

        while parts:
            opt = parts.pop(0)
            if opt == "--port" and parts:
                payload["port"] = int(parts.pop(0))
                continue
            if opt == "--cpus" and parts:
                payload["cpus"] = int(parts.pop(0))
                continue
            if opt == "--memory" and parts:
                payload["memory"] = parts.pop(0)
                continue
            if opt == "--default":
                payload["default"] = True
                continue
            if opt == "--restart":
                payload["restart"] = True
                continue
            raise ValueError(f"Unknown GATEWAY_ADMIN_ADD_WORKSPACE option: {opt}")

        result = self._gateway_admin_request("/v1/admin/workspace/add", payload, timeout=360 if payload.get("restart") else 90)
        status = "WORKSPACE_ADD_OK" if result.get("ok") else "WORKSPACE_ADD_FAILED"
        lines = [
            status,
            f"name={result.get('name', name)}",
            f"path={result.get('path', path)}",
            f"exit_code={result.get('exit_code')}",
            self._details("output", self._trim(str(result.get("output", "")), 12000)),
        ]
        if "restart_exit_code" in result:
            lines.extend([
                f"restart_exit_code={result.get('restart_exit_code')}",
                self._details("restart_output", self._trim(str(result.get("restart_output", "")), 12000)),
            ])
        return "\n".join(lines).rstrip()

    def _create_local_repo_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_CREATE_LOCAL_REPO\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_CREATE_LOCAL_REPO <name> [--github] [--github-owner OWNER] [--private|--public] [--path PATH] [--port N] [--cpus N] [--memory 16g] [--default] [--restart]")
        parts = shlex.split(m.group(1))
        if not parts:
            raise ValueError("Usage: GATEWAY_ADMIN_CREATE_LOCAL_REPO <name> [--github] [--github-owner OWNER] [--private|--public] [--path PATH] [--port N] [--cpus N] [--memory 16g] [--default] [--restart]")

        name = parts.pop(0)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
            raise ValueError("Unsafe repository name")
        payload = {"name": name}

        while parts:
            opt = parts.pop(0)
            if opt == "--path" and parts:
                payload["path"] = parts.pop(0)
                continue
            if opt == "--port" and parts:
                payload["port"] = int(parts.pop(0))
                continue
            if opt == "--cpus" and parts:
                payload["cpus"] = int(parts.pop(0))
                continue
            if opt == "--memory" and parts:
                payload["memory"] = parts.pop(0)
                continue
            if opt == "--github":
                payload["github"] = True
                continue
            if opt == "--github-owner" and parts:
                payload["github_owner"] = parts.pop(0)
                continue
            if opt == "--private":
                payload["github_private"] = True
                continue
            if opt == "--public":
                payload["github_private"] = False
                continue
            if opt == "--default":
                payload["default"] = True
                continue
            if opt == "--restart":
                payload["restart"] = True
                continue
            raise ValueError(f"Unknown GATEWAY_ADMIN_CREATE_LOCAL_REPO option: {opt}")

        result = self._gateway_admin_request("/v1/admin/repository/create-local", payload, timeout=420 if payload.get("restart") else 120)
        workspace = result.get("workspace") or {}
        key = result.get("ssh_key") or {}
        github = result.get("github") or {}
        if result.get("ok"):
            status = "LOCAL_REPO_CREATE_OK"
        elif result.get("partial_ok"):
            status = "LOCAL_REPO_CREATE_PARTIAL"
        else:
            status = "LOCAL_REPO_CREATE_FAILED"
        lines = [
            status,
            f"name={result.get('name', name)}",
            f"path={result.get('path', '(unknown)')}",
            f"intent=local_repo_workspace_bootstrap",
            f"requested_scope={'local+github' if result.get('github_requested', False) else 'local'}",
            f"push_requested=False",
            f"workspace_exit_code={workspace.get('exit_code')}",
            f"workspace_registered={workspace.get('workspace_registered', workspace.get('exit_code') == 0)}",
            f"workspace_restart_exit_code={workspace.get('restart_exit_code', '(not requested)')}",
            f"restart_ok={workspace.get('restart_ok', '(not requested)')}",
            f"ssh_key_status={key.get('status', '(unknown)')}",
            f"private_key_path={key.get('private_key_path', '(unknown)')}",
            f"public_key_path={key.get('public_key_path', '(unknown)')}",
            f"private_key_value=NOT_PRINTED",
            f"github_requested={result.get('github_requested', False)}",
            f"github_repo_created={result.get('github_repo_created', False)}",
            f"github_note={result.get('github_note', '')}",
            f"github_full_name={github.get('full_name', '(none)')}",
            f"github_ssh_url={github.get('ssh_url', '(none)')}",
            f"github_deploy_key_added={github.get('deploy_key_added', False)}",
            f"github_deploy_key_reason={github.get('deploy_key_reason', '')}",
            f"next_step={result.get('next_step', '')}",
            self._details("public_key", str(key.get("public_key", ""))),
            self._details("git_status", str(result.get("git_status", ""))),
            self._details("workspace_output", self._trim(str(workspace.get("output", "")), 12000)),
        ]
        if workspace.get("restart_output"):
            lines.append(self._details("restart_output", self._trim(str(workspace.get("restart_output", "")), 12000)))
        return "\n".join(lines).rstrip()

    def _deploy_stack_admin(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_DEPLOY_STACK(?:\s+(.+?))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_DEPLOY_STACK [branch] [--force]")
        parts = shlex.split(m.group(1) or "")
        branch = "main"
        force = False
        for part in parts:
            if part == "--force":
                force = True
                continue
            branch = part
        if not re.fullmatch(r"[A-Za-z0-9_.\\/-]{1,120}", branch):
            raise ValueError("Unsafe branch name")
        result = self._gateway_admin_request("/v1/admin/stack/deploy", {"branch": branch, "force": force}, timeout=30)
        status = "STACK_DEPLOY_SCHEDULED" if result.get("ok") else "STACK_DEPLOY_NOT_SCHEDULED"
        return (
            f"{status}\n"
            f"action={result.get('action')}\n"
            f"branch={result.get('branch', branch)}\n"
            f"pid={result.get('pid')}\n"
            f"log={result.get('log')}\n"
            + self._details("tail", self._trim(str(result.get("tail", "")), 12000))
        ).rstrip()

    def _deploy_status_admin(self) -> str:
        result = self._gateway_admin_request("/v1/admin/stack/deploy/status", {}, timeout=30)
        return (
            "STACK_DEPLOY_STATUS\n"
            f"running={result.get('running')}\n"
            f"pid={result.get('pid')}\n"
            f"head={result.get('head')}\n"
            f"log={result.get('log')}\n"
            + self._details("git_status", str(result.get("git_status", "")))
            + "\n"
            + self._details("log_tail", self._trim(str(result.get("tail", "")), 24000))
        ).rstrip()

    def _parse_web_command(self, text: str, command: str) -> tuple[str, dict, str]:
        match = re.search(rf"(?im)^\s*{re.escape(command)}\s+(.+?)\s*$", text)
        if not match:
            raise ValueError(f"Usage: {command} <url> [--max-bytes N] [--timeout N] [--text-limit N] [-- question]")
        parts = shlex.split(match.group(1))
        if not parts:
            raise ValueError(f"Usage: {command} <url> [--max-bytes N] [--timeout N] [--text-limit N] [-- question]")

        url = parts.pop(0)
        payload: dict[str, object] = {"url": url}
        question_parts: list[str] = []
        i = 0
        while i < len(parts):
            part = parts[i]
            if part == "--":
                question_parts = parts[i + 1 :]
                break
            if part in {"--max-bytes", "--timeout", "--text-limit"}:
                if i + 1 >= len(parts):
                    raise ValueError(f"{part} requires a value")
                key = part[2:].replace("-", "_")
                payload[key] = int(parts[i + 1])
                i += 2
                continue
            if command == "GATEWAY_ADMIN_WEB_ANSWER":
                question_parts = parts[i:]
                break
            raise ValueError(f"Unknown {command} option: {part}")
        question = " ".join(question_parts).strip()
        return url, payload, question

    def _web_fetch_admin(self, text: str) -> str:
        url, payload, _ = self._parse_web_command(text, "GATEWAY_ADMIN_WEB_FETCH")
        result = self._gateway_admin_request("/v1/admin/web/fetch", payload, timeout=90)
        status = "WEB_FETCH_OK" if result.get("ok") else "WEB_FETCH_FAILED"
        return (
            f"{status}\n"
            f"url={url}\n"
            f"final_url={result.get('final_url')}\n"
            f"status={result.get('status')}\n"
            f"content_type={result.get('content_type')}\n"
            f"bytes_read={result.get('bytes_read')}\n"
            f"truncated={result.get('truncated')}\n"
            f"text_truncated={result.get('text_truncated')}\n"
            f"title={result.get('title') or ''}"
            + self._details("text_preview", self._trim(str(result.get("text", "")), 12000))
        ).rstrip()

    def _web_answer_admin(self, text: str) -> str:
        url, payload, question = self._parse_web_command(text, "GATEWAY_ADMIN_WEB_ANSWER")
        if not question:
            raise ValueError("Usage: GATEWAY_ADMIN_WEB_ANSWER <url> [--max-bytes N] [--timeout N] -- <question>")
        payload["question"] = question
        result = self._gateway_admin_request("/v1/admin/web/answer", payload, timeout=240)
        status = "WEB_ANSWER_OK" if result.get("ok") else "WEB_ANSWER_FAILED"
        return (
            f"{status}\n"
            f"url={url}\n"
            f"final_url={result.get('final_url')}\n"
            f"status={result.get('status')}\n"
            f"content_type={result.get('content_type')}\n"
            f"bytes_read={result.get('bytes_read')}\n"
            f"truncated={result.get('truncated')}\n"
            f"text_truncated={result.get('text_truncated')}\n"
            f"title={result.get('title') or ''}\n"
            "answer:\n"
            f"{str(result.get('answer', '')).strip()}"
            + self._details("source_preview", self._trim(str(result.get("text", "")), 8000))
        ).rstrip()

    def _agent_loop_admin(self, text: str) -> str:
        match = re.search(r"(?im)^\s*GATEWAY_ADMIN_AGENT_LOOP\s+(.+?)\s*$", text)
        if not match:
            raise ValueError("Usage: GATEWAY_ADMIN_AGENT_LOOP <workspace> -- <task>")
        parts = shlex.split(match.group(1))
        if not parts:
            raise ValueError("Usage: GATEWAY_ADMIN_AGENT_LOOP <workspace> -- <task>")
        workspace = parts.pop(0)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace name")
        if not parts or parts[0] != "--":
            raise ValueError("GATEWAY_ADMIN_AGENT_LOOP requires -- before the task")
        task = " ".join(parts[1:]).strip()
        if not task:
            raise ValueError("GATEWAY_ADMIN_AGENT_LOOP task is empty")
        result = self._gateway_admin_request("/v1/admin/agent/loop", {"workspace": workspace, "task": task}, timeout=600)
        plan = result.get("plan") or {}
        execution = result.get("execution") or {}
        followup = result.get("followup") or {}
        recovery = result.get("recovery") or {}
        answer = str(result.get("answer", "")).strip()
        summary = str(result.get("summary", "")).strip()
        status = "AGENT_LOOP_OK" if result.get("ok") else "AGENT_LOOP_NEEDS_ATTENTION"
        lines = [
            status,
            f"requested_workspace={result.get('requested_workspace', workspace)}",
            f"controller_workspace={result.get('controller_workspace', workspace)}",
            f"workspace_exists={result.get('workspace_exists')}",
            f"workflow={result.get('workflow', plan.get('workflow', ''))}",
            f"read_only={result.get('read_only', plan.get('read_only', False))}",
            f"confidence={plan.get('confidence', '')}",
            f"reason={plan.get('reason', '')}",
            f"summary={summary}",
        ]
        if answer:
            lines.extend(["answer:", answer])
        if execution:
            lines.append(self._details("execution", json.dumps(execution, ensure_ascii=False, indent=2)))
        if followup:
            lines.append(self._details("followup", json.dumps(followup, ensure_ascii=False, indent=2)))
        if recovery:
            lines.append(self._details("recovery", json.dumps(recovery, ensure_ascii=False, indent=2)))
        lines.append(self._details("plan", json.dumps(plan, ensure_ascii=False, indent=2)))
        return "\n".join(line for line in lines if line).rstrip()

    def _gateway_admin_request(self, path: str, payload: dict, timeout: int = 90) -> dict:
        base_url = os.getenv("CODEX_GATEWAY_PUBLIC_URL", self.valves.gateway_url).rstrip("/")
        token = self._gateway_admin_token()
        if not token and not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise RuntimeError(
                "GATEWAY_ADMIN_TOKEN_MISSING\n"
                "OpenWebUI is calling the gateway over the LAN address, so it needs an admin token.\n"
                "Expected token file: codex/state/codex-gateway-admin.token"
            )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base_url + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"GATEWAY_ADMIN_HTTP_{exc.code}\n"
                f"url={base_url + path}\n"
                "response:\n"
                + self._trim(raw, 4000)
            )
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GATEWAY_ADMIN_CONNECT_FAILED\nurl={base_url + path}\nerror={exc}")

        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "GATEWAY_ADMIN_BAD_JSON\n"
                f"url={base_url + path}\n"
                f"error={exc}\n"
                "response:\n"
                + self._trim(raw, 4000)
            )

    def _gateway_admin_token(self) -> str:
        token = os.getenv("CODEX_GATEWAY_ADMIN_TOKEN", "").strip()
        if token:
            return token

        candidates = []
        env_file = os.getenv("CODEX_GATEWAY_ADMIN_TOKEN_FILE", "").strip()
        if env_file:
            candidates.append(Path(env_file))
        if self.valves.gateway_admin_token_file and self.valves.gateway_admin_token_file != "auto":
            candidates.append(Path(self.valves.gateway_admin_token_file))
        try:
            candidates.append(self._repo_root() / "codex/state/codex-gateway-admin.token")
        except Exception:
            pass
        candidates.append(Path("/data/repositories/ai-stack/codex/state/codex-gateway-admin.token"))

        seen = set()
        for path in candidates:
            resolved = str(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if value:
                return value
        return ""

    def _trim(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n[truncated at {limit} chars]"

    def _details(self, title: str, text: str) -> str:
        body = text.rstrip() or "(empty)"
        lines = body.count("\n") + 1 if body else 0
        chars = len(body)
        preview_limit_lines = 18
        preview_limit_chars = 1800
        body_lines = body.splitlines()
        if lines <= preview_limit_lines and chars <= preview_limit_chars:
            preview = body
        else:
            preview = "\n".join(body_lines[:preview_limit_lines])
            if len(preview) > preview_limit_chars:
                preview = preview[:preview_limit_chars].rstrip()
            omitted_lines = max(0, lines - preview.count("\n") - 1)
            omitted_chars = max(0, chars - len(preview))
            preview = (
                preview.rstrip()
                + f"\n[preview only: omitted {omitted_lines} lines, {omitted_chars} chars]"
            )
        return (
            f"\n{title} ({lines} lines, {chars} chars):\n"
            f"```text\n{preview}\n```"
        )

    def _parse_key_value_block(self, text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        current_key: str | None = None
        current_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip("\n")
            if current_key == "summary":
                current_lines.append(line)
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                if re.fullmatch(r"[A-Za-z0-9_:-]{1,80}", key):
                    result[key] = value
                    current_key = None
                    continue
            if line.strip() == "summary:":
                current_key = "summary"
                current_lines = []
        if current_key == "summary":
            result["summary"] = "\n".join(current_lines).strip()
        return result

    def _normalize_summary_lines(self, text: str) -> str:
        lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                lines.append(f"- {key}: {value}")
            else:
                lines.append(f"- {line}")
        return "\n".join(lines) or "(empty)"

    def _shell_join(self, command) -> str:
        if isinstance(command, list):
            try:
                return shlex.join(str(x) for x in command)
            except Exception:
                return " ".join(str(x) for x in command)
        return str(command)

    def _git_push(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_GIT_PUSH(?:\s+([A-Za-z0-9_.\\/-]+))?(?:\s+(.+?))?\s*$", text)
        branch = "main"
        message = "Update ai-stack configuration and documentation"
        if m:
            if m.group(1):
                branch = m.group(1).strip()
            if m.group(2):
                message = m.group(2).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\\/-]{1,80}", branch):
            raise ValueError("Unsafe git branch name")
        if "\n" in message or len(message) > 200:
            raise ValueError("Commit message must be a single line up to 200 chars")

        root = self._repo_root()
        self._ensure_safe_git_repo(root)
        remote_url = "git@github.com:Jeycob/Ai-Stack.git"
        remote_get = self._run_git(root, ["remote", "get-url", "origin"], timeout=20)
        if remote_get.returncode != 0:
            add = self._run_git(root, ["remote", "add", "origin", remote_url], timeout=20)
            if add.returncode != 0:
                raise RuntimeError("git remote add failed:\n" + add.stdout)
            remote_action = f"added origin {remote_url}"
        elif remote_get.stdout.strip() != remote_url:
            set_url = self._run_git(root, ["remote", "set-url", "origin", remote_url], timeout=20)
            if set_url.returncode != 0:
                raise RuntimeError("git remote set-url failed:\n" + set_url.stdout)
            remote_action = f"updated origin {remote_url}"
        else:
            remote_action = f"origin already {remote_url}"

        status = self._git_status_lines(root)
        allowed, blocked = self._classify_status_paths(status)
        if blocked:
            return "GIT_PUSH_BLOCKED\nblocked_paths:\n" + "\n".join(blocked)

        for rel in allowed:
            if self._is_runtime_ignored_path(rel):
                continue
            proc = self._run_git(root, ["add", "--", rel], timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(f"git add failed for {rel}:\n{proc.stdout}")

        staged = self._run_git(root, ["diff", "--cached", "--name-only"], timeout=30).stdout.splitlines()
        commit_output = "(no staged changes)"
        if staged:
            self._run_git(root, ["config", "user.name", "AI Stack Agent"], timeout=20)
            self._run_git(root, ["config", "user.email", "ai-stack-agent@local"], timeout=20)
            commit = self._run_git(root, ["commit", "-m", message], timeout=120)
            if commit.returncode != 0:
                raise RuntimeError("git commit failed:\n" + commit.stdout)
            commit_output = commit.stdout.strip()

        ssh_env, runtime_key = self._runtime_ssh_env(root)
        ssh_test = self._run_git(root, ["ls-remote", "--heads", "origin"], timeout=60, env=ssh_env)
        if ssh_test.returncode != 0:
            raise RuntimeError("git ls-remote failed:\n" + ssh_test.stdout)
        push = self._run_git(root, ["push", "-u", "origin", f"HEAD:{branch}"], timeout=180, env=ssh_env)
        if push.returncode != 0:
            raise RuntimeError("git push failed:\n" + push.stdout)

        head = self._run_git(root, ["rev-parse", "--short", "HEAD"], timeout=20).stdout.strip()
        return (
            "GIT_PUSH_OK\n"
            f"remote_action={remote_action}\n"
            f"branch={branch}\n"
            f"runtime_key={runtime_key}\n"
            "staged_paths:\n"
            + ("\n".join(staged) if staged else "(none)")
            + "\ncommit:\n"
            + commit_output
            + "\npush:\n"
            + push.stdout.strip()
            + f"\nhead={head}"
        )

    def _target(self, rel: str) -> Path:
        root = self._repo_root()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            raise PermissionError(f"Resolved path escaped repo root: {rel}")
        return target

    def _repo_root(self) -> Path:
        candidates = []
        if self.valves.repo_root and self.valves.repo_root != "auto":
            candidates.append(self.valves.repo_root)
        candidates.extend(x.strip() for x in self.valves.candidate_roots.split(",") if x.strip())

        checked = []
        for candidate in candidates:
            root = Path(candidate).resolve()
            checked.append(str(root))
            if (root / "codex/gateway/gateway.py").is_file():
                return root
        raise FileNotFoundError("ai-stack repo root not found; checked: " + ", ".join(checked))

    def _probe_paths(self) -> str:
        paths = [
            "/",
            "/data",
            "/data/repositories",
            "/app",
            "/app/backend",
            "/app/backend/data",
            "/mnt",
            "/mnt/c",
            "/mnt/c/Repositories",
            "/tmp",
        ]
        lines = []
        for raw in paths:
            p = Path(raw)
            try:
                if not p.exists():
                    lines.append(f"{raw}: MISSING")
                    continue
                if not p.is_dir():
                    lines.append(f"{raw}: NOT_DIR")
                    continue
                names = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())[:80]
                lines.append(f"{raw}: " + ", ".join(names))
            except Exception as exc:
                lines.append(f"{raw}: ERROR {type(exc).__name__}: {exc}")
        return "\n".join(lines)

    def _diag(self) -> str:
        root = self._repo_root()
        items = []
        items.append(f"repo_root: {root}")
        for rel, label, max_lines in [
            ("codex/gateway/gateway.py", "gateway.py head", 260),
            ("codex/audit/gateway-watch.log", "gateway-watch.log tail", 80),
            ("codex/audit/gateway.log", "gateway.log tail", 120),
        ]:
            path = root / rel
            items.append(f"\n--- {label}: {rel} ---")
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                if "tail" in label:
                    lines = lines[-max_lines:]
                else:
                    lines = lines[:max_lines]
                items.extend(lines)
            except Exception as exc:
                items.append(f"ERROR {type(exc).__name__}: {exc}")
        return "\n".join(items)
