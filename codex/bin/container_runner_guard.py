#!/usr/bin/env python3
"""Shared container runner preflight checks for codex-local workspace execution."""

from __future__ import annotations

import subprocess
import time


def container_name(workspace: str) -> str:
    return f"codex-opencode-{workspace}"


def inspect_container_state(workspace: str) -> dict[str, object]:
    name = container_name(workspace)
    started = time.time()
    cmd = ["docker", "inspect", "--format", "{{.State.Running}}", name]
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_DOCKER_UNAVAILABLE",
            "error": "docker_unavailable",
            "recovery": "Docker CLI na hostu není dostupné. Nejprve zprovozni Docker a pak znovu spusť start/deploy stacku.",
            "diagnostic_command": cmd,
            "diagnostic_output": str(exc),
            "duration_ms": int((time.time() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_INSPECT_TIMEOUT",
            "error": "container_inspect_timeout",
            "recovery": "Kontrola workspace kontejneru timeoutovala. Ověř Docker daemon a stav kontejneru, potom akci zopakuj.",
            "diagnostic_command": cmd,
            "diagnostic_output": output,
            "duration_ms": int((time.time() - started) * 1000),
        }

    output = (proc.stdout or "").strip()
    lower = output.lower()
    if proc.returncode != 0:
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_MISSING",
            "error": "container_missing",
            "recovery": (
                f"Workspace kontejner {name} neexistuje. "
                "Spusť start/deploy stacku, aby se workspace zaregistroval a kontejner vytvořil."
            ),
            "diagnostic_command": cmd,
            "diagnostic_output": proc.stdout,
            "diagnostic_exit_code": proc.returncode,
            "duration_ms": int((time.time() - started) * 1000),
        }
    if lower != "true":
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_NOT_RUNNING",
            "error": "container_not_running",
            "recovery": (
                f"Workspace kontejner {name} neběží. "
                "Spusť start/deploy stacku nebo explicitní restart workspace kontejneru a potom akci zopakuj."
            ),
            "diagnostic_command": cmd,
            "diagnostic_output": proc.stdout,
            "diagnostic_exit_code": proc.returncode,
            "duration_ms": int((time.time() - started) * 1000),
        }
    return {
        "ok": True,
        "workspace": workspace,
        "container": name,
        "duration_ms": int((time.time() - started) * 1000),
    }

