#!/usr/bin/env python3
"""Guard the boundary between LLM-first core and legacy keyword helpers.

This smoke test does not claim the old helper scripts are clean. It makes the
boundary explicit: gateway core is guarded by the LLM-first smoke, the
OpenWebUI live filter path must remain thin, and any legacy natural-language
keyword routing has to stay declared in the inventory until it is replaced by
TaskSpec/capability flows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs/codex-local-keyword-routing-inventory.json"

REQUIRED_SURFACES = {
    "codex/gateway/gateway.py": "core_llm_first",
    "codex/bin/openwebui_codex_auto_tools_filter.py": "thin_filter_with_legacy_helpers",
    "codex/bin/mentor_codex_local.py": "legacy_helper_not_core",
    "codex/bin/agent_self_improve.py": "self_improve_recovery_fallback",
}

ALLOWED_CLASSIFICATIONS = set(REQUIRED_SURFACES.values())

SUSPICIOUS_PATTERNS = (
    re.compile(r"\bif\b[^\n]*\bin\s+lower\b"),
    re.compile(r"any\([^\n]*\bin\s+lower"),
    re.compile(r"\bcues?\s*="),
    re.compile(r"_cues\s*="),
    re.compile(r"lower\s*=\s*(?:task|text|prompt|content)\.lower\("),
)


def fail(message: str) -> None:
    raise SystemExit(f"KEYWORD_ROUTING_INVENTORY_FAILED\n{message}")


def load_inventory() -> dict:
    if not INVENTORY_PATH.is_file():
        fail(f"missing inventory file: {INVENTORY_PATH.relative_to(ROOT)}")
    try:
        data = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"inventory is not valid JSON: {type(exc).__name__}: {exc}")
    if not isinstance(data, dict):
        fail("inventory root must be a JSON object")
    return data


def inventory_surfaces(data: dict) -> dict[str, dict]:
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list):
        fail("inventory.surfaces must be a list")
    indexed: dict[str, dict] = {}
    for item in surfaces:
        if not isinstance(item, dict):
            fail("inventory surface entries must be objects")
        path = str(item.get("path") or "").strip()
        classification = str(item.get("classification") or "").strip()
        if not path:
            fail("inventory surface entry is missing path")
        if classification not in ALLOWED_CLASSIFICATIONS:
            fail(f"{path}: unexpected classification {classification!r}")
        if path in indexed:
            fail(f"{path}: duplicate inventory surface")
        indexed[path] = item
    return indexed


def suspicious_lines(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in SUSPICIOUS_PATTERNS:
            if pattern.search(line):
                hits.append((lineno, line.strip()))
                break
    return hits


def assert_required_surfaces(indexed: dict[str, dict]) -> None:
    missing = sorted(set(REQUIRED_SURFACES) - set(indexed))
    if missing:
        fail(f"required routing surfaces missing from inventory: {missing!r}")

    for rel, expected_classification in sorted(REQUIRED_SURFACES.items()):
        item = indexed[rel]
        if item.get("classification") != expected_classification:
            fail(f"{rel}: expected {expected_classification!r}, got {item.get('classification')!r}")
        path = ROOT / rel
        if not path.is_file():
            fail(f"{rel}: inventory path does not exist")


def assert_core_guarded(indexed: dict[str, dict]) -> None:
    core = indexed["codex/gateway/gateway.py"]
    guard = str(core.get("guard") or "")
    if "gateway_llm_first_guard_smoke.py" not in guard:
        fail("gateway core must be guarded by gateway_llm_first_guard_smoke.py")
    forbidden = str(core.get("forbidden") or "").lower()
    if "natural-language business keyword routing" not in forbidden:
        fail("gateway core inventory must explicitly forbid natural-language business keyword routing")


def assert_filter_guarded(indexed: dict[str, dict]) -> None:
    item = indexed["codex/bin/openwebui_codex_auto_tools_filter.py"]
    guard = str(item.get("guard") or "")
    if "openwebui_filter_llm_first_smoke.py" not in guard:
        fail("OpenWebUI filter must be guarded by openwebui_filter_llm_first_smoke.py")
    if "legacy" not in str(item.get("status") or ""):
        fail("OpenWebUI filter inventory must acknowledge remaining legacy helper code")


def assert_legacy_surfaces_declared(indexed: dict[str, dict]) -> None:
    for rel in ("codex/bin/mentor_codex_local.py", "codex/bin/openwebui_codex_auto_tools_filter.py"):
        hits = suspicious_lines(ROOT / rel)
        if not hits:
            fail(f"{rel}: expected declared legacy/helper keyword patterns were not found; update inventory/test scope")
        status = str(indexed[rel].get("status") or "")
        classification = str(indexed[rel].get("classification") or "")
        if "legacy" not in status and "legacy" not in classification:
            fail(f"{rel}: suspicious keyword patterns must be declared as legacy/helper debt")
        print(f"KEYWORD_ROUTING_SURFACE path={rel} classification={classification} suspicious_lines={len(hits)}")


def assert_no_undeclared_core_surface() -> None:
    scanned = {
        "codex/gateway/gateway.py",
        "codex/bin/openwebui_codex_auto_tools_filter.py",
        "codex/bin/mentor_codex_local.py",
        "codex/bin/agent_self_improve.py",
    }
    undeclared = sorted(scanned - set(REQUIRED_SURFACES))
    if undeclared:
        fail(f"internal smoke error: scanned surfaces not declared: {undeclared!r}")


def main() -> int:
    data = load_inventory()
    indexed = inventory_surfaces(data)
    assert_required_surfaces(indexed)
    assert_core_guarded(indexed)
    assert_filter_guarded(indexed)
    assert_legacy_surfaces_declared(indexed)
    assert_no_undeclared_core_surface()
    print("KEYWORD_ROUTING_INVENTORY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
