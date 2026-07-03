#!/usr/bin/env python3
"""Shared codex-local model/runtime configuration.

The core policy is:
- one persistent default coding model
- prompt role changes behavior, not automatic model switching
- heavy model is opt-in only
- structured output is preferred for planner/tool JSON, but never hard-required
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


DEFAULT_MODEL_ALIAS = "codex-local"
HEAVY_MODEL_ALIAS = "codex-local-heavy"
EXPERIMENTAL_PLANNER_ALIAS = "codex-local-planner-exp"

DEFAULT_MODEL_NAME = "qwen2.5-coder:14b"
HEAVY_MODEL_NAME = "qwen2.5-coder:32b"

DEFAULT_MODEL_MODE = "single"
DEFAULT_STRUCTURED_OUTPUT = "auto"
DEFAULT_STRUCTURED_BACKEND = "auto"
DEFAULT_STRUCTURED_ATTEMPT_TIMEOUT = 8

ROLE_PLANNER = "planner"
ROLE_EXECUTOR = "executor"
ROLE_REVIEWER = "reviewer"
ROLE_RECOVERY = "recovery"
ROLE_DIRECT = "direct"
ROLE_AGENT = "agent"

HEAVY_REQUEST_RE = re.compile(
    r"(?i)\b(?:heavy|deep|frontier|quality\s+mode|hq\s+mode|high\s+quality|32b|30b)\b"
)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, lower: int, upper: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lower, min(upper, value))


@dataclass(frozen=True)
class CodexLocalConfig:
    default_model: str
    heavy_model: str
    model_mode: str
    allow_heavy_escalation: bool
    structured_output: str
    structured_backend: str
    experimental_planner_model: str
    structured_attempt_timeout: int = DEFAULT_STRUCTURED_ATTEMPT_TIMEOUT


def load_codex_local_config() -> CodexLocalConfig:
    return CodexLocalConfig(
        default_model=os.getenv("CODEX_LOCAL_DEFAULT_MODEL", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME,
        heavy_model=os.getenv("CODEX_LOCAL_HEAVY_MODEL", HEAVY_MODEL_NAME).strip() or HEAVY_MODEL_NAME,
        model_mode=os.getenv("CODEX_LOCAL_MODEL_MODE", DEFAULT_MODEL_MODE).strip().lower() or DEFAULT_MODEL_MODE,
        allow_heavy_escalation=env_bool("CODEX_LOCAL_ALLOW_HEAVY_ESCALATION", False),
        structured_output=os.getenv("CODEX_LOCAL_STRUCTURED_OUTPUT", DEFAULT_STRUCTURED_OUTPUT).strip().lower() or DEFAULT_STRUCTURED_OUTPUT,
        structured_backend=os.getenv("CODEX_LOCAL_STRUCTURED_BACKEND", DEFAULT_STRUCTURED_BACKEND).strip().lower() or DEFAULT_STRUCTURED_BACKEND,
        experimental_planner_model=os.getenv("CODEX_LOCAL_EXPERIMENTAL_PLANNER_MODEL", "").strip(),
        structured_attempt_timeout=env_int(
            "CODEX_LOCAL_STRUCTURED_ATTEMPT_TIMEOUT",
            DEFAULT_STRUCTURED_ATTEMPT_TIMEOUT,
            1,
            60,
        ),
    )


def codex_local_model_aliases(config: CodexLocalConfig | None = None) -> dict[str, dict[str, str]]:
    config = config or load_codex_local_config()
    aliases = {
        DEFAULT_MODEL_ALIAS: {"model": config.default_model, "role": ROLE_AGENT},
        HEAVY_MODEL_ALIAS: {"model": config.heavy_model, "role": ROLE_AGENT},
        EXPERIMENTAL_PLANNER_ALIAS: {
            "model": config.experimental_planner_model or config.default_model,
            "role": ROLE_PLANNER,
        },
        "codex-local-plan-qwen14b": {"model": config.default_model, "role": ROLE_PLANNER},
        "codex-local-build-qwen14b": {"model": config.default_model, "role": ROLE_EXECUTOR},
        "codex-local-plan-qwen32b": {"model": config.heavy_model, "role": ROLE_PLANNER},
        "codex-local-build-qwen32b": {"model": config.heavy_model, "role": ROLE_EXECUTOR},
    }
    return aliases


def is_codex_local_model_name(model_name: str) -> bool:
    return str(model_name or "").strip().startswith("codex-local")


def task_requests_heavy(task: str) -> bool:
    return bool(HEAVY_REQUEST_RE.search(str(task or "")))


def resolve_runtime_model(
    requested_model_name: str,
    *,
    task: str = "",
    role: str = ROLE_AGENT,
    config: CodexLocalConfig | None = None,
) -> dict[str, str | bool]:
    config = config or load_codex_local_config()
    aliases = codex_local_model_aliases(config)
    requested = str(requested_model_name or "").strip() or DEFAULT_MODEL_ALIAS
    alias = aliases.get(requested) or aliases[DEFAULT_MODEL_ALIAS]
    resolved_role = str(alias.get("role") or role or ROLE_AGENT)
    chosen_model = str(alias.get("model") or config.default_model)
    heavy_requested = requested == HEAVY_MODEL_ALIAS or requested in {
        "codex-local-plan-qwen32b",
        "codex-local-build-qwen32b",
    }

    if heavy_requested and not config.heavy_model:
        chosen_model = config.default_model
        heavy_requested = False
    elif requested == EXPERIMENTAL_PLANNER_ALIAS and not config.experimental_planner_model:
        chosen_model = config.default_model
    elif not heavy_requested and config.allow_heavy_escalation and task_requests_heavy(task):
        chosen_model = config.heavy_model
        heavy_requested = True
    if not chosen_model:
        chosen_model = config.default_model

    used_experimental_planner = bool(
        requested == EXPERIMENTAL_PLANNER_ALIAS and config.experimental_planner_model
    )
    return {
        "requested_model": requested,
        "resolved_alias": requested if requested in aliases else DEFAULT_MODEL_ALIAS,
        "model": chosen_model,
        "role": resolved_role,
        "heavy_requested": heavy_requested,
        "heavy_available": bool(config.heavy_model),
        "used_experimental_planner": used_experimental_planner,
    }
