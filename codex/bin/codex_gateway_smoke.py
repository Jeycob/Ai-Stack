#!/usr/bin/env python3
"""Smoke-test the local codex gateway without external dependencies."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:9101"
DEFAULT_MODEL = "codex-local"


class SmokeError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def request_json(method: str, url: str, timeout: float, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise SmokeError(f"HTTP {resp.status} from {url}: {raw[:500]}")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SmokeError(f"HTTP {exc.code} from {url}: {raw[:500]}") from exc
    except urllib.error.URLError as exc:
        raise SmokeError(f"Connection failed for {url}: {exc}") from exc


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def assistant_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    return choices[0].get("message", {}).get("content", "") if choices else ""


def test_health(base_url: str, timeout: float) -> None:
    log("[1/6] GET /health")
    data = request_json("GET", f"{base_url}/health", timeout)
    check(data.get("ok") is True, f"health response is not ok: {data}")
    check(data.get("capability_mode") == "agent-first", f"unexpected capability mode: {data}")
    check(data.get("natural_codex_local_route") == "agent_loop", f"unexpected natural route: {data}")
    check(bool(data.get("gateway_source_epoch")), f"missing gateway_source_epoch in health payload: {data}")
    check(bool(data.get("runtime_fingerprint")), f"missing runtime_fingerprint in health payload: {data}")


def test_models(base_url: str, timeout: float, model: str) -> None:
    log("[2/6] GET /v1/models")
    data = request_json("GET", f"{base_url}/v1/models", timeout)
    models = [item.get("id") for item in data.get("data", []) if isinstance(item, dict)]
    check(model in models, f"model {model!r} not found in {models}")


def test_workspaces(base_url: str, timeout: float, workspace: str) -> None:
    log("[3/6] GET /v1/workspaces")
    data = request_json("GET", f"{base_url}/v1/workspaces", timeout)
    workspaces = data.get("workspaces") or {}
    check(isinstance(workspaces, dict), f"workspaces is not a dict: {data}")
    check(workspace in workspaces, f"workspace {workspace!r} not found in {sorted(workspaces)}")


def chat_payload(model: str, workspace: str, prompt: str, stream: bool) -> dict[str, Any]:
    return {
        "model": model,
        "stream": stream,
        "messages": [{"role": "user", "content": f"repo: {workspace}\n{prompt}"}],
    }


def test_chat(base_url: str, timeout: float, model: str, workspace: str) -> None:
    log("[4/6] POST /v1/chat/completions stream=false")
    payload = chat_payload(model, workspace, "Odpovez jednim slovem: ok", False)
    data = request_json("POST", f"{base_url}/v1/chat/completions", timeout, payload)
    content = assistant_text(data)
    check(isinstance(content, str) and content.strip(), f"empty non-stream response: {data}")


def test_codex_local_natural_agent_loop(base_url: str, timeout: float, model: str, workspace: str) -> None:
    log("[5/6] POST /v1/chat/completions natural codex-local review")
    payload = chat_payload(
        model,
        workspace,
        "Prohlédni architekturu gateway/filter/helper vrstvy. Nic needituj. Řekni stručný závěr.",
        False,
    )
    data = request_json("POST", f"{base_url}/v1/chat/completions", timeout, payload)
    content = assistant_text(data)
    check("AGENT_LOOP" in content, f"natural codex-local prompt did not route through agent loop: {content[:500]!r}")
    check("planner_source=" in content, f"agent loop response is missing planner_source: {content[:500]!r}")
    check("workflow=review" in content, f"natural codex-local review did not resolve to review workflow: {content[:500]!r}")


def test_stream(base_url: str, timeout: float, model: str, workspace: str) -> None:
    log("[6/6] POST /v1/chat/completions stream=true")
    payload = chat_payload(model, workspace, "Odpovez tremi kratkymi slovy: stream smoke ok", True)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    chunks = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            check(resp.status < 400, f"stream response returned HTTP {resp.status}")
            while time.monotonic() - started < timeout:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line == "data: [DONE]":
                    break
                if line.startswith("data: "):
                    chunks += 1
                    if chunks >= 1:
                        break
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SmokeError(f"HTTP {exc.code} from stream endpoint: {raw[:500]}") from exc
    except urllib.error.URLError as exc:
        raise SmokeError(f"Stream connection failed: {exc}") from exc
    check(chunks >= 1, "stream endpoint did not return any data chunks")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the local codex gateway.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workspace", default="ai-stack")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help="Check the fast gateway contract only; skip LLM chat and streaming smoke.",
    )
    parser.add_argument("--skip-chat", action="store_true", help="Skip the simple non-stream chat smoke.")
    parser.add_argument(
        "--skip-natural-agent-loop",
        action="store_true",
        help="Skip the natural-language codex-local agent-loop smoke.",
    )
    parser.add_argument("--skip-stream", action="store_true", help="Skip streaming SSE smoke.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    try:
        test_health(base_url, args.timeout)
        test_models(base_url, args.timeout, args.model)
        test_workspaces(base_url, args.timeout, args.workspace)
        if not (args.contract_only or args.skip_chat):
            test_chat(base_url, args.timeout, args.model, args.workspace)
        if not (args.contract_only or args.skip_natural_agent_loop):
            test_codex_local_natural_agent_loop(base_url, args.timeout, args.model, args.workspace)
        if not (args.contract_only or args.skip_stream):
            test_stream(base_url, args.timeout, args.model, args.workspace)
    except SmokeError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    log("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
