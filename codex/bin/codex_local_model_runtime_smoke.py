#!/usr/bin/env python3
"""Smoke tests for shared codex-local model/runtime policy."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.bin import codex_local_config as cfg
from codex.gateway import gateway


def expect(condition: bool, label: str, detail: str) -> None:
    if not condition:
        raise SystemExit(f"CODEX_LOCAL_MODEL_RUNTIME_SMOKE_FAILED\nlabel={label}\ndetail={detail}")


def main() -> int:
    default_cfg = cfg.load_codex_local_config()
    expect(default_cfg.structured_output == "auto", "structured-output-default", repr(default_cfg))
    expect(default_cfg.structured_backend == "auto", "structured-backend-default", repr(default_cfg))
    expect(default_cfg.experimental_planner_model == "", "granite-default-off", repr(default_cfg))

    roles = [cfg.ROLE_PLANNER, cfg.ROLE_EXECUTOR, cfg.ROLE_REVIEWER, cfg.ROLE_RECOVERY]
    models = {
        role: cfg.resolve_runtime_model(cfg.DEFAULT_MODEL_ALIAS, role=role, config=default_cfg)["model"]
        for role in roles
    }
    expect(len(set(models.values())) == 1, "single-persistent-default-model", repr(models))

    no_heavy = cfg.CodexLocalConfig(
        default_model="qwen2.5-coder:14b",
        heavy_model="qwen2.5-coder:32b",
        model_mode="single",
        allow_heavy_escalation=False,
        structured_output="auto",
        structured_backend="auto",
        experimental_planner_model="",
    )
    runtime = cfg.resolve_runtime_model(
        cfg.DEFAULT_MODEL_ALIAS,
        role=cfg.ROLE_PLANNER,
        task="udelej deep analysis quality mode",
        config=no_heavy,
    )
    expect(runtime["model"] == "qwen2.5-coder:14b", "heavy-not-automatic", repr(runtime))

    yes_heavy = cfg.CodexLocalConfig(
        default_model="qwen2.5-coder:14b",
        heavy_model="qwen3-coder:30b",
        model_mode="single",
        allow_heavy_escalation=True,
        structured_output="auto",
        structured_backend="auto",
        experimental_planner_model="",
    )
    runtime = cfg.resolve_runtime_model(
        cfg.DEFAULT_MODEL_ALIAS,
        role=cfg.ROLE_PLANNER,
        task="udelej deep analysis quality mode",
        config=yes_heavy,
    )
    expect(runtime["model"] == "qwen3-coder:30b", "heavy-explicit-escalation", repr(runtime))

    missing_heavy = cfg.CodexLocalConfig(
        default_model="qwen2.5-coder:14b",
        heavy_model="",
        model_mode="single",
        allow_heavy_escalation=True,
        structured_output="auto",
        structured_backend="auto",
        experimental_planner_model="",
    )
    runtime = cfg.resolve_runtime_model(
        cfg.HEAVY_MODEL_ALIAS,
        role=cfg.ROLE_PLANNER,
        task="heavy",
        config=missing_heavy,
    )
    expect(runtime["model"] == "qwen2.5-coder:14b", "missing-heavy-falls-back", repr(runtime))

    plain_json_cfg = cfg.CodexLocalConfig(
        default_model="qwen2.5-coder:14b",
        heavy_model="qwen2.5-coder:32b",
        model_mode="single",
        allow_heavy_escalation=False,
        structured_output="none",
        structured_backend="none",
        experimental_planner_model="",
    )
    with patch.object(gateway, "CODEX_LOCAL_CONFIG", plain_json_cfg):
        response_format = gateway.codex_local_structured_response_format(
            "demo",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"foo": {"type": "string"}},
                "required": ["foo"],
            },
        )
    expect(response_format is None, "structured-disabled-no-hard-dependency", repr(response_format))

    structured_cfg = cfg.CodexLocalConfig(
        default_model="qwen2.5-coder:14b",
        heavy_model="qwen2.5-coder:32b",
        model_mode="single",
        allow_heavy_escalation=False,
        structured_output="auto",
        structured_backend="auto",
        experimental_planner_model="",
        structured_attempt_timeout=3,
    )
    gateway.reset_structured_backend_state()
    with patch.object(gateway, "CODEX_LOCAL_CONFIG", structured_cfg), patch.object(
        gateway,
        "ollama_chat",
        side_effect=[
            RuntimeError("structured unsupported"),
            {"choices": [{"message": {"content": 'oops {"foo":'}}]},
            {"choices": [{"message": {"content": '{"foo":"fixed"}'}}]},
        ],
    ):
        parsed, _raw, meta = gateway.structured_json_chat(
            "qwen2.5-coder:14b",
            [{"role": "user", "content": "test"}],
            "demo",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"foo": {"type": "string"}},
                "required": ["foo"],
            },
            timeout=10,
        )
    expect(parsed == {"foo": "fixed"}, "structured-repair-retry", repr((parsed, meta)))
    expect(meta["strategy"] == "repair_retry", "structured-repair-strategy", repr(meta))
    expect(meta["attempts"][0]["timeout"] == 3, "structured-bounded-timeout", repr(meta))
    expect(
        gateway.STRUCTURED_BACKEND_STATE["usable"] is False,
        "structured-failure-cached",
        repr(gateway.STRUCTURED_BACKEND_STATE),
    )

    with patch.object(gateway, "CODEX_LOCAL_CONFIG", structured_cfg), patch.object(
        gateway,
        "ollama_chat",
        return_value={"choices": [{"message": {"content": '{"foo":"plain"}'}}]},
    ) as chat_mock:
        parsed, _raw, meta = gateway.structured_json_chat(
            "qwen2.5-coder:14b",
            [{"role": "user", "content": "test"}],
            "demo",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"foo": {"type": "string"}},
                "required": ["foo"],
            },
            timeout=10,
        )
    expect(parsed == {"foo": "plain"}, "structured-disabled-after-failure", repr((parsed, meta)))
    expect(meta["strategy"] == "plain_json", "structured-cache-plain-strategy", repr(meta))
    expect(chat_mock.call_count == 1, "structured-cache-skips-schema-attempt", repr(chat_mock.call_args_list))

    gateway.reset_structured_backend_state()
    with patch.object(gateway, "CODEX_LOCAL_CONFIG", structured_cfg), patch.object(
        gateway,
        "ollama_chat",
        side_effect=[
            TimeoutError("structured hung"),
            {"choices": [{"message": {"content": '{"foo":"fallback"}'}}]},
        ],
    ) as chat_mock:
        parsed, _raw, meta = gateway.structured_json_chat(
            "qwen2.5-coder:14b",
            [{"role": "user", "content": "test"}],
            "demo",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"foo": {"type": "string"}},
                "required": ["foo"],
            },
            timeout=10,
        )
    expect(parsed == {"foo": "fallback"}, "structured-timeout-fallback", repr((parsed, meta)))
    expect(meta["strategy"] == "plain_json", "structured-timeout-fallback-strategy", repr(meta))
    expect(chat_mock.call_count == 2, "structured-timeout-then-plain", repr(chat_mock.call_args_list))

    print("CODEX_LOCAL_MODEL_RUNTIME_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
