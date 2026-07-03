#!/usr/bin/env python3
"""Compare local ai-stack gateway fingerprint with the running gateway /health payload."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.gateway import gateway


def request_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw or "{}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check gateway runtime fingerprint against local repo code.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9101")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    url = args.base_url.rstrip("/") + "/health"
    local_fingerprint = str(gateway.runtime_fingerprint()).strip()
    try:
        payload = request_json(url, args.timeout)
    except urllib.error.URLError as exc:
        message = {
            "ok": False,
            "marker": "CODEX_LOCAL_GATEWAY_UNAVAILABLE",
            "recovery": "Zkontroluj gateway /health a pak spusť bash codex/bin/check_ai_stack.sh",
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "local_runtime_fingerprint": local_fingerprint,
        }
        if args.json:
            print(json.dumps(message, ensure_ascii=False, indent=2))
        else:
            print("CODEX_LOCAL_GATEWAY_UNAVAILABLE")
            print(f"url={url}")
            print(f"error={message['error']}")
        return 1

    remote_fingerprint = str(payload.get("runtime_fingerprint") or "").strip()
    capability_mode = str(payload.get("capability_mode") or "").strip()
    natural_route = str(payload.get("natural_codex_local_route") or "").strip()
    ok = (
        payload.get("ok") is True
        and capability_mode == "agent-first"
        and natural_route == "agent_loop"
        and bool(remote_fingerprint)
        and remote_fingerprint == local_fingerprint
    )
    result = {
        "ok": ok,
        "url": url,
        "capability_mode": capability_mode,
        "natural_codex_local_route": natural_route,
        "remote_runtime_fingerprint": remote_fingerprint,
        "local_runtime_fingerprint": local_fingerprint,
        "gateway_health": payload,
    }
    if not remote_fingerprint:
        result["marker"] = "CODEX_LOCAL_RUNTIME_FINGERPRINT_MISSING"
        result["recovery"] = "Nasad a restartuj aktuální ai-stack runtime; /health musí vracet runtime_fingerprint."
    elif remote_fingerprint != local_fingerprint:
        result["marker"] = "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN"
        result["recovery"] = "Běží starý runtime proti novějšímu repu. Restartuj stack a znovu ověř /health."
    elif capability_mode != "agent-first" or natural_route != "agent_loop":
        result["marker"] = "CODEX_LOCAL_AGENT_ROUTE_DEGRADED"
        result["recovery"] = "Runtime nehlásí agent-first route; restartuj gateway a zkontroluj nasazenou verzi."
    else:
        result["marker"] = "CODEX_LOCAL_RUNTIME_MATCH_OK"

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["marker"])
        print(f"capability_mode={capability_mode}")
        print(f"natural_codex_local_route={natural_route}")
        print(f"remote_runtime_fingerprint={remote_fingerprint or 'missing'}")
        print(f"local_runtime_fingerprint={local_fingerprint}")
        if not ok:
            print(f"recovery={result.get('recovery', '')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
