#!/usr/bin/env python3
"""Shared Docker command helper for codex-local executor paths."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


PERMISSION_DENIED_SNIPPETS = (
    "permission denied while trying to connect to the docker api",
    "got permission denied while trying to connect to the docker daemon socket",
    "cannot connect to the docker daemon at unix:///var/run/docker.sock",
    "dial unix /var/run/docker.sock: connect: permission denied",
)

SUDO_PASSWORD_SNIPPETS = (
    "sudo: a password is required",
    "sudo: sorry, you must have a tty to run sudo",
    "sudo: a terminal is required",
)

CONTAINER_MISSING_SNIPPETS = (
    "no such object",
    "no such container",
    "error: no such object",
)


@dataclass
class DockerCommandAttempt:
    command: list[str]
    returncode: int | None
    output: str
    error: str | None = None


@dataclass
class DockerCommandResult:
    ok: bool
    command: list[str]
    output: str
    returncode: int | None
    used_sudo: bool = False
    error: str | None = None
    attempts: list[DockerCommandAttempt] = field(default_factory=list)


def docker_cli_available() -> bool:
    return shutil.which("docker") is not None


def permission_denied(output: str) -> bool:
    lower = (output or "").lower()
    return any(snippet in lower for snippet in PERMISSION_DENIED_SNIPPETS)


def sudo_password_required(output: str) -> bool:
    lower = (output or "").lower()
    return any(snippet in lower for snippet in SUDO_PASSWORD_SNIPPETS)


def container_missing(output: str) -> bool:
    lower = (output or "").lower()
    return any(snippet in lower for snippet in CONTAINER_MISSING_SNIPPETS)


def sudo_available() -> bool:
    return shutil.which("sudo") is not None


def run_docker(
    docker_args: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    allow_sudo_fallback: bool = True,
) -> DockerCommandResult:
    attempts: list[DockerCommandAttempt] = []
    first_cmd = ["docker", *docker_args]

    try:
        proc = subprocess.run(
            first_cmd,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        attempts.append(DockerCommandAttempt(command=first_cmd, returncode=None, output=str(exc), error="docker_unavailable"))
        return DockerCommandResult(
            ok=False,
            command=first_cmd,
            output=str(exc),
            returncode=None,
            error="docker_unavailable",
            attempts=attempts,
        )

    output = proc.stdout or ""
    attempts.append(DockerCommandAttempt(command=first_cmd, returncode=proc.returncode, output=output))
    if proc.returncode == 0 or not allow_sudo_fallback or not permission_denied(output) or not sudo_available():
        return DockerCommandResult(
            ok=proc.returncode == 0,
            command=first_cmd,
            output=output,
            returncode=proc.returncode,
            error="docker_permission_denied" if permission_denied(output) else None,
            attempts=attempts,
        )

    sudo_cmd = ["sudo", "-n", "docker", *docker_args]
    try:
        sudo_proc = subprocess.run(
            sudo_cmd,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        attempts.append(DockerCommandAttempt(command=sudo_cmd, returncode=None, output=str(exc), error="sudo_unavailable"))
        return DockerCommandResult(
            ok=False,
            command=first_cmd,
            output=output,
            returncode=proc.returncode,
            error="docker_permission_denied",
            attempts=attempts,
        )

    sudo_output = sudo_proc.stdout or ""
    attempts.append(DockerCommandAttempt(command=sudo_cmd, returncode=sudo_proc.returncode, output=sudo_output))
    if sudo_proc.returncode == 0:
        return DockerCommandResult(
            ok=True,
            command=sudo_cmd,
            output=sudo_output,
            returncode=0,
            used_sudo=True,
            attempts=attempts,
        )

    error = "docker_permission_denied"
    if sudo_password_required(sudo_output):
        error = "docker_permission_denied_sudo_password_required"
    return DockerCommandResult(
        ok=False,
        command=sudo_cmd,
        output=sudo_output,
        returncode=sudo_proc.returncode,
        used_sudo=True,
        error=error,
        attempts=attempts,
    )
