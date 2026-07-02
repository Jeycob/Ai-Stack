# openwebui-admin-filter-smoke: 2026-07-02T14:05:00
# secure_gateway marker added by admin filter
# openwebui-chat-deploy-test: 2026-07-02T15-18-local
# gateway-change-via-openwebui-chat: ok
# gateway-scheduled-chat-patch: ok
# gateway-chat-no-error-patch: ok
# gateway-chat-fast-ack-patch: ok
import json, os, re, subprocess, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACES_FILE = os.getenv("CODEX_WORKSPACES_FILE", "/mnt/c/Repositories/ai-stack/codex/workspaces.json")
OLLAMA_OPENAI_URL = os.getenv("OLLAMA_OPENAI_URL", "http://192.168.0.48:11434/v1")

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
            or rel in {"codex/workspaces.json", "codex/opencode-default.json", "start_docker.bat"}
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
        "You are a local coding assistant. A trusted gateway has provided a read-only repository snapshot. "
        "Use only that snapshot unless the user asks for a general explanation. "
        "Do not output tool calls, task calls, JSON function calls, or subagent markup. "
        "If the snapshot is insufficient, say exactly what extra file or command output is needed. "
        "Reply in the user's language. For build/edit requests, propose a plan or patch, but do not claim files were edited."
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

    spec, messages = model_request(payload, model_name)
    resp = ollama_chat(spec["model"], messages)

    text = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
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
        spec, messages = model_request(payload, model_name)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        with ollama_chat_stream(spec["model"], messages) as upstream:
            for raw in upstream:
                if not raw:
                    continue
                self.wfile.write(raw)
                if raw.endswith(b"\n") and not raw.endswith(b"\n\n"):
                    self.wfile.write(b"\n")
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
        if self.path != "/v1/chat/completions":
            return self.sendj({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n).decode() or "{}")
            if payload.get("stream") and not gateway_fixed_response_requested(payload):
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
