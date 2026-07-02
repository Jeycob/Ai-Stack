"""
title: Codex Gateway Admin Filter
author: OpenAI Codex
version: 0.1.0
description: Applies explicitly marked, whitelisted ai-stack gateway patches from Open WebUI conversations.
"""

from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional
import os
import py_compile
import re
import shutil
import subprocess
import threading
import time


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=-5,
            description="Run after tool auto-selection and before/after model calls.",
        )
        repo_root: str = Field(
            default="auto",
            description="ai-stack path inside the Open WebUI container, or auto.",
        )
        candidate_roots: str = Field(
            default="/data/repositories/ai-stack,/app/backend/data/repositories/ai-stack,/Repositories/ai-stack,/mnt/c/Repositories/ai-stack",
            description="Comma-separated fallback ai-stack paths.",
        )
        marker: str = Field(
            default="GATEWAY_ADMIN_APPLY",
            description="Only patches following this marker are applied.",
        )
        inject_instructions: bool = Field(
            default=True,
            description="Teach build model how to request gateway patch application.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.allowed_exact = {
            ".gitignore",
            "README.md",
            "codex/gateway/gateway.py",
            "codex/bin/start_codex_stack.sh",
            "codex/bin/watch_gateway.sh",
            "codex/bin/add_workspace.py",
            "codex/workspaces.json",
            "codex/opencode-default.json",
            "start_docker.bat",
            "docker-compose.yml",
        }

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        if self.valves.inject_instructions and self._is_ai_stack_request(body):
            body.setdefault("messages", []).insert(0, {"role": "system", "content": self._instructions()})

        latest_user = self._last_message_text(body, "user")
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_APPLY_NOW"):
            normalized = re.sub(
                r"(?im)^(\s*)GATEWAY_ADMIN_APPLY_NOW(\s*)$",
                rf"\1{self.valves.marker}\2",
                latest_user,
                count=1,
            )
            if not self._extract_patches(normalized):
                raise RuntimeError("NO_PATCH_FOUND: GATEWAY_ADMIN_APPLY_NOW requires a fenced unified diff block.")
            result = self._apply_from_text(normalized)
            return self._direct_response(body, result)

        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_DIAG"):
            return self._direct_response(body, self._diag())
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_PROBE"):
            return self._direct_response(body, self._probe_paths())
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_READ"):
            return self._direct_response(body, self._read_requested_file(latest_user))
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_SSH_KEYGEN"):
            return self._direct_response(body, self._ssh_keygen(latest_user))
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_INSTALL_SSH_CLIENT"):
            return self._direct_response(body, self._install_ssh_client())
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_GIT_STATUS"):
            return self._direct_response(body, self._git_status())
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_GIT_UNTRACK_IGNORED"):
            return self._direct_response(body, self._git_untrack_ignored())
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_GIT_PUSH"):
            return self._direct_response(body, self._git_push(latest_user))
        if self._admin_command_requested(latest_user, "GATEWAY_ADMIN_APPLY_NOW"):
            normalized = re.sub(
                r"(?im)^(\s*)GATEWAY_ADMIN_APPLY_NOW(\s*)$",
                rf"\1{self.valves.marker}\2",
                latest_user,
                count=1,
            )
            if not self._extract_patches(normalized):
                raise RuntimeError("NO_PATCH_FOUND: GATEWAY_ADMIN_APPLY_NOW requires a fenced unified diff block.")
            result = self._apply_from_text(normalized)
            body.setdefault("messages", []).append(
                {"role": "system", "content": "Gateway admin filter result:\n" + result}
            )
            return body
        if self._admin_command_requested(latest_user, self.valves.marker):
            if not self._extract_patches(latest_user):
                body["stream"] = False
                self._replace_last_user_text(
                    body,
                    latest_user,
                    latest_user.replace(
                        self.valves.marker,
                        "[Gateway admin patch requested, but no diff was provided.]",
                    ),
                )
                body.setdefault("messages", []).append(
                    {
                        "role": "system",
                        "content": (
                            "The user requested a gateway admin patch but did not provide a diff. "
                            "Do not say it was applied. If the requested edit is safe, reply with "
                            "GATEWAY_ADMIN_APPLY followed by one fenced unified diff block for a "
                            "whitelisted ai-stack file."
                        ),
                    }
                )
                return body
            result = self._schedule_apply(latest_user)
            body.setdefault("messages", []).append(
                {
                    "role": "system",
                    "content": (
                        "Gateway admin filter result:\n"
                        + result
                        + "\nReply exactly: GATEWAY_PATCH_SCHEDULED"
                    ),
                }
            )
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body

    def _instructions(self) -> str:
        return (
            "You may propose safe edits as a normal unified diff when asked, but do not include "
            "internal GATEWAY_ADMIN_* markers in ordinary assistant replies. The Open WebUI "
            "Gateway Admin Filter applies patches only from explicit user-side technical payloads. "
            "Do not claim files were edited unless an admin filter result is shown."
        )

    def _is_ai_stack_request(self, body: dict) -> bool:
        model = str(body.get("model") or "")
        text = "\n".join(self._message_texts(body)[-8:]).lower()
        return "codex-local-" in model and "repo: ai-stack" in text

    def _message_texts(self, body: dict) -> list[str]:
        out = []
        for msg in body.get("messages") or []:
            content = msg.get("content", "")
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                out.append("\n".join(parts))
        return out

    def _last_message_text(self, body: dict, role: str) -> str:
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") == role:
                content = msg.get("content", "")
                return content if isinstance(content, str) else str(content)
        return ""

    def _assistant_text(self, body: dict) -> str:
        if isinstance(body.get("choices"), list) and body["choices"]:
            msg = body["choices"][0].get("message") or {}
            content = msg.get("content")
            return content if isinstance(content, str) else ""
        return self._last_message_text(body, "assistant")

    def _append_assistant_text(self, body: dict, suffix: str) -> None:
        if isinstance(body.get("choices"), list) and body["choices"]:
            msg = body["choices"][0].setdefault("message", {})
            msg["content"] = (msg.get("content") or "") + suffix
            return
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") == "assistant":
                msg["content"] = (msg.get("content") or "") + suffix
                return

    def _replace_last_user_text(self, body: dict, old: str, new: str) -> None:
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str) and content == old:
                msg["content"] = new
            return

    def _admin_command_requested(self, text: str, command: str) -> bool:
        return re.search(rf"(?im)^\s*{re.escape(command)}(?:\s|$)", text) is not None

    def _direct_response(self, body: dict, text: str) -> dict:
        body["stream"] = False
        body["messages"] = [{"role": "user", "content": "GATEWAY_ADMIN_DIRECT_RESPONSE\n" + text}]
        return body

    def _apply_from_text(self, text: str) -> str:
        patches = self._extract_patches(text)
        if not patches:
            return "NO_PATCH_FOUND: marker was present but no fenced diff block was found."

        results = []
        changed = []
        for patch in patches:
            files = self._parse_unified_diff(patch)
            if not files:
                return "NO_PATCH_FILES: unified diff did not contain file hunks."
            for rel, hunks in files:
                changed.append(rel)
                results.append(self._apply_file(rel, hunks))

        for rel in sorted(set(changed)):
            if rel.endswith(".py"):
                py_compile.compile(str(self._target(rel)), doraise=True)

        return "PATCH_APPLIED\n" + "\n".join(results)

    def _schedule_apply(self, text: str) -> str:
        delay = 30.0

        def worker():
            time.sleep(delay)
            try:
                result = self._apply_from_text(text)
            except Exception as exc:
                result = f"PATCH_FAILED {type(exc).__name__}: {exc}"
            self._admin_log(result)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return f"PATCH_SCHEDULED delay_seconds={int(delay)} log=codex/audit/openwebui-gateway-admin.log"

    def _admin_log(self, text: str) -> None:
        try:
            root = self._repo_root()
            log = root / "codex/audit/openwebui-gateway-admin.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] "))
                f.write(text.replace("\n", " | "))
                f.write("\n")
        except Exception:
            pass

    def _extract_patches(self, text: str) -> list[str]:
        marker_pos = text.find(self.valves.marker)
        if marker_pos < 0:
            return []
        tail = text[marker_pos + len(self.valves.marker):]
        blocks = re.findall(r"```(?:diff|patch)?\s*\n(.*?)```", tail, flags=re.DOTALL | re.IGNORECASE)
        if blocks:
            return [b.strip("\n") + "\n" for b in blocks if b.strip()]
        tail = tail.strip()
        return [tail + "\n"] if tail.startswith(("diff --git ", "--- ")) else []

    def _parse_unified_diff(self, patch: str):
        lines = patch.splitlines(keepends=True)
        files = []
        i = 0
        while i < len(lines):
            if lines[i].startswith("diff --git "):
                i += 1
                continue
            if not lines[i].startswith("--- "):
                i += 1
                continue
            old_path = lines[i][4:].strip().split("\t", 1)[0]
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                raise ValueError("Malformed patch: expected +++ after ---")
            new_path = lines[i][4:].strip().split("\t", 1)[0]
            rel = self._clean_patch_path(new_path if new_path != "/dev/null" else old_path)
            self._assert_allowed(rel)
            i += 1
            hunks = []
            while i < len(lines):
                if lines[i].startswith(("diff --git ", "--- ")):
                    break
                if not lines[i].startswith("@@ "):
                    i += 1
                    continue
                header = lines[i]
                i += 1
                hunk_lines = []
                while i < len(lines) and not lines[i].startswith(("@@ ", "diff --git ", "--- ")):
                    if not lines[i].startswith("\\ No newline"):
                        hunk_lines.append(lines[i])
                    i += 1
                hunks.append((header, hunk_lines))
            files.append((rel, hunks))
        return files

    def _apply_file(self, rel: str, hunks) -> str:
        target = self._target(rel)
        existed = target.exists()
        original = target.read_text(encoding="utf-8").splitlines(keepends=True) if existed else []
        output = []
        pos = 0
        for header, hunk_lines in hunks:
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
            if not m:
                raise ValueError(f"Malformed hunk header for {rel}: {header.strip()}")
            old_start = max(0, int(m.group(1)) - 1)
            if old_start < pos:
                raise ValueError(f"Overlapping hunks in {rel}")
            output.extend(original[pos:old_start])
            cur = old_start
            for line in hunk_lines:
                if not line:
                    continue
                tag = line[0]
                value = line[1:]
                if tag == " ":
                    if cur >= len(original) or not self._same_line(original[cur], value):
                        raise ValueError(f"Context mismatch in {rel} near line {cur + 1}")
                    output.append(original[cur])
                    cur += 1
                elif tag == "-":
                    if cur >= len(original) or not self._same_line(original[cur], value):
                        raise ValueError(f"Delete mismatch in {rel} near line {cur + 1}")
                    cur += 1
                elif tag == "+":
                    output.append(value)
                else:
                    # OpenWebUI/chat copy-paste sometimes strips the leading
                    # space from unified-diff context lines. Treat such bare
                    # lines as context, but still require an exact match.
                    value = line
                    if cur >= len(original) or not self._same_line(original[cur], value):
                        raise ValueError(f"Context mismatch in {rel} near line {cur + 1}")
                    output.append(original[cur])
                    cur += 1
            pos = cur
        output.extend(original[pos:])
        stamp = time.strftime("%Y%m%d%H%M%S")
        target.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            shutil.copy2(target, target.with_name(target.name + ".bak-" + stamp))
        target.write_text("".join(output), encoding="utf-8")
        action = "WROTE" if existed else "CREATED"
        return f"{action} {rel} hunks={len(hunks)}"

    def _same_line(self, left: str, right: str) -> bool:
        return left.rstrip("\r\n") == right.rstrip("\r\n")

    def _clean_patch_path(self, path: str) -> str:
        path = path.strip().strip('"')
        if path.startswith(("a/", "b/")):
            path = path[2:]
        path = path.lstrip("/")
        if not path or ".." in Path(path).parts:
            raise ValueError(f"Unsafe patch path: {path!r}")
        return path

    def _assert_allowed(self, rel: str) -> None:
        if rel in self.allowed_exact:
            return
        if rel.startswith("docs/") and rel.endswith(".md"):
            return
        if rel.startswith("codex/bin/") and rel.endswith((".py", ".sh")):
            return
        raise PermissionError(f"Path is not whitelisted for gateway admin edits: {rel}")

    def _read_requested_file(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_READ\s+(.+?)\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_READ <whitelisted-relative-path>")
        rel = self._clean_patch_path(m.group(1))
        self._assert_allowed(rel)
        target = self._target(rel)
        if not target.is_file():
            raise FileNotFoundError(f"Whitelisted file does not exist: {rel}")
        data = target.read_text(encoding="utf-8", errors="replace")
        max_chars = 30000
        suffix = "" if len(data) <= max_chars else f"\n\n[truncated at {max_chars} chars]"
        return f"FILE {rel}\n--- BEGIN ---\n{data[:max_chars]}\n--- END ---{suffix}"

    def _ssh_keygen(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_SSH_KEYGEN(?:\s+([A-Za-z0-9_.-]+))?(?:\s+(.+?))?\s*$", text)
        if not m:
            raise ValueError("Usage: GATEWAY_ADMIN_SSH_KEYGEN [safe-name] [comment]")

        name = m.group(1) or "github-ai-stack"
        comment = (m.group(2) or f"{name}@ai-stack-local").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError("SSH key name must match [A-Za-z0-9_.-]{1,64}")
        if "\n" in comment or len(comment) > 160:
            raise ValueError("SSH key comment must be a single line up to 160 chars")

        root = self._repo_root()
        key_dir = root / "codex/state/ssh"
        key_path = key_dir / f"{name}_ed25519"
        pub_path = Path(str(key_path) + ".pub")
        key_rel = key_path.relative_to(root).as_posix()
        pub_rel = pub_path.relative_to(root).as_posix()

        key_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(key_dir, 0o700)
        except OSError:
            pass

        ssh_keygen = shutil.which("ssh-keygen")
        if not ssh_keygen:
            try:
                status = self._ssh_keygen_with_cryptography(key_path, pub_path, comment)
            except ImportError:
                return (
                    "SSH_KEYGEN_MISSING\n"
                    "Neither ssh-keygen nor Python cryptography is available in the environment that runs OpenWebUI.\n"
                    "Install openssh-client in that container/environment, or generate the key manually in WSL:\n"
                    f"mkdir -p {key_dir}\n"
                    f"ssh-keygen -t ed25519 -N '' -C '{comment}' -f '{key_path}'\n"
                    f"chmod 700 '{key_dir}' && chmod 600 '{key_path}' && chmod 644 '{pub_path}'"
                )
        else:
            if key_path.exists():
                if not pub_path.exists():
                    proc = subprocess.run(
                        [ssh_keygen, "-y", "-f", str(key_path)],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        timeout=20,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError("Existing private key found, but public key could not be derived:\n" + proc.stdout)
                    pub_path.write_text(proc.stdout.strip() + "\n", encoding="utf-8")
                status = "SSH_KEY_EXISTS"
            else:
                if pub_path.exists():
                    raise FileExistsError(f"Public key already exists without private key: {pub_rel}")
                proc = subprocess.run(
                    [ssh_keygen, "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key_path)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                )
                if proc.returncode != 0:
                    raise RuntimeError("ssh-keygen failed:\n" + proc.stdout)
                status = "SSH_KEY_READY"

        for path, mode in [(key_path, 0o600), (pub_path, 0o644)]:
            try:
                os.chmod(path, mode)
            except OSError:
                pass

        ignore_status = self._git_ignore_status(root, key_rel)
        public_key = pub_path.read_text(encoding="utf-8").strip()
        private_mode = oct(key_path.stat().st_mode & 0o777)
        public_mode = oct(pub_path.stat().st_mode & 0o777)
        permission_status = "strict" if (key_path.stat().st_mode & 0o077) == 0 else "WARNING_private_key_permissions_are_too_open_on_this_mount"
        return (
            f"{status}\n"
            f"private_key_path={key_rel}\n"
            f"public_key_path={pub_rel}\n"
            f"private_key_mode={private_mode}\n"
            f"public_key_mode={public_mode}\n"
            f"permission_status={permission_status}\n"
            f"git_ignore_status={ignore_status}\n"
            "private_key_value=NOT_PRINTED\n"
            "public_key_value:\n"
            f"{public_key}"
        )

    def _ssh_keygen_with_cryptography(self, key_path: Path, pub_path: Path, comment: str) -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        if key_path.exists():
            if not pub_path.exists():
                private_key = serialization.load_ssh_private_key(key_path.read_bytes(), password=None)
                public_key = private_key.public_key()
                public_text = public_key.public_bytes(
                    serialization.Encoding.OpenSSH,
                    serialization.PublicFormat.OpenSSH,
                ).decode("utf-8")
                pub_path.write_text(f"{public_text} {comment}\n", encoding="utf-8")
            return "SSH_KEY_EXISTS"

        if pub_path.exists():
            raise FileExistsError(f"Public key already exists without private key: {pub_path}")

        private_key = ed25519.Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
        key_path.write_bytes(private_bytes)
        pub_path.write_text(public_bytes.decode("utf-8") + f" {comment}\n", encoding="utf-8")
        return "SSH_KEY_READY"

    def _install_ssh_client(self) -> str:
        if shutil.which("ssh"):
            return "SSH_CLIENT_READY\nssh_path=" + str(shutil.which("ssh"))
        if os.geteuid() != 0:
            return (
                "SSH_CLIENT_INSTALL_BLOCKED\n"
                f"current_uid={os.geteuid()}\n"
                "OpenWebUI process is not root, so it cannot install packages itself.\n"
                "Run manually in WSL:\n"
                "sudo docker exec -u root open-webui sh -lc 'apt-get update && apt-get install -y --no-install-recommends openssh-client || apk add --no-cache openssh-client'\n"
            )

        commands = []
        if shutil.which("apt-get"):
            commands.append(["sh", "-lc", "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client"])
        if shutil.which("apk"):
            commands.append(["apk", "add", "--no-cache", "openssh-client"])
        if shutil.which("dnf"):
            commands.append(["dnf", "install", "-y", "openssh-clients"])
        if shutil.which("yum"):
            commands.append(["yum", "install", "-y", "openssh-clients"])
        if not commands:
            return (
                "SSH_CLIENT_INSTALL_BLOCKED\n"
                "No supported package manager found in OpenWebUI environment.\n"
                "Install openssh-client in the OpenWebUI image/container manually."
            )

        outputs = []
        for cmd in commands:
            proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
            outputs.append("$ " + " ".join(cmd) + "\n" + proc.stdout[-4000:])
            if proc.returncode == 0 and shutil.which("ssh"):
                return "SSH_CLIENT_INSTALLED\nssh_path=" + str(shutil.which("ssh")) + "\ninstall_output_tail:\n" + outputs[-1]
        return "SSH_CLIENT_INSTALL_FAILED\n" + "\n\n".join(outputs)

    def _git_ignore_status(self, root: Path, rel: str) -> str:
        git = shutil.which("git")
        if git:
            proc = subprocess.run([git, "-C", str(root), "check-ignore", "--no-index", "-q", rel], timeout=10)
            if proc.returncode == 0:
                return "ignored"
        gitignore = root / ".gitignore"
        if gitignore.is_file():
            text = gitignore.read_text(encoding="utf-8", errors="replace")
            if rel.startswith("codex/state/") and "codex/state/" in text:
                return "git_check_failed_but_codex_state_is_listed_in_gitignore"
        return "unknown_git_not_available"

    def _run_git(self, root: Path, args: list[str], timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
        git = shutil.which("git")
        if not git:
            raise RuntimeError("git is not available in the OpenWebUI environment")
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            [git, "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=merged_env,
        )

    def _ensure_safe_git_repo(self, root: Path) -> str:
        proc = self._run_git(root, ["config", "--global", "--add", "safe.directory", str(root)], timeout=20)
        return proc.stdout.strip()

    def _git_status_lines(self, root: Path) -> list[str]:
        proc = self._run_git(root, ["status", "--porcelain=v1", "-uall"], timeout=30)
        if proc.returncode != 0:
            raise RuntimeError("git status failed:\n" + proc.stdout)
        return [line for line in proc.stdout.splitlines() if line.strip()]

    def _git_status(self) -> str:
        root = self._repo_root()
        safe_out = self._ensure_safe_git_repo(root)
        branch = self._run_git(root, ["branch", "--show-current"], timeout=20).stdout.strip() or "(detached)"
        remote = self._run_git(root, ["remote", "-v"], timeout=20).stdout.strip()
        status = self._git_status_lines(root)
        sensitive = [p for p in self._status_paths(status) if self._is_sensitive_path(p)]
        allowed, blocked = self._classify_status_paths(status)
        return (
            "GIT_STATUS\n"
            f"repo={root}\n"
            f"safe_directory_configured=yes\n"
            f"safe_directory_output={safe_out}\n"
            f"branch={branch}\n"
            "remote:\n"
            f"{remote or '(none)'}\n"
            "changed_paths:\n"
            + ("\n".join(status) if status else "(clean)")
            + "\nallowed_for_auto_commit:\n"
            + ("\n".join(allowed) if allowed else "(none)")
            + "\nblocked_paths:\n"
            + ("\n".join(blocked) if blocked else "(none)")
            + "\nsensitive_paths_seen:\n"
            + ("\n".join(sensitive) if sensitive else "(none)")
        )

    def _status_paths(self, status_lines: list[str]) -> list[str]:
        paths = []
        for line in status_lines:
            raw = line[3:] if len(line) > 3 else line
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            paths.append(raw.strip())
        return paths

    def _is_sensitive_path(self, rel: str) -> bool:
        lower = rel.lower()
        sensitive_names = {".env", "id_rsa", "id_ed25519", "known_hosts"}
        if rel in sensitive_names or Path(rel).name in sensitive_names:
            return True
        return lower.startswith("codex/state/") or lower.startswith("codex/audit/") or lower.endswith(".pem") or lower.endswith(".key")

    def _is_runtime_ignored_path(self, rel: str) -> bool:
        lower = rel.lower()
        return (
            lower.startswith("logs/")
            or lower.startswith("codex/state/")
            or lower.startswith("codex/audit/")
            or lower.startswith("codex/workspaces/")
            or "__pycache__/" in lower
            or lower.endswith(".pyc")
            or ".bak-" in lower
            or rel == ".env"
        )

    def _is_commit_allowed_path(self, rel: str) -> bool:
        if self._is_sensitive_path(rel):
            return False
        if rel in self.allowed_exact:
            return True
        if rel.startswith("docs/") and rel.endswith(".md"):
            return True
        if rel.startswith("codex/bin/") and rel.endswith((".py", ".sh")):
            return True
        return False

    def _classify_status_paths(self, status_lines: list[str]) -> tuple[list[str], list[str]]:
        allowed = []
        blocked = []
        for line in status_lines:
            rel = self._status_paths([line])[0]
            staged_deletion = line.startswith("D ")
            if self._is_commit_allowed_path(rel) or (staged_deletion and self._is_runtime_ignored_path(rel)):
                allowed.append(rel)
            else:
                blocked.append(rel)
        return sorted(set(allowed)), sorted(set(blocked))

    def _runtime_ssh_env(self, root: Path) -> tuple[dict[str, str], str]:
        source_key = root / "codex/state/ssh/github-ai-stack_ed25519"
        if not source_key.is_file():
            raise FileNotFoundError("SSH private key not found. Run GATEWAY_ADMIN_SSH_KEYGEN first.")
        state_root = Path(os.getenv("DATA_DIR", "/app/backend/data")) / "codex-runtime/ssh"
        state_root.mkdir(parents=True, exist_ok=True)
        runtime_key = state_root / "github-ai-stack_ed25519"
        known_hosts = state_root / "known_hosts"
        runtime_key.write_bytes(source_key.read_bytes())
        try:
            os.chmod(state_root, 0o700)
            os.chmod(runtime_key, 0o600)
        except OSError:
            pass
        mode = runtime_key.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError(f"Runtime SSH key permissions are too open: {oct(mode)}")
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("ssh client is not available in the OpenWebUI environment")
        env = {
            "GIT_SSH_COMMAND": (
                f"{ssh} -i {runtime_key} -o IdentitiesOnly=yes "
                f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={known_hosts}"
            )
        }
        return env, runtime_key.as_posix()

    def _git_untrack_ignored(self) -> str:
        root = self._repo_root()
        self._ensure_safe_git_repo(root)
        status = self._git_status_lines(root)
        candidates = [rel for rel in self._status_paths(status) if self._is_runtime_ignored_path(rel)]
        if not candidates:
            return "GIT_UNTRACK_IGNORED_OK\nremoved_from_index=(none)"

        removed = []
        skipped = []
        for rel in sorted(set(candidates)):
            proc = self._run_git(root, ["rm", "--cached", "--ignore-unmatch", "--", rel], timeout=30)
            if proc.returncode == 0:
                removed.append(rel)
            else:
                skipped.append(f"{rel}: {proc.stdout.strip()}")

        return (
            "GIT_UNTRACK_IGNORED_OK\nremoved_from_index:\n"
            + ("\n".join(removed) if removed else "(none)")
            + "\nskipped:\n"
            + ("\n".join(skipped) if skipped else "(none)")
        )

    def _git_push(self, text: str) -> str:
        m = re.search(r"(?im)^\s*GATEWAY_ADMIN_GIT_PUSH(?:\s+([A-Za-z0-9_.\\/-]+))?(?:\s+(.+?))?\s*$", text)
        branch = "main"
        message = "Update ai-stack configuration and documentation"
        if m:
            if m.group(1):
                branch = m.group(1).strip()
            if m.group(2):
                message = m.group(2).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\\/-]{1,80}", branch):
            raise ValueError("Unsafe git branch name")
        if "\n" in message or len(message) > 200:
            raise ValueError("Commit message must be a single line up to 200 chars")

        root = self._repo_root()
        self._ensure_safe_git_repo(root)
        remote_url = "git@github.com:Jeycob/Ai-Stack.git"
        remote_get = self._run_git(root, ["remote", "get-url", "origin"], timeout=20)
        if remote_get.returncode != 0:
            add = self._run_git(root, ["remote", "add", "origin", remote_url], timeout=20)
            if add.returncode != 0:
                raise RuntimeError("git remote add failed:\n" + add.stdout)
            remote_action = f"added origin {remote_url}"
        elif remote_get.stdout.strip() != remote_url:
            set_url = self._run_git(root, ["remote", "set-url", "origin", remote_url], timeout=20)
            if set_url.returncode != 0:
                raise RuntimeError("git remote set-url failed:\n" + set_url.stdout)
            remote_action = f"updated origin {remote_url}"
        else:
            remote_action = f"origin already {remote_url}"

        status = self._git_status_lines(root)
        allowed, blocked = self._classify_status_paths(status)
        if blocked:
            return "GIT_PUSH_BLOCKED\nblocked_paths:\n" + "\n".join(blocked)

        for rel in allowed:
            if self._is_runtime_ignored_path(rel):
                continue
            proc = self._run_git(root, ["add", "--", rel], timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(f"git add failed for {rel}:\n{proc.stdout}")

        staged = self._run_git(root, ["diff", "--cached", "--name-only"], timeout=30).stdout.splitlines()
        commit_output = "(no staged changes)"
        if staged:
            self._run_git(root, ["config", "user.name", "AI Stack Agent"], timeout=20)
            self._run_git(root, ["config", "user.email", "ai-stack-agent@local"], timeout=20)
            commit = self._run_git(root, ["commit", "-m", message], timeout=120)
            if commit.returncode != 0:
                raise RuntimeError("git commit failed:\n" + commit.stdout)
            commit_output = commit.stdout.strip()

        ssh_env, runtime_key = self._runtime_ssh_env(root)
        ssh_test = self._run_git(root, ["ls-remote", "--heads", "origin"], timeout=60, env=ssh_env)
        if ssh_test.returncode != 0:
            raise RuntimeError("git ls-remote failed:\n" + ssh_test.stdout)
        push = self._run_git(root, ["push", "-u", "origin", f"HEAD:{branch}"], timeout=180, env=ssh_env)
        if push.returncode != 0:
            raise RuntimeError("git push failed:\n" + push.stdout)

        head = self._run_git(root, ["rev-parse", "--short", "HEAD"], timeout=20).stdout.strip()
        return (
            "GIT_PUSH_OK\n"
            f"remote_action={remote_action}\n"
            f"branch={branch}\n"
            f"runtime_key={runtime_key}\n"
            "staged_paths:\n"
            + ("\n".join(staged) if staged else "(none)")
            + "\ncommit:\n"
            + commit_output
            + "\npush:\n"
            + push.stdout.strip()
            + f"\nhead={head}"
        )

    def _target(self, rel: str) -> Path:
        root = self._repo_root()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            raise PermissionError(f"Resolved path escaped repo root: {rel}")
        return target

    def _repo_root(self) -> Path:
        candidates = []
        if self.valves.repo_root and self.valves.repo_root != "auto":
            candidates.append(self.valves.repo_root)
        candidates.extend(x.strip() for x in self.valves.candidate_roots.split(",") if x.strip())

        checked = []
        for candidate in candidates:
            root = Path(candidate).resolve()
            checked.append(str(root))
            if (root / "codex/gateway/gateway.py").is_file():
                return root
        raise FileNotFoundError("ai-stack repo root not found; checked: " + ", ".join(checked))

    def _probe_paths(self) -> str:
        paths = [
            "/",
            "/data",
            "/data/repositories",
            "/app",
            "/app/backend",
            "/app/backend/data",
            "/mnt",
            "/mnt/c",
            "/mnt/c/Repositories",
            "/tmp",
        ]
        lines = []
        for raw in paths:
            p = Path(raw)
            try:
                if not p.exists():
                    lines.append(f"{raw}: MISSING")
                    continue
                if not p.is_dir():
                    lines.append(f"{raw}: NOT_DIR")
                    continue
                names = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())[:80]
                lines.append(f"{raw}: " + ", ".join(names))
            except Exception as exc:
                lines.append(f"{raw}: ERROR {type(exc).__name__}: {exc}")
        return "\n".join(lines)

    def _diag(self) -> str:
        root = self._repo_root()
        items = []
        items.append(f"repo_root: {root}")
        for rel, label, max_lines in [
            ("codex/gateway/gateway.py", "gateway.py head", 260),
            ("codex/audit/gateway-watch.log", "gateway-watch.log tail", 80),
            ("codex/audit/gateway.log", "gateway.log tail", 120),
        ]:
            path = root / rel
            items.append(f"\n--- {label}: {rel} ---")
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                if "tail" in label:
                    lines = lines[-max_lines:]
                else:
                    lines = lines[:max_lines]
                items.extend(lines)
            except Exception as exc:
                items.append(f"ERROR {type(exc).__name__}: {exc}")
        return "\n".join(items)
