#!/usr/bin/env python3
"""Compare local ai-stack gateway fingerprint with the running gateway /health payload."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.gateway import gateway
from codex.bin.openwebui_runtime import discover_gateway_base_urls


def request_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw or "{}")


def candidate_base_urls(explicit_base_url: str) -> list[str]:
    candidates = []
    for item in (
        explicit_base_url,
        os.getenv("CODEX_GATEWAY_URL", ""),
        os.getenv("CODEX_GATEWAY_PUBLIC_URL", ""),
    ):
        value = str(item or "").strip().rstrip("/")
        if value:
            candidates.append(value)
    candidates.extend(discover_gateway_base_urls(ROOT))
    result = []
    seen = set()
    for item in candidates:
        value = str(item or "").strip().rstrip("/")
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def local_repo_root() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except Exception:
        return str(ROOT)
    value = (proc.stdout or "").strip()
    return value or str(ROOT)


def local_repo_commit_short() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except Exception:
        return ""
    return (proc.stdout or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check gateway runtime fingerprint against local repo code.")
    parser.add_argument("--base-url", default=os.getenv("CODEX_GATEWAY_URL", "http://127.0.0.1:9101"))
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    local_fingerprint = str(gateway.runtime_fingerprint()).strip()
    local_epoch = str(getattr(gateway, "GATEWAY_SOURCE_EPOCH", "") or "").strip()
    local_root = local_repo_root()
    local_commit = local_repo_commit_short()
    tried = []
    payload = None
    url = ""
    last_error = ""
    for base_url in candidate_base_urls(args.base_url):
        url = base_url.rstrip("/") + "/health"
        try:
            payload = request_json(url, args.timeout)
            tried.append({"base_url": base_url, "ok": True})
            break
        except urllib.error.URLError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            tried.append({"base_url": base_url, "ok": False, "error": last_error})
        except Exception as exc:  # pragma: no cover - defensive parity with runtime helper
            last_error = f"{type(exc).__name__}: {exc}"
            tried.append({"base_url": base_url, "ok": False, "error": last_error})
    if payload is None:
        message = {
            "ok": False,
            "marker": "CODEX_LOCAL_GATEWAY_UNAVAILABLE",
            "recovery": "Zkontroluj gateway /health a pak spusť bash codex/bin/check_ai_stack.sh",
            "url": url,
            "error": last_error or "unknown",
            "tried": tried,
            "local_runtime_fingerprint": local_fingerprint,
            "local_gateway_source_epoch": local_epoch,
        }
        if args.json:
            print(json.dumps(message, ensure_ascii=False, indent=2))
        else:
            print("CODEX_LOCAL_GATEWAY_UNAVAILABLE")
            print(f"url={url}")
            print(f"error={message['error']}")
        return 1

    remote_fingerprint = str(payload.get("runtime_fingerprint") or "").strip()
    remote_epoch = str(payload.get("gateway_source_epoch") or "").strip()
    remote_root = str(payload.get("runtime_repo_root") or "").strip()
    remote_commit = str(payload.get("runtime_commit") or "").strip()
    same_checkout = bool(remote_root) and Path(remote_root).resolve() == Path(local_root).resolve()
    same_commit = bool(local_commit) and bool(remote_commit) and local_commit == remote_commit
    capability_mode = str(payload.get("capability_mode") or "").strip()
    natural_route = str(payload.get("natural_codex_local_route") or "").strip()
    fingerprint_match = bool(remote_fingerprint) and remote_fingerprint == local_fingerprint
    source_epoch_match = bool(remote_epoch) and remote_epoch == local_epoch
    ok = (
        payload.get("ok") is True
        and capability_mode == "agent-first"
        and natural_route == "agent_loop"
        and source_epoch_match
        and bool(remote_fingerprint)
        and (fingerprint_match if same_checkout else (same_commit or fingerprint_match))
    )
    result = {
        "ok": ok,
        "url": url,
        "tried": tried,
        "capability_mode": capability_mode,
        "natural_codex_local_route": natural_route,
        "remote_runtime_fingerprint": remote_fingerprint,
        "local_runtime_fingerprint": local_fingerprint,
        "remote_gateway_source_epoch": remote_epoch,
        "local_gateway_source_epoch": local_epoch,
        "remote_repo_root": remote_root,
        "local_repo_root": local_root,
        "remote_runtime_commit": remote_commit,
        "local_repo_commit": local_commit,
        "same_checkout": same_checkout,
        "same_commit": same_commit,
        "fingerprint_match": fingerprint_match,
        "source_epoch_match": source_epoch_match,
        "gateway_health": payload,
    }
    if not remote_epoch:
        result["marker"] = "CODEX_LOCAL_GATEWAY_SOURCE_EPOCH_MISSING"
        result["recovery"] = "Restartuj ai-stack gateway; /health musí vracet gateway_source_epoch z aktuálního gateway.py."
    elif not source_epoch_match:
        result["marker"] = "CODEX_LOCAL_GATEWAY_SOURCE_EPOCH_DRIFT"
        result["recovery"] = "Běží starý gateway proces nebo jiný checkout. Restartuj stack a ověř gateway_source_epoch."
    elif not remote_fingerprint:
        result["marker"] = "CODEX_LOCAL_RUNTIME_FINGERPRINT_MISSING"
        result["recovery"] = "Nasad a restartuj aktuální ai-stack runtime; /health musí vracet runtime_fingerprint."
    elif same_checkout and not fingerprint_match:
        result["marker"] = "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN"
        result["recovery"] = "Stejný checkout vrací jiný runtime_fingerprint; běží starý proces. Restartuj stack a znovu ověř /health."
    elif same_commit and not fingerprint_match:
        result["marker"] = "CODEX_LOCAL_RUNTIME_FINGERPRINT_WARNING"
        result["recovery"] = (
            "Runtime běží na správném commitu, ale fingerprint helperu se liší. "
            "Ber to jako diagnostické varování; pokud se objeví skutečné chování starého runtime, restartuj stack."
        )
    elif not same_checkout and not same_commit:
        result["marker"] = "CODEX_LOCAL_RUNTIME_CLONE_DRIFT"
        result["recovery"] = "Tento check běží z jiného checkoutu než live runtime a commity se liší. Synchronizuj clone nebo spusť check v runtime checkoutu."
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
        print(f"same_checkout={'true' if same_checkout else 'false'}")
        print(f"same_commit={'true' if same_commit else 'false'}")
        print(f"fingerprint_match={'true' if fingerprint_match else 'false'}")
        print(f"source_epoch_match={'true' if source_epoch_match else 'false'}")
        print(f"remote_repo_root={remote_root or 'missing'}")
        print(f"local_repo_root={local_root}")
        print(f"remote_runtime_commit={remote_commit or 'missing'}")
        print(f"local_repo_commit={local_commit or 'missing'}")
        print(f"remote_gateway_source_epoch={remote_epoch or 'missing'}")
        print(f"local_gateway_source_epoch={local_epoch or 'missing'}")
        print(f"remote_runtime_fingerprint={remote_fingerprint or 'missing'}")
        print(f"local_runtime_fingerprint={local_fingerprint}")
        if not ok:
            print(f"recovery={result.get('recovery', '')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
