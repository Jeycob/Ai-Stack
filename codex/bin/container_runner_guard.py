#!/usr/bin/env python3
"""Shared container runner preflight checks for codex-local workspace execution."""

from __future__ import annotations

import subprocess
import time

from docker_runner import container_missing, permission_denied, run_docker


def container_name(workspace: str) -> str:
    return f"codex-opencode-{workspace}"


def inspect_container_state(workspace: str) -> dict[str, object]:
    name = container_name(workspace)
    started = time.time()
    docker_args = ["inspect", "--format", "{{.State.Running}}", name]
    try:
        result = run_docker(docker_args, timeout=15)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_INSPECT_TIMEOUT",
            "error": "container_inspect_timeout",
            "recovery": "Kontrola workspace kontejneru timeoutovala. Ověř Docker daemon a stav kontejneru, potom akci zopakuj.",
            "diagnostic_command": ["docker", *docker_args],
            "diagnostic_output": output,
            "duration_ms": int((time.time() - started) * 1000),
        }

    attempts = [
        {"command": attempt.command, "returncode": attempt.returncode, "output": attempt.output, "error": attempt.error}
        for attempt in result.attempts
    ]
    if result.error == "docker_unavailable":
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_DOCKER_UNAVAILABLE",
            "error": "docker_unavailable",
            "recovery": "Docker CLI na hostu není dostupné. Nejprve zprovozni Docker a pak znovu spusť start/deploy stacku.",
            "diagnostic_command": result.command,
            "diagnostic_output": result.output,
            "diagnostic_attempts": attempts,
            "duration_ms": int((time.time() - started) * 1000),
        }

    output = (result.output or "").strip()
    lower = output.lower()
    if result.returncode != 0 and permission_denied(output):
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_DOCKER_PERMISSION_DENIED",
            "error": "docker_permission_denied",
            "recovery": (
                "Gateway runner nemá přístup k Docker socketu. "
                "Přidej gateway user do docker group nebo povol passwordless sudo pro docker a znovu spusť start/deploy stacku."
            ),
            "diagnostic_command": result.command,
            "diagnostic_output": result.output,
            "diagnostic_attempts": attempts,
            "diagnostic_exit_code": result.returncode,
            "duration_ms": int((time.time() - started) * 1000),
        }
    if result.returncode != 0 and container_missing(output):
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
            "diagnostic_command": result.command,
            "diagnostic_output": result.output,
            "diagnostic_attempts": attempts,
            "diagnostic_exit_code": result.returncode,
            "duration_ms": int((time.time() - started) * 1000),
        }
    if result.returncode != 0:
        return {
            "ok": False,
            "workspace": workspace,
            "container": name,
            "marker": "WORKSPACE_CONTAINER_INSPECT_FAILED",
            "error": "container_inspect_failed",
            "recovery": (
                f"Nepodařilo se ověřit stav workspace kontejneru {name}. "
                "Zkontroluj Docker daemon, logy gateway a stav kontejneru, potom akci zopakuj."
            ),
            "diagnostic_command": result.command,
            "diagnostic_output": result.output,
            "diagnostic_attempts": attempts,
            "diagnostic_exit_code": result.returncode,
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
            "diagnostic_command": result.command,
            "diagnostic_output": result.output,
            "diagnostic_attempts": attempts,
            "diagnostic_exit_code": result.returncode,
            "duration_ms": int((time.time() - started) * 1000),
        }
    return {
        "ok": True,
        "workspace": workspace,
        "container": name,
        "diagnostic_command": result.command,
        "diagnostic_attempts": attempts,
        "used_sudo": result.used_sudo,
        "duration_ms": int((time.time() - started) * 1000),
    }
