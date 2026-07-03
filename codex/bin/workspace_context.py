#!/usr/bin/env python3
"""Shared workspace/context resolver for codex-local routing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE_LABEL_PATTERN = r"(?:repo|repository|repositar|repozitar|repozitář|projekt|project|workspace)"
WORKSPACE_NAME_PATTERN = r"[A-Za-z0-9_.-]{1,80}"
WORKSPACE_VALUE_RE = re.compile(rf"(?i)\b({WORKSPACE_NAME_PATTERN})\b")
BOOTSTRAP_TARGET_LABEL_PATTERN = r"(?:repository|repozit\w*|reposit\w*|workspace|projekt|project|repo)"
BOOTSTRAP_CREATE_REPO_PATTERNS = (
    rf"(?i)\b(?:vytvor|vytvoř|zaloz|založ|create)\b\s+(?:mi\s+)?(?:(?:novy|nový|nove|nové|new)\s+)?{BOOTSTRAP_TARGET_LABEL_PATTERN}\b\s*:?\s*({WORKSPACE_NAME_PATTERN})\b",
)
BOOTSTRAP_REPO_PATTERNS = BOOTSTRAP_CREATE_REPO_PATTERNS + (
    rf"(?i)\b{BOOTSTRAP_TARGET_LABEL_PATTERN}\b\s+({WORKSPACE_NAME_PATTERN})\b",
    rf"(?i)\bv\s+(?:repository|repozit\w*|reposit\w*|workspace|projektu|projectu|repo)\b\s+({WORKSPACE_NAME_PATTERN})\b",
    rf"(?i)\b(?:workspace|projekt|project)\b\s+({WORKSPACE_NAME_PATTERN})\b",
)
ASSISTANT_CONTEXT_PATTERNS = (
    rf"(?im)^\s*requested_workspace\s*=\s*({WORKSPACE_NAME_PATTERN})\s*$",
    rf"(?im)^\s*controller_workspace\s*=\s*({WORKSPACE_NAME_PATTERN})\s*$",
    rf"(?im)^\s*workspace\s*=\s*({WORKSPACE_NAME_PATTERN})\s*$",
    rf"(?im)^\s*name\s*=\s*({WORKSPACE_NAME_PATTERN})\s*$",
    rf"(?im)^\s*repo\s*:\s*({WORKSPACE_NAME_PATTERN})\s*$",
)


@dataclass(frozen=True)
class WorkspaceResolution:
    workspace: str
    source: str
    explicit: bool
    workspace_exists: bool


def load_workspace_registry(workspaces_file: str | Path) -> tuple[str, dict[str, dict]]:
    path = Path(workspaces_file)
    data = json.loads(path.read_text(encoding="utf-8"))
    default = str(data.get("default") or "smoke").strip() or "smoke"
    workspaces = data.get("workspaces") or {}
    if not isinstance(workspaces, dict):
        workspaces = {}
    return default, workspaces


def canonical_workspace_name(candidate: str, workspaces: dict[str, dict]) -> str | None:
    value = str(candidate or "").strip()
    if not value:
        return None
    if value in workspaces:
        return value
    lowered = value.lower()
    matches = [name for name in workspaces if name.lower() == lowered]
    if len(matches) == 1:
        return matches[0]
    return None


def infer_repo_name_from_text(text: str) -> str:
    value = str(text or "")
    for pattern in BOOTSTRAP_REPO_PATTERNS:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return ""


def bootstrap_repo_name_from_text(text: str) -> str:
    value = str(text or "")
    for pattern in BOOTSTRAP_CREATE_REPO_PATTERNS:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return ""


def _workspace_from_label(text: str, workspaces: dict[str, dict]) -> str | None:
    patterns = (
        rf"(?im)^\s*{WORKSPACE_LABEL_PATTERN}\s*:\s*({WORKSPACE_NAME_PATTERN})\s*$",
        rf"(?im)^\s*{WORKSPACE_LABEL_PATTERN}\s+({WORKSPACE_NAME_PATTERN})\s*$",
    )
    value = str(text or "")
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            resolved = canonical_workspace_name(match.group(1), workspaces)
            if resolved:
                return resolved
    return None


def _workspace_from_first_line(text: str, workspaces: dict[str, dict]) -> str | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return None
    first = lines[0]
    match = re.fullmatch(rf"({WORKSPACE_NAME_PATTERN})\s*:", first)
    if match:
        return canonical_workspace_name(match.group(1), workspaces)
    return None


def _workspace_from_first_token(text: str, workspaces: dict[str, dict]) -> str | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return None
    first = lines[0]
    token_match = re.match(rf"^\s*({WORKSPACE_NAME_PATTERN})(?:\s|:|$)", first)
    if not token_match:
        return None
    return canonical_workspace_name(token_match.group(1), workspaces)


def resolve_workspace_from_text(text: str, workspaces: dict[str, dict]) -> WorkspaceResolution | None:
    for source, resolver in (
        ("label", _workspace_from_label),
        ("first_line", _workspace_from_first_line),
        ("first_token", _workspace_from_first_token),
    ):
        workspace = resolver(text, workspaces)
        if workspace:
            return WorkspaceResolution(workspace, source, True, True)
    inferred = infer_repo_name_from_text(text)
    inferred_name = canonical_workspace_name(inferred, workspaces)
    bootstrap_name = canonical_workspace_name(bootstrap_repo_name_from_text(text), workspaces)
    if bootstrap_name and inferred_name == bootstrap_name:
        return None
    if inferred_name:
        return WorkspaceResolution(inferred_name, "inferred_repo_name", False, True)
    return None


def _message_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def iter_message_texts(messages: Iterable[dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for message in messages or []:
        out.append((str(message.get("role") or ""), _message_text(message)))
    return out


def resolve_workspace_from_history(messages: Iterable[dict], workspaces: dict[str, dict]) -> WorkspaceResolution | None:
    items = iter_message_texts(messages)
    for role, text in reversed(items):
        if role == "system":
            continue
        direct = resolve_workspace_from_text(text, workspaces)
        if direct:
            return WorkspaceResolution(direct.workspace, f"history_{role}_{direct.source}", False, True)
        for pattern in ASSISTANT_CONTEXT_PATTERNS:
            match = re.search(pattern, text)
            if not match:
                continue
            candidate = canonical_workspace_name(match.group(1), workspaces)
            if candidate:
                return WorkspaceResolution(candidate, f"history_{role}_context", False, True)
        inferred = canonical_workspace_name(infer_repo_name_from_text(text), workspaces)
        if inferred:
            return WorkspaceResolution(inferred, f"history_{role}_repo_name", False, True)
    return None


def resolve_workspace_context(
    text: str,
    messages: Iterable[dict],
    workspaces_file: str | Path,
    fallback_workspace: str | None = None,
) -> WorkspaceResolution:
    default, workspaces = load_workspace_registry(workspaces_file)
    fallback = canonical_workspace_name(fallback_workspace or "", workspaces) or canonical_workspace_name("ai-stack", workspaces) or default
    direct = resolve_workspace_from_text(text, workspaces)
    if direct:
        return direct
    history = resolve_workspace_from_history(messages, workspaces)
    if history:
        return history
    return WorkspaceResolution(fallback, "fallback", False, fallback in workspaces)


def strip_workspace_routing(text: str, workspaces: dict[str, dict] | None = None) -> str:
    value = str(text or "")
    cleaned = []
    for raw in value.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(rf"(?i){WORKSPACE_LABEL_PATTERN}\s*:\s*{WORKSPACE_NAME_PATTERN}", line):
            continue
        if re.fullmatch(rf"(?i){WORKSPACE_LABEL_PATTERN}\s+{WORKSPACE_NAME_PATTERN}", line):
            continue
        if workspaces and re.fullmatch(rf"{WORKSPACE_NAME_PATTERN}\s*:", line):
            candidate = line.split(":", 1)[0].strip()
            if canonical_workspace_name(candidate, workspaces):
                continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()
