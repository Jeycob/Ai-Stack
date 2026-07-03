# openwebui-admin-filter-smoke: 2026-07-02T14:05:00
# secure_gateway marker added by admin filter
# openwebui-chat-deploy-test: 2026-07-02T15-18-local
# gateway-change-via-openwebui-chat: ok
# gateway-scheduled-chat-patch: ok
# gateway-chat-no-error-patch: ok
# gateway-chat-fast-ack-patch: ok
import json, os, re, subprocess, sys, time, uuid, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from workspace_scan import collect, load_workspace

WORKSPACES_FILE = os.getenv("CODEX_WORKSPACES_FILE", "/mnt/c/Repositories/ai-stack/codex/workspaces.json")
OLLAMA_OPENAI_URL = os.getenv("OLLAMA_OPENAI_URL", "http://192.168.0.48:11434/v1")
REPO_ROOT = Path(WORKSPACES_FILE).resolve().parents[1]
ADMIN_TOKEN_FILE = os.getenv("CODEX_GATEWAY_ADMIN_TOKEN_FILE", "")
ADMIN_TOKEN = os.getenv("CODEX_GATEWAY_ADMIN_TOKEN", "")
if not ADMIN_TOKEN and ADMIN_TOKEN_FILE:
    try:
        ADMIN_TOKEN = Path(ADMIN_TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        ADMIN_TOKEN = ""

MODELS = {
    "codex-local-plan-qwen14b": {"model": "qwen2.5-coder:14b", "mode": "plan"},
    "codex-local-build-qwen14b": {"model": "qwen2.5-coder:14b", "mode": "build"},
    "codex-local-plan-qwen32b": {"model": "qwen2.5-coder:32b", "mode": "plan"},
    "codex-local-build-qwen32b": {"model": "qwen2.5-coder:32b", "mode": "build"},
}

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "target", ".next", "__pycache__"}
IMPORTANT = {
    "README.md", "README", "package.json", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "settings.gradle",
    "CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml"
}

def load_registry():
    with open(WORKSPACES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("default", "smoke"), data.get("workspaces", {})

def content_to_text(content):
    if isinstance(content, list):
        return "\n".join(str(x.get("text", x)) for x in content)
    return str(content)

def select_workspace(messages):
    default, workspaces = load_registry()
    full = "\n".join(content_to_text(m.get("content", "")) for m in messages)
    m = re.search(r"(?im)^\s*(?:repo|workspace|project)\s*:\s*([A-Za-z0-9_.-]+)\s*$", full)
    name = m.group(1) if m else default
    if name not in workspaces:
        raise ValueError(f"Unknown workspace '{name}'. Allowed: {', '.join(sorted(workspaces))}")
    return name, workspaces[name]

def strip_routing(text):
    return re.sub(r"(?im)^\s*(?:repo|workspace|project)\s*:\s*[A-Za-z0-9_.-]+\s*$", "", text).strip()

def run_ro(args, cwd, timeout=8):
    try:
        p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return p.stdout.strip()
    except Exception as e:
        return f"[{type(e).__name__}: {e}]"

def list_files(root):
    files = set()
    ignored_prefixes = (
        ".git/",
        "codex/state/",
        "codex/audit/",
        "logs/",
        "node_modules/",
        "__pycache__/",
        ".venv/",
        "venv/",
        "dist/",
        "build/",
        ".next/",
    )

    if (root / ".git").exists():
        out = run_ro(["git", "ls-files", "--cached", "--others", "--exclude-standard"], root, 10)
        if out:
            files.update(x.strip() for x in out.splitlines() if x.strip())

    force_files = [
        "docker-compose.yml",
        "start_docker.bat",
        ".gitignore",
        "codex/workspaces.json",
        "codex/opencode-default.json",
        "docs/codex-local-operating-context.md",
        "docs/codex-local-model-system-prompt.md",
    ]
    force_dirs = [
        "codex/gateway",
        "codex/bin",
    ]

    for rel in force_files:
        if (root / rel).is_file():
            files.add(rel)

    for rel_dir in force_dirs:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if any(rel.startswith(prefix) for prefix in ignored_prefixes):
                continue
            if path.stat().st_size <= 512_000:
                files.add(rel)

    priority = (
        "codex/gateway/gateway.py",
        "docs/codex-local-operating-context.md",
        "docs/codex-local-model-system-prompt.md",
        "codex/bin/start_codex_stack.sh",
        "codex/bin/watch_gateway.sh",
        "codex/bin/add_workspace.py",
        "codex/workspaces.json",
        "codex/opencode-default.json",
        "docker-compose.yml",
        "start_docker.bat",
    )

    def rank(rel):
        if rel in priority:
            return (0, priority.index(rel), rel)
        if rel.startswith("codex/gateway/") or rel.startswith("codex/bin/"):
            return (1, 0, rel)
        return (2, 0, rel)

    return sorted(files, key=rank)[:500]

def read_small(root, rel, limit=4000):
    p = Path(root) / rel
    try:
        data = p.read_bytes()[:limit]
        if b"\x00" in data:
            return ""
        return data.decode("utf-8", "replace")
    except Exception:
        return ""

def repo_snapshot(name, cfg):
    root = Path(cfg["path"])
    if not root.exists():
        raise ValueError(f"Workspace path does not exist: {root}")

    files = list_files(root)
    top = []
    for p in sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:80]:
        if p.name not in IGNORE_DIRS:
            top.append(("[d] " if p.is_dir() else "[f] ") + p.name)

    status = run_ro(["git", "status", "--short", "--branch"], root, 8) if (root / ".git").exists() else "not a git repo"

    snippets = []
    for rel in files:
        if (
            Path(rel).name in IMPORTANT
            or rel.lower().startswith("readme")
            or rel.startswith("codex/gateway/")
            or rel.startswith("codex/bin/")
            or rel in {
                "codex/workspaces.json",
                "codex/opencode-default.json",
                "docs/codex-local-operating-context.md",
                "start_docker.bat",
            }
        ):
            txt = read_small(root, rel)
            if txt:
                snippets.append(f"--- {rel} ---\n{txt[:4000]}")
        if sum(len(x) for x in snippets) > 14000:
            break

    return "\n".join([
        f"WORKSPACE: {name}",
        f"PATH: {root}",
        "",
        "GIT STATUS:",
        status[:4000],
        "",
        "TOP LEVEL:",
        "\n".join(top),
        "",
        "FILES:",
        "\n".join(files[:300]),
        "",
        "IMPORTANT FILE SNIPPETS:",
        "\n\n".join(snippets)[:16000],
    ])

def direct_messages(payload_messages, workspace_name, snapshot, mode):
    system = (
        "You are a local coding assistant. A trusted gateway has provided a repository snapshot for analysis. "
        "Use only that snapshot unless the user asks for a general explanation. "
        "Do not output tool calls, task calls, JSON function calls, or subagent markup. "
        "If the snapshot is insufficient, say exactly what extra file or command output is needed. "
        "Reply in the user's language. When asked for exact file content, quote it exactly and do not translate it. "
        "Normal snapshot chat does not execute actions directly: do not claim shell commands, package installs, key generation, "
        "GitHub repository creation, pushes, or file edits were executed. "
        "If the user asks to create, modify, install, generate keys, push, or run commands, first say clearly that you did not execute it. "
        "Then explain that execution should go through an audited capability workflow for that workspace. "
        "For build/edit requests, propose a plan or patch, but do not ask for OS basics already visible in the snapshot."
    )
    msgs = [{"role": "system", "content": system}, {"role": "system", "content": snapshot}]
    for m in payload_messages:
        role = m.get("role", "user")
        content = strip_routing(content_to_text(m.get("content", "")))
        if content:
            msgs.append({"role": role if role in ("system", "user", "assistant") else "user", "content": content})
    return msgs

def ollama_chat(model_id, messages, timeout=300):
    body = json.dumps({"model": model_id, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(f"{OLLAMA_OPENAI_URL}/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer local")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")

def ollama_chat_stream(model_id, messages, timeout=300):
    body = json.dumps({"model": model_id, "messages": messages, "stream": True}).encode()
    req = urllib.request.Request(f"{OLLAMA_OPENAI_URL}/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer local")
    return urllib.request.urlopen(req, timeout=timeout)

def gateway_admin_text(payload):
    return "\n".join(content_to_text(m.get("content", "")) for m in payload.get("messages", []) if m.get("role") != "system")

def gateway_fixed_response_requested(payload):
    admin_text = gateway_admin_text(payload)
    direct_prefix = "GATEWAY_ADMIN_DIRECT_RESPONSE"
    direct_match = re.search(rf"(?ims)^\s*{re.escape(direct_prefix)}\s*\n(.*)", admin_text)
    has_admin_marker = "GATEWAY_ADMIN_APPLY" in admin_text
    has_admin_patch = "diff --git " in admin_text or "\n--- " in admin_text or "\n+++" in admin_text
    return bool(direct_match or (has_admin_marker and has_admin_patch))

def admin_ok(handler):
    if handler.client_address[0] in {"127.0.0.1", "::1"}:
        return True
    if not ADMIN_TOKEN:
        return False
    auth = handler.headers.get("Authorization", "")
    if auth == f"Bearer {ADMIN_TOKEN}":
        return True
    return handler.headers.get("X-Codex-Admin-Token", "") == ADMIN_TOKEN

def admin_add_workspace(payload):
    name = str(payload.get("name") or "").strip()
    path = str(payload.get("path") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
        raise ValueError("Unsafe workspace name")
    if not path:
        raise ValueError("Workspace path is required")
    port = payload.get("port")
    cpus = payload.get("cpus", 8)
    memory = str(payload.get("memory", "16g"))
    default = bool(payload.get("default", False))
    restart = bool(payload.get("restart", False))

    script = REPO_ROOT / "codex/bin/add_workspace.py"
    if not script.is_file():
        raise FileNotFoundError("codex/bin/add_workspace.py is missing")
    cmd = [os.environ.get("PYTHON", "python3"), str(script), name, path, "--cpus", str(cpus), "--memory", memory]
    if port is not None:
        cmd.extend(["--port", str(int(port))])
    if default:
        cmd.append("--default")
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    result = {
        "ok": proc.returncode == 0,
        "action": "add_workspace",
        "name": name,
        "path": path,
        "exit_code": proc.returncode,
        "output": proc.stdout.strip(),
    }

    if restart:
        bash = subprocess.run(
            ["bash", str(REPO_ROOT / "codex/bin/start_codex_stack.sh")],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
        result["restart_exit_code"] = bash.returncode
        result["restart_output"] = bash.stdout.strip()
        result["ok"] = result["ok"] and bash.returncode == 0
    return result

def safe_child_path(base_root, path):
    base = Path(base_root).resolve(strict=False)
    target = Path(path)
    if not target.is_absolute():
        target = base / target
    target = target.resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Path must stay under {base}") from exc
    return target

def run_text(cmd, cwd, timeout=60):
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip()

def generate_repo_ssh_key(name, comment):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
        raise ValueError("Unsafe SSH key name")
    if "\n" in comment or len(comment) > 160:
        raise ValueError("SSH key comment must be one line up to 160 chars")

    key_dir = REPO_ROOT / "codex/state/ssh"
    key_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(key_dir, 0o700)
    except OSError:
        pass

    key_path = key_dir / f"{name}_ed25519"
    pub_path = Path(str(key_path) + ".pub")
    if key_path.exists():
        if not pub_path.exists():
            rc, out = run_text(["ssh-keygen", "-y", "-f", str(key_path)], REPO_ROOT, 20)
            if rc != 0:
                raise RuntimeError("Existing private key found, but public key could not be derived:\n" + out)
            pub_path.write_text(out.strip() + "\n", encoding="utf-8")
        status = "SSH_KEY_EXISTS"
    else:
        if pub_path.exists():
            raise FileExistsError(f"Public key already exists without private key: {pub_path}")
        rc, out = run_text(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key_path)],
            REPO_ROOT,
            30,
        )
        if rc != 0:
            raise RuntimeError("ssh-keygen failed:\n" + out)
        status = "SSH_KEY_READY"

    for path, mode in [(key_path, 0o600), (pub_path, 0o644)]:
        try:
            os.chmod(path, mode)
        except OSError:
            pass

    return {
        "status": status,
        "private_key_path": key_path.relative_to(REPO_ROOT).as_posix(),
        "public_key_path": pub_path.relative_to(REPO_ROOT).as_posix(),
        "private_key_mode": oct(key_path.stat().st_mode & 0o777),
        "public_key_mode": oct(pub_path.stat().st_mode & 0o777),
        "public_key": pub_path.read_text(encoding="utf-8").strip(),
    }

def github_token():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return token
    token_file = os.getenv("GITHUB_TOKEN_FILE", "").strip()
    candidates = [token_file] if token_file else []
    candidates.append(str(REPO_ROOT / "codex/state/github-api.token"))
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            continue
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    return ""

def github_api(token, method, path, payload=None, timeout=30):
    url = "https://api.github.com" + path
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "ai-stack-codex-local",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw or "{}")
        except json.JSONDecodeError:
            body = {"message": raw[:2000]}
        return exc.code, body

def github_create_repo(name, key, owner="", private=True):
    token = github_token()
    if not token:
        return {
            "ok": False,
            "created": False,
            "reason": "GITHUB_TOKEN_MISSING",
            "note": "Set GITHUB_TOKEN or ignored codex/state/github-api.token to enable GitHub repository creation.",
        }
    if owner and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", owner):
        raise ValueError("Unsafe GitHub owner")

    status, user = github_api(token, "GET", "/user")
    if status >= 400:
        return {"ok": False, "created": False, "reason": "GITHUB_AUTH_FAILED", "status": status, "response": user}
    login = str(user.get("login") or "").strip()
    target_owner = owner or login

    create_path = f"/orgs/{target_owner}/repos" if owner and owner != login else "/user/repos"
    status, repo = github_api(token, "POST", create_path, {"name": name, "private": private, "auto_init": False})
    if status == 422 and isinstance(repo, dict):
        message = str(repo.get("message", ""))
        errors = repo.get("errors") or []
        already_exists = "already exists" in json.dumps(errors, ensure_ascii=False).lower() or "name already exists" in message.lower()
        if already_exists:
            status, repo = github_api(token, "GET", f"/repos/{target_owner}/{name}")
            if status >= 400:
                return {"ok": False, "created": False, "reason": "GITHUB_REPO_EXISTS_BUT_LOOKUP_FAILED", "status": status, "response": repo}
            created = False
        else:
            return {"ok": False, "created": False, "reason": "GITHUB_CREATE_FAILED", "status": status, "response": repo}
    elif status >= 400:
        return {"ok": False, "created": False, "reason": "GITHUB_CREATE_FAILED", "status": status, "response": repo}
    else:
        created = True

    full_name = str(repo.get("full_name") or f"{target_owner}/{name}")
    ssh_url = str(repo.get("ssh_url") or f"git@github.com:{full_name}.git")
    key_title = f"ai-stack-{name}"
    status, deploy_key = github_api(
        token,
        "POST",
        f"/repos/{full_name}/keys",
        {"title": key_title, "key": key["public_key"], "read_only": False},
    )
    deploy_key_added = status in {200, 201}
    deploy_key_reason = ""
    if not deploy_key_added:
        raw = json.dumps(deploy_key, ensure_ascii=False).lower()
        if status == 422 and ("key is already in use" in raw or "already_exists" in raw or "already exists" in raw):
            deploy_key_reason = "DEPLOY_KEY_ALREADY_PRESENT_OR_IN_USE"
        else:
            deploy_key_reason = "DEPLOY_KEY_ADD_FAILED"

    return {
        "ok": bool(full_name) and (deploy_key_added or deploy_key_reason == "DEPLOY_KEY_ALREADY_PRESENT_OR_IN_USE"),
        "created": created,
        "full_name": full_name,
        "ssh_url": ssh_url,
        "html_url": repo.get("html_url"),
        "private": repo.get("private"),
        "deploy_key_added": deploy_key_added,
        "deploy_key_reason": deploy_key_reason,
        "deploy_key_status": status,
    }

def admin_create_local_repo(payload):
    name = str(payload.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
        raise ValueError("Unsafe repository name")

    base_root = os.getenv("CODEX_REPOSITORIES_ROOT", "/mnt/c/Repositories")
    repo_path = safe_child_path(base_root, payload.get("path") or name)
    restart = bool(payload.get("restart", False))
    cpus = int(payload.get("cpus", 8))
    memory = str(payload.get("memory", "16g"))
    port = payload.get("port")
    default = bool(payload.get("default", False))
    github = bool(payload.get("github", False))
    github_owner = str(payload.get("github_owner") or "").strip()
    github_private = bool(payload.get("github_private", True))

    if repo_path.exists() and not repo_path.is_dir():
        raise FileExistsError(f"Repository path exists but is not a directory: {repo_path}")
    repo_path.mkdir(parents=True, exist_ok=True)

    existing_entries = [p.name for p in repo_path.iterdir() if p.name != ".git"]
    git_dir = repo_path / ".git"
    if existing_entries and not git_dir.is_dir():
        raise FileExistsError(f"Repository path is not empty and is not a git repo: {repo_path}")

    commands = []
    if not git_dir.is_dir():
        rc, out = run_text(["git", "init", "-b", "main"], repo_path, 60)
        if rc != 0:
            rc, out = run_text(["git", "init"], repo_path, 60)
            if rc == 0:
                branch_rc, branch_out = run_text(["git", "branch", "-M", "main"], repo_path, 30)
                out = out + ("\n" + branch_out if branch_out else "")
                rc = branch_rc
        commands.append(["git init", rc, out])
        if rc != 0:
            raise RuntimeError("git init failed:\n" + out)

    readme = repo_path / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {name}\n\nLocal workspace created by ai-stack codex-local.\n",
            encoding="utf-8",
        )

    gitignore = repo_path / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".env\n__pycache__/\n*.pyc\nnode_modules/\ndist/\nbuild/\n", encoding="utf-8")

    for cmd in [
        ["git", "config", "user.email", "ai-sandbox@local"],
        ["git", "config", "user.name", "AI Sandbox"],
    ]:
        rc, out = run_text(cmd, repo_path, 30)
        commands.append([" ".join(cmd), rc, out])
        if rc != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed:\n{out}")

    rc, head_out = run_text(["git", "rev-parse", "--verify", "HEAD"], repo_path, 20)
    initial_commit = rc != 0
    if initial_commit:
        for cmd in [
            ["git", "add", "README.md", ".gitignore"],
            ["git", "commit", "-m", "Initial commit"],
        ]:
            rc, out = run_text(cmd, repo_path, 60)
            commands.append([" ".join(cmd), rc, out])
            if rc != 0:
                raise RuntimeError(f"{' '.join(cmd)} failed:\n{out}")

    key = generate_repo_ssh_key(f"github-{name}", f"{name}@local")
    github_result = github_create_repo(name, key, owner=github_owner, private=github_private) if github else {
        "ok": False,
        "created": False,
        "reason": "GITHUB_NOT_REQUESTED",
        "note": "Pass github=true to create a GitHub repository when a token is configured.",
    }
    if github_result.get("ssh_url"):
        rc, out = run_text(["git", "remote", "remove", "origin"], repo_path, 20)
        commands.append(["git remote remove origin", rc, out])
        rc, out = run_text(["git", "remote", "add", "origin", str(github_result["ssh_url"])], repo_path, 20)
        commands.append(["git remote add origin", rc, out])
        if rc != 0:
            raise RuntimeError("git remote add origin failed:\n" + out)

    workspace_payload = {
        "name": name,
        "path": str(repo_path),
        "cpus": cpus,
        "memory": memory,
        "default": default,
        "restart": restart,
    }
    if port is not None:
        workspace_payload["port"] = int(port)
    workspace_result = admin_add_workspace(workspace_payload)

    rc, status_out = run_text(["git", "status", "--short", "--branch"], repo_path, 30)
    ok = bool(workspace_result.get("ok")) and rc == 0 and (not github or bool(github_result.get("ok")))
    return {
        "ok": ok,
        "action": "create_local_repo",
        "name": name,
        "path": str(repo_path),
        "workspace": workspace_result,
        "ssh_key": key,
        "github": github_result,
        "github_requested": github,
        "github_repo_created": bool(github_result.get("created", False)),
        "github_note": github_result.get("note") or github_result.get("reason", ""),
        "git_status": status_out,
        "commands": commands,
    }

def admin_run_workspace(payload):
    workspace = str(payload.get("workspace") or "").strip()
    command = payload.get("command") or []
    timeout = int(payload.get("timeout", 300))
    env_map = payload.get("env") or {}
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("Unsafe workspace name")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a non-empty string list")
    if not isinstance(env_map, dict):
        raise ValueError("env must be an object")

    script = REPO_ROOT / "codex/bin/run_check.py"
    if not script.is_file():
        raise FileNotFoundError("codex/bin/run_check.py is missing")

    cmd = [os.environ.get("PYTHON", "python3"), str(script), "--timeout", str(timeout), "--json"]
    for key, value in env_map.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("env keys and values must be strings")
        cmd.extend(["--env", f"{key}={value}"])
    cmd.append(workspace)
    cmd.append("--")
    cmd.extend(command)
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(timeout + 30, 60),
    )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = {
            "ok": proc.returncode == 0,
            "workspace": workspace,
            "command": command,
            "exit_code": proc.returncode,
            "output": proc.stdout,
        }
    result["runner_exit_code"] = proc.returncode
    return result

def admin_workspace_action(payload):
    workspace = str(payload.get("workspace") or "").strip()
    action = str(payload.get("action") or "").strip()
    timeout = int(payload.get("timeout") or 900)
    env_map = payload.get("env") or {}
    dry_run = bool(payload.get("dry_run", False))

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    if action not in {"install", "test", "build", "lint", "verify", "smoke"}:
        raise ValueError("action must be one of install, test, build, lint, verify, smoke")
    if timeout < 1 or timeout > 3600:
        raise ValueError("timeout must be between 1 and 3600")
    if not isinstance(env_map, dict):
        raise ValueError("env must be an object")

    script = REPO_ROOT / "codex/bin/workspace_action.py"
    if not script.is_file():
        raise FileNotFoundError("codex/bin/workspace_action.py is missing")

    cmd = [os.environ.get("PYTHON", "python3"), str(script), action, "--timeout", str(timeout), "--json"]
    if dry_run:
        cmd.append("--dry-run")
    for key, value in env_map.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("env keys and values must be strings")
        cmd.extend(["--env", f"{key}={value}"])
    cmd.extend(["--workspace", workspace, "--workspaces-file", WORKSPACES_FILE])
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(timeout + 30, 60),
    )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = {
            "ok": proc.returncode == 0,
            "workspace": workspace,
            "action": action,
            "exit_code": proc.returncode,
            "output": proc.stdout,
        }
    result["workspace"] = workspace
    result["runner_exit_code"] = proc.returncode
    return result

def workspace_autopilot_recommendation(workspace: str) -> dict:
    def build_hint(text: str, patch_target: str, patch_hint: str, patch_summary: str) -> dict:
        read_command = f"GATEWAY_ADMIN_READ_NUMBERED {patch_target} 1 200" if patch_target else ""
        return {
            "text": text,
            "patch_target": patch_target,
            "patch_hint": patch_hint,
            "patch_summary": patch_summary,
            "read_command": read_command,
        }

    try:
        root = load_workspace(WORKSPACES_FILE, workspace)
        scan = collect(root, 60)
    except Exception as exc:
        return build_hint(
            f"Workspace needs manual review because scan data is unavailable: {exc}",
            "",
            "",
            "",
        )

    manifests = scan.get("manifests") or []
    languages = scan.get("languages") or []
    package_scripts = scan.get("package_scripts") or []
    candidate_commands = scan.get("candidate_commands") or []
    manifest_names = {Path(rel).name for rel in manifests}

    if not manifests:
        return build_hint(
            "Workspace has no recognized project manifest yet; first add build or package metadata for the detected stack.",
            "",
            "Add a standard project manifest such as package.json, pyproject.toml, Cargo.toml, go.mod, pom.xml, or CMakeLists.txt.",
            "Create the primary project manifest for the detected stack.",
        )
    if "package.json" in manifest_names and not package_scripts:
        return build_hint(
            "Node workspace has package.json but no scripts; add at least build, test or lint scripts so codex-local can continue automatically.",
            "package.json",
            "Add scripts.test, scripts.build, or scripts.lint entries under package.json:scripts.",
            "Extend package.json:scripts with standard test/build/lint commands.",
        )
    if {"pyproject.toml", "requirements.txt"} & manifest_names and not any("pytest" in cmd for cmd in candidate_commands):
        target = "pyproject.toml" if "pyproject.toml" in manifest_names else "requirements.txt"
        return build_hint(
            "Python workspace is missing an obvious test entrypoint; consider adding tests/ or a pytest-compatible setup.",
            target,
            "Add a pytest-compatible test layout, or declare the test dependency/configuration in pyproject.toml or requirements.txt.",
            "Add a pytest-compatible test entrypoint and test dependency/configuration.",
        )
    if any(lang in {"javascript/typescript", "python", "rust", "go", "jvm", "c/cpp"} for lang in languages) and not candidate_commands:
        target = manifests[0] if manifests else ""
        return build_hint(
            "Project shape is recognized, but no runnable lint/test/build command was inferred; expose one through standard manifests or scripts.",
            target,
            "Add a standard lint, test, or build entrypoint in the main project manifest so capability routing can infer it.",
            "Expose at least one standard lint/test/build entrypoint in the main manifest.",
        )
    if candidate_commands:
        return build_hint(
            f"No safe next capability matched the current allowlist. The first inferred manual command to review is: {candidate_commands[0]}",
            manifests[0] if manifests else "",
            f"Review whether this command should be exposed through a standard script or manifest entry: {candidate_commands[0]}",
            f"Expose the inferred command through a standard manifest/script entry: {candidate_commands[0]}",
        )
    return build_hint(
        "No safe next capability was inferred; inspect manifests and add a standard verify, smoke, lint, test or build entrypoint.",
        manifests[0] if manifests else "",
        "Expose at least one standard verify, smoke, lint, test, or build entrypoint in the project manifest.",
        "Add at least one standard verify/smoke/lint/test/build entrypoint to the project manifest.",
    )

def admin_workspace_autopilot(payload):
    workspace = str(payload.get("workspace") or "").strip()
    timeout = int(payload.get("timeout") or 1800)
    env_map = payload.get("env") or {}
    recommend_only = bool(payload.get("recommend_only", False))
    allow_actions = payload.get("allow_actions") or ["install", "verify", "smoke", "test", "build", "lint"]
    max_steps = int(payload.get("max_steps") or 1)

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    if timeout < 1 or timeout > 3600:
        raise ValueError("timeout must be between 1 and 3600")
    if max_steps < 1 or max_steps > 5:
        raise ValueError("max_steps must be between 1 and 5")
    if not isinstance(env_map, dict):
        raise ValueError("env must be an object")
    if isinstance(allow_actions, str):
        allow_actions = [x.strip().lower() for x in allow_actions.split(",") if x.strip()]
    if not isinstance(allow_actions, list):
        raise ValueError("allow_actions must be a list or comma-separated string")
    allow_actions = [str(x).strip().lower() for x in allow_actions if str(x).strip()]
    invalid = sorted(set(allow_actions) - {"install", "verify", "smoke", "test", "build", "lint"})
    if invalid:
        raise ValueError("allow_actions supports only install, verify, smoke, test, build, lint")
    if not allow_actions:
        raise ValueError("allow_actions must not be empty")

    def plan_candidates(executed_names):
        verify_result = admin_workspace_action({
            "workspace": workspace,
            "action": "verify",
            "timeout": timeout,
            "env": env_map,
            "dry_run": True,
        })
        verify_steps_local = verify_result.get("verify_steps") or []
        candidates = []
        for step in verify_steps_local:
            action = str(step.get("action") or "").strip().lower()
            if action in allow_actions and step.get("supported") and action not in executed_names:
                candidates.append(action)

        install_probe_local = None
        if not candidates and "install" in allow_actions and "install" not in executed_names:
            install_probe_local = admin_workspace_action({
                "workspace": workspace,
                "action": "install",
                "timeout": timeout,
                "env": env_map,
                "dry_run": True,
            })
            if install_probe_local.get("ok"):
                candidates.append("install")
        return verify_result, verify_steps_local, candidates, install_probe_local

    verify, verify_steps, candidate_actions, install_probe = plan_candidates(set())

    chosen_action = candidate_actions[0] if candidate_actions else None
    if chosen_action == "install" and install_probe is not None:
        chosen_reason = "verify found no runnable step, but dependency install is supported"
    elif chosen_action:
        chosen_reason = f"verify dry-run found a supported {chosen_action} step"
    else:
        chosen_reason = ""

    if not chosen_action:
        recommendation = workspace_autopilot_recommendation(workspace)
        return {
            "ok": False,
            "workspace": workspace,
            "action": "autopilot",
            "recommend_only": recommend_only,
            "allow_actions": allow_actions,
            "max_steps": max_steps,
            "chosen_action": "none",
            "reason": "No supported next action was found within the allowed action set.",
            "recommendation": recommendation.get("text", ""),
            "patch_target": recommendation.get("patch_target", ""),
            "patch_hint": recommendation.get("patch_hint", ""),
            "patch_summary": recommendation.get("patch_summary", ""),
            "read_command": recommendation.get("read_command", ""),
            "duration_ms": verify.get("duration_ms", 0),
            "verify_steps": verify_steps,
            "install_probe": install_probe,
            "executed_actions": [],
            "stop_reason": "no_supported_action",
            "output": "",
        }

    if recommend_only:
        return {
            "ok": True,
            "workspace": workspace,
            "action": "autopilot",
            "recommend_only": True,
            "allow_actions": allow_actions,
            "max_steps": max_steps,
            "chosen_action": chosen_action,
            "reason": chosen_reason,
            "recommendation": "",
            "patch_target": "",
            "patch_hint": "",
            "patch_summary": "",
            "read_command": "",
            "duration_ms": verify.get("duration_ms", 0),
            "verify_steps": verify_steps,
            "install_probe": install_probe,
            "executed_actions": [],
            "stop_reason": "recommend_only",
            "output": "",
        }

    total_started = time.time()
    executed_actions = []
    last_result = None
    current_verify_steps = verify_steps
    current_install_probe = install_probe
    stop_reason = "max_steps_reached"
    for idx in range(max_steps):
        remaining_names = {step["action"] for step in executed_actions if step.get("action")}
        if idx == 0:
            next_candidates = candidate_actions
        else:
            _, current_verify_steps, next_candidates, current_install_probe = plan_candidates(remaining_names)
        if not next_candidates:
            stop_reason = "no_more_supported_actions"
            break
        action_name = next_candidates[0]
        last_result = admin_workspace_action({
            "workspace": workspace,
            "action": action_name,
            "timeout": timeout,
            "env": env_map,
            "dry_run": False,
        })
        executed_actions.append({
            "action": action_name,
            "ok": bool(last_result.get("ok")),
            "exit_code": last_result.get("exit_code"),
            "runner_exit_code": last_result.get("runner_exit_code"),
            "duration_ms": last_result.get("duration_ms"),
            "resolved_from": last_result.get("resolved_from"),
            "command": last_result.get("command", []),
            "output": last_result.get("output", ""),
            "error": last_result.get("error"),
        })
        if not last_result.get("ok"):
            stop_reason = "step_failed"
            break

    ok = all(step.get("ok") for step in executed_actions) if executed_actions else False
    output_parts = []
    for step in executed_actions:
        output_parts.append(
            f"== {step['action']} ==\n{str(step.get('output', '')).rstrip()}"
        )
    final_summary = []
    if executed_actions:
        final_summary.append("summary:")
        for step in executed_actions:
            if step.get("ok"):
                final_summary.append(f"- {step['action']}: ok")
            else:
                final_summary.append(f"- {step['action']}: failed ({step.get('error') or step.get('exit_code')})")
        final_summary.append(f"stop_reason={stop_reason}")
    return {
        "ok": ok,
        "workspace": workspace,
        "action": "autopilot",
        "recommend_only": False,
        "allow_actions": allow_actions,
        "max_steps": max_steps,
        "chosen_action": chosen_action,
        "reason": chosen_reason,
        "recommendation": "",
        "patch_target": "",
        "patch_hint": "",
        "patch_summary": "",
        "read_command": "",
        "duration_ms": int((time.time() - total_started) * 1000),
        "verify_steps": current_verify_steps,
        "install_probe": current_install_probe,
        "executed_actions": executed_actions,
        "stop_reason": stop_reason,
        "exit_code": last_result.get("exit_code") if last_result else None,
        "runner_exit_code": last_result.get("runner_exit_code") if last_result else None,
        "output": "\n\n".join(output_parts + (["\n".join(final_summary)] if final_summary else [])).strip(),
    }

def pid_running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

def tail_text(path, max_bytes=24000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    if len(data.encode("utf-8", "replace")) >= max_bytes:
        return "[tail truncated]\n" + data
    return data

def admin_deploy_stack(payload):
    branch = str(payload.get("branch") or "main").strip()
    force = bool(payload.get("force", False))
    if not re.fullmatch(r"[A-Za-z0-9_.\\/-]{1,120}", branch):
        raise ValueError("Unsafe branch name")

    script = REPO_ROOT / "codex/bin/deploy_ai_stack.sh"
    if not script.is_file():
        raise FileNotFoundError("codex/bin/deploy_ai_stack.sh is missing")

    state_dir = REPO_ROOT / "codex/state"
    audit_dir = REPO_ROOT / "codex/audit"
    state_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    pid_file = state_dir / "deploy-ai-stack.pid"
    log_file = audit_dir / "deploy-ai-stack.log"

    old_pid = None
    if pid_file.is_file():
        try:
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = None
    if old_pid and pid_running(old_pid) and not force:
        return {
            "ok": False,
            "action": "deploy_already_running",
            "pid": old_pid,
            "log": str(log_file),
            "tail": tail_text(log_file),
        }

    env = os.environ.copy()
    env["AI_STACK_BRANCH"] = branch
    env.setdefault("AI_STACK_REMOTE", "origin")

    with open(log_file, "ab") as log:
        log.write(f"\n[{time.strftime('%F %T')}] scheduling deploy branch={branch}\n".encode())
        proc = subprocess.Popen(
            [str(script)],
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    pid_file.write_text(str(proc.pid) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "action": "deploy_scheduled",
        "pid": proc.pid,
        "branch": branch,
        "log": str(log_file),
    }

def admin_deploy_status(payload):
    state_dir = REPO_ROOT / "codex/state"
    audit_dir = REPO_ROOT / "codex/audit"
    pid_file = state_dir / "deploy-ai-stack.pid"
    log_file = audit_dir / "deploy-ai-stack.log"
    pid = None
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = None
    running = bool(pid and pid_running(pid))
    head = run_ro(["git", "rev-parse", "--short", "HEAD"], REPO_ROOT, 8)
    status = run_ro(["git", "status", "--short", "--branch"], REPO_ROOT, 8)
    return {
        "ok": True,
        "action": "deploy_status",
        "pid": pid,
        "running": running,
        "head": head,
        "git_status": status,
        "log": str(log_file),
        "tail": tail_text(log_file),
    }

def fallback_response_text(payload):
    text = strip_routing(gateway_admin_text(payload)).strip()
    execute_re = re.compile(
        r"(?i)\b(vytvor|vytvoř|zaloz|založ|push|pushni|commit|commitni|install|nainstaluj|"
        r"spust|spusť|uprav|edituj|generate|vygeneruj|ssh|github|repo|repository)\b"
    )
    if execute_re.search(text):
        return (
            "Tuhle akci jsem sam primo nevykonal. "
            "Umim analyzovat snapshot repozitare a navrhnout plan nebo patch, ale shell, instalace balicku, "
            "generovani klicu, vytvareni GitHub repozitaru, push a realne editace souboru maji jit pres "
            "auditovany capability workflow pro dany workspace. Pokud takova schopnost chybi, ma si agent "
            "rict o rozsireni workspace profilu misto toho, aby akci predstiral."
        )
    return (
        "Model vratil prazdnou odpoved. Zkus prosim dotaz zopakovat nebo ho zuzit; "
        "gateway to zachytila, aby v OpenWebUI nezustala prazdna zprava."
    )

def normal_chat_requires_tool(payload):
    text = strip_routing(gateway_admin_text(payload)).strip()
    if "GATEWAY_ADMIN_" in text:
        return False
    risky_re = re.compile(
        r"(?is)\b(ssh|github|push|pushni|install|nainstaluj|spust|spusť|shell|command|"
        r"vygeneruj\s+.*(?:klic|klíč|key)|generate\s+.*key|"
        r"(?:vytvor|vytvoř|zaloz|založ)\s+.*(?:repo|repository|repozitar|repozitář))\b"
    )
    return bool(risky_re.search(text))

def sse_line_info(raw):
    line = raw.decode("utf-8", "replace").strip()
    if not line.startswith("data:"):
        return "", False, False
    data = line.split(":", 1)[1].strip()
    if data == "[DONE]":
        return "", True, False
    try:
        obj = json.loads(data)
    except Exception:
        return "", False, False
    choices = obj.get("choices") or []
    if not choices:
        return "", False, False
    choice = choices[0]
    delta = choice.get("delta") or {}
    message = choice.get("message") or {}
    content = delta.get("content") or message.get("content") or ""
    return content if isinstance(content, str) else "", False, choice.get("finish_reason") is not None

def sse_chunk(result_id, created, model_name, content="", finish_reason=None):
    delta = {} if finish_reason else {"role": "assistant", "content": content}
    return {
        "id": result_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }

def model_request(payload, model_name):
    spec = MODELS.get(model_name, MODELS["codex-local-plan-qwen14b"])
    workspace_name, workspace = select_workspace(payload.get("messages", []))
    snapshot = repo_snapshot(workspace_name, workspace)
    return spec, direct_messages(payload.get("messages", []), workspace_name, snapshot, spec["mode"])

def completion(payload):
    model_name = payload.get("model") or "codex-local-plan-qwen14b"
    admin_text = gateway_admin_text(payload)
    direct_prefix = "GATEWAY_ADMIN_DIRECT_RESPONSE"
    direct_match = re.search(rf"(?ims)^\s*{re.escape(direct_prefix)}\s*\n(.*)", admin_text)
    if direct_match:
        direct_text = direct_match.group(1).strip()
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": direct_text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(direct_text.split()), "total_tokens": len(direct_text.split())},
        }
    has_admin_marker = "GATEWAY_ADMIN_APPLY" in admin_text
    has_admin_patch = "diff --git " in admin_text or "\n--- " in admin_text or "\n+++" in admin_text
    if has_admin_marker and has_admin_patch:
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "GATEWAY_PATCH_SCHEDULED"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 3, "total_tokens": 3},
        }

    if normal_chat_requires_tool(payload):
        text = fallback_response_text(payload)
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(text.split()), "total_tokens": len(text.split())},
        }

    spec, messages = model_request(payload, model_name)
    resp = ollama_chat(spec["model"], messages)

    text = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not text:
        text = fallback_response_text(payload)
    usage = resp.get("usage", {})
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }

class H(BaseHTTPRequestHandler):
    def sendj(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def sendsse(self, result):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        text = result["choices"][0]["message"]["content"]
        chunk = {"id": result["id"], "object": "chat.completion.chunk", "created": result["created"], "model": result["model"], "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
        done = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def proxy_ollama_sse(self, payload):
        model_name = payload.get("model") or "codex-local-plan-qwen14b"
        result_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())
        spec, messages = model_request(payload, model_name)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        seen_text = []
        pending_finish = []
        with ollama_chat_stream(spec["model"], messages) as upstream:
            for raw in upstream:
                if not raw:
                    continue
                content, done_seen, finish_seen = sse_line_info(raw)
                if done_seen:
                    break
                if content:
                    seen_text.append(content)
                if finish_seen:
                    pending_finish.append(raw)
                    continue
                self.wfile.write(raw)
                if raw.endswith(b"\n") and not raw.endswith(b"\n\n"):
                    self.wfile.write(b"\n")
                self.wfile.flush()
        if "".join(seen_text).strip():
            for raw in pending_finish:
                self.wfile.write(raw)
                if raw.endswith(b"\n") and not raw.endswith(b"\n\n"):
                    self.wfile.write(b"\n")
        else:
            fallback = fallback_response_text(payload)
            self.wfile.write(f"data: {json.dumps(sse_chunk(result_id, created, model_name, fallback), ensure_ascii=False)}\n\n".encode())
            self.wfile.write(f"data: {json.dumps(sse_chunk(result_id, created, model_name, finish_reason='stop'), ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        if self.path == "/health":
            default, workspaces = load_registry()
            return self.sendj({"ok": True, "default": default, "workspaces": sorted(workspaces)})
        if self.path == "/v1/models":
            now = int(time.time())
            return self.sendj({"object": "list", "data": [{"id": k, "object": "model", "created": now, "owned_by": "local"} for k in MODELS]})
        if self.path == "/v1/workspaces":
            default, workspaces = load_registry()
            return self.sendj({"default": default, "workspaces": workspaces})
        self.sendj({"error": "not found"}, 404)

    def do_POST(self):
        try:
            if self.path == "/v1/admin/workspace/add":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_add_workspace(payload))
            if self.path == "/v1/admin/repository/create-local":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_create_local_repo(payload))
            if self.path == "/v1/admin/workspace/run":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_run_workspace(payload))
            if self.path == "/v1/admin/workspace/action":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_workspace_action(payload))
            if self.path == "/v1/admin/workspace/autopilot":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_workspace_autopilot(payload))
            if self.path == "/v1/admin/stack/deploy":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_deploy_stack(payload))
            if self.path == "/v1/admin/stack/deploy/status":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_deploy_status(payload))
            if self.path != "/v1/chat/completions":
                return self.sendj({"error": "not found"}, 404)
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n).decode() or "{}")
            if payload.get("stream") and not gateway_fixed_response_requested(payload) and not normal_chat_requires_tool(payload):
                return self.proxy_ollama_sse(payload)
            result = completion(payload)
            return self.sendsse(result) if payload.get("stream") else self.sendj(result)
        except Exception as e:
            self.sendj({"error": {"message": str(e), "type": e.__class__.__name__}}, 500)

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%F %T')}] {self.address_string()} {fmt % args}", flush=True)

if __name__ == "__main__":
    port = int(os.getenv("GATEWAY_PORT", "9101"))
    print(f"codex-gateway listening on http://0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
