"""
title: Codex Auto Tools Filter
author: OpenAI Codex
version: 0.1.10
description: Dynamically attaches Codex toolsets and delegates codex-local prompts to the gateway TaskSpec agent loop.
"""

import re
import shlex
import json
import os
import sys
import importlib.util
from pathlib import Path
from typing import Optional

WORKSPACE_LABEL_PATTERN = r"(?:repo|repository|repositar|repozitar|repozitář|projekt|project|workspace)"
FILE_LABEL_PATTERN = r"(?:soubor|file|path|cesta)"
EMBEDDED_CAPABILITY_ROADMAP = None
MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

def _load_workspace_context_module():
    candidates = []
    env_path = os.getenv("CODEX_WORKSPACE_CONTEXT_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(MODULE_DIR / "workspace_context.py")
    for root in (
        "/data/repositories/ai-stack",
        "/app/backend/data/repositories/ai-stack",
        "/Repositories/ai-stack",
        "/mnt/c/Repositories/ai-stack",
    ):
        candidates.append(Path(root) / "codex/bin/workspace_context.py")
    seen = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        spec = importlib.util.spec_from_file_location("codex_workspace_context_runtime", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise ModuleNotFoundError(
        "workspace_context.py not found; set CODEX_WORKSPACE_CONTEXT_PATH or mount ai-stack at /data/repositories/ai-stack"
    )


_workspace_context = _load_workspace_context_module()
load_workspace_registry = _workspace_context.load_workspace_registry
resolve_workspace_context = _workspace_context.resolve_workspace_context
strip_workspace_routing = _workspace_context.strip_workspace_routing

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


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=-10,
            description="Run early so Codex tool_ids or admin intents are prepared before later filters/model calls.",
        )
        repo_root: str = Field(
            default="auto",
            description="ai-stack path inside the Open WebUI container, or auto.",
        )
        candidate_roots: str = Field(
            default="/data/repositories/ai-stack,/app/backend/data/repositories/ai-stack,/Repositories/ai-stack,/mnt/c/Repositories/ai-stack",
            description="Comma-separated fallback ai-stack paths.",
        )
        enable_codex_local_agent_loop_bridge: bool = Field(
            default=True,
            description="Wrap codex-local prompts in GATEWAY_ADMIN_AGENT_LOOP; intent reasoning stays in the gateway TaskSpec planner.",
        )
        enable_codex_local_intent_router: bool = Field(
            default=True,
            description="Deprecated alias for enable_codex_local_agent_loop_bridge.",
        )
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.lite = ["codex_lite_workspace_tools"]
        self.extra = ["codex_extra_workspace_tools"]
        self.ssh = ["codex_ssh_key_tools"]
        self.aider = ["aider_container_access"]
        roadmap = self._load_capability_roadmap_payload()
        self.capability_roadmap = self._extract_capabilities(roadmap)
        self.workspace_actions = self._extract_workspace_actions(roadmap)

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        model = body.get("model")
        if not model:
            return body

        bridge_enabled = (
            self.valves.enable_codex_local_agent_loop_bridge
            and self.valves.enable_codex_local_intent_router
        )
        if bridge_enabled and str(model).startswith("codex-local"):
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

    def _agent_loop_admin_command(self, workspace: str, task: str) -> str:
        target = (workspace or "ai-stack").strip() or "ai-stack"
        prompt = " ".join(str(task or "").split()).strip()
        if not prompt:
            prompt = "Analyzuj projekt a navrhni další bezpečný capability krok."
        return f"GATEWAY_ADMIN_AGENT_LOOP {shlex.quote(target)} -- {shlex.quote(prompt[:3000])}"

    def _mentor_helper_task(self, workspace: str, mode: str, *args: str) -> str:
        workspace = (workspace or "ai-stack").strip() or "ai-stack"
        parts = [str(item).strip() for item in args if str(item).strip()]
        task = parts[0] if parts else ""
        lower_task = task.lower()

        if mode == "brief":
            return task or f"Shrň stručný execution brief pro workspace {workspace}."
        if mode == "review":
            return task or f"Proveď review workspace {workspace}. Nic needituj. Najdi hlavní rizika a navrhni další krok."
        if mode == "boundary":
            return task or f"Popiš boundary a capability hranice workspace {workspace}. Nic needituj."
        if mode == "profile":
            return task or f"Analyzuj runtime/capability profil workspace {workspace}. Nic needituj."
        if mode == "report":
            return task or f"Vytvoř kompaktní technický report pro workspace {workspace}. Nic needituj."
        if mode == "plan":
            return task or f"Připrav krátký sekvenční plán pro workspace {workspace}. Nic needituj."
        if mode == "scaffold-plan":
            return task or f"Navrhni scaffold/bootstrap plán pro workspace {workspace}. Nic needituj."
        if mode == "next-helper":
            return task or f"Urči další nejlepší capability krok pro workspace {workspace}. Nic needituj."
        if mode == "release-prep":
            return f"Zkontroluj release readiness workspace {workspace}, shrň blokery a navrhni další krok."
        if mode == "publish-plan":
            return f"Připrav publish plán pro workspace {workspace} a navrhni další auditované kroky."
        if mode == "deploy":
            return "Pullni ai-stack a nasaď poslední změny. Po dokončení napiš stručný stav."
        if mode == "push-check":
            return "Zkontroluj, jestli jsou změny připravené na push, a stručně řekni co případně blokuje publish."
        if mode == "push":
            return "Commitni povolené změny a pushni je do GitHubu. Po dokončení napiš stručný stav."
        if mode == "web-answer":
            return task or "Odpověz na veřejnou webovou otázku a vrať stručný výsledek."
        if mode == "web-fetch":
            return task or "Načti veřejný web a vrať stručný textový výsledek."
        if mode == "create-repo":
            github = any(part == "--github" for part in parts[1:])
            repo_name = task or workspace
            suffix = " a připrav i GitHub remote." if github else "."
            return f"Vytvoř nové repository {repo_name}{suffix}"
        if mode == "bootstrap-dispatch":
            if "--execute" in parts[1:] or "pust" in lower_task or "rozbeh" in lower_task or "rozběh" in lower_task:
                return task or f"Bootstrapuj workspace {workspace} a pokračuj nejbližším bezpečným capability krokem."
            return task or f"Navrhni bootstrap první krok pro workspace {workspace}."
        return task or f"Analyzuj workspace {workspace} a pokračuj nejbližším bezpečným capability krokem."

    def _mentor_helper_command(
        self,
        workspace: str,
        mode: str,
        *args: str,
        timeout: int = 120,
        stateless: bool = True,
    ) -> str:
        del timeout, stateless
        task = self._mentor_helper_task(workspace, mode, *args)
        target_workspace = workspace
        if mode in {"create-repo", "bootstrap-dispatch", "deploy", "push", "push-check"}:
            target_workspace = "ai-stack"
        return self._agent_loop_admin_command(target_workspace, task)

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
        )
        explicit_helper_cues = (
            "mentor brief",
            "execution brief",
            "review",
            "report",
            "plan",
            "workflow",
            "runtime profile",
            "next helper",
            "backlog",
            "dispatch",
            "top task",
        )
        if any(cue in lower for cue in explicit_helper_cues):
            return False
        return any(cue in lower for cue in read_only_cues) and any(cue in lower for cue in analysis_cues)

    def _route_codex_local_admin_intent(self, body: dict) -> dict | None:
        latest = self._last_user_message(body)
        text = self._message_text(latest)
        if not latest or not text:
            return None
        if "GATEWAY_ADMIN_" in text:
            return None
        task_text = self._agent_loop_task_text(text)
        if not task_text:
            return None
        workspace = self._workspace_from_text(text, body)
        if not workspace:
            workspace = "ai-stack"
        command = f"GATEWAY_ADMIN_AGENT_LOOP {shlex.quote(workspace)} -- {shlex.quote(task_text[:3000])}"
        self._set_message_text(latest, f"repo: {workspace}\n" + command)
        body["stream"] = True
        return body

    def _agent_loop_task_text(self, text: str) -> str:
        cleaned = strip_workspace_routing(text, self._workspaces())
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        return " ".join(lines).strip() or str(text or "").strip()

    def _non_repo_lines(self, text: str) -> list[str]:
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

    def _extract_task_list(self, text: str) -> list[str]:
        tasks = []
        for stripped in self._non_repo_lines(text):
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

    def _extract_brief_task(self, text: str) -> str | None:
        patterns = [
            r"(?is)\b(?:mentor\s+brief|execution\s+brief|kratky\s+brief|krátký\s+brief)\b\s*(?:pro|k|na|task|ukol|úkol)?\s*:\s*(.+)\s*$",
            r"(?is)\b(?:dej|udelej|udělej|vytvor|vytvoř|priprav|připrav)\b.+?\b(?:mentor\s+brief|execution\s+brief|kratky\s+brief|krátký\s+brief)\b\s+(?:pro|k|na)\s+(.+?)\s*$",
            r"(?is)\b(?:jaky|jaký)\s+brief\s+(?:ma|má)\s+dostat\s+model\s+pro\s+(.+?)\s*$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip().strip("\"'")
                candidate = re.sub(r"(?i)^(?:task|ukol|úkol)\s*:\s*", "", candidate).strip()
                candidate = candidate.rstrip("?. ")
                if candidate:
                    return candidate

        lines = self._non_repo_lines(text)
        filtered = []
        for line in lines:
            lower = line.lower()
            if any(
                needle in lower
                for needle in (
                    "mentor brief",
                    "execution brief",
                    "kratky brief",
                    "krátký brief",
                    "jaky brief",
                    "jaký brief",
                    "co ma dostat model",
                    "co má dostat model",
                )
            ):
                continue
            filtered.append(line)
        if filtered:
            return " ".join(filtered)
        return None

    def _brief_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "brief", task, timeout=120)

    def _line_value(self, text: str, label_pattern: str) -> str | None:
        match = re.search(rf"(?im)^\s*{label_pattern}\s*:\s*(.+?)\s*$", text)
        if not match:
            return None
        value = match.group(1).strip().strip("`").strip().strip("\"'")
        return value or None

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
            ("opencode-default.json", "codex/opencode-default.json"),
        ]
        for needle, rel in known:
            if needle in lower:
                return rel
        return None

    def _looks_like_file_read_or_explain(self, text: str) -> bool:
        lower = text.lower()
        cues = (
            "precti",
            "přečti",
            "cti ",
            "čti ",
            "read ",
            "show ",
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
        return any(cue in lower for cue in cues)

    def _natural_file_context_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace or not self._looks_like_file_read_or_explain(text):
            return None
        rel = self._file_from_text(text)
        if not rel:
            return None
        question = " ".join(self._non_repo_lines(text)).strip() or "Přečti a vysvětli soubor."
        return (
            f"GATEWAY_ADMIN_EXPLAIN_FILE {shlex.quote(workspace)} {shlex.quote(rel)} "
            f"1 400 -- {shlex.quote(question[:1200])}"
        )

    def _natural_workspace_brief_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        if not any(
            needle in lower
            for needle in (
                "mentor brief",
                "execution brief",
                "kratky brief",
                "krátký brief",
                "jaky brief",
                "jaký brief",
                "co ma dostat model",
                "co má dostat model",
            )
        ):
            return None
        task = self._extract_brief_task(text)
        if not task:
            return None
        return self._brief_helper_command(workspace, task)

    def _review_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "review", timeout=120)

    def _natural_workspace_review_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        cue_needles = (
            "code review",
            "udělej review",
            "udelej review",
            "review kodu",
            "review kódu",
            "zkontroluj rizika",
            "najdi rizika",
            "najdi regrese",
            "najdi regres",
            "architektonicke review",
            "architektonické review",
            "kritika navrhu",
            "kritika návrhu",
        )
        if not any(needle in lower for needle in cue_needles):
            return None
        return self._review_helper_command(workspace, "review")

    def _extract_single_task(self, text: str, patterns: list[str], cue_needles: tuple[str, ...]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip().strip("\"'")
                candidate = re.sub(r"(?i)^(?:task|ukol|úkol)\s*:\s*", "", candidate).strip()
                candidate = candidate.rstrip("?. ")
                if candidate:
                    return candidate

        lines = self._non_repo_lines(text)
        filtered = []
        for line in lines:
            lower = line.lower()
            if any(needle in lower for needle in cue_needles):
                continue
            filtered.append(line)
        if filtered:
            return " ".join(filtered)
        return None

    def _boundary_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "boundary", task, timeout=120)

    def _extract_boundary_task(self, text: str) -> str | None:
        patterns = [
            r"(?is)\b(?:proc|proč)\s+(?:to\s+)?(?:nejde|nelze)\b.*?\bpro\s+(.+?)\s*$",
            r"(?is)\b(?:jake|jaké|jaky|jaký)\s+guardraily\b.*?\bpro\s+(.+?)\s*$",
            r"(?is)\b(?:jaka|jaká|jaky|jaký)\s+capability\b.*?\bchybi\b.*?\bpro\s+(.+?)\s*$",
            r"(?is)\b(?:why\s+can't|why\s+cant|why\s+not)\b.*?\bfor\s+(.+?)\s*$",
            r"(?is)\b(?:sirsi|širší)\s+scope\b.*?\bpro\s+(.+?)\s*$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip().strip("\"'")
                candidate = re.sub(r"(?i)^(?:task|ukol|úkol)\s*:\s*", "", candidate).strip()
                candidate = candidate.rstrip("?. ")
                if candidate:
                    return candidate

        lines = self._non_repo_lines(text)
        filtered = []
        for line in lines:
            lower = line.lower()
            if any(
                needle in lower
                for needle in (
                    "proc to nejde",
                    "proč to nejde",
                    "proc to nelze",
                    "proč to nelze",
                    "jake guardraily",
                    "jaké guardraily",
                    "jaka capability chybi",
                    "jaká capability chybí",
                    "sirsi scope",
                    "širší scope",
                    "why can't",
                    "why cant",
                    "why not",
                )
            ):
                continue
            filtered.append(line)
        if filtered:
            return " ".join(filtered)
        return None

    def _natural_workspace_boundary_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        if not any(
            needle in lower
            for needle in (
                "proc to nejde",
                "proč to nejde",
                "proc to nelze",
                "proč to nelze",
                "jake guardraily",
                "jaké guardraily",
                "jaka capability chybi",
                "jaká capability chybí",
                "sirsi scope",
                "širší scope",
                "why can't",
                "why cant",
                "why not",
            )
        ):
            return None
        task = self._extract_boundary_task(text)
        if not task:
            return None
        return self._boundary_helper_command(workspace, task)

    def _profile_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "profile", task, timeout=120)

    def _natural_workspace_profile_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        cue_needles = (
            "jaky workflow",
            "jaký workflow",
            "jaky runtime profile",
            "jaký runtime profile",
            "jakou pravomoc",
            "jakou šířku pravomocí",
            "jaky scope",
            "jaký scope",
            "co bys zvolil za workflow",
            "what workflow",
            "what runtime profile",
        )
        if not any(needle in lower for needle in cue_needles):
            return None
        task = self._extract_single_task(
            text,
            [
                r"(?is)\b(?:jaky|jaký)\s+workflow\b.*?\bpro\s+(.+?)\s*$",
                r"(?is)\b(?:jaky|jaký)\s+runtime\s+profile\b.*?\bpro\s+(.+?)\s*$",
                r"(?is)\b(?:jakou)\s+(?:pravomoc|šířku\s+pravomocí)\b.*?\bpro\s+(.+?)\s*$",
                r"(?is)\b(?:what\s+workflow|what\s+runtime\s+profile)\b.*?\bfor\s+(.+?)\s*$",
            ],
            cue_needles,
        )
        if not task:
            return None
        return self._profile_helper_command(workspace, task)

    def _report_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "report", task, timeout=120)

    def _natural_workspace_report_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        cue_needles = (
            "mentor report",
            "udelej report",
            "udělej report",
            "compact report",
            "shrni workflow",
            "shrn workflow",
            "summarize workflow",
        )
        if not any(needle in lower for needle in cue_needles):
            return None
        task = self._extract_single_task(
            text,
            [
                r"(?is)\b(?:mentor\s+report|compact\s+report)\b\s*(?:pro|for)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:udelej|udělej|priprav|připrav|shrni|shrn)\b.+?\b(?:report|workflow)\b.*?\bpro\s+(.+?)\s*$",
                r"(?is)\b(?:summarize\s+workflow)\b.*?\bfor\s+(.+?)\s*$",
            ],
            cue_needles,
        )
        if not task:
            return None
        return self._report_helper_command(workspace, task)

    def _plan_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "plan", task, timeout=120)

    def _natural_workspace_plan_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        cue_needles = (
            "kratky plan",
            "krátký plán",
            "sequenced plan",
            "udelej plan",
            "udělej plán",
            "priprav plan",
            "připrav plán",
            "jaky plan",
            "jaký plán",
        )
        if not any(needle in lower for needle in cue_needles):
            return None
        task = self._extract_single_task(
            text,
            [
                r"(?is)\b(?:kratky|krátký)\s+(?:plan|plán)\b\s*(?:pro|for)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:sequenced\s+plan)\b\s*(?:for)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:udelej|udělej|priprav|připrav)\b.+?\b(?:plan|plán)\b.*?\bpro\s+(.+?)\s*$",
                r"(?is)\b(?:jaky|jaký)\s+(?:plan|plán)\b.*?\bpro\s+(.+?)\s*$",
            ],
            cue_needles,
        )
        if not task:
            return None
        return self._plan_helper_command(workspace, task)

    def _scaffold_plan_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "scaffold-plan", task, timeout=120)

    def _bootstrap_dispatch_helper_command(self, workspace: str, task: str, execute: bool = True) -> str:
        prompt = task or f"Bootstrapuj workspace {workspace}."
        if execute:
            prompt = prompt.rstrip(".") + " Pokračuj nejbližším bezpečným capability krokem a vrať konkrétní výsledek."
        else:
            prompt = prompt.rstrip(".") + " Navrhni první bootstrap krok. Nic ještě nespouštěj."
        return self._agent_loop_admin_command("ai-stack", prompt)

    def _natural_workspace_bootstrap_dispatch_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        cue_needles = (
            "bootstrap prvni krok",
            "bootstrap první krok",
            "prvni bootstrap krok",
            "první bootstrap krok",
            "spust starter",
            "spusť starter",
            "rozjed starter",
            "rozjeď starter",
            "proveď scaffold",
            "proved scaffold",
            "run scaffold",
            "run starter",
            "bootstrap dispatch",
        )
        if not any(needle in lower for needle in cue_needles):
            return None
        task = self._extract_single_task(
            text,
            [
                r"(?is)\b(?:bootstrap\s+(?:prvni|první)\s+krok|prvni\s+bootstrap\s+krok|první\s+bootstrap\s+krok)\b\s*(?:pro|for)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:spust|spusť|rozjed|rozjeď|proved|proveď|run)\b.+?\b(?:starter|scaffold)\b\s*(?:pro|for)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:bootstrap\s+dispatch)\b\s*(?:pro|for)?\s*:?\s*(.+?)\s*$",
            ],
            cue_needles,
        )
        if not task:
            return None
        return self._bootstrap_dispatch_helper_command(workspace, task, execute=True)

    def _natural_workspace_scaffold_plan_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        cue_needles = (
            "scaffold plan",
            "starter plan",
            "bootstrap plan",
            "plan scaffold",
            "plan starteru",
            "plan starter",
            "priprav scaffold",
            "připrav scaffold",
            "priprav starter plan",
            "připrav starter plan",
            "jak scaffoldovat",
            "jak bootstrapovat",
        )
        if not any(needle in lower for needle in cue_needles):
            return None
        task = self._extract_single_task(
            text,
            [
                r"(?is)\b(?:scaffold\s+plan|starter\s+plan|bootstrap\s+plan)\b\s*(?:pro|for)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:priprav|připrav)\b.+?\b(?:scaffold|starter|bootstrap)\b.*?\bpro\s+(.+?)\s*$",
                r"(?is)\b(?:jak\s+scaffoldovat|jak\s+bootstrapovat)\b\s*(.+?)\s*$",
            ],
            cue_needles,
        )
        if not task:
            return None
        return self._scaffold_plan_helper_command(workspace, task)

    def _natural_workspace_fix_plan_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        cue_needles = (
            "najdi bug a navrhni opravu",
            "najdi bug a oprav plan",
            "najdi problém a navrhni opravu",
            "najdi problem a navrhni opravu",
            "navrhni opravu",
            "repair plan",
            "fix plan",
            "bugfix plan",
            "plan opravy",
            "plán opravy",
        )
        if not any(needle in lower for needle in cue_needles):
            return None
        task = self._extract_single_task(
            text,
            [
                r"(?is)\b(?:najdi\s+bug\s+a\s+navrhni\s+opravu)\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:najdi\s+probl[eé]m\s+a\s+navrhni\s+opravu)\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
                r"(?is)\b(?:navrhni\s+opravu|plan\s+opravy|plán\s+opravy|repair\s+plan|fix\s+plan|bugfix\s+plan)\b\s*(?:pro|for)?\s*:?\s*(.+?)\s*$",
            ],
            cue_needles,
        )
        if not task:
            task = "Najdi bug a navrhni opravu."
        return self._plan_helper_command(workspace, task)

    def _next_helper_command(self, workspace: str, task: str) -> str:
        return self._mentor_helper_command(workspace, "next-helper", task, timeout=120)

    def _extract_next_helper_task(self, text: str) -> str | None:
        patterns = [
            r"(?is)\b(?:jaky|jaký)\s+helper\b.+?\bpro\s+(.+?)\s*$",
            r"(?is)\b(?:co\s+mam|co\s+mám|co\s+ma|co\s+má)\s+(?:spustit|pustit|udelat|udělat)\s+dal\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
            r"(?is)\b(?:co\s+mam|co\s+mám|co\s+ma|co\s+má)\s+(?:spustit|pustit|udelat|udělat)\s+dál\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
            r"(?is)\b(?:co\s+opravit|co\s+fixnout)\s+(?:jako\s+)?(?:prvni|první)\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
            r"(?is)\b(?:jaky|jaký)\s+(?:je\s+)?(?:dalsi|další)\s+(?:safe\s+patch\s+krok|patch\s+krok)\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
            r"(?is)\b(?:jaky|jaký)\s+(?:je\s+)?(?:dalsi|další)\s+(?:bugfix\s+krok|fix\s+krok)\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
            r"(?is)\b(?:jaky|jaký)\s+(?:dalsi|další)\s+helper\b\s*(?:pro)?\s*:?\s*(.+?)\s*$",
            r"(?is)\b(?:next\s+helper)\b\s*(?:for)?\s*:?\s*(.+?)\s*$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip().strip("\"'")
                candidate = re.sub(r"(?i)^(?:task|ukol|úkol)\s*:\s*", "", candidate).strip()
                candidate = candidate.rstrip("?. ")
                if candidate:
                    return candidate

        lines = self._non_repo_lines(text)
        filtered = []
        for line in lines:
            lower = line.lower()
            if any(
                needle in lower
                for needle in (
                    "jaky helper",
                    "jaký helper",
                    "jaky dalsi helper",
                    "jaký další helper",
                    "co opravit jako prvni",
                    "co opravit jako první",
                    "co fixnout jako prvni",
                    "co fixnout jako první",
                    "dalsi safe patch krok",
                    "další safe patch krok",
                    "dalsi patch krok",
                    "další patch krok",
                    "dalsi bugfix krok",
                    "další bugfix krok",
                    "co mam spustit dal",
                    "co mám spustit dál",
                    "co mam pustit dal",
                    "co mám pustit dál",
                    "co mám pustit dál",
                    "co má pustit dál",
                    "next helper",
                )
            ):
                continue
            filtered.append(line)
        if filtered:
            return " ".join(filtered)
        return None

    def _natural_workspace_next_helper_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        if not any(
            needle in lower
            for needle in (
                "jaky helper",
                "jaký helper",
                "jaky dalsi helper",
                "jaký další helper",
                "co opravit jako prvni",
                "co opravit jako první",
                "co fixnout jako prvni",
                "co fixnout jako první",
                "jaky dalsi safe patch krok",
                "jaký další safe patch krok",
                "jaky je dalsi safe patch krok",
                "jaký je další safe patch krok",
                "jaky dalsi patch krok",
                "jaký další patch krok",
                "jaky je dalsi patch krok",
                "jaký je další patch krok",
                "jaky dalsi bugfix krok",
                "jaký další bugfix krok",
                "jaky je dalsi bugfix krok",
                "jaký je další bugfix krok",
                "co mam spustit dal",
                "co mám spustit dál",
                "co mam pustit dal",
                "co mám pustit dál",
                "co mám pustit dál",
                "co má pustit dál",
                "next helper",
            )
        ):
            return None
        task = self._extract_next_helper_task(text)
        if not task:
            return None
        return self._next_helper_command(workspace, task)

    def _mentor_tasks_helper_command(self, mode: str, workspace: str, tasks: list[str], recommend_only: bool = False) -> str:
        joined = "\n".join(f"- {task.strip()}" for task in tasks if task.strip())
        if mode == "top":
            prompt = f"Ve workspace {workspace} vyber z těchto úkolů ten nejdůležitější a stručně zdůvodni pořadí:\n{joined}"
        elif mode == "dispatch":
            prompt = f"Ve workspace {workspace} vyber další capability krok nad tímto backlogem a stručně zdůvodni volbu:\n{joined}"
        else:
            prompt = f"Ve workspace {workspace} analyzuj tento backlog a navrhni další krok:\n{joined}"
        if recommend_only:
            prompt += "\nNic nespouštěj, jen doporuč další bezpečný capability krok."
        return self._agent_loop_admin_command(workspace, prompt)

    def _natural_workspace_top_command(self, text: str) -> str | None:
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
                "co ma delat jako prvni",
                "co má dělat jako první",
                "ktery ukol je prvni",
                "který úkol je první",
                "ktery bug je prvni",
                "který bug je první",
                "ktery bug ma nejvyssi prioritu",
                "který bug má nejvyšší prioritu",
                "jaky bug ma nejvyssi prioritu",
                "jaký bug má nejvyšší prioritu",
                "jaky je top task",
                "jaký je top task",
                "co je top task",
                "co je prvni ukol",
                "co je první úkol",
                "proc je to prvni",
                "proč je to první",
                "proc je prvni",
                "proč je první",
                "proc zrovna tenhle",
                "proč zrovna tenhle",
                "why this first",
                "why first",
            )
        ):
            return None
        return self._mentor_tasks_helper_command("top", workspace, tasks)

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
                "priorita bugu",
                "priority bugu",
                "poradi bugu",
                "pořadí bugů",
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
        return self._mentor_tasks_helper_command("backlog", workspace, tasks)

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
                "vyber dalsi safe patch krok",
                "vyber další safe patch krok",
                "vyber dalsi bugfix krok",
                "vyber další bugfix krok",
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
                "co ma delat jako prvni",
                "co má dělat jako první",
                "ktery ukol je prvni",
                "který úkol je první",
                "jaky je top task",
                "jaký je top task",
                "co je top task",
                "co je prvni ukol",
                "co je první úkol",
                "jen doporuc prvni krok",
                "jen doporuč první krok",
                "prvni krok bez spusteni",
                "první krok bez spuštění",
            )
        ):
            return None
        recommend_only = any(
            needle in lower
            for needle in (
                "jen doporuc",
                "jen doporuč",
                "bez spusteni",
                "bez spuštění",
            )
        ) or not any(
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
        return self._mentor_tasks_helper_command("dispatch", workspace, tasks, recommend_only=recommend_only)

    def _repo_root(self) -> Path:
        candidates = []
        if self.valves.repo_root and self.valves.repo_root != "auto":
            candidates.append(self.valves.repo_root)
        workspace_context_file = getattr(_workspace_context, "__file__", "")
        if workspace_context_file:
            try:
                candidates.append(str(Path(workspace_context_file).resolve().parents[2]))
            except Exception:
                pass
        module_file = globals().get("__file__")
        if module_file:
            try:
                candidates.append(str(Path(module_file).resolve().parents[2]))
            except Exception:
                pass
        candidates.extend(x.strip() for x in self.valves.candidate_roots.split(",") if x.strip())

        checked = []
        seen = set()
        for candidate in candidates:
            try:
                root = Path(candidate).resolve()
            except Exception:
                root = Path(candidate)
            key = str(root)
            if key in seen:
                continue
            seen.add(key)
            checked.append(key)
            if (root / "codex/gateway/gateway.py").is_file():
                return root
        raise FileNotFoundError("ai-stack repo root not found; checked: " + ", ".join(checked))

    def _roadmap_path(self) -> Path:
        return self._repo_root() / "docs" / "codex-local-capability-roadmap.json"

    def _load_capability_roadmap_payload(self) -> dict:
        if isinstance(EMBEDDED_CAPABILITY_ROADMAP, dict):
            return EMBEDDED_CAPABILITY_ROADMAP
        try:
            payload = json.loads(self._roadmap_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _extract_capabilities(self, payload: dict) -> dict[str, dict]:
        capabilities = payload.get("capabilities")
        return capabilities if isinstance(capabilities, dict) else {}

    def _extract_workspace_actions(self, payload: dict) -> dict[str, dict]:
        actions = payload.get("workspace_actions")
        return actions if isinstance(actions, dict) else {}

    def _match_workspace_action(self, text: str) -> tuple[str, dict] | tuple[None, None]:
        lower = text.lower()
        for action, spec in self.workspace_actions.items():
            if not isinstance(spec, dict):
                continue
            cues = spec.get("cues") or []
            if any(isinstance(cue, str) and cue.lower() in lower for cue in cues):
                return action, spec
        return None, None

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

    def _natural_workspace_release_boundary_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        if not any(
            token in lower
            for token in (
                "release",
                "publish package",
                "publish",
                "github actions",
                "tag release",
                "vytvor tag",
                "vytvoř tag",
                "vytvor release",
                "vytvoř release",
            )
        ):
            return None
        lines = self._non_repo_lines(text)
        task = " ".join(line.strip() for line in lines if line.strip())
        if not task:
            task = "Vytvoř release a pushni to na GitHub"
        return self._boundary_helper_command(workspace, task)

    def _natural_workspace_release_prep_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        if not any(
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
        ):
            return None
        if any(
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
            return None
        return self._mentor_helper_command(workspace, "release-prep", timeout=240)

    def _natural_workspace_publish_plan_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()
        if not any(
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
            return None
        if any(
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
            return None
        return self._mentor_helper_command(workspace, "publish-plan", timeout=300)

    def _extract_public_url(self, text: str) -> str | None:
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

        domain_match = re.search(r"\b([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(/[^\s<>'\")]+)?", text)
        if domain_match:
            domain = domain_match.group(1).lower()
            if domain in {"localhost", "host.docker.internal"} or domain.endswith((".local", ".localhost", ".internal")):
                return None
            suffix = domain_match.group(2) or "/"
            return "https://" + domain + suffix.rstrip(".,;:!?)]}")
        return None

    def _looks_like_web_intent(self, text: str) -> bool:
        lower = text.lower()
        cues = (
            "http://",
            "https://",
            "stahni",
            "stáhni",
            "nacti",
            "načti",
            "fetch",
            "download",
            "precti",
            "přečti",
            "podivej se",
            "podívej se",
            "z webu",
            "z internetu",
            "na webu",
            "internet",
            "url",
            "seznam.cz",
        )
        return any(cue in lower for cue in cues)

    def _looks_like_web_question(self, text: str) -> bool:
        lower = text.lower()
        question_cues = (
            "?",
            "kdo ",
            "co ",
            "jaky ",
            "jaký ",
            "jaka ",
            "jaká ",
            "jake ",
            "jaké ",
            "kdy ",
            "kde ",
            "proc ",
            "proč ",
            "who ",
            "what ",
            "when ",
            "where ",
            "why ",
            "dneska",
            "dnes ",
            "svatek",
            "svátek",
        )
        return any(cue in lower for cue in question_cues)

    def _extract_web_question(self, text: str) -> str:
        question = " ".join(self._non_repo_lines(text)).strip() or text.strip()
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
        return question or (" ".join(self._non_repo_lines(text)).strip() or text.strip())[:1200]

    def _natural_web_command(self, text: str) -> str | None:
        if not self._looks_like_web_intent(text):
            return None
        url = self._extract_public_url(text)
        if not url:
            return None
        if self._looks_like_web_question(text):
            question = self._extract_web_question(text)
            return f"GATEWAY_ADMIN_WEB_ANSWER {shlex.quote(url)} -- {shlex.quote(question[:1200])}"
        return f"GATEWAY_ADMIN_WEB_FETCH {shlex.quote(url)} --max-bytes 300000"

    def _mentions_ai_stack(self, text: str) -> bool:
        return re.search(rf"(?im)^\s*{WORKSPACE_LABEL_PATTERN}\s*:\s*ai-stack\s*$", text) is not None or "ai-stack" in text.lower()

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
        push_words = [
            "pushni zmeny",
            "pushni změny",
            "commitni a pushni",
            "commitni zmeny",
            "commitni změny",
            "commit a push",
            "commit and push",
            "git push",
            "pushni ai-stack",
            "pushni to do githubu",
            "push changes",
            "publish zmeny",
            "publish změny",
        ]
        push_check_words = [
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
            "muze to jit pushnout",
            "může to jít pushnout",
            "je to ready na push",
        ]
        release_words = [
            "release",
            "publish package",
            "github actions",
            "tag release",
            "vytvor release",
            "vytvoř release",
        ]

        if any(word in lower for word in status_words):
            return "GATEWAY_ADMIN_DEPLOY_STATUS"
        if any(word in lower for word in deploy_words):
            return "GATEWAY_ADMIN_DEPLOY_STACK"
        if any(word in lower for word in push_check_words):
            return "GATEWAY_ADMIN_GIT_STATUS"
        if any(word in lower for word in push_words) and not any(word in lower for word in release_words):
            message = self._extract_ai_stack_push_message(text)
            return f"GATEWAY_ADMIN_GIT_PUSH main {message}"
        return None

    def _extract_ai_stack_push_message(self, text: str) -> str:
        patterns = [
            r'(?im)^\s*(?:commit\s+message|message|msg|zprava|zpráva)\s*:\s*(.+?)\s*$',
            r'(?is)\b(?:s\s+commitem|s\s+message)\s+"([^"]+)"',
            r"(?is)\b(?:s\s+commitem|s\s+message)\s+'([^']+)'",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = " ".join(match.group(1).strip().split())
                if candidate:
                    return shlex.quote(candidate[:200])
        return shlex.quote("Update ai-stack via codex-local")

    def _bootstrap_improve_helper_command(self, task: str) -> str:
        return f"GATEWAY_ADMIN_AGENT_LOOP ai-stack -- {shlex.quote(task[:3000])}"

    def _natural_workspace_ssh_key_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        task = " ".join(self._non_repo_lines(text)).strip() or text.strip()
        lower = task.lower()
        create_cues = ("vytvor", "vytvoř", "zaloz", "založ", "create", "bootstrap", "priprav", "připrav")
        repo_cues = ("workspace", "repository", "repozitar", "repozitář", "repo ", "projekt")
        if any(cue in lower for cue in create_cues) and any(cue in lower for cue in repo_cues):
            return None
        if not any(cue in lower for cue in ("ssh", "klic", "klíč", "key", "deploy key", "github")):
            return None
        if not any(cue in lower for cue in ("vygeneruj", "vytvor", "vytvoř", "generate", "create")):
            return None
        key_name = re.sub(r"[^A-Za-z0-9_.-]", "-", f"github-{workspace}")[:64].strip("-") or "github-workspace"
        return f"GATEWAY_ADMIN_SSH_KEYGEN {shlex.quote(key_name)} {shlex.quote(workspace + '@local')}"

    def _negated_near(self, lower: str, word: str) -> bool:
        return re.search(rf"\b(?:bez|bez\s+toho\s+aby|without|no)\s+{re.escape(word)}\b", lower) is not None

    def _github_requested_for_bootstrap(self, lower: str) -> bool:
        if self._negated_near(lower, "github") or "bez githubu" in lower or "bez github" in lower:
            return False
        return any(
            phrase in lower
            for phrase in (
                "github repository",
                "github repo",
                "github repozitar",
                "github repozitář",
                "na githubu",
                "na github",
                "do githubu",
                "do github",
                "github remote",
            )
        )

    def _restart_requested_for_bootstrap(self, lower: str) -> bool:
        if (
            "bez restartu" in lower
            or "bez restart" in lower
            or "nerestartuj" in lower
            or "nerestartovat" in lower
            or "without restart" in lower
            or "no restart" in lower
        ):
            return False
        restart_words = (
            "restartni stack",
            "restartuj stack",
            "restartni gateway",
            "restartuj gateway",
            "restartni workspace",
            "restartuj workspace",
            "nastartuj workspace",
            "spust workspace",
            "spusť workspace",
            "zaregistruj a spust",
            "zaregistruj a spusť",
        )
        return any(word in lower for word in restart_words)

    def _natural_create_repo_command(self, text: str) -> str | None:
        lower = text.lower()
        task = " ".join(self._non_repo_lines(text)).strip() or text.strip()
        task_lower = task.lower()
        create_words = ["vytvor", "vytvoř", "zaloz", "založ", "create", "bootstrap", "priprav", "připrav"]
        repo_words = ["repository", "repozitar", "repozitář", "repo ", "projekt ", "workspace "]
        setup_words = [
            "ssh key",
            "ssh klic",
            "ssh klíč",
            "github",
            "deploy key",
            "git remote",
            "origin",
            "git init",
            "init git",
            "initni git",
            "inicializuj git",
            "initialize git",
        ]
        followthrough_words = [
            "doinstaluj",
            "nainstaluj",
            "install",
            "stahni co je treba",
            "stáhni co je třeba",
            "stahnout co je treba",
            "stáhnout co je třeba",
            "stahnout co potrebuje",
            "stáhnout co potřebuje",
            "zavislost",
            "závislost",
            "napis kod",
            "napiš kód",
            "vytvor kod",
            "vytvoř kód",
            "vytvorit kod",
            "vytvořit kód",
            "napis zaklad",
            "napiš základ",
            "zaklad appky",
            "základ appky",
            "zaklad aplikace",
            "základ aplikace",
            "implementuj",
            "dopln kod",
            "doplň kód",
            "udelej appku",
            "udělej appku",
            "udelej projekt",
            "udělej projekt",
            "priprav starter",
            "připrav starter",
            "priprav scaffold",
            "připrav scaffold",
            "rozbehni",
            "rozběhni",
            "spust to",
            "spusť to",
            "pust to",
            "pusť to",
            "pustit",
            "pusit",
            "build",
            "testy",
            "dotahni",
            "dotáhni",
            "pokracuj sam",
            "pokračuj sám",
            "pokračuj sam",
            "co je treba",
            "co je třeba",
        ]
        has_create = any(word in lower for word in create_words)
        has_repo = any(word in lower for word in repo_words)
        has_setup = any(word in lower for word in setup_words)
        if not (has_create and (has_repo or has_setup)):
            return None

        routed_name = self._workspace_from_text(text)
        if routed_name and routed_name.lower() not in {"ai-stack", "smoke", "github", "gitlab", "remote", "new", "novy", "nový", "nove", "nové"}:
            has_task_create = any(word in task_lower for word in create_words)
            has_task_repo = any(
                word in task_lower
                for word in ("repository", "repozitar", "repozitář", "repo", "projekt", "workspace")
            )
            key_requested = any(word in task_lower for word in ("ssh key", "ssh klic", "ssh klíč", "vygeneruj klic", "vygeneruj klíč"))
            asks_only_for_key = key_requested and not (has_task_create and has_task_repo)
            if has_task_create and has_task_repo and not asks_only_for_key:
                if any(word in lower for word in followthrough_words):
                    return self._bootstrap_improve_helper_command(task)
                github = " --github" if self._github_requested_for_bootstrap(lower) else ""
                restart = " --restart" if self._restart_requested_for_bootstrap(lower) else ""
                return f"GATEWAY_ADMIN_CREATE_LOCAL_REPO {routed_name}{github}{restart}"

        patterns = [
            r"(?i)\b(?:vytvor|vytvoř|zaloz|založ|create)\b\s+(?:mi\s+)?(?:(?:novy|nový|nove|nové|new)\s+)?(?:(?:github|gitlab|remote)\s+)?(?:repository|repozitar|repozitář|repo)\s+([A-Za-z0-9_.-]{1,80})\b",
            r"(?i)\b(?:vytvor|vytvoř|zaloz|založ|create|bootstrap|priprav|připrav)\b\s+(?:mi\s+)?(?:(?:novy|nový|nove|nové|new)\s+)?(?:projekt|workspace)\s+([A-Za-z0-9_.-]{1,80})\b",
            r"(?i)\b(?:vytvor|vytvoř|zaloz|založ|create)\b\s+([A-Za-z0-9_.-]{1,80})\b\s+(?:repository|repozitar|repozitář|repo|projekt|workspace)\b",
            r"(?i)\b(?:repository|repozitar|repozitář|repo)\s+([A-Za-z0-9_.-]{1,80})\b",
            r"(?i)\b(?:projekt|workspace)\s+([A-Za-z0-9_.-]{1,80})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            name = match.group(1)
            if name.lower() in {"ai-stack", "smoke", "github", "gitlab", "remote", "new", "novy", "nový", "nove", "nové"}:
                continue
            if any(word in lower for word in followthrough_words):
                task = " ".join(self._non_repo_lines(text)).strip() or text.strip()
                return self._bootstrap_improve_helper_command(task)
            github = " --github" if self._github_requested_for_bootstrap(lower) else ""
            restart = " --restart" if self._restart_requested_for_bootstrap(lower) else ""
            return f"GATEWAY_ADMIN_CREATE_LOCAL_REPO {name}{github}{restart}"
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

    def _looks_like_workspace_edit(self, text: str) -> bool:
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
                "canvas",
                "html",
                "kouli",
                "sphere",
            )
        )

    def _natural_workspace_edit_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace or not self._looks_like_workspace_edit(text):
            return None
        task = " ".join(self._non_repo_lines(text)).strip() or text.strip()
        if not task:
            return None
        lower = task.lower()
        run_after = ""
        run_cues = {
            "test": ("test", "testy", "otestuj"),
            "build": ("build", "sestav", "zbuild"),
            "lint": ("lint",),
            "verify": ("verify", "over", "ověř", "zkontroluj"),
            "install": ("install", "nainstaluj", "doinstaluj", "zavislosti", "závislosti"),
            "smoke": ("spust", "spusť", "rozbehni", "rozběhni", "run it", "smoke"),
        }
        for action, cues in run_cues.items():
            if any(cue in lower for cue in cues):
                run_after = action
                break
        suffix = f" --run-after {run_after}" if run_after else ""
        return f"GATEWAY_ADMIN_WORKSPACE_EDIT {workspace} --timeout 900{suffix} -- {shlex.quote(task[:1800])}"

    def _natural_workspace_action_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        action, spec = self._match_workspace_action(text)
        if action and spec:
            timeout = int(spec.get("timeout", 900))
            runner = str(spec.get("runner", "container"))
            runner_arg = f" --runner {runner}" if runner else ""
            return f"GATEWAY_ADMIN_WORKSPACE_ACTION {workspace} {action}{runner_arg} --timeout {timeout}"
        return None

    def _delegate_helper_command(self, workspace: str, task: str) -> str:
        # Route broad "take it from here" prompts straight into the capability-first
        # agent loop. The older nested mentor helper path could recurse back into
        # OpenWebUI visible-chat helpers and stall on chat GET/POST timeouts.
        return f"GATEWAY_ADMIN_AGENT_LOOP {shlex.quote(workspace)} -- {shlex.quote(task[:3000])}"

    def _natural_workspace_delegate_command(self, text: str) -> str | None:
        workspace = self._workspace_from_text(text)
        if not workspace:
            return None
        lower = text.lower()

        delegate_needles = (
            "fixni to",
            "dotahni to",
            "dotáhni to",
            "dokonci to",
            "dokonči to",
            "rozbehni to",
            "rozběhni to",
            "vem si to cele",
            "vezmi si to celé",
            "postarej se o to",
            "postarej se o to sam",
            "postarej se o to sám",
            "dotahni co pujde",
            "dotáhni co půjde",
            "udelej co je potreba",
            "udělej co je potřeba",
            "pokracuj jako codex",
            "pokračuj jako codex",
            "bud autonomni",
            "buď autonomní",
            "vyber workflow a proved",
            "vyber workflow a proveď",
            "sam rozhodni workflow",
            "sám rozhodni workflow",
            "mentorovane to proved",
            "mentorovaně to proveď",
            "proved to jako codex",
            "proveď to jako codex",
            "dotahni co zvladnes",
            "dotáhni co zvládneš",
            "udělej maximum",
            "udělej co zvládneš",
        )
        if not any(needle in lower for needle in delegate_needles):
            return None

        lines = self._non_repo_lines(text)
        task = " ".join(lines).strip()
        if not task:
            return None
        return self._delegate_helper_command(workspace, task)

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
            "udelej maximum",
            "udělej maximum",
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
            "zkus vsechno potrebne",
            "zkus všechno potřebné",
        ]
        if any(needle in lower for needle in recommend_only):
            return f"GATEWAY_ADMIN_WORKSPACE_AUTOPILOT {workspace} --recommend-only --timeout 2400"
        if any(needle in lower for needle in autopilot):
            return (
                f"GATEWAY_ADMIN_WORKSPACE_AUTOPILOT {workspace} "
                "--timeout 2400 --max-steps 3 "
                "--allow-actions install,verify,smoke,test,build,lint"
            )
        return None

    def _workspaces_file(self) -> Path:
        env_path = os.getenv("CODEX_WORKSPACES_FILE", "").strip()
        if env_path:
            return Path(env_path)
        return self._repo_root() / "codex/workspaces.json"

    def _workspaces(self) -> dict[str, dict]:
        try:
            return load_workspace_registry(self._workspaces_file())[1]
        except Exception:
            return {}

    def _workspace_from_text(self, text: str, body: dict | None = None) -> str | None:
        try:
            resolved = resolve_workspace_context(
                text,
                (body or {}).get("messages") or [],
                self._workspaces_file(),
                fallback_workspace="ai-stack",
            )
        except Exception:
            return None
        return resolved.workspace if resolved.workspace_exists or resolved.workspace == "ai-stack" else resolved.workspace

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
