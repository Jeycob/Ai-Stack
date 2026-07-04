#!/usr/bin/env python3
"""Offline guard for the OpenWebUI codex-local filter.

The filter may resolve workspace labels and wrap the user's latest message in
GATEWAY_ADMIN_AGENT_LOOP. It must not choose business workflows from Czech or
English natural-language phrases before the gateway TaskSpec planner sees the
task.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILTER_PATH = ROOT / "codex/bin/openwebui_codex_auto_tools_filter.py"


def fail(message: str) -> None:
    raise SystemExit(f"OPENWEBUI_FILTER_LLM_FIRST_SMOKE_FAILED\n{message}")


def load_filter_module(workspaces_file: Path):
    import os

    old = os.environ.get("CODEX_WORKSPACES_FILE")
    os.environ["CODEX_WORKSPACES_FILE"] = str(workspaces_file)
    try:
        spec = importlib.util.spec_from_file_location("openwebui_codex_auto_tools_filter_smoke", FILTER_PATH)
        if spec is None or spec.loader is None:
            fail(f"unable to load {FILTER_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            os.environ.pop("CODEX_WORKSPACES_FILE", None)
        else:
            os.environ["CODEX_WORKSPACES_FILE"] = old


def build_workspaces(tmp: Path) -> Path:
    import json

    ai_stack = tmp / "ai-stack"
    test2 = tmp / "Test2"
    ai_stack.mkdir()
    test2.mkdir()
    path = tmp / "workspaces.json"
    path.write_text(
        json.dumps(
            {
                "default": "ai-stack",
                "workspaces": {
                    "ai-stack": {"path": str(ai_stack), "port": 4098},
                    "Test2": {"path": str(test2), "port": 4100},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def route(filter_obj, prompt: str, model: str = "codex-local") -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}
    routed = filter_obj.inlet(body)
    messages = routed.get("messages") or []
    if not messages:
        fail(f"routed body has no messages for prompt={prompt!r}")
    return str(messages[-1].get("content") or "")


def assert_codex_local_always_delegates_to_agent_loop() -> None:
    with tempfile.TemporaryDirectory(prefix="owui-filter-llm-first-") as raw_tmp:
        workspaces = build_workspaces(Path(raw_tmp))
        module = load_filter_module(workspaces)
        filter_obj = module.Filter()
        prompts = (
            "ahoj kdo jsi a co umíš?",
            "co je dnes za den?",
            "jaké známé osobnosti se narodily v dnešní den?",
            "vypiš výsledky",
            "repo: Test2\nvytvor tam ssh klic a vypis mi public",
            "repo: Test2\ninitni git repo a pushni sem git@github.com:owner/repo.git",
            "repo: ai-stack\npullni ai-stack a nasad",
            "repo: ai-stack\npřidej capability workspace_profile pro bounded workspace profile",
        )
        forbidden_admin = (
            "GATEWAY_ADMIN_CREATE_LOCAL_REPO",
            "GATEWAY_ADMIN_SSH_KEYGEN",
            "GATEWAY_ADMIN_WEB_ANSWER",
            "GATEWAY_ADMIN_DEPLOY_STACK",
            "GATEWAY_ADMIN_GIT_PUSH",
            "GATEWAY_ADMIN_WORKSPACE_ACTION",
            "GATEWAY_ADMIN_WORKSPACE_AUTOPILOT",
            "GATEWAY_ADMIN_EXPLAIN_FILE",
        )
        for prompt in prompts:
            routed = route(filter_obj, prompt)
            if "GATEWAY_ADMIN_AGENT_LOOP" not in routed:
                fail(f"codex-local prompt did not route to agent loop: {prompt!r} -> {routed!r}")
            found = [item for item in forbidden_admin if item in routed]
            if found:
                fail(f"codex-local prompt used pre-TaskSpec admin workflow {found!r}: {prompt!r} -> {routed!r}")
    print("CODEX_LOCAL_FILTER_AGENT_LOOP_ONLY_OK")


def assert_live_codex_local_path_has_no_keyword_router_calls() -> None:
    with tempfile.TemporaryDirectory(prefix="owui-filter-llm-first-") as raw_tmp:
        workspaces = build_workspaces(Path(raw_tmp))
        module = load_filter_module(workspaces)
        filter_cls = module.Filter
        live_sources = {
            "inlet": inspect.getsource(filter_cls.inlet),
            "_route_codex_local_admin_intent": inspect.getsource(filter_cls._route_codex_local_admin_intent),
            "_agent_loop_task_text": inspect.getsource(filter_cls._agent_loop_task_text),
        }

    joined = "\n".join(live_sources.values())
    forbidden_fragments = (
        "_natural_",
        "GATEWAY_ADMIN_CREATE_LOCAL_REPO",
        "GATEWAY_ADMIN_SSH_KEYGEN",
        "GATEWAY_ADMIN_WEB_ANSWER",
        "GATEWAY_ADMIN_DEPLOY_STACK",
        "GATEWAY_ADMIN_GIT_PUSH",
        "GATEWAY_ADMIN_WORKSPACE_ACTION",
        "GATEWAY_ADMIN_WORKSPACE_AUTOPILOT",
    )
    found = [fragment for fragment in forbidden_fragments if fragment in joined]
    if found:
        fail(
            "live codex-local filter path contains pre-TaskSpec routing fragments "
            f"{found!r}; keep natural language reasoning inside gateway TaskSpec planner"
        )
    if "GATEWAY_ADMIN_AGENT_LOOP" not in joined:
        fail("live codex-local filter path no longer delegates to GATEWAY_ADMIN_AGENT_LOOP")
    print("LIVE_CODEX_LOCAL_FILTER_PATH_SOURCE_OK")


def assert_non_codex_not_forced_by_keywords() -> None:
    with tempfile.TemporaryDirectory(prefix="owui-filter-llm-first-") as raw_tmp:
        workspaces = build_workspaces(Path(raw_tmp))
        module = load_filter_module(workspaces)
        filter_obj = module.Filter()
        prompt = "vygeneruj ssh klic pro github a pushni repo"
        routed = route(filter_obj, prompt, model="mistral-small")
        if "GATEWAY_ADMIN_" in routed:
            fail(f"non-codex model was forced into admin route by keywords: {routed!r}")
    print("NON_CODEX_KEYWORDS_NOT_FORCED_OK")


def main() -> int:
    assert_codex_local_always_delegates_to_agent_loop()
    assert_live_codex_local_path_has_no_keyword_router_calls()
    assert_non_codex_not_forced_by_keywords()
    print("OPENWEBUI_FILTER_LLM_FIRST_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
