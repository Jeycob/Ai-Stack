#!/usr/bin/env python3
"""Offline smoke for workspace container runner boundary diagnostics."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "codex/bin"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = load_module("container_runner_guard_smoke_mod", BIN / "container_runner_guard.py")
run_check = load_module("run_check_container_boundary_smoke_mod", BIN / "run_check.py")
workspace_action = load_module("workspace_action_container_boundary_smoke_mod", BIN / "workspace_action.py")
docker_runner = load_module("docker_runner_container_boundary_smoke_mod", BIN / "docker_runner.py")


def assert_contains(value: str, needle: str, label: str) -> None:
    if needle not in value:
        raise SystemExit(f"{label}: missing {needle!r} in {value!r}")


def assert_guard_markers() -> None:
    with patch.object(
        guard,
        "run_docker",
        return_value=docker_runner.DockerCommandResult(
            ok=False,
            command=["docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
            output="docker",
            returncode=None,
            error="docker_unavailable",
        ),
    ):
        result = guard.inspect_container_state("Test2")
    if result.get("marker") != "WORKSPACE_CONTAINER_DOCKER_UNAVAILABLE":
        raise SystemExit(f"unexpected docker-unavailable marker: {result!r}")

    denied_proc = subprocess.CompletedProcess(
        ["docker", "inspect"],
        1,
        stdout="permission denied while trying to connect to the Docker API at unix:///var/run/docker.sock\n",
    )
    denied_sudo_proc = subprocess.CompletedProcess(
        ["sudo", "-n", "docker", "inspect"],
        1,
        stdout="sudo: a password is required\n",
    )
    with patch.object(
        guard,
        "run_docker",
        return_value=docker_runner.DockerCommandResult(
            ok=False,
            command=["sudo", "-n", "docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
            output=denied_proc.stdout,
            returncode=1,
            used_sudo=True,
            error="docker_permission_denied_sudo_password_required",
            attempts=[
                docker_runner.DockerCommandAttempt(
                    command=["docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
                    returncode=denied_proc.returncode,
                    output=denied_proc.stdout,
                ),
                docker_runner.DockerCommandAttempt(
                    command=["sudo", "-n", "docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
                    returncode=denied_sudo_proc.returncode,
                    output=denied_sudo_proc.stdout,
                ),
            ],
        ),
    ):
        result = guard.inspect_container_state("Test2")
    if result.get("marker") != "WORKSPACE_CONTAINER_DOCKER_PERMISSION_DENIED":
        raise SystemExit(f"unexpected docker-permission marker: {result!r}")

    missing_proc = docker_runner.DockerCommandResult(
        ok=False,
        command=["docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
        output="Error: No such object: codex-opencode-Test2\n",
        returncode=1,
        attempts=[
            docker_runner.DockerCommandAttempt(
                command=["docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
                returncode=1,
                output="Error: No such object: codex-opencode-Test2\n",
            )
        ],
    )
    with patch.object(guard, "run_docker", return_value=missing_proc):
        result = guard.inspect_container_state("Test2")
    if result.get("marker") != "WORKSPACE_CONTAINER_MISSING":
        raise SystemExit(f"unexpected container-missing marker: {result!r}")

    stopped_proc = docker_runner.DockerCommandResult(
        ok=True,
        command=["docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
        output="false\n",
        returncode=0,
        attempts=[
            docker_runner.DockerCommandAttempt(
                command=["docker", "inspect", "--format", "{{.State.Running}}", "codex-opencode-Test2"],
                returncode=0,
                output="false\n",
            )
        ],
    )
    with patch.object(guard, "run_docker", return_value=stopped_proc):
        result = guard.inspect_container_state("Test2")
    if result.get("marker") != "WORKSPACE_CONTAINER_NOT_RUNNING":
        raise SystemExit(f"unexpected container-not-running marker: {result!r}")
    print("CONTAINER_RUNNER_GUARD_MARKERS_OK")


def assert_run_check_boundary_result() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="run-check-boundary-"))
    result = run_check.run_checked_command(
        "Test2",
        tmpdir,
        ["pwd"],
        30,
        {},
        "container",
    )
    if result.get("marker") != "WORKSPACE_CONTAINER_MISSING":
        raise SystemExit(f"run_check should surface container-missing marker, got {result!r}")
    assert_contains(str(result.get("recovery") or ""), "neexistuje", "run-check-recovery")
    print("RUN_CHECK_CONTAINER_BOUNDARY_OK")


def assert_run_check_exec_permission_result() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="run-check-exec-permission-"))
    with patch.object(
        run_check,
        "inspect_container_state",
        return_value={"ok": True, "workspace": "Test2", "container": "codex-opencode-Test2"},
    ), patch.object(
        run_check,
        "run_docker",
        return_value=docker_runner.DockerCommandResult(
            ok=False,
            command=["sudo", "-n", "docker", "exec", "--workdir", "/workspace", "codex-opencode-Test2", "pwd"],
            output="sudo: a password is required\n",
            returncode=1,
            used_sudo=True,
            error="docker_permission_denied_sudo_password_required",
        ),
    ):
        result = run_check.run_checked_command("Test2", tmpdir, ["pwd"], 30, {}, "container")
    if result.get("marker") != "WORKSPACE_CONTAINER_DOCKER_PERMISSION_DENIED":
        raise SystemExit(f"run_check should surface docker exec permission marker, got {result!r}")
    assert_contains(str(result.get("recovery") or ""), "Docker socket", "run-check-exec-permission-recovery")
    print("RUN_CHECK_EXEC_PERMISSION_BOUNDARY_OK")


def assert_workspace_action_smoke_boundary() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="workspace-action-boundary-"))
    with patch.object(workspace_action, "resolve_smoke", return_value=(["npm", "run", "dev"], "node-smoke-script")), patch.object(
        workspace_action,
        "inspect_container_state",
        return_value={
            "ok": False,
            "marker": "WORKSPACE_CONTAINER_NOT_RUNNING",
            "error": "container_not_running",
            "recovery": "Workspace kontejner codex-opencode-Test2 neběží.",
            "diagnostic_output": "false\n",
        },
    ):
        result = workspace_action.run_smoke(tmpdir, 30, {}, False, "container", "Test2")
    if result.get("marker") != "WORKSPACE_CONTAINER_NOT_RUNNING":
        raise SystemExit(f"workspace_action.run_smoke should surface not-running marker, got {result!r}")
    assert_contains(str(result.get("recovery") or ""), "neběží", "workspace-action-recovery")
    print("WORKSPACE_ACTION_CONTAINER_BOUNDARY_OK")


def assert_workspace_action_exec_permission_boundary() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="workspace-action-exec-permission-"))
    with patch.object(workspace_action, "resolve_smoke", return_value=(["npm", "run", "dev"], "node-smoke-script")), patch.object(
        workspace_action,
        "inspect_container_state",
        return_value={"ok": True, "workspace": "Test2", "container": "codex-opencode-Test2"},
    ), patch.object(
        workspace_action,
        "run_docker",
        return_value=docker_runner.DockerCommandResult(
            ok=False,
            command=["sudo", "-n", "docker", "exec", "--workdir", "/workspace", "codex-opencode-Test2", "npm", "run", "dev"],
            output="sudo: a password is required\n",
            returncode=1,
            used_sudo=True,
            error="docker_permission_denied_sudo_password_required",
        ),
    ):
        result = workspace_action.run_smoke(tmpdir, 30, {}, False, "container", "Test2")
    if result.get("marker") != "WORKSPACE_CONTAINER_DOCKER_PERMISSION_DENIED":
        raise SystemExit(f"workspace_action.run_smoke should surface docker exec permission marker, got {result!r}")
    assert_contains(str(result.get("recovery") or ""), "Docker socket", "workspace-action-exec-permission-recovery")
    print("WORKSPACE_ACTION_EXEC_PERMISSION_BOUNDARY_OK")


def main() -> int:
    assert_guard_markers()
    with patch.object(
        run_check,
        "inspect_container_state",
        return_value={
            "ok": False,
            "marker": "WORKSPACE_CONTAINER_MISSING",
            "error": "container_missing",
            "recovery": "Workspace kontejner codex-opencode-Test2 neexistuje.",
            "diagnostic_output": "missing\n",
        },
    ):
        assert_run_check_boundary_result()
    assert_run_check_exec_permission_result()
    assert_workspace_action_smoke_boundary()
    assert_workspace_action_exec_permission_boundary()
    print("CONTAINER_RUNNER_GUARD_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
