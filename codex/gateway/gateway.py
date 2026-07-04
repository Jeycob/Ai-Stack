# openwebui-admin-filter-smoke: 2026-07-02T14:05:00
# secure_gateway marker added by admin filter
# openwebui-chat-deploy-test: 2026-07-02T15-18-local
# gateway-change-via-openwebui-chat: ok
# gateway-scheduled-chat-patch: ok
# gateway-chat-no-error-patch: ok
# gateway-chat-fast-ack-patch: ok
import hashlib, html, inspect, ipaddress, json, os, re, shlex, socket, subprocess, sys, threading, time, uuid, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from workspace_context import (
    WORKSPACE_LABEL_PATTERN,
    bootstrap_repo_name_from_text,
    canonical_workspace_name,
    infer_repo_name_from_text,
    load_workspace_registry,
    resolve_workspace_context,
    strip_workspace_routing,
)
from codex_local_config import (
    DEFAULT_MODEL_ALIAS,
    ROLE_AGENT,
    ROLE_DIRECT,
    ROLE_EXECUTOR,
    ROLE_PLANNER,
    ROLE_RECOVERY,
    ROLE_REVIEWER,
    codex_local_model_aliases,
    is_codex_local_model_name,
    load_codex_local_config,
    resolve_runtime_model,
)
from openwebui_runtime import discover_openwebui_base_urls
from workspace_scan import collect, load_workspace

GATEWAY_SOURCE_EPOCH = "2026-07-04-agent-self-improve-v1"

OLLAMA_OPENAI_URL = os.getenv("OLLAMA_OPENAI_URL", "http://192.168.0.48:11434/v1")
OPENWEBUI_HEALTH_URL = os.getenv("OPENWEBUI_HEALTH_URL", "http://127.0.0.1:9090/")
OPENWEBUI_LOADER_URL = os.getenv("OPENWEBUI_LOADER_URL", "http://127.0.0.1:9090/static/loader.js")
ADMIN_TOKEN_FILE = os.getenv("CODEX_GATEWAY_ADMIN_TOKEN_FILE", "")
ADMIN_TOKEN = os.getenv("CODEX_GATEWAY_ADMIN_TOKEN", "")
if not ADMIN_TOKEN and ADMIN_TOKEN_FILE:
    try:
        ADMIN_TOKEN = Path(ADMIN_TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        ADMIN_TOKEN = ""

CODEX_LOCAL_CONFIG = load_codex_local_config()
MODELS = codex_local_model_aliases(CODEX_LOCAL_CONFIG)
STRUCTURED_BACKEND_STATE = {
    "usable": None,
    "strategy": "unprobed",
    "last_error": "",
    "last_schema": "",
    "last_checked": 0.0,
}


def env_truthy(name):
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _repo_root_candidates():
    env_workspaces = os.getenv("CODEX_WORKSPACES_FILE", "").strip()
    if env_workspaces:
        try:
            yield Path(env_workspaces).resolve().parents[1]
        except Exception:
            pass
    env_repo_root = os.getenv("CODEX_REPO_ROOT", "").strip()
    if env_repo_root:
        try:
            yield Path(env_repo_root).resolve()
        except Exception:
            pass
    try:
        yield Path(__file__).resolve().parents[2]
    except Exception:
        pass
    for raw in (
        "/mnt/c/Repositories/ai-stack",
        "/data/repositories/ai-stack",
        "/app/backend/data/repositories/ai-stack",
        "/Repositories/ai-stack",
    ):
        try:
            yield Path(raw).resolve()
        except Exception:
            yield Path(raw)


def _resolve_repo_root():
    checked = []
    seen = set()
    for root in _repo_root_candidates():
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        checked.append(key)
        if (root / "codex/gateway/gateway.py").is_file():
            return root
    fallback = next(iter(seen), str(Path.cwd()))
    return Path(fallback)


def _resolve_workspaces_file(repo_root: Path):
    env_workspaces = os.getenv("CODEX_WORKSPACES_FILE", "").strip()
    candidates = []
    if env_workspaces:
        candidates.append(Path(env_workspaces))
    candidates.append(repo_root / "codex/workspaces.json")
    for raw in (
        "/mnt/c/Repositories/ai-stack/codex/workspaces.json",
        "/data/repositories/ai-stack/codex/workspaces.json",
        "/app/backend/data/repositories/ai-stack/codex/workspaces.json",
        "/Repositories/ai-stack/codex/workspaces.json",
    ):
        candidates.append(Path(raw))
    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            return resolved
    return candidates[0] if env_workspaces else repo_root / "codex/workspaces.json"


REPO_ROOT = _resolve_repo_root()
WORKSPACES_FILE = str(_resolve_workspaces_file(REPO_ROOT))
CAPABILITY_ROADMAP_FILE = REPO_ROOT / "docs" / "codex-local-capability-roadmap.json"

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "target", ".next", "__pycache__"}
IMPORTANT = {
    "README.md", "README", "package.json", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "settings.gradle",
    "CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml"
}
WORKSPACE_LABEL_RE = WORKSPACE_LABEL_PATTERN
SENSITIVE_FILE_PREFIXES = (
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
SENSITIVE_FILE_NAMES = {".env"}

def load_registry():
    return load_workspace_registry(WORKSPACES_FILE)

def load_capability_roadmap_payload():
    try:
        payload = json.loads(CAPABILITY_ROADMAP_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

def load_workspace_action_registry():
    actions = load_capability_roadmap_payload().get("workspace_actions")
    if not isinstance(actions, dict):
        return {}
    return {str(name): spec for name, spec in actions.items() if isinstance(spec, dict)}


def load_capability_roadmap_registry():
    capabilities = load_capability_roadmap_payload().get("capabilities")
    if not isinstance(capabilities, dict):
        return {}
    return {str(name): spec for name, spec in capabilities.items() if isinstance(spec, dict)}


def load_dynamic_capability_aliases():
    aliases = {}
    for name, spec in load_capability_roadmap_registry().items():
        canonical = str(name or "").strip()
        if not canonical:
            continue
        for alias in spec.get("aliases") or []:
            key = capability_key(alias)
            if key and key not in aliases:
                aliases[key] = canonical
    return aliases


def content_to_text(content):
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content)


def parse_agent_loop_request_text(text):
    match = re.search(r"(?im)^\s*GATEWAY_ADMIN_AGENT_LOOP\s+(.+?)\s*$", str(text or ""))
    if not match:
        return None
    try:
        parts = shlex.split(match.group(1))
    except ValueError:
        return None
    if not parts:
        return None
    workspace = parts.pop(0)
    if "--" not in parts:
        return None
    marker = parts.index("--")
    task = " ".join(parts[marker + 1 :]).strip()
    if not workspace or not task:
        return None
    return workspace, task


def clean_conversation_text_for_taskspec(role, text):
    text = str(text or "")
    parsed_agent_loop = parse_agent_loop_request_text(text)
    if role == "user" and parsed_agent_loop:
        _, task = parsed_agent_loop
        text = task
    text = re.sub(r"<!--\s*CODEX_DEBUG.*?-->", "", text, flags=re.S | re.I)
    text = re.sub(r"-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", text, flags=re.S)
    text = re.sub(r"(?im)^.*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)\s*[:=].*?$", "[REDACTED_SECRET_LINE]", text)
    kept_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not line:
            continue
        if lowered.startswith("gateway_admin_") or lowered.startswith("agent_loop_"):
            continue
        if lowered.startswith("requested_workspace=") or lowered.startswith("controller_workspace="):
            continue
        if lowered.startswith("planner_source=") or lowered.startswith("routing_provenance="):
            continue
        if lowered.startswith("workflow=") or lowered.startswith("read_only="):
            continue
        if lowered.startswith("model_runtime") or lowered.startswith("execution="):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip()


def recent_conversation_context(messages, max_messages=8, max_chars=3000):
    context = []
    for message in (messages or [])[-max_messages:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        if role == "system":
            continue
        text = content_to_text(message.get("content", "")).strip()
        if not text:
            continue
        text = clean_conversation_text_for_taskspec(role, text)
        if not text:
            continue
        context.append(f"{role}: {preview_text(text, 700)}")
    rendered = "\n".join(context)
    if len(rendered) > max_chars:
        rendered = rendered[-max_chars:]
    return rendered

def select_workspace(messages):
    default, workspaces = load_registry()
    full = "\n".join(content_to_text(m.get("content", "")) for m in messages)
    resolved = resolve_workspace_context(full, messages, WORKSPACES_FILE, fallback_workspace=default)
    name = resolved.workspace
    if name not in workspaces:
        raise ValueError(f"Unknown workspace '{name}'. Allowed: {', '.join(sorted(workspaces))}")
    return name, workspaces[name]

def strip_routing(text):
    _, workspaces = load_registry()
    return strip_workspace_routing(text, workspaces)

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
    role_hint = {
        ROLE_PLANNER: "planner mode: return a concise plan only",
        ROLE_EXECUTOR: "executor mode: explain the nearest safe execution path without claiming actions you did not run",
        ROLE_REVIEWER: "reviewer mode: validate outcome and point to concrete blockers or evidence",
        ROLE_RECOVERY: "recovery mode: focus on root cause and next safe recovery step",
    }.get(str(mode or ""), "agent mode: answer directly from the snapshot")
    system = (
        "You are a local coding assistant. A trusted gateway has provided a repository snapshot for analysis. "
        "Use only that snapshot unless the user asks for a general explanation. "
        f"Current role: {role_hint}. "
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

def ollama_chat(model_id, messages, timeout=300, response_format=None, extra_body=None):
    body_payload = {"model": model_id, "messages": messages, "stream": False}
    if response_format:
        body_payload["response_format"] = response_format
    if extra_body:
        body_payload.update(extra_body)
    body = json.dumps(body_payload).encode()
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

WEB_FETCH_DEFAULT_MAX_BYTES = 300_000
WEB_FETCH_HARD_MAX_BYTES = 2_000_000
WEB_FETCH_DEFAULT_TIMEOUT = 20
WEB_FETCH_HARD_TIMEOUT = 60
WEB_FETCH_TEXT_LIMIT = 30_000

BLOCKED_WEB_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "host.docker.internal",
    "docker.internal",
}


class HTMLTextExtractor(HTMLParser):
    skip_tags = {"script", "style", "noscript", "svg", "canvas"}
    break_tags = {
        "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav",
        "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.skip_tags:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.break_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.skip_tags and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self.break_tags:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = html.unescape(data or "").strip()
        if text:
            self.parts.append(text)

    def text(self):
        raw = " ".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        lines = [line.strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line).strip()


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public_web_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def clamp_int(value, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))

def http_probe(url, timeout=2, max_bytes=8192):
    started = time.time()
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(max_bytes)
            content_type = resp.headers.get("Content-Type", "")
            return {
                "ok": True,
                "url": url,
                "status": getattr(resp, "status", 200),
                "content_type": content_type,
                "bytes": len(body),
                "duration_ms": int((time.time() - started) * 1000),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "url": url,
            "status": exc.code,
            "error": f"HTTP {exc.code}",
            "duration_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.time() - started) * 1000),
        }


def openwebui_probe_candidates():
    env_candidates = discover_openwebui_base_urls(REPO_ROOT)
    explicit_root = urllib.parse.urlparse(str(OPENWEBUI_HEALTH_URL or "").strip())
    explicit_loader = urllib.parse.urlparse(str(OPENWEBUI_LOADER_URL or "").strip())
    if explicit_root.scheme and explicit_root.netloc:
        base = f"{explicit_root.scheme}://{explicit_root.netloc}"
        if base not in env_candidates:
            env_candidates.insert(0, base)
    if explicit_loader.scheme and explicit_loader.netloc:
        base = f"{explicit_loader.scheme}://{explicit_loader.netloc}"
        if base not in env_candidates:
            env_candidates.insert(0, base)
    return env_candidates


def probe_openwebui_runtime():
    tried = []
    for base_url in openwebui_probe_candidates():
        base = str(base_url or "").rstrip("/")
        root = http_probe(base + "/", timeout=2, max_bytes=4096)
        loader = http_probe(base + "/static/loader.js", timeout=2, max_bytes=8192)
        tried.append(
            {
                "base_url": base,
                "root_ok": bool(root.get("ok")),
                "loader_ok": bool(loader.get("ok")),
            }
        )
        if root.get("ok") and loader.get("ok"):
            return {
                "ok": True,
                "base_url": base,
                "root": root,
                "loader": loader,
                "tried": tried,
            }
    root = http_probe(OPENWEBUI_HEALTH_URL, timeout=2, max_bytes=4096)
    loader = http_probe(OPENWEBUI_LOADER_URL, timeout=2, max_bytes=8192)
    return {
        "ok": bool(root.get("ok")) and bool(loader.get("ok")),
        "base_url": "",
        "root": root,
        "loader": loader,
        "tried": tried,
    }

def runtime_health():
    openwebui = probe_openwebui_runtime()
    root = openwebui["root"]
    loader = openwebui["loader"]
    workspaces_file_exists = Path(WORKSPACES_FILE).is_file()
    roadmap_exists = CAPABILITY_ROADMAP_FILE.is_file()
    git_head = run_ro(["git", "rev-parse", "--short", "HEAD"], REPO_ROOT, 8)
    capability_registry_issues = agent_capability_registry_issues()
    local_admin_ready = True
    lan_admin_ready = bool(ADMIN_TOKEN)
    readiness_issues = []
    if not workspaces_file_exists:
        readiness_issues.append("WORKSPACES_FILE_MISSING")
    if not roadmap_exists:
        readiness_issues.append("CAPABILITY_ROADMAP_MISSING")
    if not root.get("ok"):
        readiness_issues.append("OPENWEBUI_ROOT_UNAVAILABLE")
    if not loader.get("ok"):
        readiness_issues.append("OPENWEBUI_LOADER_UNAVAILABLE")
    if not lan_admin_ready:
        readiness_issues.append("GATEWAY_ADMIN_TOKEN_MISSING")
    if capability_registry_issues:
        readiness_issues.append("CAPABILITY_REGISTRY_INVALID")
    codex_local_ready = not readiness_issues
    return {
        "codex_local_ready": codex_local_ready,
        "capability_mode": "agent-first",
        "natural_codex_local_route": "agent_loop",
        "model_runtime": {
            "default_alias": DEFAULT_MODEL_ALIAS,
            "default_model": CODEX_LOCAL_CONFIG.default_model,
            "heavy_alias": "codex-local-heavy",
            "heavy_model": CODEX_LOCAL_CONFIG.heavy_model,
            "model_mode": CODEX_LOCAL_CONFIG.model_mode,
            "allow_heavy_escalation": CODEX_LOCAL_CONFIG.allow_heavy_escalation,
            "structured_output": CODEX_LOCAL_CONFIG.structured_output,
            "structured_backend": CODEX_LOCAL_CONFIG.structured_backend,
            "structured_attempt_timeout": CODEX_LOCAL_CONFIG.structured_attempt_timeout,
            "structured_backend_usable": STRUCTURED_BACKEND_STATE.get("usable"),
            "structured_backend_strategy": STRUCTURED_BACKEND_STATE.get("strategy"),
            "structured_backend_last_error": STRUCTURED_BACKEND_STATE.get("last_error"),
            "experimental_planner_model": CODEX_LOCAL_CONFIG.experimental_planner_model,
        },
        "runtime_repo_root": str(REPO_ROOT),
        "runtime_commit": git_head,
        "gateway_source_epoch": GATEWAY_SOURCE_EPOCH,
        "runtime_fingerprint": runtime_fingerprint(),
        "gateway_admin": {
            "local_admin_ready": local_admin_ready,
            "lan_admin_ready": lan_admin_ready,
            "token_present": bool(ADMIN_TOKEN),
            "token_file_configured": bool(ADMIN_TOKEN_FILE),
        },
        "workspace_registry": {
            "path": WORKSPACES_FILE,
            "exists": workspaces_file_exists,
        },
        "capability_roadmap": {
            "path": str(CAPABILITY_ROADMAP_FILE),
            "exists": roadmap_exists,
        },
        "capability_registry": {
            "implemented": sorted(
                name
                for name, spec in agent_capability_registry().items()
                if isinstance(spec, dict) and spec.get("implemented")
            ),
            "issues": capability_registry_issues,
            "contract_issues": agent_capability_contract_issues(),
        },
        "readiness_issues": readiness_issues,
        "openwebui": {
            "ok": bool(root.get("ok")) and bool(loader.get("ok")),
            "base_url": str(openwebui.get("base_url") or "").strip(),
            "candidates": openwebui_probe_candidates(),
            "tried": openwebui.get("tried") or [],
            "root": root,
            "loader": loader,
        },
    }


def blocked_public_ip(ip):
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def assert_public_web_url(url):
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("WEB_FETCH_UNSAFE_URL: only http and https are allowed")
    if parsed.username or parsed.password:
        raise ValueError("WEB_FETCH_UNSAFE_URL: credentials in URL are not allowed")
    host = (parsed.hostname or "").strip(".").lower()
    if not host:
        raise ValueError("WEB_FETCH_UNSAFE_URL: hostname is required")
    if host in BLOCKED_WEB_HOSTS or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("WEB_FETCH_BLOCKED_HOST: local/internal hostnames are not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"WEB_FETCH_DNS_FAILED: {host}: {exc}") from exc
    if not infos:
        raise ValueError(f"WEB_FETCH_DNS_FAILED: {host}: no addresses")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if blocked_public_ip(ip):
            raise ValueError(f"WEB_FETCH_BLOCKED_HOST: {host} resolved to non-public address {ip}")
    return parsed


def html_title(source):
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", source or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()


def html_to_text(source):
    parser = HTMLTextExtractor()
    try:
        parser.feed(source)
        parser.close()
        text = parser.text()
    except Exception:
        text = re.sub(r"(?is)<(script|style|noscript|svg|canvas)\b.*?</\1>", " ", source)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text


def decode_web_text(raw, content_type):
    charset = "utf-8"
    ctype = content_type or ""
    match = re.search(r"(?i)\bcharset=([A-Za-z0-9_.:-]+)", ctype)
    if match:
        charset = match.group(1)
    decoded = raw.decode(charset, "replace")
    is_html = "html" in ctype.lower() or "<html" in decoded[:2048].lower()
    if is_html:
        return html_to_text(decoded), html_title(decoded)
    if re.search(r"(?i)\b(text/|json|xml|javascript|yaml|csv|markdown)", ctype):
        return decoded.strip(), ""
    return "", ""


def admin_web_fetch(payload):
    url = str(payload.get("url") or "").strip()
    method = str(payload.get("method") or "GET").strip().upper()
    if method not in {"GET", "HEAD"}:
        raise ValueError("WEB_FETCH_UNSAFE_METHOD: only GET and HEAD are allowed")
    max_bytes = clamp_int(payload.get("max_bytes"), WEB_FETCH_DEFAULT_MAX_BYTES, 1_000, WEB_FETCH_HARD_MAX_BYTES)
    timeout = clamp_int(payload.get("timeout"), WEB_FETCH_DEFAULT_TIMEOUT, 1, WEB_FETCH_HARD_TIMEOUT)
    text_limit = clamp_int(payload.get("text_limit"), WEB_FETCH_TEXT_LIMIT, 1_000, WEB_FETCH_TEXT_LIMIT)

    assert_public_web_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CodexLocalGateway/1.0; +https://github.com/Jeycob/Ai-Stack)",
        "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.2",
        "Accept-Language": "cs,en;q=0.8",
    }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirectHandler)
    req = urllib.request.Request(url, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        assert_public_web_url(final_url)
        status = getattr(resp, "status", resp.getcode())
        content_type = resp.headers.get("Content-Type", "")
        raw = b"" if method == "HEAD" else resp.read(max_bytes + 1)

    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    text, title = decode_web_text(raw, content_type)
    text_truncated = len(text) > text_limit
    if text_truncated:
        text = text[:text_limit].rstrip()
    return {
        "ok": True,
        "url": url,
        "final_url": final_url,
        "method": method,
        "status": status,
        "content_type": content_type,
        "bytes_read": len(raw),
        "truncated": truncated,
        "title": title,
        "text": text,
        "text_truncated": text_truncated,
    }


def admin_web_answer(payload):
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValueError("WEB_ANSWER_QUESTION_REQUIRED")
    fetch = admin_web_fetch(payload)
    source = fetch.get("text") or ""
    if not source.strip():
        answer = "Načtený veřejný zdroj neobsahoval čitelný text pro zodpovězení dotazu."
    else:
        messages = [
            {
                "role": "system",
                "content": (
                    "Odpovídej česky. Použij pouze přiložený veřejně stažený text ze zdroje. "
                    "Když odpověď ve zdroji není, řekni to stručně a nehádej."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Dotaz:\n{question}\n\n"
                    f"Zdroj: {fetch.get('final_url') or fetch.get('url')}\n"
                    f"Název: {fetch.get('title') or '(bez titulku)'}\n\n"
                    f"Text zdroje:\n{source[:18_000]}"
                ),
            },
        ]
        response = ollama_chat(codex_local_runtime_model_name(role=ROLE_REVIEWER), messages, timeout=180)
        answer = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not answer:
            answer = "Model nevrátil odpověď nad načteným zdrojem."
    result = dict(fetch)
    result["question"] = question
    result["answer"] = answer
    return result


def agent_direct_answer_response(task, intent_class="direct_answer", conversation_context=""):
    now = time.localtime()
    runtime_context = {
        "local_time": time.strftime("%Y-%m-%d %H:%M:%S %Z", now),
        "local_weekday": time.strftime("%A", now),
        "timezone": time.tzname[0] if time.tzname else "",
        "agent_name": "codex-local",
        "agent_surface": "OpenWebUI",
        "recent_conversation_context": str(conversation_context or "")[-6000:],
        "capability_summary": agent_capability_human_summary(max_items_per_scope=6),
        "implemented_capabilities": sorted(
            name for name, spec in agent_capability_registry().items()
            if isinstance(spec, dict) and spec.get("implemented")
        ),
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Answer the user's ordinary chat request directly and concisely from the supplied runtime context. "
                "Do not inspect repositories, do not mention missing workspace capabilities, and do not invent tool execution. "
                "If the user writes Czech, answer Czech. For date/time questions, derive the answer from runtime_context. "
                "For questions about who you are or what you can do, use runtime_context and the capability summary. "
                "For simple arithmetic, give the result plainly. For creative writing, write the requested text. "
                "For follow-ups, use recent_conversation_context instead of switching to repository work."
            ),
        },
        {
            "role": "user",
            "content": (
                "runtime_context:\n"
                + json.dumps(runtime_context, ensure_ascii=False, indent=2)
                + "\n\nuser_request:\n"
                + str(task or "").strip()
            ),
        },
    ]
    response = ollama_chat(codex_local_runtime_model_name(task=task, role=ROLE_DIRECT), messages, timeout=120)
    answer = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    return {
        "ok": bool(answer),
        "action": intent_class or "direct_answer",
        "answer": answer or "Model nevrátil přímou odpověď.",
        "usage": response.get("usage", {}),
    }


def parse_search_results_from_html(source, limit=5):
    results = []
    for match in re.finditer(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', source or ""):
        href = html.unescape(match.group(1)).strip()
        title = re.sub(r"(?s)<[^>]+>", " ", match.group(2))
        title = re.sub(r"\s+", " ", html.unescape(title)).strip()
        if not href or not title:
            continue
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("/l/?kh=") or "uddg=" in href:
            parsed = urllib.parse.urlparse(href)
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("uddg"):
                href = params["uddg"][0]
        if not href.startswith(("http://", "https://")):
            continue
        try:
            assert_public_web_url(href)
        except Exception:
            continue
        parsed_href = urllib.parse.urlparse(href)
        host = (parsed_href.hostname or "").lower()
        title_lower = title.lower()
        if (
            host.endswith("duckduckgo.com")
            or host.endswith("bing.com")
            or host.endswith("microsoft.com")
        ) and (
            parsed_href.path in {"", "/", "/html/", "/lite/", "/search"}
            or "duckduckgo" in title_lower
            or "bing" in title_lower
        ):
            continue
        if any(item["url"] == href for item in results):
            continue
        results.append({"title": title[:180], "url": href})
        if len(results) >= limit:
            break
    return results


def public_web_search_queries(query):
    base = " ".join(str(query or "").split())
    if not base:
        return []
    queries = [base]
    now = time.localtime()
    queries.append(f"{base} {time.strftime('%Y-%m-%d', now)}")
    queries.append(f"{base} {time.strftime('%B %d %Y', now)}")
    deduped = []
    seen = set()
    for item in queries:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:3]


def public_web_search_urls(query):
    encoded = urllib.parse.quote_plus(query)
    return [
        ("duckduckgo_html", "https://duckduckgo.com/html/?q=" + encoded),
        ("duckduckgo_lite", "https://lite.duckduckgo.com/lite/?q=" + encoded),
        ("bing", "https://www.bing.com/search?q=" + encoded),
    ]


def fetch_public_search_html(url, timeout=20, max_bytes=400_000):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirectHandler)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CodexLocalGateway/1.0)",
            "Accept": "text/html,*/*;q=0.5",
        },
    )
    with opener.open(req, timeout=timeout) as resp:
        return resp.read(max_bytes).decode("utf-8", "replace")


def admin_public_web_search(payload):
    query = str(payload.get("query") or payload.get("question") or "").strip()
    if not query or len(query) > 300:
        raise ValueError("PUBLIC_WEB_SEARCH_QUERY_REQUIRED")
    attempts = []
    results = []
    selected_url = ""
    selected_provider = ""
    for search_query in public_web_search_queries(query):
        for provider, search_url in public_web_search_urls(search_query):
            try:
                raw_html = fetch_public_search_html(search_url, timeout=20)
                parsed_results = parse_search_results_from_html(raw_html, limit=5)
                attempts.append(
                    {
                        "provider": provider,
                        "query": search_query,
                        "url": search_url,
                        "result_count": len(parsed_results),
                    }
                )
                if parsed_results:
                    results = parsed_results
                    selected_url = search_url
                    selected_provider = provider
                    break
            except Exception as exc:
                attempts.append(
                    {
                        "provider": provider,
                        "query": search_query,
                        "url": search_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        if results:
            break
    answer = ""
    if results:
        bullets = "\n".join(f"- {item['title']}: {item['url']}" for item in results)
        answer = f"Našel jsem veřejné výsledky pro `{query}`:\n{bullets}"
    else:
        answer = (
            f"Veřejné vyhledání pro `{query}` nevrátilo parsovatelné výsledky. "
            "Nechci jako výsledek vracet homepage vyhledávače."
        )
    return {
        "ok": bool(results),
        "action": "public_web_search",
        "query": query,
        "search_url": selected_url or (attempts[0]["url"] if attempts else ""),
        "provider": selected_provider,
        "attempts": attempts,
        "results": results,
        "answer": answer,
        "recovery": "" if results else "Zkontroluj outbound HTTP z gateway runtime nebo nastav alternativní veřejný search provider.",
    }


AGENT_LOOP_WORKFLOWS = {
    "direct_answer",
    "meta",
    "workspace_search",
    "review",
    "edit",
    "action",
    "run",
    "autopilot",
    "bootstrap",
    "workspace_git_publish",
    "ssh_key_create",
    "ssh_key_show_public",
    "web_answer",
    "web_fetch",
    "web_search",
    "self_improve",
    "deploy",
    "clarify",
}
AGENT_LOOP_ACTIONS = {"install", "verify", "smoke", "test", "build", "lint"}
AGENT_CAPABILITY_TO_WORKFLOW = {
    "direct_answer": "direct_answer",
    "creative_answer": "direct_answer",
    "workspace_context_set": "meta",
    "workspace_context_status": "meta",
    "capability_catalog_show": "meta",
    "agent_runtime_status": "meta",
    "review": "review",
    "workspace_search": "workspace_search",
    "workspace_edit": "edit",
    "edit": "edit",
    "workspace_run": "run",
    "run": "run",
    "workspace_autopilot": "autopilot",
    "autopilot": "autopilot",
    "workspace_repo_bootstrap": "bootstrap",
    "bootstrap": "bootstrap",
    "workspace_git_publish": "workspace_git_publish",
    "ssh_key_create": "ssh_key_create",
    "ssh_key_show_public": "ssh_key_show_public",
    "public_web_access": "web_answer",
    "public_web_search": "web_search",
    "web_answer": "web_answer",
    "web_fetch": "web_fetch",
    "agent_self_improve": "self_improve",
    "agent_capability_develop": "self_improve",
    "capability_implement": "self_improve",
    "self_improve": "self_improve",
    "stack_deploy": "deploy",
    "deploy": "deploy",
    "workspace_action_chain": "autopilot",
    "workspace_expose_preview": "autopilot",
    "await_user_confirmation": "clarify",
    "clarify_or_infer_capability": "clarify",
    "clarify": "clarify",
}
CORE_AGENT_CAPABILITIES = {
    "direct_answer": {
        "workflow": "direct_answer",
        "summary": "Answer ordinary non-workspace questions directly without repository review or tool execution.",
        "scope": "conversation",
        "implemented": True,
    },
    "creative_answer": {
        "workflow": "direct_answer",
        "summary": "Generate creative prose directly without treating it as repository work.",
        "scope": "conversation",
        "implemented": True,
    },
    "workspace_context_set": {
        "workflow": "meta",
        "summary": "Deterministically acknowledge or report requested workspace context.",
        "scope": "agent_metadata",
        "implemented": True,
    },
    "workspace_context_status": {
        "workflow": "meta",
        "summary": "Deterministically report the current resolved workspace context.",
        "scope": "agent_metadata",
        "implemented": True,
    },
    "capability_catalog_show": {
        "workflow": "meta",
        "summary": "Deterministically show the implemented capability catalog.",
        "scope": "agent_metadata",
        "implemented": True,
    },
    "agent_runtime_status": {
        "workflow": "meta",
        "summary": "Deterministically show codex-local runtime status.",
        "scope": "agent_metadata",
        "implemented": True,
    },
    "review": {
        "workflow": "review",
        "summary": "Read-only analysis over a repository snapshot; never edits.",
        "scope": "workspace_snapshot",
        "implemented": True,
    },
    "workspace_search": {
        "workflow": "workspace_search",
        "summary": "Bounded search over a registered workspace, using rg when available and Python fallback otherwise; returns matching files and lines.",
        "scope": "workspace_snapshot",
        "implemented": True,
    },
    "workspace_edit": {
        "workflow": "edit",
        "summary": "Audited workspace edit through unified diff application.",
        "scope": "workspace_repo",
        "implemented": True,
    },
    "edit": {
        "workflow": "edit",
        "summary": "Alias for audited workspace edit.",
        "scope": "workspace_repo",
        "implemented": True,
    },
    "workspace_run": {
        "workflow": "run",
        "summary": "Run one explicit short command inside the workspace runtime.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "run": {
        "workflow": "run",
        "summary": "Alias for explicit workspace command execution.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "workspace_autopilot": {
        "workflow": "autopilot",
        "summary": "Audited verify/recovery loop over bounded workspace actions.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "autopilot": {
        "workflow": "autopilot",
        "summary": "Alias for audited workspace autopilot.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "bootstrap": {
        "workflow": "bootstrap",
        "summary": "Alias for audited repository/workspace bootstrap.",
        "scope": "workspace_bootstrap",
        "implemented": True,
    },
    "workspace_repo_bootstrap": {
        "workflow": "bootstrap",
        "summary": "Audited repository/workspace bootstrap.",
        "scope": "workspace_bootstrap",
        "implemented": True,
    },
    "workspace_git_publish": {
        "workflow": "workspace_git_publish",
        "summary": "Existing workspace git init/origin/commit/push using workspace SSH key.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "ssh_key_create": {
        "workflow": "ssh_key_create",
        "summary": "Alias for idempotent workspace SSH key creation.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "ssh_key_show_public": {
        "workflow": "ssh_key_show_public",
        "summary": "Alias for returning the workspace SSH public key.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "public_web_access": {
        "workflow": "web_answer",
        "summary": "Public HTTP/HTTPS fetch and answer from downloaded content.",
        "scope": "public_web",
        "implemented": True,
    },
    "public_web_search": {
        "workflow": "web_search",
        "summary": "Bounded public web search for a query when no concrete URL was provided.",
        "scope": "public_web",
        "implemented": True,
    },
    "web_answer": {
        "workflow": "web_answer",
        "summary": "Alias for public web answer capability.",
        "scope": "public_web",
        "implemented": True,
    },
    "web_fetch": {
        "workflow": "web_fetch",
        "summary": "Alias for public web fetch capability.",
        "scope": "public_web",
        "implemented": True,
    },
    "agent_self_improve": {
        "workflow": "self_improve",
        "summary": "Audited routine for collecting OpenWebUI failures, creating regression artifacts, verifying, and preparing deploy/E2E.",
        "scope": "stack_runtime",
        "implemented": True,
    },
    "agent_capability_develop": {
        "workflow": "self_improve",
        "summary": "Audited routine for designing a new codex-local capability with registry, executor, roadmap, tests, and guarded patch proposal.",
        "scope": "stack_runtime",
        "implemented": True,
    },
    "self_improve": {
        "workflow": "self_improve",
        "summary": "Alias for agent_self_improve.",
        "scope": "stack_runtime",
        "implemented": True,
    },
    "deploy": {
        "workflow": "deploy",
        "summary": "Alias for ai-stack deploy flow.",
        "scope": "stack_runtime",
        "implemented": True,
    },
    "workspace_action_chain": {
        "workflow": "autopilot",
        "summary": "Plan and run a bounded install/build/test/smoke style workspace action chain.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "workspace_expose_preview": {
        "workflow": "autopilot",
        "summary": "Prepare a safe local app preview/expose step through the existing workspace runtime.",
        "scope": "workspace_runtime",
        "implemented": True,
    },
    "await_user_confirmation": {
        "workflow": "clarify",
        "summary": "Stop before push/deploy or another checkpoint and ask for explicit user confirmation.",
        "scope": "mentoring",
        "implemented": True,
    },
    "stack_deploy": {
        "workflow": "deploy",
        "summary": "Audited ai-stack deploy/restart flow.",
        "scope": "stack_runtime",
        "implemented": True,
    },
    "clarify": {
        "workflow": "clarify",
        "summary": "Return a precise missing-input or missing-capability recovery step.",
        "scope": "mentoring",
        "implemented": True,
    },
    "clarify_or_infer_capability": {
        "workflow": "clarify",
        "summary": "Planner fallback marker for asking the capability selector or returning a precise recovery step.",
        "scope": "mentoring",
        "implemented": True,
    },
}

CANONICAL_AGENT_CAPABILITY_ALIASES = {
    "answer": "direct_answer",
    "direct": "direct_answer",
    "direct_answer": "direct_answer",
    "ordinary_answer": "direct_answer",
    "chat_answer": "direct_answer",
    "creative": "creative_answer",
    "creative_answer": "creative_answer",
    "story": "creative_answer",
    "write_story": "creative_answer",
    "analyze": "review",
    "analysis": "review",
    "audit": "review",
    "read_only": "review",
    "read_only_review": "review",
    "review": "review",
    "workspace_review": "review",
    "capabilities": "capability_catalog_show",
    "capability": "capability_catalog_show",
    "capability_catalog": "capability_catalog_show",
    "capability_catalog_show": "capability_catalog_show",
    "capability_registry": "capability_catalog_show",
    "capability_status": "capability_catalog_show",
    "capability_list": "capability_catalog_show",
    "show_capabilities": "capability_catalog_show",
    "list_capabilities": "capability_catalog_show",
    "create_repo": "workspace_repo_bootstrap",
    "create_repository": "workspace_repo_bootstrap",
    "create_workspace": "workspace_repo_bootstrap",
    "new_repo": "workspace_repo_bootstrap",
    "new_repository": "workspace_repo_bootstrap",
    "new_workspace": "workspace_repo_bootstrap",
    "repo_bootstrap": "workspace_repo_bootstrap",
    "repository_bootstrap": "workspace_repo_bootstrap",
    "bootstrap": "workspace_repo_bootstrap",
    "git_publish": "workspace_git_publish",
    "git_push": "workspace_git_publish",
    "github_push": "workspace_git_publish",
    "publish": "workspace_git_publish",
    "push": "workspace_git_publish",
    "remote_push": "workspace_git_publish",
    "ssh": "ssh_key_create",
    "ssh_key": "ssh_key_create",
    "ssh_key_create": "ssh_key_create",
    "ssh_keygen": "ssh_key_create",
    "github_ssh_key": "ssh_key_create",
    "create_ssh_key": "ssh_key_create",
    "generate_ssh_key": "ssh_key_create",
    "workspace_ssh": "ssh_key_create",
    "workspace_ssh_key": "ssh_key_create",
    "workspace_ssh_key_create": "ssh_key_create",
    "public_key": "ssh_key_show_public",
    "ssh_public_key": "ssh_key_show_public",
    "public_ssh_key": "ssh_key_show_public",
    "show_public_key": "ssh_key_show_public",
    "return_public_key": "ssh_key_show_public",
    "workspace_public_key": "ssh_key_show_public",
    "workspace_ssh_key_show_public": "ssh_key_show_public",
    "ssh_key_show_public": "ssh_key_show_public",
    "search": "workspace_search",
    "repo_search": "workspace_search",
    "repository_search": "workspace_search",
    "workspace_search": "workspace_search",
    "search_workspace": "workspace_search",
    "grep": "workspace_search",
    "rg": "workspace_search",
    "internet": "public_web_access",
    "internet_access": "public_web_access",
    "public_web": "public_web_access",
    "web": "public_web_access",
    "web_access": "public_web_access",
    "web_answer": "public_web_access",
    "web_fetch": "public_web_access",
    "fetch_web": "public_web_access",
    "download_web": "public_web_access",
    "public_web_search": "public_web_search",
    "web_search": "public_web_search",
    "internet_search": "public_web_search",
    "search_web": "public_web_search",
    "search_internet": "public_web_search",
    "agent_self_improve": "agent_self_improve",
    "agent_capability_develop": "agent_capability_develop",
    "capability_develop": "agent_capability_develop",
    "capability_implement": "agent_capability_develop",
    "implement_capability": "agent_capability_develop",
    "develop_capability": "agent_capability_develop",
    "new_capability": "agent_capability_develop",
    "self_improve": "agent_self_improve",
    "self_improvement": "agent_self_improve",
    "improve_agent": "agent_self_improve",
    "codex_self_improve": "agent_self_improve",
    "deploy": "stack_deploy",
    "stack_deploy": "stack_deploy",
    "restart_stack": "stack_deploy",
    "agent_runtime_status": "agent_runtime_status",
    "runtime_status": "agent_runtime_status",
    "system_status": "agent_runtime_status",
    "status": "agent_runtime_status",
    "workspace_context_set": "workspace_context_set",
    "context_set": "workspace_context_set",
    "workspace_set": "workspace_context_set",
    "switch_workspace": "workspace_context_set",
    "set_workspace": "workspace_context_set",
    "change_workspace": "workspace_context_set",
    "workspace_context_status": "workspace_context_status",
    "context_status": "workspace_context_status",
    "workspace_status": "workspace_context_status",
    "workspace_info": "workspace_context_status",
    "current_workspace": "workspace_context_status",
    "workspace_list": "workspace_context_status",
    "workspaces": "workspace_context_status",
    "list_workspaces": "workspace_context_status",
    "workspace_edit": "workspace_edit",
    "edit": "workspace_edit",
    "workspace_run": "workspace_run",
    "run": "workspace_run",
    "command": "workspace_run",
    "shell": "workspace_run",
    "workspace_autopilot": "workspace_autopilot",
    "autopilot": "workspace_autopilot",
    "workspace_action_chain": "workspace_action_chain",
    "action_chain": "workspace_action_chain",
    "install_build_test": "workspace_action_chain",
    "workspace_expose_preview": "workspace_expose_preview",
    "expose_preview": "workspace_expose_preview",
    "preview": "workspace_expose_preview",
    "await_user_confirmation": "await_user_confirmation",
    "user_confirmation": "await_user_confirmation",
    "confirm_before_continue": "await_user_confirmation",
    "clarify": "clarify_or_infer_capability",
    "clarify_or_infer_capability": "clarify_or_infer_capability",
}

WORKSPACE_ACTION_ALIASES = {
    "dependency_install": "install",
    "dependencies": "install",
    "install": "install",
    "setup": "install",
    "verify": "verify",
    "check": "verify",
    "smoke": "smoke",
    "run_app": "smoke",
    "test": "test",
    "tests": "test",
    "run_tests": "test",
    "build": "build",
    "compile": "build",
    "lint": "lint",
}


def capability_key(value):
    key = str(value or "").strip().lower()
    key = re.sub(r"[\s./-]+", "_", key)
    key = re.sub(r"[^a-z0-9_:]+", "", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return key


def canonicalize_workspace_action(action):
    key = capability_key(action)
    return WORKSPACE_ACTION_ALIASES.get(key, key)


def canonicalize_agent_capability(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = capability_key(raw)
    alias = CANONICAL_AGENT_CAPABILITY_ALIASES.get(key) or load_dynamic_capability_aliases().get(key)
    if alias:
        return alias
    if key.startswith("workspace_action:"):
        action = canonicalize_workspace_action(key.split(":", 1)[1])
        return f"workspace_action:{action}" if action else ""
    if key.startswith("workspace_action_"):
        action = canonicalize_workspace_action(key.removeprefix("workspace_action_"))
        return f"workspace_action:{action}" if action else ""
    return raw


def canonicalize_agent_capabilities(capabilities):
    out = []
    seen = set()
    for item in capabilities or []:
        capability = canonicalize_agent_capability(item)
        if not capability or capability in seen:
            continue
        seen.add(capability)
        out.append(capability)
    return out


def extract_json_object(text):
    source = str(text or "").strip()
    if not source:
        raise ValueError("AGENT_PLAN_EMPTY")
    fenced = re.search(r"(?is)```(?:json)?\s*(\{.*?\})\s*```", source)
    if fenced:
        source = fenced.group(1).strip()
    start = source.find("{")
    if start < 0:
        raise ValueError("AGENT_PLAN_JSON_NOT_FOUND")
    depth = 0
    in_string = False
    escaped = False
    for idx, ch in enumerate(source[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(source[start : idx + 1])
    raise ValueError("AGENT_PLAN_JSON_UNCLOSED")


def codex_local_model_runtime(requested_model_name="", *, task="", role=ROLE_AGENT):
    return resolve_runtime_model(
        requested_model_name or DEFAULT_MODEL_ALIAS,
        task=task,
        role=role,
        config=CODEX_LOCAL_CONFIG,
    )


def codex_local_runtime_model_name(requested_model_name="", *, task="", role=ROLE_AGENT):
    runtime = codex_local_model_runtime(requested_model_name, task=task, role=role)
    return str(runtime.get("model") or CODEX_LOCAL_CONFIG.default_model)


def codex_local_runtime_surface(requested_model_name="", *, task="", role=ROLE_AGENT):
    runtime = codex_local_model_runtime(requested_model_name, task=task, role=role)
    return {
        "requested_model": str(runtime.get("requested_model") or ""),
        "resolved_alias": str(runtime.get("resolved_alias") or ""),
        "role": str(runtime.get("role") or role),
        "model": str(runtime.get("model") or ""),
        "heavy_requested": bool(runtime.get("heavy_requested")),
        "heavy_available": bool(runtime.get("heavy_available")),
        "used_experimental_planner": bool(runtime.get("used_experimental_planner")),
        "structured_output": CODEX_LOCAL_CONFIG.structured_output,
        "structured_backend": CODEX_LOCAL_CONFIG.structured_backend,
        "structured_attempt_timeout": CODEX_LOCAL_CONFIG.structured_attempt_timeout,
        "structured_backend_usable": STRUCTURED_BACKEND_STATE.get("usable"),
        "structured_backend_strategy": STRUCTURED_BACKEND_STATE.get("strategy"),
        "structured_backend_last_error": STRUCTURED_BACKEND_STATE.get("last_error"),
    }


def codex_local_structured_response_format(schema_name, schema):
    backend = CODEX_LOCAL_CONFIG.structured_backend
    mode = CODEX_LOCAL_CONFIG.structured_output
    if mode == "none" or backend == "none":
        return None
    if mode == "auto" and backend == "auto" and STRUCTURED_BACKEND_STATE.get("usable") is False:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
        },
    }


def structured_json_repair_messages(raw_text, schema_name, schema):
    return [
        {
            "role": "system",
            "content": (
                "Repair the assistant output into valid JSON only. "
                "Do not add commentary. Preserve intent, drop unsupported fields, "
                "and fit the target schema as closely as possible."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Schema name: {schema_name}\n"
                f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                f"Broken output:\n{raw_text}"
            ),
        },
    ]


def reset_structured_backend_state():
    STRUCTURED_BACKEND_STATE.update({
        "usable": None,
        "strategy": "unprobed",
        "last_error": "",
        "last_schema": "",
        "last_checked": 0.0,
    })


def mark_structured_backend(ok, *, schema_name="", error=""):
    STRUCTURED_BACKEND_STATE.update({
        "usable": bool(ok),
        "strategy": "json_schema" if ok else "plain_json_fallback",
        "last_error": str(error or "")[:500],
        "last_schema": str(schema_name or "")[:120],
        "last_checked": time.time(),
    })


def structured_json_chat(model_id, messages, schema_name, schema, timeout=240):
    attempts = []
    response_format = codex_local_structured_response_format(schema_name, schema)
    if CODEX_LOCAL_CONFIG.structured_output == "auto" and response_format:
        try:
            structured_timeout = max(1, min(int(timeout), int(CODEX_LOCAL_CONFIG.structured_attempt_timeout)))
            response = ollama_chat(
                model_id,
                messages,
                timeout=structured_timeout,
                response_format=response_format,
            )
            raw = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            attempts.append({"stage": "structured", "ok": True, "timeout": structured_timeout})
            mark_structured_backend(True, schema_name=schema_name)
            return extract_json_object(raw), raw, {"strategy": "structured", "attempts": attempts}
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            attempts.append({
                "stage": "structured",
                "ok": False,
                "timeout": max(1, min(int(timeout), int(CODEX_LOCAL_CONFIG.structured_attempt_timeout))),
                "error": error,
            })
            mark_structured_backend(False, schema_name=schema_name, error=error)
    response = ollama_chat(model_id, messages, timeout=timeout)
    raw = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    try:
        parsed = extract_json_object(raw)
        attempts.append({"stage": "plain_json", "ok": True})
        return parsed, raw, {"strategy": "plain_json", "attempts": attempts}
    except Exception as exc:
        attempts.append({"stage": "plain_json", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    repair_response = ollama_chat(
        model_id,
        structured_json_repair_messages(raw, schema_name, schema),
        timeout=timeout,
    )
    repaired_raw = repair_response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    repaired = extract_json_object(repaired_raw)
    attempts.append({"stage": "repair_retry", "ok": True})
    return repaired, repaired_raw, {"strategy": "repair_retry", "attempts": attempts}


def agent_read_only_requested(task):
    lower = str(task or "").lower()
    cues = (
        "nic needituj",
        "bez editace",
        "jen analyzuj",
        "jen analysis",
        "jen popis",
        "jen vysvetli",
        "jen vysvětli",
        "jen rekni",
        "jen řekni",
        "odpovez pouze",
        "odpověz pouze",
        "answer only",
        "respond only",
        "read only",
        "readonly",
        "do not edit",
        "don't edit",
    )
    return any(cue in lower for cue in cues)


def agent_extract_repo_name(task):
    return bootstrap_repo_name_from_text(task) or infer_repo_name_from_text(task)


def agent_controller_workspace(requested_workspace):
    default, workspaces = load_registry()
    if requested_workspace in workspaces:
        return requested_workspace, True, workspaces
    if "ai-stack" in workspaces:
        return "ai-stack", False, workspaces
    return default, False, workspaces


def agent_capability_registry():
    registry = {name: dict(spec) for name, spec in CORE_AGENT_CAPABILITIES.items()}

    for name, spec in load_capability_roadmap_registry().items():
        capability = canonicalize_agent_capability(name)
        if not capability:
            continue
        entry = registry.setdefault(capability, {})
        workflow = str(spec.get("workflow") or entry.get("workflow") or AGENT_CAPABILITY_TO_WORKFLOW.get(capability, "clarify")).strip()
        entry["workflow"] = workflow or "clarify"
        entry["summary"] = str(spec.get("summary") or entry.get("summary") or "").strip()
        entry["scope"] = str(spec.get("scope") or entry.get("scope") or "").strip()
        entry["implemented"] = bool(spec.get("implemented", True))
        aliases = canonicalize_agent_capabilities(spec.get("aliases") or [])
        if aliases:
            entry["aliases"] = aliases
        planned_workflow = str(spec.get("planned_workflow") or "").strip()
        if planned_workflow:
            entry["planned_workflow"] = planned_workflow
        executor = str(spec.get("executor") or "").strip()
        if executor:
            entry["executor"] = executor
        tests = [str(item).strip() for item in (spec.get("tests") or []) if str(item).strip()]
        if tests:
            entry["tests"] = tests
        if spec.get("draft") is not None:
            entry["draft"] = bool(spec.get("draft"))

    for action, spec in load_workspace_action_registry().items():
        capability = f"workspace_action:{str(action).strip().lower()}"
        registry[capability] = {
            "workflow": "action",
            "action": str(action).strip().lower(),
            "summary": str(spec.get("summary") or "Audited workspace action").strip(),
            "scope": "workspace_runtime",
            "implemented": True,
        }
    return registry


def agent_capability_registry_issues():
    registry = agent_capability_registry()
    issues = []
    required = {
        "review",
        "workspace_search",
        "workspace_repo_bootstrap",
        "workspace_git_publish",
        "ssh_key_create",
        "ssh_key_show_public",
        "public_web_access",
        "stack_deploy",
        "workspace_context_set",
        "workspace_context_status",
        "capability_catalog_show",
        "agent_runtime_status",
        "agent_self_improve",
        "agent_capability_develop",
    }
    for capability in sorted(required):
        entry = registry.get(capability)
        if not entry:
            issues.append(f"missing:{capability}")
        elif not entry.get("implemented"):
            issues.append(f"not_implemented:{capability}")
    for alias, target in sorted(CANONICAL_AGENT_CAPABILITY_ALIASES.items()):
        canonical = canonicalize_agent_capability(alias)
        if canonical != target:
            issues.append(f"alias_drift:{alias}->{canonical}!={target}")
        entry = registry.get(target)
        if not entry:
            issues.append(f"alias_target_missing:{alias}->{target}")
        elif not entry.get("implemented") and target != "clarify_or_infer_capability":
            issues.append(f"alias_target_not_implemented:{alias}->{target}")
    for action in sorted(AGENT_LOOP_ACTIONS):
        capability = f"workspace_action:{action}"
        entry = registry.get(capability)
        if not entry:
            issues.append(f"missing_action:{capability}")
        elif not entry.get("implemented"):
            issues.append(f"not_implemented_action:{capability}")
    issues.extend(agent_capability_contract_issues(registry))
    return issues


def agent_capability_input_schema(capability, entry):
    workflow = str(entry.get("workflow") or AGENT_CAPABILITY_TO_WORKFLOW.get(capability, "clarify")).strip()
    action = str(entry.get("action") or "").strip()
    if capability.startswith("workspace_action:"):
        action = capability.split(":", 1)[1]
        workflow = "action"
    common_workspace = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "workspace": {"type": "string"},
            "task": {"type": "string"},
        },
        "required": ["workspace"],
    }
    by_workflow = {
        "meta": {"type": "object", "properties": {"workspace": {"type": "string"}}, "required": ["workspace"], "additionalProperties": True},
        "review": common_workspace,
        "workspace_search": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"workspace": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["workspace", "query"],
        },
        "edit": common_workspace,
        "run": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"workspace": {"type": "string"}, "command": {"type": "array", "items": {"type": "string"}}},
            "required": ["workspace", "command"],
        },
        "action": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"workspace": {"type": "string"}, "action": {"type": "string", "default": action}, "task": {"type": "string"}},
            "required": ["workspace", "action"],
        },
        "autopilot": common_workspace,
        "bootstrap": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"repo_name": {"type": "string"}, "target_repo_name": {"type": "string"}, "followup_actions": {"type": "array", "items": {"type": "string"}}},
            "required": ["repo_name"],
        },
        "workspace_git_publish": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"workspace": {"type": "string"}, "remote_url": {"type": "string"}, "branch": {"type": "string"}},
            "required": ["workspace", "remote_url"],
        },
        "ssh_key_create": common_workspace,
        "ssh_key_show_public": common_workspace,
        "web_search": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
        "web_fetch": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"url": {"type": "string"}, "question": {"type": "string"}},
            "required": ["url"],
        },
        "web_answer": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"url": {"type": "string"}, "question": {"type": "string"}},
            "required": ["url", "question"],
        },
        "direct_answer": {
            "type": "object",
            "additionalProperties": True,
            "properties": {"task": {"type": "string"}, "conversation_context": {"type": "string"}},
            "required": ["task"],
        },
        "self_improve": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "workspace": {"type": "string"},
                "chat_url": {"type": "string"},
                "chat_id": {"type": "string"},
                "mode": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["workspace"],
        },
        "deploy": {"type": "object", "additionalProperties": True, "properties": {"workspace": {"type": "string"}}, "required": []},
        "clarify": {"type": "object", "additionalProperties": True, "properties": {"missing_inputs": {"type": "array", "items": {"type": "string"}}}, "required": []},
    }
    return dict(entry.get("input_schema") or by_workflow.get(workflow) or {"type": "object", "additionalProperties": True, "required": []})


def agent_capability_output_schema(capability, entry):
    workflow = str(entry.get("workflow") or AGENT_CAPABILITY_TO_WORKFLOW.get(capability, "clarify")).strip()
    base = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "ok": {"type": "boolean"},
            "workflow": {"type": "string", "default": workflow},
            "summary": {"type": "string"},
            "answer": {"type": "string"},
            "recovery": {"type": ["object", "string"]},
        },
        "required": ["ok"],
    }
    return dict(entry.get("output_schema") or base)


def agent_capability_safety_profile(capability, entry):
    scope = str(entry.get("scope") or "mentoring").strip()
    risk_by_scope = {
        "conversation": "low",
        "agent_metadata": "low",
        "workspace_snapshot": "low",
        "public_web": "low",
        "mentoring": "low",
        "workspace_repo": "medium",
        "workspace_runtime": "medium",
        "workspace_bootstrap": "medium",
        "remote_repo": "high",
        "host_runtime": "high",
        "stack_runtime": "high",
    }
    profile = dict(entry.get("safety_profile") or {})
    profile.setdefault("scope", scope)
    profile.setdefault("risk_level", risk_by_scope.get(scope, "medium"))
    profile.setdefault("secrets_policy", "never read or return private keys, tokens, .env, or codex/state secrets")
    profile.setdefault("runner_boundary", "workspace/container first; host runtime only through explicit audited capability")
    return profile


def agent_capability_contract(capability, registry=None):
    registry = registry or agent_capability_registry()
    canonical = canonicalize_agent_capability(capability)
    entry = dict(registry.get(canonical) or {})
    workflow = str(entry.get("workflow") or AGENT_CAPABILITY_TO_WORKFLOW.get(canonical, "clarify")).strip() or "clarify"
    executor = str(entry.get("executor") or "").strip()
    if not executor:
        executor = f"workspace_action:{entry.get('action')}" if canonical.startswith("workspace_action:") else f"workflow:{workflow}"
    tests = [str(item).strip() for item in (entry.get("tests") or []) if str(item).strip()]
    if not tests:
        tests = ["python3 codex/bin/gateway_recovery_smoke.py"]
        if workflow in {"self_improve"}:
            tests.append("python3 codex/bin/agent_self_improve_smoke.py")
        if workflow in {"action", "autopilot", "run", "workspace_git_publish"}:
            tests.append("python3 codex/bin/workspace_context_regression_smoke.py")
    return {
        "capability_id": canonical,
        "workflow": workflow,
        "summary": str(entry.get("summary") or "").strip(),
        "scope": str(entry.get("scope") or "").strip(),
        "implemented": bool(entry.get("implemented")),
        "input_schema": agent_capability_input_schema(canonical, entry),
        "output_schema": agent_capability_output_schema(canonical, entry),
        "safety_profile": agent_capability_safety_profile(canonical, entry),
        "executor": executor,
        "recovery": str(entry.get("recovery") or entry.get("recovery_hint") or "Return a precise blocker, evidence, and next safe recovery step.").strip(),
        "tests": tests,
    }


def agent_capability_contract_issues(registry=None):
    registry = registry or agent_capability_registry()
    issues = []
    required_keys = {"capability_id", "workflow", "input_schema", "output_schema", "safety_profile", "executor", "recovery", "tests"}
    for capability, entry in sorted(registry.items()):
        if not isinstance(entry, dict) or not entry.get("implemented"):
            continue
        contract = agent_capability_contract(capability, registry)
        missing = sorted(key for key in required_keys if not contract.get(key))
        for key in missing:
            issues.append(f"contract_missing:{capability}:{key}")
        for key in ("input_schema", "output_schema", "safety_profile"):
            if not isinstance(contract.get(key), dict):
                issues.append(f"contract_invalid:{capability}:{key}")
        if not isinstance(contract.get("tests"), list) or not contract.get("tests"):
            issues.append(f"contract_invalid:{capability}:tests")
    return issues


def agent_capability_catalog():
    capability_registry = agent_capability_registry()
    lines = [
        "- workspace_context_set: deterministically acknowledge or switch the resolved workspace context",
        "- workspace_context_status: deterministically report current workspace, path, and registered workspaces",
        "- capability_catalog_show: show implemented capabilities and canonical aliases",
        "- agent_runtime_status: show codex-local runtime/readiness status",
        "- review: read-only analysis over repository snapshot; never edits",
        "- workspace_search: bounded rg search over the workspace; returns matching files and lines",
        "- edit: safe repository edit through audited unified diff application; optional verify/test/build/smoke follow-up",
        "- action: one audited workspace action from {install, verify, smoke, test, build, lint}",
        "- run: execute one explicit short command inside codex-opencode-<workspace> and return output",
        "- autopilot: recovery/verify loop over install/verify/smoke/test/build/lint",
        "- bootstrap: create local repository/workspace, init git, generate SSH key, optionally continue with follow-up actions",
        "- workspace_git_publish: operate inside an existing workspace, ensure git init/origin/commit/push using the workspace SSH key",
        "- ssh_key_create: create or reuse the workspace SSH key idempotently and return the public key path",
        "- ssh_key_show_public: return the workspace SSH public key; if missing, create it idempotently first",
        "- web_answer: answer a question from a public HTTP/HTTPS source",
        "- web_fetch: fetch text from a public HTTP/HTTPS source",
        "- agent_self_improve: collect a chat failure, write diagnosis/regression artifacts, run smoke checks, and prepare deploy/E2E",
        "- deploy: ai-stack deploy/restart flow",
        "- clarify: ask for one missing piece of information instead of pretending to execute",
        "",
        "Workspace actions:",
    ]
    for action in ("install", "verify", "smoke", "test", "build", "lint"):
        spec = capability_registry.get(f"workspace_action:{action}") or {}
        summary = str(spec.get("summary", "")).strip()
        lines.append(f"- {action}: {summary or 'audited workspace action'}")
    draft_lines = []
    for name in sorted(capability_registry):
        spec = capability_registry.get(name) or {}
        if spec.get("implemented"):
            continue
        summary = str(spec.get("summary") or "").strip() or "planned capability draft"
        planned_workflow = str(spec.get("planned_workflow") or spec.get("workflow") or "clarify").strip()
        aliases = ", ".join(spec.get("aliases") or [])
        suffix = f" (planned workflow: {planned_workflow})"
        if aliases:
            suffix += f" aliases: {aliases}"
        draft_lines.append(f"- {name}: {summary}{suffix}")
    if draft_lines:
        lines.extend(["", "Planned capabilities:"])
        lines.extend(draft_lines)
    return "\n".join(lines)


def agent_capability_human_summary(max_items_per_scope=4):
    registry = agent_capability_registry()
    scope_order = [
        ("conversation", "Bezny chat"),
        ("public_web", "Web"),
        ("workspace_runtime", "Workspace"),
        ("workspace_bootstrap", "Bootstrap"),
        ("stack_runtime", "Self-improve/deploy"),
        ("agent_metadata", "Meta"),
    ]
    grouped = {scope: [] for scope, _label in scope_order}
    extras = []
    for name, spec in sorted(registry.items()):
        if not isinstance(spec, dict) or not spec.get("implemented"):
            continue
        scope = str(spec.get("scope") or "").strip()
        if scope in grouped:
            grouped[scope].append(name)
        else:
            extras.append(name)

    lines = []
    for scope, label in scope_order:
        items = grouped.get(scope) or []
        if not items:
            continue
        preview = ", ".join(f"`{item}`" for item in items[:max_items_per_scope])
        if len(items) > max_items_per_scope:
            preview += f" +{len(items) - max_items_per_scope}"
        lines.append(f"{label}: {preview}")
    if extras:
        preview = ", ".join(f"`{item}`" for item in extras[:max_items_per_scope])
        if len(extras) > max_items_per_scope:
            preview += f" +{len(extras) - max_items_per_scope}"
        lines.append(f"Dalsi: {preview}")
    return " | ".join(lines)


def agent_infer_action_from_task(task):
    """TaskSpec-only compatibility hook; gateway core does not infer actions from prose."""
    return ""


def agent_infer_followup_actions(task):
    """TaskSpec-only compatibility hook; follow-up actions must come from planner output."""
    return []


def agent_edit_requested(task):
    """TaskSpec-only compatibility hook; edit intent must come from capability selection."""
    return False


def agent_bootstrap_requested(task):
    """TaskSpec-only compatibility hook; bootstrap intent must come from TaskSpec."""
    return False


def agent_ssh_key_show_public_requested(task):
    """TaskSpec-only compatibility hook; public-key intent must come from capabilities."""
    return False


def agent_ssh_key_create_requested(task):
    """TaskSpec-only compatibility hook; SSH-key intent must come from capabilities."""
    return False


def normalize_capability_identifier(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.strip("`'\"")
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)
    text = text.strip("._:-")
    text = re.sub(r"_+", "_", text)
    if not text:
        return ""
    if not re.match(r"^[A-Za-z][A-Za-z0-9_.:-]{1,79}$", text):
        return ""
    return text.replace("-", "_")


def agent_target_capability_name_from_task(task):
    """Extract only explicit structured capability identifiers.

    Natural-language requests such as "přidej capability X" belong to the
    TaskSpec planner. This helper is a guard for already-structured fields
    typed into chat or passed by admin tooling.
    """
    text = strip_routing(str(task or "")).strip()
    if not text:
        return ""
    patterns = (
        r"(?i)\btarget_capability_name\s*[:=]\s*[`'\"]?([A-Za-z][A-Za-z0-9_.:-]{1,79})[`'\"]?",
        r"(?i)\b(?:capability|capabilities|feature)\s*[:=]\s*[`'\"]?([A-Za-z][A-Za-z0-9_.:-]{1,79})[`'\"]?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        normalized = normalize_capability_identifier(match.group(1))
        if normalized:
            return normalized
    return ""


def agent_capability_develop_requested(task):
    """TaskSpec-only compatibility hook; capability-development intent belongs to TaskSpec."""
    if agent_target_capability_name_from_task(task):
        return True
    return False


def agent_workspace_ssh_comment(task, workspace):
    text = str(task or "")
    match = re.search(r'(?i)\b-C\s+["\']?([^"\']{1,160})["\']?', text)
    if match:
        return match.group(1).strip()
    email = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.I)
    if email:
        return email.group(0)
    return f"{workspace}@local"


def agent_remote_url_from_task(task):
    text = str(task or "").strip()
    patterns = (
        r"\bgit@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?\b",
        r"\bssh://git@github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?\b",
        r"\bhttps://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0)
    return ""


def agent_git_publish_requested(task):
    """Structural fallback: a concrete Git remote URL is enough to publish an existing workspace."""
    return agent_remote_url_from_task(task) != ""


def agent_new_workspace_request(task):
    """TaskSpec-only compatibility hook; new workspace intent must come from TaskSpec."""
    return False


def agent_executable_task_requested(task):
    """TaskSpec-only compatibility hook; execution intent is capability-selector work."""
    return False


def agent_public_url_from_task(task):
    text = str(task or "").strip()
    match = re.search(r"https?://[^\s<>'\")]+", text)
    if match:
        return match.group(0).rstrip(".,;:!?)]}")
    return ""


def agent_web_question_requested(task):
    """TaskSpec-only compatibility hook; public-web intent belongs to TaskSpec."""
    return False


def agent_capability_help_requested(task):
    """TaskSpec-only compatibility hook; help/capability intent belongs to TaskSpec."""
    return False


def agent_preview_requested(task):
    """TaskSpec-only compatibility hook; preview intent belongs to TaskSpec."""
    return False


def agent_user_confirmation_requested(task):
    """TaskSpec-only compatibility hook; confirmation belongs to TaskSpec safety fields."""
    return False


def agent_deploy_requested(task):
    """TaskSpec-only compatibility hook; stack deploy intent belongs to TaskSpec/admin capability."""
    return False


def agent_run_requested(task):
    """TaskSpec-only compatibility hook; explicit commands are detected structurally."""
    return False


def agent_explicit_command_requested(task):
    text = str(task or "").strip()
    if re.search(r"(?is)```(?:bash|sh|shell)?\s*\n.+?\n```", text):
        return True
    if re.search(r"`([^`\n]{1,300})`", text):
        return True
    if re.fullmatch(r"\s*(?:pwd|git\s+status(?:\s+--short)?(?:\s+--branch)?|ls(?:\s+-[A-Za-z]+)?|python3?\s+--version|node\s+--version|npm\s+--version)\s*", text):
        return True
    return False


def agent_infer_command_from_task(task):
    text = str(task or "").strip()
    fenced = re.search(r"(?is)```(?:bash|sh|shell)?\s*\n(.+?)\n```", text)
    if fenced:
        line = next((item.strip() for item in fenced.group(1).splitlines() if item.strip()), "")
        if line:
            return ["sh", "-lc", line]
    inline = re.search(r"`([^`\n]{1,300})`", text)
    if inline:
        return ["sh", "-lc", inline.group(1).strip()]
    if re.fullmatch(r"\s*git\s+status(?:\s+--short)?(?:\s+--branch)?\s*", text):
        return ["git", "status", "--short", "--branch"]
    if re.fullmatch(r"\s*pwd\s*", text):
        return ["pwd"]
    if re.fullmatch(r"\s*ls(?:\s+-[A-Za-z]+)?\s*", text):
        return ["ls", "-la"]
    if re.fullmatch(r"\s*python3?\s+--version\s*", text):
        return ["python3", "--version"]
    if re.fullmatch(r"\s*node\s+--version\s*", text):
        return ["node", "--version"]
    if re.fullmatch(r"\s*npm\s+--version\s*", text):
        return ["npm", "--version"]
    return []


def agent_meta_capability_from_task(task):
    """TaskSpec-only compatibility hook; meta intents belong to required_capabilities."""
    return ""


def agent_workspace_search_query_from_task(task):
    """TaskSpec-only compatibility hook; repository search query belongs to TaskSpec.search_query."""
    return ""


def looks_like_followup_reference(task):
    """TaskSpec-only compatibility hook; follow-up references must come from TaskSpec."""
    return False


def placeholder_followup_text(text):
    """Return True only for structured planner markers, not human-language prose."""
    normalized = " ".join(str(text or "").strip().split())
    return normalized in {"__referent_required__", "__followup_reference__"}


def recent_nontrivial_context_message(conversation_context, current_task=""):
    current = " ".join(str(current_task or "").strip().split()).lower()
    lines = [line.strip() for line in str(conversation_context or "").splitlines() if line.strip()]
    assistant_fallback = ""
    for raw in reversed(lines):
        role_match = re.match(r"^(user|assistant):\s*", raw, flags=re.I)
        role = role_match.group(1).lower() if role_match else ""
        line = re.sub(r"^(user|assistant):\s*", "", raw, flags=re.I).strip()
        if not line:
            continue
        lowered = " ".join(line.split()).lower()
        if current and lowered == current:
            continue
        if lowered.startswith("ag") and ("workflow=" in lowered or "requested_workspace=" in lowered):
            continue
        if "codeX_debug".lower() in lowered or lowered.startswith("{") or lowered.startswith("["):
            continue
        if any(marker in lowered for marker in ("requested_workspace=", "controller_workspace=", "workflow=", "planner_source=")):
            continue
        if role == "user":
            return line
        if not assistant_fallback:
            assistant_fallback = line
    return assistant_fallback


def resolved_referents(task, conversation_context, requested_workspace="", controller_workspace=""):
    """Do not infer human-language pronouns in gateway core.

    The LLM TaskSpec planner receives recent conversation context and should
    return explicit referents. Gateway validation can reject unresolved
    placeholders, but it must not decide what "to/tam/výsledky" means by
    keyword matching.
    """
    return {}


def agent_fallback_plan(task, requested_workspace, controller_workspace, workspace_exists):
    """Return a small policy fallback when the LLM planner is unavailable.

    The normal path is LLM-first planning. This helper exists only so the
    gateway can still recover into a bounded capability workflow when the
    planner call fails or returns unusable output.
    """
    url = agent_public_url_from_task(task)
    command = agent_infer_command_from_task(task)

    provenance = "fallback:structural"
    if workspace_exists and agent_git_publish_requested(task):
        workflow = "workspace_git_publish"
    elif url:
        workflow = "web_fetch"
    elif command:
        workflow = "run"
    else:
        return None

    remote_url = agent_remote_url_from_task(task) if workflow == "workspace_git_publish" else ""
    raw = {
        "workflow": workflow,
        "reason": "Deterministic bounded fallback matched after LLM planner failure.",
        "read_only": workflow == "review",
        "workspace": requested_workspace if workspace_exists else controller_workspace,
        "action": "",
        "command": command if workflow == "run" else [],
        "run_after": "",
        "followup_actions": [],
        "repo_name": "",
        "github": "github.com" in remote_url.lower(),
        "remote_url": remote_url,
        "desired_end_state": "git_init_origin_commit_push_main" if workflow == "workspace_git_publish" else "",
        "url": url,
        "question": str(task or "").strip() if workflow == "web_answer" else "",
        "search_query": "",
        "meta_capability": "",
        "required_capabilities": [],
        "routing_provenance": provenance,
        "capability_locked": True,
        "ssh_comment": agent_workspace_ssh_comment(task, requested_workspace if workspace_exists else controller_workspace)
        if workflow in {"ssh_key_create", "ssh_key_show_public"}
        else "",
        "confidence": "high",
    }
    return normalize_agent_plan(raw, requested_workspace, controller_workspace, workspace_exists, task), json.dumps(raw, ensure_ascii=False)


def normalize_agent_command(value):
    if isinstance(value, list) and value and all(isinstance(item, str) and item.strip() for item in value):
        command = [item.strip() for item in value]
    elif isinstance(value, str) and value.strip():
        command = ["sh", "-lc", value.strip()]
    else:
        return []
    if len(command) > 12:
        raise ValueError("agent run command is too long")
    if sum(len(item) for item in command) > 1200:
        raise ValueError("agent run command text is too long")
    joined = " ".join(command)
    if "mentor_codex_local.py" in joined or "owui_chat_turn.py" in joined:
        raise ValueError("agent run refuses nested OpenWebUI helper commands")
    return command


def python_command_like(value):
    name = os.path.basename(str(value or "")).strip().lower()
    return name in {"python", "python3", "python.exe", "python3.exe"}


def nested_helper_command_kind(command):
    if not isinstance(command, list) or len(command) < 2:
        return ""
    if not python_command_like(command[0]):
        return ""
    script = str(command[1] or "")
    if script == "codex/bin/mentor_codex_local.py":
        return "mentor"
    if script == "codex/bin/owui_chat_turn.py":
        return "owui"
    return ""


def parse_nested_mentor_helper_command(command):
    if nested_helper_command_kind(command) != "mentor":
        return None
    idx = 2
    while idx < len(command) and str(command[idx]).startswith("--"):
        idx += 1
    if idx >= len(command):
        return None
    mode = str(command[idx] or "").strip()
    if mode not in {
        "delegate",
        "profile",
        "report",
        "plan",
        "next-helper",
        "bootstrap-improve",
        "audit",
        "review",
        "improve",
        "autopilot",
        "apply-safe",
    }:
        return None
    idx += 1
    while idx < len(command) and str(command[idx]).startswith("--"):
        idx += 1
    if idx >= len(command):
        return None
    workspace = str(command[idx] or "").strip()
    idx += 1
    task = str(command[idx] or "").strip() if idx < len(command) else ""
    return {"mode": mode, "workspace": workspace, "task": task}


def mentor_helper_agent_loop_task(parsed):
    mode = str((parsed or {}).get("mode") or "").strip()
    workspace = str((parsed or {}).get("workspace") or "").strip()
    task = str((parsed or {}).get("task") or "").strip()
    if mode == "delegate":
        return task
    if mode == "review":
        return task or f"Proveď senior review workspace {workspace}. Nic needituj. Najdi hlavní rizika a navrhni další bezpečný krok."
    if mode == "audit":
        return task or f"Proveď technický audit workspace {workspace}. Nic needituj. Řekni 3 největší blockery a navrhni další bezpečný krok."
    if mode == "improve":
        return task or f"Pokračuj autonomně ve workspace {workspace}. Proveď nejbližší bezpečný capability krok, případný malý patch a vrať konkrétní výsledek."
    if mode == "autopilot":
        return task or f"Ověř workspace {workspace}, pokračuj nejbližším bezpečným capability krokem a vrať stručný průběh."
    if mode == "bootstrap-improve":
        return task or f"Bootstrapuj workspace {workspace}, připrav repozitář a pokračuj nejbližším bezpečným capability krokem."
    if mode == "apply-safe":
        return task or f"Připrav a auditovaně aplikuj malý bezpečný patch ve workspace {workspace}."
    if mode in {"profile", "report", "plan", "next-helper"}:
        return task or f"Prohlédni workspace {workspace}. Nic needituj. Řekni nejbližší další capability krok."
    return ""


def rescue_nested_workspace_helper(workspace, command):
    parsed = parse_nested_mentor_helper_command(command)
    if parsed:
        helper_workspace = str(parsed.get("workspace") or "").strip() or workspace
        task = mentor_helper_agent_loop_task(parsed)
        if task:
            result = admin_agent_loop({"workspace": helper_workspace, "task": task})
            text = agent_loop_response_text(result)
            return {
                "ok": bool(result.get("ok")),
                "action": "workspace_run_rescued_agent_loop",
                "workspace": helper_workspace,
                "runner": "agent_loop",
                "command": command,
                "executed_command": ["GATEWAY_ADMIN_AGENT_LOOP", helper_workspace, "--", task],
                "exit_code": 0 if result.get("ok") else 1,
                "runner_exit_code": 0 if result.get("ok") else 1,
                "duration_ms": 0,
                "output": text,
                "rescued": True,
                "rescue_kind": "mentor_helper_to_agent_loop",
            }
    if nested_helper_command_kind(command) == "owui":
        text = (
            "WORKSPACE_RUN_NESTED_OWUI_HELPER_BLOCKED\n"
            "reason=direct owui_chat_turn invocation inside workspace run would recurse into OpenWebUI chat flow\n"
            "recovery=Route the intent through GATEWAY_ADMIN_AGENT_LOOP or run the underlying capability directly instead of calling owui_chat_turn.py."
        )
        return {
            "ok": False,
            "action": "workspace_run_blocked",
            "workspace": workspace,
            "runner": "blocked",
            "command": command,
            "executed_command": command,
            "exit_code": 1,
            "runner_exit_code": 1,
            "duration_ms": 0,
            "output": text,
            "rescued": False,
            "rescue_kind": "owui_helper_blocked",
        }
    return None


def _boolish(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "ano"}:
            return True
        if lowered in {"0", "false", "no", "n", "ne"}:
            return False
    return default


def _string_list(value):
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def agent_capability_hints_from_task(task, workspace_exists):
    """Last-resort structural hints when the LLM selector is unavailable.

    This function must not interpret human-language intent. It may only surface
    already-structured inputs: concrete Git remotes, public URLs, explicit
    target_capability_name/capability fields, and explicit commands.
    """
    capabilities = []
    remote_url = agent_remote_url_from_task(task)
    target_capability_name = agent_target_capability_name_from_task(task)
    public_url = agent_public_url_from_task(task)
    explicit_command = agent_infer_command_from_task(task)
    if target_capability_name:
        capabilities.append("agent_capability_develop")
    if remote_url and workspace_exists:
        capabilities.append("workspace_git_publish")
    if public_url and not remote_url:
        capabilities.append("public_web_access")
    if explicit_command:
        capabilities.append("workspace_run")
    if not capabilities:
        capabilities.append("clarify_or_infer_capability")
    return canonicalize_agent_capabilities(capabilities)


def agent_capability_selector_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "required_capabilities": {"type": "array", "items": {"type": "string"}},
            "missing_inputs": {"type": "array", "items": {"type": "string"}},
            "desired_end_state": {"type": "string"},
            "action": {"type": "string"},
            "run_after": {"type": "string"},
            "followup_actions": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string"},
            "recovery_plan": {"type": "string"},
        },
        "required": [
            "required_capabilities",
            "missing_inputs",
            "desired_end_state",
            "action",
            "run_after",
            "followup_actions",
            "confidence",
            "recovery_plan",
        ],
    }


def agent_taskspec_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent_class": {
                "type": "string",
                "enum": [
                    "direct_answer",
                    "creative_answer",
                    "workspace_bootstrap",
                    "workspace_edit",
                    "workspace_action_chain",
                    "web_search",
                    "web_fetch",
                    "capability_help",
                    "self_improve",
                    "clarify",
                ],
            },
            "referents": {"type": "object"},
            "current_workspace": {"type": "string"},
            "target_workspace": {"type": "string"},
            "user_goal": {"type": "string"},
            "is_new_workspace_request": {"type": "boolean"},
            "is_existing_workspace_task": {"type": "boolean"},
            "target_repo_name": {"type": "string"},
            "target_capability_name": {"type": "string"},
            "remote_url": {"type": "string"},
            "desired_end_state": {"type": "string"},
            "required_capabilities": {"type": "array", "items": {"type": "string"}},
            "missing_inputs": {"type": "array", "items": {"type": "string"}},
            "risk_level": {"type": "string"},
            "recovery_plan": {"type": "string"},
            "read_only": {"type": "boolean"},
            "command": {"type": "array", "items": {"type": "string"}},
            "action": {"type": "string"},
            "run_after": {"type": "string"},
            "followup_actions": {"type": "array", "items": {"type": "string"}},
            "execution_plan": {"type": "array", "items": {"type": "object"}},
            "url": {"type": "string"},
            "question": {"type": "string"},
            "search_query": {"type": "string"},
            "ssh_comment": {"type": "string"},
            "confidence": {"type": "string"},
            "needs_user_input": {"type": "boolean"},
            "answer_visibility": {"type": "string", "enum": ["summary", "details", "hidden_debug"]},
        },
        "required": [
            "intent_class",
            "referents",
            "current_workspace",
            "target_workspace",
            "user_goal",
            "is_new_workspace_request",
            "is_existing_workspace_task",
            "target_repo_name",
            "target_capability_name",
            "remote_url",
            "desired_end_state",
            "required_capabilities",
            "missing_inputs",
            "risk_level",
            "recovery_plan",
            "read_only",
            "command",
            "action",
            "run_after",
            "followup_actions",
            "execution_plan",
            "url",
            "question",
            "search_query",
            "ssh_comment",
            "confidence",
            "needs_user_input",
            "answer_visibility",
        ],
    }


def agent_capability_selector_messages(
    partial_spec,
    requested_workspace,
    controller_workspace,
    workspace_exists,
    task,
    conversation_context="",
):
    capability_registry = agent_capability_registry()
    capability_names = sorted(
        name
        for name, meta in capability_registry.items()
        if isinstance(meta, dict) and meta.get("implemented")
    )
    partial_json = json.dumps(partial_spec or {}, ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": (
                "You are a bounded capability selector for a local Codex-like engineering agent. "
                "Return compact JSON only. Do not output workflow names unless they are represented as capabilities. "
                "Choose only from implemented capabilities that appear in the provided capability list. "
                "Prefer existing-workspace capabilities over bootstrap when the target workspace already exists. "
                "If the user provided a concrete git remote URL for an existing workspace, prefer workspace_git_publish. "
                "If the user asks for read-only analysis, prefer review/read_only_review. "
                "If the user asks for explicit shell command execution, prefer workspace_run. "
                "If information is truly missing, keep required_capabilities empty and put the missing fields into missing_inputs.\n\n"
                "Output schema:\n"
                "{\n"
                '  "required_capabilities": ["workspace_git_publish"],\n'
                '  "missing_inputs": [],\n'
                '  "desired_end_state": "short concrete end state or empty",\n'
                '  "action": "install|verify|smoke|test|build|lint or empty",\n'
                '  "run_after": "install|verify|smoke|test|build|lint or empty",\n'
                '  "followup_actions": ["install","smoke"],\n'
                '  "confidence": "high|medium|low",\n'
                '  "recovery_plan": "one-line recovery plan"\n'
                "}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Requested workspace: {requested_workspace}\n"
                f"Controller workspace: {controller_workspace}\n"
                f"Requested workspace exists: {workspace_exists}\n"
                f"Implemented capabilities: {', '.join(capability_names)}\n"
                f"Partial TaskSpec:\n{partial_json}\n\n"
                f"Recent conversation context:\n{conversation_context or '(none)'}\n\n"
                f"User task:\n{task}"
            ),
        },
    ]


def agent_select_capabilities_with_llm(
    partial_spec,
    requested_workspace,
    controller_workspace,
    workspace_exists,
    task,
    conversation_context="",
):
    model_id = codex_local_runtime_model_name(task=task, role=ROLE_PLANNER)
    parsed, raw, _meta = structured_json_chat(
        model_id,
        agent_capability_selector_messages(
            partial_spec,
            requested_workspace,
            controller_workspace,
            workspace_exists,
            task,
            conversation_context,
        ),
        "capability_selector",
        agent_capability_selector_schema(),
        timeout=180,
    )
    registry = agent_capability_registry()
    selected_capabilities = []
    seen_capabilities = set()
    for item in canonicalize_agent_capabilities(_string_list(parsed.get("required_capabilities"))):
        if item in seen_capabilities:
            continue
        meta = registry.get(item) or {}
        if meta.get("implemented"):
            selected_capabilities.append(item)
            seen_capabilities.add(item)

    action = str(parsed.get("action") or "").strip().lower()
    if action not in AGENT_LOOP_ACTIONS:
        action = ""
    run_after = str(parsed.get("run_after") or "").strip().lower()
    if run_after not in AGENT_LOOP_ACTIONS:
        run_after = ""
    followup_actions = [
        item.lower()
        for item in _string_list(parsed.get("followup_actions"))
        if item.lower() in AGENT_LOOP_ACTIONS
    ]
    confidence = str(parsed.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    return {
        "required_capabilities": selected_capabilities,
        "missing_inputs": _string_list(parsed.get("missing_inputs")),
        "desired_end_state": str(parsed.get("desired_end_state") or "").strip(),
        "action": action,
        "run_after": run_after,
        "followup_actions": followup_actions,
        "confidence": confidence,
        "recovery_plan": str(parsed.get("recovery_plan") or "").strip(),
        "raw": raw,
    }


def split_agent_capabilities(capabilities):
    registry = agent_capability_registry()
    implemented = []
    missing = []
    seen_implemented = set()
    seen_missing = set()
    for raw in capabilities or []:
        capability = canonicalize_agent_capability(raw)
        if not capability:
            continue
        entry = registry.get(capability) or {}
        if entry.get("implemented"):
            if capability not in seen_implemented:
                implemented.append(capability)
                seen_implemented.add(capability)
        elif capability not in seen_missing:
            missing.append(capability)
            seen_missing.add(capability)
    return implemented, missing


def taskspec_desired_public_ssh_key(spec, desired_end_state=""):
    """Return True when TaskSpec semantics require returning a public SSH key."""
    if not isinstance(spec, dict):
        spec = {}
    fields = [
        desired_end_state,
        spec.get("desired_end_state"),
        spec.get("target_capability_name"),
    ]
    for step in spec.get("execution_plan") or []:
        if not isinstance(step, dict):
            continue
        fields.extend([step.get("goal"), step.get("desired_end_state"), step.get("capability")])

    normalized = " ".join(normalize_capability_identifier(item).lower() for item in fields if item)
    public_key_markers = {
        "workspace_public_key_returned",
        "public_key_returned",
        "ssh_public_key_returned",
        "ssh_key_show_public",
        "workspace_ssh_key_show_public",
    }
    if any(marker in normalized for marker in public_key_markers):
        return True
    capabilities = canonicalize_agent_capabilities(_string_list(spec.get("required_capabilities")))
    return "ssh_key_show_public" in capabilities


def agent_taskspec_messages(requested_workspace, controller_workspace, workspace_exists, task, snapshot, conversation_context=""):
    registry = load_registry()[1]
    workspace_list = ", ".join(sorted(registry))
    return [
        {
            "role": "system",
            "content": (
                "You are the TaskSpec planner for a local Codex-like engineering agent. "
                "Return JSON only. Do not explain. Do not output the workflow directly. "
                "First understand the user's actual goal and target state, then describe the work as TaskSpec.\n\n"
                "Output schema:\n"
                "{\n"
                '  "intent_class": "direct_answer|creative_answer|workspace_bootstrap|workspace_edit|workspace_action_chain|web_search|web_fetch|capability_help|self_improve|clarify",\n'
                '  "referents": {"to": "resolved prior topic or empty", "tam": "resolved workspace/project or empty"},\n'
                '  "current_workspace": "workspace-name",\n'
                '  "target_workspace": "workspace-name or empty",\n'
                '  "user_goal": "what the user actually wants to end up with",\n'
                '  "is_new_workspace_request": false,\n'
                '  "is_existing_workspace_task": true,\n'
                '  "target_repo_name": "repo or workspace name or empty",\n'
                '  "target_capability_name": "new or existing capability name or empty",\n'
                '  "remote_url": "git@github.com:owner/repo.git or empty",\n'
                '  "desired_end_state": "concrete end state",\n'
                '  "required_capabilities": ["workspace_git_publish"],\n'
                '  "missing_inputs": [],\n'
                '  "risk_level": "low|medium|high",\n'
                '  "recovery_plan": "what to do if the preferred capability is blocked",\n'
                '  "read_only": false,\n'
                '  "command": ["optional","explicit","command"],\n'
                '  "action": "install|verify|smoke|test|build|lint or empty",\n'
                '  "run_after": "install|verify|smoke|test|build|lint or empty",\n'
                '  "followup_actions": ["install","smoke"],\n'
                '  "execution_plan": [{"capability": "workspace_action_chain", "goal": "short step goal"}],\n'
                '  "url": "public url or empty",\n'
                '  "question": "public-web question or empty",\n'
                '  "search_query": "workspace search query or empty",\n'
                '  "ssh_comment": "ssh key comment or empty",\n'
                '  "confidence": "high|medium|low",\n'
                '  "needs_user_input": false,\n'
                '  "answer_visibility": "summary|details|hidden_debug"\n'
                "}\n\n"
                "Planning rules:\n"
                "- Bootstrap is only for clearly new repository/workspace requests.\n"
                "- Ordinary math, short factual questions, greetings, and story/prose requests are direct_answer or creative_answer, not repository review.\n"
                "- Capability/help questions are capability_help, not repository review.\n"
                "- Public web questions without a concrete URL are web_search; public web questions with a URL are web_fetch/web_search as appropriate.\n"
                "- Resolve follow-up referents such as to/tam/ten projekt from the conversation context when it is present.\n"
                "- If the current message contains pronouns or follow-up placeholders like it/that/to/tam/ten projekt/výsledky/results, resolve them from Recent conversation context before selecting a capability.\n"
                "- Follow-ups asking to list/show previous results should answer from Recent conversation context when the results are already present, or continue the previous web_search/web_fetch if more fetching is needed; do not switch to repository work.\n"
                "- For multi-step app work, use workspace_action_chain with an execution_plan instead of inventing missing capabilities like build.\n"
                "- Before push/deploy/destructive steps that need user confirmation, include await_user_confirmation in required_capabilities and set needs_user_input=true.\n"
                "- If an existing workspace is the target and the user mentions git init, origin, remote, push, or a remote URL, prefer existing-workspace git publishing instead of bootstrap.\n"
                "- If the task asks for SSH key creation in an existing workspace, request the SSH capability instead of bootstrap or raw ssh-keygen.\n"
                "- If the task asks for the public key, request the public-key capability.\n"
                "- If the task asks to switch/report workspace context, show capabilities, or report runtime status, request the matching meta capability.\n"
                "- If the task asks to search the repository, request workspace_search and put the concrete search text into search_query.\n"
                "- If the task is read-only or explicitly says not to edit, mark read_only=true and keep required_capabilities minimal.\n"
                "- If there is a concrete remote URL, put it into remote_url exactly.\n"
                "- If the user includes an explicit shell command, preserve it in command, but only if it is truly the user's intended action.\n"
                "- If the intent is unclear or an input is missing, put it in missing_inputs instead of inventing a different task.\n"
                "- If the task could be done safely by an existing capability, name that capability in required_capabilities.\n"
                "- If the task is to add, implement, design, or improve a codex-local capability, request agent_capability_develop and put the desired capability identifier into target_capability_name.\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Requested workspace: {requested_workspace}\n"
                f"Controller workspace: {controller_workspace}\n"
                f"Requested workspace exists: {workspace_exists}\n"
                f"Known workspaces: {workspace_list}\n\n"
                f"Capability catalog:\n{agent_capability_catalog()}\n\n"
                f"Recent conversation context:\n{conversation_context or '(none)'}\n\n"
                f"User task:\n{task}\n\n"
                f"Repository snapshot for the controller workspace:\n{snapshot[:18000]}"
            ),
        },
    ]


def normalize_agent_taskspec(spec, requested_workspace, controller_workspace, workspace_exists, task, conversation_context=""):
    if not isinstance(spec, dict):
        spec = {}
    allowed_intents = {
        "direct_answer",
        "creative_answer",
        "workspace_bootstrap",
        "workspace_edit",
        "workspace_action_chain",
        "web_search",
        "web_fetch",
        "capability_help",
        "self_improve",
        "clarify",
    }
    intent_class = str(spec.get("intent_class") or "").strip().lower()
    if intent_class not in allowed_intents:
        intent_class = ""
    referents = spec.get("referents") if isinstance(spec.get("referents"), dict) else {}
    if not referents:
        referents = resolved_referents(task, conversation_context, requested_workspace, controller_workspace)
    target_workspace = str(spec.get("target_workspace") or "").strip()
    execution_plan = spec.get("execution_plan") if isinstance(spec.get("execution_plan"), list) else []
    execution_plan_capabilities = []
    for step in execution_plan:
        if not isinstance(step, dict):
            continue
        for key in ("capability", "capabilities", "required_capabilities"):
            value = step.get(key)
            if isinstance(value, list):
                execution_plan_capabilities.extend(value)
            elif value:
                execution_plan_capabilities.append(value)
    needs_user_input = _boolish(spec.get("needs_user_input"), default=False)
    answer_visibility = str(spec.get("answer_visibility") or "summary").strip().lower()
    if answer_visibility not in {"summary", "details", "hidden_debug"}:
        answer_visibility = "summary"
    fallback_workspace = requested_workspace if workspace_exists else controller_workspace
    requested_new_workspace = _boolish(spec.get("is_new_workspace_request"), default=False) or intent_class == "workspace_bootstrap"
    bootstrap_repo = ""
    remote_url = str(spec.get("remote_url") or "").strip() or agent_remote_url_from_task(task)
    read_only = True if intent_class in {"direct_answer", "creative_answer", "capability_help"} else _boolish(spec.get("read_only"), default=False)
    current_workspace = str(spec.get("current_workspace") or "").strip() or target_workspace or fallback_workspace
    user_goal = str(spec.get("user_goal") or "").strip() or str(task or "").strip()
    target_repo_name = str(spec.get("target_repo_name") or "").strip() or bootstrap_repo
    target_capability_name = str(spec.get("target_capability_name") or "").strip()
    if not target_capability_name:
        target_capability_name = agent_target_capability_name_from_task(task)
    if target_repo_name and current_workspace == requested_workspace and not workspace_exists and requested_workspace != controller_workspace:
        current_workspace = controller_workspace
    is_new_workspace_request = _boolish(
        spec.get("is_new_workspace_request"),
        default=intent_class == "workspace_bootstrap",
    )
    if bootstrap_repo:
        is_new_workspace_request = True
        target_repo_name = bootstrap_repo
    is_existing_workspace_task = _boolish(
        spec.get("is_existing_workspace_task"),
        default=workspace_exists and not is_new_workspace_request,
    )
    desired_end_state = str(spec.get("desired_end_state") or "").strip()
    if not desired_end_state:
        if is_new_workspace_request:
            desired_end_state = "new_workspace_registered_with_git_and_ssh_ready"
        elif remote_url and workspace_exists:
            desired_end_state = "git_init_origin_commit_push_main"
        elif agent_public_url_from_task(task):
            desired_end_state = "public_web_answer_returned"
        elif agent_infer_command_from_task(task):
            desired_end_state = "explicit_command_completed"
        else:
            desired_end_state = "intent_clarified"
    planner_required_capabilities = canonicalize_agent_capabilities(
        _string_list(spec.get("required_capabilities")) + execution_plan_capabilities
    )
    intent_capabilities = []
    if intent_class == "direct_answer":
        intent_capabilities.append("direct_answer")
    elif intent_class == "creative_answer":
        intent_capabilities.append("creative_answer")
    elif intent_class == "workspace_bootstrap":
        intent_capabilities.append("workspace_repo_bootstrap")
    elif intent_class == "workspace_edit":
        intent_capabilities.append("workspace_edit")
    elif intent_class == "workspace_action_chain":
        intent_capabilities.append("workspace_action_chain")
    elif intent_class == "web_search":
        intent_capabilities.append("public_web_search")
    elif intent_class == "web_fetch":
        intent_capabilities.append("public_web_access")
    elif intent_class == "capability_help":
        intent_capabilities.append("capability_catalog_show")
    elif intent_class == "self_improve":
        intent_capabilities.append("agent_self_improve")
    elif intent_class == "clarify":
        intent_capabilities.append("clarify_or_infer_capability")
    if needs_user_input:
        intent_capabilities.append("await_user_confirmation")
    intent_capabilities = canonicalize_agent_capabilities(intent_capabilities)
    if intent_capabilities:
        planner_required_capabilities = canonicalize_agent_capabilities(intent_capabilities + planner_required_capabilities)
    selector_source = "planner" if planner_required_capabilities else "none"
    llm_capability_selection = None
    if (
        not planner_required_capabilities
        or (
            planner_required_capabilities == ["clarify_or_infer_capability"]
            and not _string_list(spec.get("missing_inputs"))
        )
    ):
        try:
            llm_capability_selection = agent_select_capabilities_with_llm(
                spec,
                requested_workspace,
                controller_workspace,
                workspace_exists,
                task,
                conversation_context,
            )
        except Exception:
            llm_capability_selection = None
    llm_required_capabilities = (
        llm_capability_selection.get("required_capabilities") if isinstance(llm_capability_selection, dict) else []
    ) or []
    if llm_required_capabilities:
        required_capabilities = llm_required_capabilities
        selector_source = "llm_capability_selector"
    else:
        required_capabilities = planner_required_capabilities or agent_capability_hints_from_task(task, workspace_exists)
        if not planner_required_capabilities:
            selector_source = "structural_fallback"
    required_capabilities = canonicalize_agent_capabilities(required_capabilities)
    if intent_capabilities:
        required_capabilities = canonicalize_agent_capabilities(intent_capabilities + required_capabilities)
    meta_capability = ""
    if intent_class == "capability_help":
        meta_capability = "capability_catalog_show"
    else:
        meta_capability = next(
            (
                capability
                for capability in required_capabilities
                if capability in {
                    "workspace_context_set",
                    "workspace_context_status",
                    "capability_catalog_show",
                    "agent_runtime_status",
                }
            ),
            "",
        )
    if meta_capability and meta_capability not in required_capabilities:
        required_capabilities.insert(0, meta_capability)
    if is_new_workspace_request and "workspace_repo_bootstrap" not in required_capabilities:
        required_capabilities.insert(0, "workspace_repo_bootstrap")
    if target_capability_name and "agent_capability_develop" not in required_capabilities:
        required_capabilities.insert(0, "agent_capability_develop")
    if "agent_capability_develop" in required_capabilities:
        required_capabilities = [
            cap
            for cap in required_capabilities
            if cap not in {"workspace_edit", "edit", "workspace_autopilot", "autopilot", "clarify_or_infer_capability"}
        ]
    if remote_url and workspace_exists and not is_new_workspace_request and "workspace_git_publish" not in required_capabilities:
        required_capabilities.insert(0, "workspace_git_publish")
    # Capability precedence is semantic rather than prompt-specific: returning the
    # public key is a superset operation because it idempotently creates the key
    # when needed, so it wins over plain key creation whenever both are present.
    if (
        "ssh_key_create" in required_capabilities
        and "ssh_key_show_public" not in required_capabilities
        and taskspec_desired_public_ssh_key(spec, desired_end_state)
    ):
        required_capabilities.insert(0, "ssh_key_show_public")
    if "ssh_key_show_public" in required_capabilities and "ssh_key_create" in required_capabilities:
        required_capabilities = [cap for cap in required_capabilities if cap != "ssh_key_create"]
    referent_topic = str(referents.get("to") or "").strip()
    search_query = str(spec.get("search_query") or "").strip()
    if placeholder_followup_text(search_query):
        search_query = ""
    if not search_query and referent_topic and intent_class in {"web_search", "web_fetch"}:
        search_query = referent_topic
    if (
        search_query
        and workspace_exists
        and "workspace_search" not in required_capabilities
        and "public_web_search" not in required_capabilities
    ):
        required_capabilities.insert(0, "workspace_search")
    url = str(spec.get("url") or "").strip() or agent_public_url_from_task(task)
    if (
        read_only
        and not is_new_workspace_request
        and not remote_url
        and not url
        and "workspace_search" not in required_capabilities
        and "direct_answer" not in required_capabilities
        and "creative_answer" not in required_capabilities
        and "capability_catalog_show" not in required_capabilities
        and "public_web_search" not in required_capabilities
        and "await_user_confirmation" not in required_capabilities
        and not meta_capability
        and not agent_explicit_command_requested(task)
    ):
        required_capabilities = ["review"]
    missing_inputs = _string_list(spec.get("missing_inputs"))
    if (
        not missing_inputs
        and isinstance(llm_capability_selection, dict)
        and llm_capability_selection.get("missing_inputs")
        and not llm_required_capabilities
    ):
        missing_inputs = _string_list(llm_capability_selection.get("missing_inputs"))
    if is_new_workspace_request and not target_repo_name:
        missing_inputs.append("target_repo_name")
    if remote_url and not workspace_exists and not is_new_workspace_request:
        missing_inputs.append("existing_workspace")
    if "workspace_search" in required_capabilities and not search_query:
        missing_inputs.append("search_query")
    if "workspace_search" in required_capabilities and not workspace_exists:
        missing_inputs.append("existing_workspace")
    if "public_web_search" in required_capabilities and not (
        search_query or str(spec.get("question") or "").strip() or referent_topic
    ):
        missing_inputs.append("search_query")
    risk_level = str(spec.get("risk_level") or "").strip().lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "low" if read_only else ("medium" if remote_url else "low")
    recovery_plan = str(spec.get("recovery_plan") or "").strip()
    if not recovery_plan and isinstance(llm_capability_selection, dict):
        recovery_plan = str(llm_capability_selection.get("recovery_plan") or "").strip()
    if not recovery_plan:
        if remote_url and workspace_exists:
            recovery_plan = (
                "If git auth or push fails, return MANUAL_STEP_REQUIRED with the workspace public key and the exact next GitHub/SSH step."
            )
        elif is_new_workspace_request:
            recovery_plan = "If bootstrap cannot finish, return the created workspace, SSH public key path, and the exact next step."
        else:
            recovery_plan = "If the capability is missing or blocked, return NEEDS_ATTENTION with the missing capability and a concrete recovery step."
    action = str(spec.get("action") or "").strip().lower()
    if not action and isinstance(llm_capability_selection, dict):
        action = str(llm_capability_selection.get("action") or "").strip().lower()
    if action not in AGENT_LOOP_ACTIONS:
        action = ""
    run_after = str(spec.get("run_after") or "").strip().lower()
    if not run_after and isinstance(llm_capability_selection, dict):
        run_after = str(llm_capability_selection.get("run_after") or "").strip().lower()
    if run_after not in AGENT_LOOP_ACTIONS:
        run_after = ""
    followup_actions = [
        item.lower() for item in _string_list(spec.get("followup_actions")) if item.lower() in AGENT_LOOP_ACTIONS
    ]
    if not followup_actions and isinstance(llm_capability_selection, dict):
        followup_actions = [
            item.lower()
            for item in _string_list(llm_capability_selection.get("followup_actions"))
            if item.lower() in AGENT_LOOP_ACTIONS
        ]
    unresolved_followup_search = False
    question = str(spec.get("question") or "").strip()
    if placeholder_followup_text(question):
        question = ""
    if not question and referent_topic and intent_class in {"web_search", "web_fetch"}:
        question = referent_topic
    if intent_class == "web_search" and not search_query:
        if question:
            search_query = question
        else:
            unresolved_followup_search = True
    if unresolved_followup_search and "resolved_search_query" not in missing_inputs:
        missing_inputs.append("resolved_search_query")
    ssh_comment = str(spec.get("ssh_comment") or "").strip() or agent_workspace_ssh_comment(task, target_repo_name or fallback_workspace)
    confidence = str(spec.get("confidence") or "medium").strip().lower()
    if confidence == "medium" and isinstance(llm_capability_selection, dict) and llm_capability_selection.get("confidence"):
        confidence = str(llm_capability_selection.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    command = []
    try:
        command = normalize_agent_command(spec.get("command") or [])
    except ValueError:
        command = []
    if not command:
        command = agent_infer_command_from_task(task)
    required_capabilities = canonicalize_agent_capabilities(required_capabilities)
    if read_only and required_capabilities == ["review"]:
        action = ""
        run_after = ""
        followup_actions = []
        command = []
    return {
        "intent_class": intent_class,
        "referents": referents,
        "current_workspace": current_workspace,
        "target_workspace": target_workspace,
        "user_goal": user_goal,
        "is_new_workspace_request": bool(is_new_workspace_request),
        "is_existing_workspace_task": bool(is_existing_workspace_task),
        "target_repo_name": target_repo_name,
        "target_capability_name": target_capability_name,
        "remote_url": remote_url,
        "desired_end_state": desired_end_state,
        "required_capabilities": required_capabilities,
        "missing_capabilities": split_agent_capabilities(required_capabilities)[1],
        "missing_inputs": _string_list(missing_inputs),
        "risk_level": risk_level,
        "recovery_plan": recovery_plan,
        "read_only": read_only,
        "command": command,
        "action": action,
        "run_after": run_after,
        "followup_actions": followup_actions,
        "execution_plan": execution_plan,
        "url": url,
        "question": question,
        "search_query": search_query,
        "meta_capability": meta_capability,
        "ssh_comment": ssh_comment,
        "confidence": confidence,
        "capability_selector_source": selector_source,
        "needs_user_input": needs_user_input,
        "answer_visibility": answer_visibility,
    }


def agent_taskspec_to_plan(spec, requested_workspace, controller_workspace, workspace_exists, task):
    required_capabilities = canonicalize_agent_capabilities(_string_list(spec.get("required_capabilities")))
    implemented_capabilities, missing_capabilities = split_agent_capabilities(required_capabilities)
    capabilities = set(implemented_capabilities)
    intent_class = str(spec.get("intent_class") or "").strip().lower()
    action_capability = next(
        (
            capability.split(":", 1)[1].strip().lower()
            for capability in implemented_capabilities
            if capability.startswith("workspace_action:")
        ),
        "",
    )
    read_only = bool(spec.get("read_only"))
    workflow = "clarify"
    action = ""
    command = []
    run_after = ""
    followup_actions = list(spec.get("followup_actions") or [])
    repo_name = ""
    remote_url = str(spec.get("remote_url") or "").strip()
    meta_capability = next(
        (
            capability
            for capability in implemented_capabilities
            if capability in {
                "workspace_context_set",
                "workspace_context_status",
                "capability_catalog_show",
                "agent_runtime_status",
            }
        ),
        "",
    )

    if missing_capabilities:
        workflow = "clarify"
    elif spec.get("missing_inputs"):
        workflow = "clarify"
    elif spec.get("needs_user_input") or "await_user_confirmation" in capabilities:
        workflow = "clarify"
    elif "direct_answer" in capabilities or "creative_answer" in capabilities or intent_class in {"direct_answer", "creative_answer"}:
        workflow = "direct_answer"
    elif meta_capability:
        workflow = "meta"
    elif spec.get("is_new_workspace_request"):
        workflow = "bootstrap"
        repo_name = str(spec.get("target_repo_name") or "").strip()
    elif workspace_exists and ("workspace_git_publish" in capabilities or remote_url):
        workflow = "workspace_git_publish"
    elif workspace_exists and "ssh_key_show_public" in capabilities:
        workflow = "ssh_key_show_public"
    elif workspace_exists and "ssh_key_create" in capabilities:
        workflow = "ssh_key_create"
    elif "agent_capability_develop" in capabilities or "agent_self_improve" in capabilities:
        workflow = "self_improve"
    elif "stack_deploy" in capabilities:
        workflow = "deploy"
    elif "public_web_search" in capabilities or intent_class == "web_search":
        workflow = "web_search"
    elif "public_web_access" in capabilities and spec.get("url"):
        workflow = "web_answer" if spec.get("question") else "web_fetch"
    elif workspace_exists and "workspace_search" in capabilities:
        workflow = "workspace_search"
    elif (
        "workspace_action_chain" in capabilities
        or "workspace_expose_preview" in capabilities
        or "workspace_autopilot" in capabilities
    ):
        workflow = "autopilot"
    elif read_only:
        workflow = "review"
    elif "workspace_edit" in capabilities:
        workflow = "edit"
        run_after = str(spec.get("run_after") or "").strip().lower()
    elif action_capability or spec.get("action"):
        workflow = "action"
        action = str(spec.get("action") or action_capability or "").strip().lower()
    elif spec.get("command"):
        workflow = "run"
        command = spec.get("command") or []

    workspace = str(spec.get("current_workspace") or "").strip() or (requested_workspace if workspace_exists else controller_workspace)
    if workflow in {"direct_answer", "bootstrap", "deploy", "self_improve", "web_search"}:
        workspace = controller_workspace
    elif workflow in {"meta", "workspace_search", "review", "edit", "action", "run", "autopilot", "ssh_key_create", "ssh_key_show_public", "workspace_git_publish"}:
        workspace = requested_workspace if workspace_exists else controller_workspace

    return {
        "intent_class": intent_class,
        "workflow": workflow,
        "reason": preview_text(spec.get("user_goal") or spec.get("desired_end_state") or "", 180),
        "read_only": read_only,
        "workspace": workspace,
        "action": action,
        "command": command,
        "run_after": run_after,
        "followup_actions": followup_actions,
        "repo_name": repo_name,
        "target_capability_name": str(spec.get("target_capability_name") or "").strip(),
        "github": "github.com" in remote_url.lower(),
        "remote_url": remote_url,
        "desired_end_state": str(spec.get("desired_end_state") or "").strip(),
        "required_capabilities": required_capabilities,
        "missing_capabilities": missing_capabilities,
        "missing_inputs": _string_list(spec.get("missing_inputs")),
        "meta_capability": meta_capability,
        "search_query": str(spec.get("search_query") or "").strip(),
        "url": str(spec.get("url") or "").strip(),
        "question": str(spec.get("question") or "").strip(),
        "ssh_comment": str(spec.get("ssh_comment") or "").strip(),
        "confidence": str(spec.get("confidence") or "medium").strip().lower(),
        "referents": spec.get("referents") if isinstance(spec.get("referents"), dict) else {},
        "target_workspace": str(spec.get("target_workspace") or "").strip(),
        "execution_plan": spec.get("execution_plan") if isinstance(spec.get("execution_plan"), list) else [],
        "needs_user_input": bool(spec.get("needs_user_input")),
        "answer_visibility": str(spec.get("answer_visibility") or "summary").strip().lower(),
        "capability_locked": True,
    }


def agent_plan_messages(requested_workspace, controller_workspace, workspace_exists, task, snapshot, conversation_context=""):
    return agent_taskspec_messages(requested_workspace, controller_workspace, workspace_exists, task, snapshot, conversation_context)


def normalize_agent_plan(plan, requested_workspace, controller_workspace, workspace_exists, task):
    if not isinstance(plan, dict):
        plan = {}
    workflow = str(plan.get("workflow") or "").strip().lower() or "clarify"
    if workflow not in AGENT_LOOP_WORKFLOWS:
        workflow = "clarify"
    capability_locked = bool(plan.get("capability_locked"))
    read_only = bool(plan.get("read_only")) if capability_locked else agent_read_only_requested(task)
    if read_only and workflow != "review":
        workflow = "review"
    action = str(plan.get("action") or "").strip().lower()
    if action not in AGENT_LOOP_ACTIONS:
        action = ""
    run_after = str(plan.get("run_after") or "").strip().lower()
    if run_after not in AGENT_LOOP_ACTIONS:
        run_after = ""
    followup_actions = [
        str(item).strip().lower()
        for item in (plan.get("followup_actions") or [])
        if str(item).strip().lower() in AGENT_LOOP_ACTIONS
    ]
    try:
        command = normalize_agent_command(plan.get("command")) if workflow == "run" else []
    except ValueError:
        command = []
    repo_name = str(plan.get("repo_name") or "").strip()
    if not repo_name and workflow == "bootstrap":
        repo_name = agent_extract_repo_name(task) or (
            requested_workspace if not workspace_exists and requested_workspace != controller_workspace else ""
        )
    workspace = str(plan.get("workspace") or "").strip() or requested_workspace or controller_workspace
    if workflow in {"meta", "workspace_search", "review", "edit", "action", "run", "autopilot", "deploy"}:
        workspace = requested_workspace if workspace_exists else controller_workspace
    confidence = str(plan.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    required_capabilities = canonicalize_agent_capabilities(_string_list(plan.get("required_capabilities")))
    action_capability = next(
        (
            capability.split(":", 1)[1].strip().lower()
            for capability in required_capabilities
            if capability.startswith("workspace_action:")
        ),
        "",
    )
    inferred_action = ""
    inferred_followups = []
    bootstrap_requested = False
    git_publish_requested = False
    ssh_key_create_requested = False
    ssh_key_show_public_requested = False
    run_requested = False
    explicit_command_requested = False
    remote_url = str(plan.get("remote_url") or "").strip()
    if not capability_locked:
        inferred_action = agent_infer_action_from_task(task)
        inferred_followups = agent_infer_followup_actions(task)
        bootstrap_requested = agent_bootstrap_requested(task)
        git_publish_requested = agent_git_publish_requested(task)
        ssh_key_create_requested = agent_ssh_key_create_requested(task)
        ssh_key_show_public_requested = agent_ssh_key_show_public_requested(task)
        run_requested = agent_run_requested(task)
        explicit_command_requested = agent_explicit_command_requested(task)
        remote_url = remote_url or agent_remote_url_from_task(task)
    ssh_comment = str(plan.get("ssh_comment") or "").strip()
    if not ssh_comment:
        ssh_comment = (
            agent_workspace_ssh_comment(task, requested_workspace if workspace_exists else controller_workspace)
            if not capability_locked
            else f"{requested_workspace if workspace_exists else controller_workspace}@local"
        )
    if not capability_locked:
        if bootstrap_requested:
            workflow = "bootstrap"
        elif workspace_exists and git_publish_requested:
            workflow = "workspace_git_publish"
        elif workspace_exists and ssh_key_show_public_requested:
            workflow = "ssh_key_show_public"
        elif workspace_exists and ssh_key_create_requested:
            workflow = "ssh_key_create"
        if workflow == "bootstrap" and not bootstrap_requested:
            if agent_edit_requested(task):
                workflow = "edit"
            elif inferred_action:
                workflow = "action"
                action = action or inferred_action
            elif workspace_exists and git_publish_requested:
                workflow = "workspace_git_publish"
            elif workspace_exists and ssh_key_show_public_requested:
                workflow = "ssh_key_show_public"
            elif workspace_exists and ssh_key_create_requested:
                workflow = "ssh_key_create"
            elif run_requested:
                workflow = "run"
            else:
                workflow = "clarify"
        if workflow == "web_fetch" and agent_web_question_requested(task):
            workflow = "web_answer"
    if workflow in {"web_fetch", "web_answer"} and not str(plan.get("url") or "").strip():
        plan["url"] = agent_public_url_from_task(task)
    if workflow == "web_answer" and not str(plan.get("question") or "").strip():
        plan["question"] = str(task or "").strip()
    if not capability_locked and not read_only and workflow == "review" and inferred_action:
        workflow = "action"
        action = action or inferred_action
    if not capability_locked and not read_only and workflow == "review" and run_requested:
        workflow = "run"
    if not capability_locked and not read_only and workflow == "review" and agent_edit_requested(task):
        workflow = "edit"
    if workflow == "edit" and not run_after and inferred_action:
        run_after = inferred_action
    if workflow in {"bootstrap", "autopilot"} and not followup_actions and not capability_locked:
        followup_actions = inferred_followups
    if workflow == "action" and not action and action_capability:
        action = action_capability
    if workflow == "run" and inferred_action and not explicit_command_requested and not capability_locked:
        workflow = "action"
        action = action or inferred_action
        command = []
    if workflow == "run" and workspace_exists and ssh_key_create_requested and not bootstrap_requested and not capability_locked:
        workflow = "ssh_key_create"
        command = []
    if workflow == "run" and workspace_exists and ssh_key_show_public_requested and not bootstrap_requested and not capability_locked:
        workflow = "ssh_key_show_public"
        command = []
    if workflow == "run" and workspace_exists and git_publish_requested and not bootstrap_requested and not capability_locked:
        workflow = "workspace_git_publish"
        command = []
    if workflow == "run" and not command:
        command = agent_infer_command_from_task(task)
    if workflow == "run" and not command:
        workflow = "clarify"
    if workflow == "bootstrap" and not repo_name:
        repo_name = agent_extract_repo_name(task) or (
            requested_workspace if not workspace_exists and requested_workspace != controller_workspace else ""
        )
    if workflow != "bootstrap":
        repo_name = ""
    return {
        "workflow": workflow,
        "reason": str(plan.get("reason") or "").strip(),
        "read_only": read_only,
        "workspace": workspace,
        "action": action,
        "command": command,
        "run_after": run_after,
        "followup_actions": followup_actions,
        "repo_name": repo_name,
        "github": "github.com" in remote_url.lower(),
        "remote_url": remote_url if workflow == "workspace_git_publish" else "",
        "desired_end_state": str(plan.get("desired_end_state") or "").strip(),
        "url": str(plan.get("url") or "").strip(),
        "question": str(plan.get("question") or "").strip(),
        "ssh_comment": ssh_comment,
        "confidence": confidence,
        "required_capabilities": required_capabilities,
        "missing_capabilities": _string_list(plan.get("missing_capabilities")),
        "missing_inputs": _string_list(plan.get("missing_inputs")),
        "meta_capability": str(plan.get("meta_capability") or "").strip(),
        "search_query": str(plan.get("search_query") or "").strip(),
        "capability_locked": capability_locked,
        "routing_provenance": str(plan.get("routing_provenance") or "").strip(),
    }


def agent_review_response(workspace, task):
    default, workspaces = load_registry()
    cfg = workspaces.get(workspace) or workspaces.get(default)
    if not cfg:
        raise ValueError(f"Unknown workspace '{workspace}'")
    try:
        snapshot = repo_snapshot(workspace, cfg)
    except Exception as exc:
        snapshot = f"SNAPSHOT_UNAVAILABLE: {exc}"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior coding agent doing a read-only review over a trusted repository snapshot. "
                "Reply in Czech. Never claim edits or execution. "
                "Base every conclusion only on the provided snapshot. "
                "Do not answer with generic software advice. "
                "If the snapshot is insufficient, say exactly which file or command output is missing. "
                "When listing blockers, risks, or architecture observations, make them concrete and reference the relevant file paths."
            ),
        },
        {
            "role": "user",
            "content": f"Úkol:\n{task}\n\nSnapshot:\n{snapshot[:22000]}",
        },
    ]
    response = ollama_chat(codex_local_runtime_model_name(task=task, role=ROLE_REVIEWER), messages, timeout=240)
    return response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


def agent_plan(task, requested_workspace, controller_workspace, workspace_exists, conversation_context=""):
    default, workspaces = load_registry()
    cfg = workspaces.get(controller_workspace) or workspaces.get(default)
    try:
        snapshot = repo_snapshot(controller_workspace, cfg) if cfg else ""
    except Exception as exc:
        snapshot = f"SNAPSHOT_UNAVAILABLE: {exc}"
    model_id = codex_local_runtime_model_name(task=task, role=ROLE_PLANNER)
    parsed, raw, _meta = structured_json_chat(
        model_id,
        agent_taskspec_messages(requested_workspace, controller_workspace, workspace_exists, task, snapshot, conversation_context),
        "agent_taskspec",
        agent_taskspec_schema(),
        timeout=240,
    )
    taskspec = normalize_agent_taskspec(parsed, requested_workspace, controller_workspace, workspace_exists, task, conversation_context)
    plan = agent_taskspec_to_plan(taskspec, requested_workspace, controller_workspace, workspace_exists, task)
    return plan, taskspec, raw


def admin_agent_meta(plan, requested_workspace, controller_workspace, workspace_exists, workspaces):
    capability = str(plan.get("meta_capability") or "").strip() or "workspace_context_status"
    current = requested_workspace if workspace_exists else controller_workspace
    cfg = workspaces.get(current) or {}
    known = sorted(workspaces)
    if capability == "workspace_context_set":
        if workspace_exists:
            answer = (
                f"Pracuju ve workspace `{requested_workspace}`."
                + (f" Cesta: `{cfg.get('path', '')}`." if cfg.get("path") else "")
                + " Muzeme rovnou pokracovat dalsim ukolem v nem."
            )
        else:
            answer = (
                f"Workspace `{requested_workspace}` neni registrovany; zustavam v `{controller_workspace}`."
                + (f" Zname workspaces: {', '.join(f'`{item}`' for item in known[:6])}." if known else "")
            )
        return {
            "ok": bool(workspace_exists),
            "capability": capability,
            "current_workspace": current,
            "requested_workspace": requested_workspace,
            "workspace_exists": bool(workspace_exists),
            "path": str(cfg.get("path") or ""),
            "known_workspaces": known,
            "answer": answer,
        }
    if capability == "capability_catalog_show":
        registry = agent_capability_registry()
        implemented = sorted(
            name for name, spec in registry.items() if isinstance(spec, dict) and spec.get("implemented")
        )
        aliases = {
            alias: target
            for alias, target in sorted(CANONICAL_AGENT_CAPABILITY_ALIASES.items())
            if alias != target
        }
        return {
            "ok": True,
            "capability": capability,
            "current_workspace": current,
            "implemented": implemented,
            "aliases": aliases,
            "issues": agent_capability_registry_issues(),
            "contract_issues": agent_capability_contract_issues(registry),
            "contract_count": len([name for name in implemented if agent_capability_contract(name, registry).get("implemented")]),
            "catalog": agent_capability_catalog(),
            "answer": (
                "Jsem codex-local, lokální Codex-like agent v OpenWebUI. "
                "Umím běžný chat, veřejný web, práci ve workspacech a repozitářích, kód, testy, "
                "Git/SSH/push/deploy i self-improve workflow. "
                + agent_capability_human_summary()
                + ". Pro konkrétní repo stačí napsat třeba `repo: Test2` a pak normální zadání."
            ),
        }
    if capability == "agent_runtime_status":
        health = runtime_health()
        model_runtime = health.get("model_runtime") or {}
        default_model = str(model_runtime.get("default_model") or "").strip()
        heavy_model = str(model_runtime.get("heavy_model") or "").strip()
        return {
            "ok": bool(health.get("codex_local_ready")),
            "capability": capability,
            "current_workspace": current,
            "runtime_commit": health.get("runtime_commit"),
            "runtime_fingerprint": health.get("runtime_fingerprint"),
            "readiness_issues": health.get("readiness_issues") or [],
            "model_runtime": health.get("model_runtime") or {},
            "answer": (
                f"Runtime je {'pripraveny' if health.get('codex_local_ready') else 'nepripraveny'} pro workspace `{current}`. "
                f"Commit `{health.get('runtime_commit')}`"
                + (f", default model `{default_model}`" if default_model else "")
                + (f", heavy model `{heavy_model}`" if heavy_model else "")
                + "."
            ),
        }
    return {
        "ok": True,
        "capability": "workspace_context_status",
        "current_workspace": current,
        "requested_workspace": requested_workspace,
        "controller_workspace": controller_workspace,
        "workspace_exists": bool(workspace_exists),
        "path": str(cfg.get("path") or ""),
        "known_workspaces": known,
        "answer": (
            f"Jsem ve workspace `{current}`."
            + (f" Cesta: `{cfg.get('path', '')}`." if cfg.get("path") else "")
            + (f" Zname workspaces: {', '.join(f'`{item}`' for item in known[:6])}." if known else "")
        ),
    }


WORKSPACE_SEARCH_SKIP_PREFIXES = (
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


def _workspace_search_skipped(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    if not rel:
        return False
    if rel.endswith("/"):
        rel_dir = rel
    else:
        rel_dir = rel + "/" if "." not in Path(rel).name else rel
    return any(rel.startswith(prefix) or rel_dir.startswith(prefix) for prefix in WORKSPACE_SEARCH_SKIP_PREFIXES)


def _workspace_search_python_fallback(root: Path, query: str, max_matches: int, timeout: int, started: float):
    matches = []
    case_sensitive = any(ch.isupper() for ch in query)
    needle = query if case_sensitive else query.lower()
    root_resolved = root.resolve(strict=False)
    scanned_files = 0

    for current, dirs, names in os.walk(root_resolved):
        if time.time() - started > timeout:
            return {
                "ok": False,
                "action": "workspace_search",
                "root": str(root),
                "query": query,
                "command": ["python_fallback_workspace_search", query],
                "exit_code": 124,
                "match_count": len(matches),
                "matches": matches,
                "output": "\n".join(matches) or "workspace search timed out",
                "duration_ms": int((time.time() - started) * 1000),
                "search_backend": "python_fallback",
                "scanned_files": scanned_files,
            }

        current_path = Path(current)
        rel_current = current_path.relative_to(root_resolved).as_posix()
        if rel_current == ".":
            rel_current = ""

        kept_dirs = []
        for dirname in dirs:
            rel_dir = (Path(rel_current) / dirname).as_posix() if rel_current else dirname
            if not _workspace_search_skipped(rel_dir + "/"):
                kept_dirs.append(dirname)
        dirs[:] = kept_dirs

        for name in names:
            rel_path = (Path(rel_current) / name).as_posix() if rel_current else name
            if _workspace_search_skipped(rel_path):
                continue
            path = current_path / name
            try:
                if not path.is_file() or path.stat().st_size > 512_000:
                    continue
                scanned_files += 1
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line_no, line in enumerate(handle, 1):
                        haystack = line if case_sensitive else line.lower()
                        if needle not in haystack:
                            continue
                        preview = line.rstrip("\n\r")
                        matches.append(f"{rel_path}:{line_no}:{preview}")
                        if len(matches) >= max_matches:
                            return {
                                "ok": True,
                                "action": "workspace_search",
                                "root": str(root),
                                "query": query,
                                "command": ["python_fallback_workspace_search", query],
                                "exit_code": 0,
                                "match_count": len(matches),
                                "truncated": True,
                                "matches": matches,
                                "output": "\n".join(matches),
                                "duration_ms": int((time.time() - started) * 1000),
                                "search_backend": "python_fallback",
                                "scanned_files": scanned_files,
                            }
            except OSError:
                continue

    return {
        "ok": True,
        "action": "workspace_search",
        "root": str(root),
        "query": query,
        "command": ["python_fallback_workspace_search", query],
        "exit_code": 0,
        "match_count": len(matches),
        "truncated": False,
        "matches": matches,
        "output": "\n".join(matches),
        "duration_ms": int((time.time() - started) * 1000),
        "search_backend": "python_fallback",
        "scanned_files": scanned_files,
    }


def admin_workspace_search(payload):
    workspace = str(payload.get("workspace") or "").strip()
    query = str(payload.get("query") or "").strip()
    max_matches = int(payload.get("max_matches") or 80)
    timeout = int(payload.get("timeout") or 20)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    if not query or len(query) > 200:
        raise ValueError("query must be 1..200 characters")
    max_matches = max(1, min(max_matches, 200))
    timeout = max(1, min(timeout, 60))
    root = workspace_root(workspace)
    cmd = [
        "rg",
        "--fixed-strings",
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--smart-case",
        "--max-count",
        str(max_matches),
        "--max-filesize",
        "512K",
        "--glob",
        "!.git/**",
        "--glob",
        "!codex/state/**",
        "--glob",
        "!codex/audit/**",
        "--glob",
        "!logs/**",
        "--glob",
        "!node_modules/**",
        "--glob",
        "!__pycache__/**",
        "--glob",
        "!.venv/**",
        "--glob",
        "!venv/**",
        "--glob",
        "!dist/**",
        "--glob",
        "!build/**",
        "--glob",
        "!.next/**",
        query,
        ".",
    ]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError:
        result = _workspace_search_python_fallback(root, query, max_matches, timeout, started)
        result["workspace"] = workspace
        result["rg_error"] = "rg is not installed in the workspace runtime; used bounded Python fallback."
        return result
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "action": "workspace_search",
            "workspace": workspace,
            "root": str(root),
            "query": query,
            "command": cmd,
            "exit_code": 124,
            "match_count": 0,
            "matches": [],
            "output": str(exc.stdout or exc.stderr or "workspace search timed out"),
            "duration_ms": int((time.time() - started) * 1000),
        }
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    matches = lines[:max_matches]
    return {
        "ok": proc.returncode in {0, 1},
        "action": "workspace_search",
        "workspace": workspace,
        "root": str(root),
        "query": query,
        "command": cmd,
        "exit_code": proc.returncode,
        "match_count": len(matches),
        "truncated": len(lines) > len(matches),
        "matches": matches,
        "output": "\n".join(matches),
        "duration_ms": int((time.time() - started) * 1000),
    }


def admin_agent_loop(payload):
    requested_workspace = str(payload.get("workspace") or "").strip() or "ai-stack"
    requested_model = str(payload.get("model") or DEFAULT_MODEL_ALIAS).strip() or DEFAULT_MODEL_ALIAS
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", requested_workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    task = str(payload.get("task") or "").strip()
    if not task or len(task) > 6000:
        raise ValueError("task must be 1..6000 characters")
    conversation_context = recent_conversation_context(payload.get("messages") or [])
    controller_workspace, workspace_exists, workspaces = agent_controller_workspace(requested_workspace)
    planner_source = "llm"
    try:
        plan, taskspec, raw_plan = agent_plan(task, requested_workspace, controller_workspace, workspace_exists, conversation_context)
    except Exception as exc:
        fallback = agent_fallback_plan(task, requested_workspace, controller_workspace, workspace_exists)
        if fallback:
            plan, raw_plan = fallback
        else:
            raw_plan = {
                "error": f"{type(exc).__name__}: {exc}",
                "recovery": "Restart or reconnect the LLM backend, then retry. Gateway will not infer natural-language intent with keyword routing.",
            }
            plan = {
                "workflow": "clarify",
                "reason": "LLM planner unavailable and no structural fallback applies.",
                "read_only": True,
                "workspace": requested_workspace if workspace_exists else controller_workspace,
                "action": "",
                "command": [],
                "run_after": "",
                "followup_actions": [],
                "repo_name": "",
                "github": False,
                "remote_url": "",
                "desired_end_state": "planner_recovered_or_user_retries",
                "url": "",
                "question": "",
                "ssh_comment": "",
                "confidence": "low",
                "required_capabilities": ["clarify_or_infer_capability"],
                "missing_capabilities": [],
                "missing_inputs": ["llm_planner_online"],
                "meta_capability": "",
                "search_query": "",
                "capability_locked": True,
                "routing_provenance": "fallback:planner_offline",
            }
        taskspec = {
            "current_workspace": requested_workspace if workspace_exists else controller_workspace,
            "user_goal": str(task or "").strip(),
            "is_new_workspace_request": bool(plan.get("workflow") == "bootstrap"),
            "is_existing_workspace_task": bool(workspace_exists and plan.get("workflow") != "bootstrap"),
            "target_repo_name": agent_extract_repo_name(task),
            "remote_url": agent_remote_url_from_task(task),
            "desired_end_state": str(plan.get("desired_end_state") or plan.get("workflow") or "").strip(),
            "required_capabilities": _string_list(plan.get("required_capabilities")) or ["clarify_or_infer_capability"],
            "missing_inputs": _string_list(plan.get("missing_inputs")),
            "risk_level": "medium",
            "recovery_plan": str(raw_plan.get("recovery") if isinstance(raw_plan, dict) else "") or "LLM planner failed; deterministic structural fallback selected the narrowest bounded capability.",
            "read_only": bool(plan.get("read_only")),
            "command": plan.get("command") or [],
            "action": str(plan.get("action") or "").strip(),
            "run_after": str(plan.get("run_after") or "").strip(),
            "followup_actions": plan.get("followup_actions") or [],
            "url": str(plan.get("url") or "").strip(),
            "question": str(plan.get("question") or "").strip(),
            "ssh_comment": str(plan.get("ssh_comment") or "").strip(),
            "confidence": "medium",
            "routing_provenance": str(plan.get("routing_provenance") or "fallback:structural"),
        }
        planner_source = "fallback"

    result = {
        "ok": False,
        "requested_workspace": requested_workspace,
        "controller_workspace": controller_workspace,
        "workspace_exists": workspace_exists,
        "model_runtime": codex_local_runtime_surface(requested_model, task=task, role=ROLE_AGENT),
        "task": task,
        "conversation_context": conversation_context,
        "plan": plan,
        "taskspec": taskspec,
        "raw_plan": raw_plan,
        "planner_source": planner_source,
        "routing_provenance": (
            str(taskspec.get("routing_provenance") or "").strip()
            or ("llm_taskspec" if planner_source == "llm" else "fallback:structural")
        ),
        "workflow": plan["workflow"],
        "read_only": plan["read_only"],
    }

    workflow = plan["workflow"]
    if workflow == "clarify":
        missing_capabilities = _string_list(plan.get("missing_capabilities") or taskspec.get("missing_capabilities"))
        missing_inputs = _string_list(plan.get("missing_inputs") or taskspec.get("missing_inputs"))
        result["ok"] = False
        if plan.get("needs_user_input") or "await_user_confirmation" in _string_list(plan.get("required_capabilities")):
            result["summary"] = "AWAIT_USER_CONFIRMATION: workflow is paused before a user-confirmed step."
            result["recovery"] = {
                "text": str(taskspec.get("recovery_plan") or "Potvrď pokračování a agent naváže dalším bezpečným capability krokem."),
                "required_capabilities": _string_list(taskspec.get("required_capabilities")),
            }
            result["answer"] = (
                "Čekám na potvrzení před dalším krokem. "
                + str(taskspec.get("recovery_plan") or "Potvrď prosím, že mám pokračovat.")
            ).strip()
        elif missing_capabilities:
            result["summary"] = "NEEDS_ATTENTION: TaskSpec requested unsupported capability."
            result["recovery"] = {
                "text": "Doplň nebo implementuj pojmenovanou capability, pak task zopakuj. Gateway nebude provádět jinou akci jako náhradu.",
                "missing_capabilities": missing_capabilities,
                "capability_registry": "docs/codex-local-capability-roadmap.json",
            }
            result["answer"] = (
                "NEEDS_ATTENTION: chybí podporovaná capability "
                + ", ".join(missing_capabilities)
                + ". Neprovedu náhradní workflow, aby se nestalo něco jiného než uživatel chtěl."
            )
        elif missing_inputs:
            result["summary"] = "NEEDS_ATTENTION: TaskSpec is missing required inputs."
            result["recovery"] = {
                "text": "Doplň chybějící vstupy a zopakuj task; bez nich by agent musel hádat cílový stav.",
                "missing_inputs": missing_inputs,
            }
            result["answer"] = "NEEDS_ATTENTION: chybí vstupy " + ", ".join(missing_inputs) + "."
        else:
            result["summary"] = "NEEDS_ATTENTION: Agent could not map TaskSpec to an executable capability."
            result["recovery"] = {
                "text": str(taskspec.get("recovery_plan") or "Zpřesni cíl nebo přidej capability do registry."),
                "required_capabilities": _string_list(taskspec.get("required_capabilities")),
            }
            result["answer"] = (
                "NEEDS_ATTENTION: nerozpoznal jsem bezpečný capability krok. "
                "Neprovedu náhradní akci, která by mohla mířit jinam než zadání."
            )
        return result

    if workflow == "direct_answer":
        execution = agent_direct_answer_response(task, plan.get("intent_class") or "direct_answer", conversation_context)
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = "Direct answer completed." if result["ok"] else "Direct answer failed."
        result["answer"] = str(execution.get("answer") or "").strip()
        return result

    if workflow == "meta":
        execution = admin_agent_meta(plan, requested_workspace, controller_workspace, workspace_exists, workspaces)
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = "Meta capability completed." if result["ok"] else "Meta capability needs attention."
        result["answer"] = str(execution.get("answer") or "").strip()
        if not result["ok"]:
            result["recovery"] = {
                "text": "Použij registrovaný workspace nebo vytvoř nový workspace bootstrapem.",
                "known_workspaces": sorted(workspaces),
            }
        return result

    if workflow == "review":
        answer = agent_review_response(plan["workspace"], task)
        result["ok"] = bool(answer)
        result["summary"] = "Read-only review completed."
        result["answer"] = answer or "Read-only review nedala žádný obsah."
        return result

    if workflow == "web_fetch":
        fetch = admin_web_fetch({"url": plan["url"]})
        result["ok"] = bool(fetch.get("ok"))
        result["summary"] = "Public web fetch completed." if result["ok"] else "Public web fetch failed."
        result["execution"] = fetch
        return result

    if workflow == "web_answer":
        answer_result = admin_web_answer({"url": plan["url"], "question": plan["question"] or task})
        result["ok"] = bool(answer_result.get("ok"))
        result["summary"] = "Public web answer completed." if result["ok"] else "Public web answer failed."
        result["execution"] = answer_result
        return result

    if workflow == "web_search":
        search_result = admin_public_web_search({"query": plan.get("search_query") or plan.get("question") or task})
        result["ok"] = bool(search_result.get("ok"))
        result["summary"] = "Public web search completed." if result["ok"] else "Public web search failed."
        result["execution"] = search_result
        result["answer"] = str(search_result.get("answer") or "").strip()
        if not result["ok"]:
            result["recovery"] = {
                "text": str(search_result.get("recovery") or "Veřejné vyhledání selhalo; zkus konkrétní URL nebo oprav outbound síť."),
                "query": search_result.get("query"),
                "error": search_result.get("error"),
            }
        return result

    if workflow == "deploy":
        deploy = admin_deploy_stack({"branch": "main"})
        result["ok"] = bool(deploy.get("ok"))
        result["summary"] = "ai-stack deploy scheduled." if result["ok"] else "ai-stack deploy was not scheduled."
        result["execution"] = deploy
        return result

    if workflow == "self_improve":
        requested_capabilities = _string_list(taskspec.get("required_capabilities"))
        self_improve_mode = "capability_develop" if "agent_capability_develop" in requested_capabilities else "diagnose"
        execution = admin_agent_self_improve({
            "workspace": "ai-stack",
            "mode": self_improve_mode,
            "dry_run": True,
            "prompt": task,
            "expected_behavior": str(taskspec.get("desired_end_state") or "").strip(),
            "capability_name": str(taskspec.get("target_capability_name") or "").strip(),
            "target_capability_name": str(taskspec.get("target_capability_name") or "").strip(),
            "feature_request": task if self_improve_mode == "capability_develop" else "",
            "timeout": 900,
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = "Self-improve diagnosis artifact created." if result["ok"] else "Self-improve needs attention."
        result["answer"] = (
            f"AGENT_SELF_IMPROVE {'OK' if result['ok'] else 'NEEDS_ATTENTION'}\n"
            f"mode={execution.get('mode')}\n"
            f"dry_run={execution.get('dry_run')}\n"
            f"artifact_dir={execution.get('artifact_dir')}\n"
            f"exit_code={execution.get('exit_code')}"
        )
        return result

    if workflow in {"workspace_search", "edit", "action", "autopilot", "ssh_key_create", "ssh_key_show_public"} and not workspace_exists:
        result["summary"] = f"Workspace '{requested_workspace}' zatím není registrovaný."
        result["recovery"] = {
            "text": "Nejdřív vytvoř nebo zaregistruj workspace, případně použij bootstrap workflow.",
            "suggested_workflow": "bootstrap",
        }
        return result

    if workflow == "workspace_search":
        execution = admin_workspace_search({
            "workspace": plan["workspace"],
            "query": plan.get("search_query") or taskspec.get("search_query") or agent_workspace_search_query_from_task(task),
            "max_matches": 80,
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = "Workspace search completed." if result["ok"] else "Workspace search failed."
        if not result["ok"]:
            result["recovery"] = {
                "text": "Zkontroluj, že je v runtime dostupný `rg`, nebo přidej fallback search capability.",
                "query": execution.get("query"),
            }
        return result

    if workflow == "edit":
        execution = admin_workspace_edit({
            "workspace": plan["workspace"],
            "task": task,
            "run_after": plan["run_after"],
            "timeout": 900,
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = "Safe edit applied and verified." if result["ok"] else "Safe edit failed."
        if not result["ok"] and plan["run_after"]:
            result["recovery"] = workspace_action_failure_recommendation(plan["workspace"], plan["run_after"], execution.get("run_result") or execution)
        return result

    if workflow == "action":
        action = plan["action"] or "verify"
        execution = admin_workspace_action({
            "workspace": plan["workspace"],
            "action": action,
            "timeout": int((load_workspace_action_registry().get(action) or {}).get("timeout", 900)),
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = f"Workspace action {action} completed." if result["ok"] else f"Workspace action {action} failed."
        if not result["ok"]:
            result["recovery"] = workspace_action_failure_recommendation(plan["workspace"], action, execution)
        return result

    if workflow == "run":
        command = plan.get("command") or []
        if not command:
            result["summary"] = "Run workflow needs a concrete command."
            result["recovery"] = {"text": "Uveď konkrétní krátký příkaz, který se má spustit v workspace kontejneru."}
            return result
        execution = admin_run_workspace({
            "workspace": plan["workspace"],
            "command": command,
            "timeout": 300,
            "runner": "container",
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = "Workspace command completed." if result["ok"] else "Workspace command failed."
        return result

    if workflow == "autopilot":
        allow_actions = plan["followup_actions"] or ["install", "verify", "smoke", "test", "build", "lint"]
        execution = admin_workspace_autopilot({
            "workspace": plan["workspace"],
            "allow_actions": allow_actions,
            "max_steps": 3,
            "task": task,
            "desired_end_state": plan.get("desired_end_state") or taskspec.get("desired_end_state") or "",
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = "Autopilot loop completed." if result["ok"] else "Autopilot loop stopped on a blocker."
        if not result["ok"]:
            result["recovery"] = {
                "text": str(execution.get("recommendation", "")).strip(),
                "patch_target": str(execution.get("patch_target", "")).strip(),
                "patch_hint": str(execution.get("patch_hint", "")).strip(),
                "patch_summary": str(execution.get("patch_summary", "")).strip(),
                "read_command": str(execution.get("read_command", "")).strip(),
            }
        return result

    if workflow == "ssh_key_create":
        execution = admin_workspace_ssh_key({
            "workspace": plan["workspace"],
            "mode": "create",
            "comment": plan.get("ssh_comment") or f"{plan['workspace']}@local",
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = str(execution.get("summary") or "Workspace SSH key completed.")
        return result

    if workflow == "ssh_key_show_public":
        execution = admin_workspace_ssh_key({
            "workspace": plan["workspace"],
            "mode": "show_public",
            "comment": plan.get("ssh_comment") or f"{plan['workspace']}@local",
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = str(execution.get("summary") or "Workspace SSH public key completed.")
        return result

    if workflow == "workspace_git_publish":
        execution = admin_workspace_git_publish({
            "workspace": plan["workspace"],
            "remote_url": plan.get("remote_url") or agent_remote_url_from_task(task),
            "branch": "main",
            "comment": plan.get("ssh_comment") or f"{plan['workspace']}@local",
            "commit_message": str(payload.get("commit_message") or "chore: publish workspace").strip() or "chore: publish workspace",
            "timeout": 1200,
        })
        result["execution"] = execution
        result["ok"] = bool(execution.get("ok"))
        result["summary"] = str(execution.get("summary") or "Workspace git publish completed.")
        if execution.get("status") == "MANUAL_STEP_REQUIRED":
            result["recovery"] = {
                "text": str(execution.get("recovery") or "").strip(),
                "public_key": str(execution.get("public_key") or "").strip(),
                "public_key_path": str(execution.get("public_key_path") or "").strip(),
                "remote_url": str(execution.get("remote_url") or "").strip(),
            }
        return result

    if workflow == "bootstrap":
        repo_name = plan["repo_name"]
        if not repo_name or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", repo_name):
            result["summary"] = "Bootstrap workflow needs a valid repository name."
            result["recovery"] = {"text": "Uveď prosím jméno nového repository/workspace."}
            return result
        execution = admin_create_local_repo({
            "name": repo_name,
            "github": plan["github"],
            "restart": False,
        })
        result["execution"] = execution
        if execution.get("ok") or execution.get("partial_ok"):
            followup = None
            if plan["followup_actions"]:
                followup = admin_workspace_autopilot({
                    "workspace": repo_name,
                    "allow_actions": plan["followup_actions"],
                    "max_steps": min(3, max(1, len(plan["followup_actions"]))),
                    "task": task,
                    "desired_end_state": plan.get("desired_end_state") or taskspec.get("desired_end_state") or "",
                })
            result["followup"] = followup
            result["ok"] = bool(execution.get("ok")) and (followup is None or bool(followup.get("ok")))
            result["summary"] = "Repository bootstrap completed." if result["ok"] else "Repository bootstrap completed with follow-up blockers."
            if followup and not followup.get("ok"):
                result["recovery"] = {
                    "text": str(followup.get("recommendation", "")).strip(),
                    "patch_target": str(followup.get("patch_target", "")).strip(),
                    "patch_hint": str(followup.get("patch_hint", "")).strip(),
                    "patch_summary": str(followup.get("patch_summary", "")).strip(),
                    "read_command": str(followup.get("read_command", "")).strip(),
                }
            elif plan["github"]:
                result["recovery"] = {
                    "text": "Po vložení vygenerovaného public key do GitHubu mi potvrď pokračování a zkusím remote/push krok.",
                }
            return result
        result["summary"] = "Repository bootstrap failed."
        result["ok"] = False
        return result

    result["summary"] = f"Workflow {workflow} zatím není implementovaný."
    return result

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
        "workspace_registered": proc.returncode == 0,
        "action": "add_workspace",
        "name": name,
        "path": path,
        "exit_code": proc.returncode,
        "output": proc.stdout.strip(),
    }

    if restart:
        start_script = str(REPO_ROOT / "codex/bin/start_codex_stack.sh")
        attempts = [
            ("direct", ["bash", start_script]),
        ]
        wsl_exe = os.getenv("WSL_EXE", "/mnt/c/Windows/System32/wsl.exe")
        distro = os.getenv("WSL_DEPLOY_DISTRO") or os.getenv("WSL_DISTRO_NAME") or "Ubuntu"
        if Path(wsl_exe).is_file():
            attempts.append(("wsl-root", [wsl_exe, "-d", distro, "-u", "root", "-e", "bash", start_script]))
        restart_attempts = []
        restart_ok = False
        for label, command in attempts:
            bash = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=360,
            )
            restart_attempts.append({
                "method": label,
                "command": command,
                "exit_code": bash.returncode,
                "output": bash.stdout.strip(),
            })
            if bash.returncode == 0:
                restart_ok = True
                break
        result["restart_attempts"] = restart_attempts
        result["restart_exit_code"] = restart_attempts[-1]["exit_code"] if restart_attempts else None
        result["restart_output"] = restart_attempts[-1]["output"] if restart_attempts else ""
        result["restart_ok"] = restart_ok
        result["ok"] = result["ok"] and result["restart_ok"]
        if not result["restart_ok"]:
            result["next_step"] = "Run the ai-stack deploy/restart capability after reviewing the created workspace."
    elif restart:
        result["restart_ok"] = False
    else:
        result["restart_ok"] = None
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

def workspace_root(workspace_name):
    _, workspaces = load_registry()
    if workspace_name not in workspaces:
        raise ValueError(f"Unknown workspace '{workspace_name}'. Allowed: {', '.join(sorted(workspaces))}")
    root = Path(workspaces[workspace_name]["path"])
    if not root.exists():
        raise ValueError(f"Workspace path does not exist: {root}")
    return root

def safe_workspace_file(workspace_name, rel_path):
    if not isinstance(rel_path, str):
        raise ValueError("path must be a string")
    rel = rel_path.strip().strip('"').strip("'").lstrip("/")
    if not rel:
        raise ValueError("path is required")
    root = workspace_root(workspace_name)
    target = safe_child_path(root, rel)
    rel_norm = target.relative_to(root.resolve(strict=False)).as_posix()
    if Path(rel_norm).name in SENSITIVE_FILE_NAMES:
        raise PermissionError(f"Refusing to read sensitive file: {rel_norm}")
    if any(rel_norm.startswith(prefix) for prefix in SENSITIVE_FILE_PREFIXES):
        raise PermissionError(f"Refusing to read ignored/runtime path: {rel_norm}")
    if not target.is_file():
        raise FileNotFoundError(f"File does not exist in workspace {workspace_name}: {rel_norm}")
    if target.stat().st_size > 512_000:
        raise ValueError(f"File is too large for inline explanation: {rel_norm}")
    return root, target, rel_norm

def read_numbered_workspace_file(workspace_name, rel_path, start=1, end=None, max_lines=400):
    _, target, rel_norm = safe_workspace_file(workspace_name, rel_path)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(1, int(start or 1))
    default_end = start + max_lines - 1
    end = int(end or default_end)
    end = min(max(start, end), total)
    if end - start + 1 > max_lines:
        end = start + max_lines - 1
    width = max(4, len(str(max(end, total, 1))))
    numbered = "\n".join(f"{idx:0{width}d}: {lines[idx - 1]}" for idx in range(start, end + 1))
    return {
        "path": rel_norm,
        "start": start,
        "end": end,
        "total_lines": total,
        "numbered": numbered,
        "truncated": end < total,
    }

def admin_explain_file(payload):
    workspace = str(payload.get("workspace") or "").strip()
    path = str(payload.get("path") or "").strip()
    start = int(payload.get("start") or 1)
    end = payload.get("end")
    question = str(payload.get("question") or "").strip()
    model = str(payload.get("model") or "qwen2.5-coder:14b")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("Unsafe workspace name")
    if model not in {"qwen2.5-coder:14b", "qwen2.5-coder:32b"}:
        raise ValueError("Unsupported explain model")

    data = read_numbered_workspace_file(workspace, path, start=start, end=end, max_lines=400)
    prompt_question = question or "Přečti tento soubor a vysvětli ho řádek po řádku."
    messages = [
        {
            "role": "system",
            "content": (
                "Jsi lokální senior coding agent. Vysvětli poskytnutý očíslovaný soubor. "
                "Odpovídej česky, věcně a neopisuj celý soubor. Používej odkazy na čísla řádků. "
                "Když jsou sousední řádky jedna logická část, spoj je do jedné stručné položky. "
                "Neříkej, že soubor nemáš; soubor je níže ve zprávě."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Workspace: {workspace}\n"
                f"Soubor: {data['path']}\n"
                f"Rozsah: {data['start']}-{data['end']} z {data['total_lines']} řádků\n"
                f"Úkol: {prompt_question}\n\n"
                "Očíslovaný obsah:\n"
                f"```text\n{data['numbered']}\n```"
            ),
        },
    ]
    resp = ollama_chat(model, messages, timeout=300)
    answer = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    return {
        "ok": bool(answer),
        "action": "file_explain",
        "workspace": workspace,
        "path": data["path"],
        "start": data["start"],
        "end": data["end"],
        "total_lines": data["total_lines"],
        "truncated": data["truncated"],
        "question": prompt_question,
        "answer": answer or "Model nevrátil odpověď.",
        "numbered": data["numbered"],
        "usage": resp.get("usage", {}),
    }

def extract_unified_diff(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Model did not return any patch text")
    fenced = re.search(r"(?is)```(?:diff|patch)?\s*\n(.*?)\n```", text)
    diff = fenced.group(1).strip() if fenced else text.strip()
    if "diff --git " in diff:
        start = diff.find("diff --git ")
        diff = diff[start:].strip()
    else:
        start = diff.find("--- ")
        if start >= 0:
            diff = diff[start:].strip()
    if not diff.startswith(("diff --git ", "--- ")):
        raise ValueError("Model response did not contain a unified diff")
    return diff + ("\n" if not diff.endswith("\n") else "")

def diff_target_paths(diff_text):
    paths = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                for item in parts[2:4]:
                    if item.startswith(("a/", "b/")):
                        rel = item[2:]
                        if rel != "/dev/null":
                            paths.add(rel)
            continue
        if line.startswith(("--- ", "+++ ")):
            rel = line[4:].strip().split("\t", 1)[0]
            if rel == "/dev/null":
                continue
            if rel.startswith(("a/", "b/")):
                rel = rel[2:]
            paths.add(rel)
    return sorted(paths)

def validate_edit_paths(root, paths, max_files):
    if not paths:
        raise ValueError("Patch does not reference any workspace file")
    if len(paths) > max_files:
        raise ValueError(f"Patch touches too many files: {len(paths)} > {max_files}")
    safe_paths = []
    root_resolved = root.resolve(strict=False)
    for rel in paths:
        rel = rel.strip().replace("\\", "/").lstrip("/")
        if not rel or rel == ".":
            raise ValueError("Patch contains an empty path")
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise PermissionError(f"Refusing unsafe patch path: {rel}")
        if Path(rel).name in SENSITIVE_FILE_NAMES:
            raise PermissionError(f"Refusing sensitive patch path: {rel}")
        if any(rel.startswith(prefix) for prefix in SENSITIVE_FILE_PREFIXES):
            raise PermissionError(f"Refusing runtime/ignored patch path: {rel}")
        target = (root / rel).resolve(strict=False)
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise PermissionError(f"Patch path escapes workspace: {rel}") from exc
        safe_paths.append(rel)
    return safe_paths

def workspace_edit_snapshot(root, max_bytes=18000):
    files = list_files(root)
    status = run_ro(["git", "status", "--short", "--branch"], root, 10) if (root / ".git").exists() else "not a git repo"
    snippets = []
    total = 0
    preferred_names = {
        "README.md", "README", "package.json", "pyproject.toml", "requirements.txt",
        "index.html", "src/main.js", "src/App.jsx", "main.py", "app.py",
    }
    for rel in files:
        include = Path(rel).name in preferred_names or rel.lower().startswith("readme")
        if include or len(snippets) < 8:
            text = read_small(root, rel, 3000)
            if text:
                block = f"--- {rel} ---\n{text[:3000]}"
                snippets.append(block)
                total += len(block)
        if total >= max_bytes:
            break
    return "\n".join([
        "GIT STATUS:",
        status[:4000],
        "",
        "FILES:",
        "\n".join(files[:300]),
        "",
        "SNIPPETS:",
        "\n\n".join(snippets)[:max_bytes],
    ])

def admin_workspace_edit(payload):
    workspace = str(payload.get("workspace") or "").strip()
    task = str(payload.get("task") or "").strip()
    model = str(payload.get("model") or "qwen2.5-coder:14b").strip()
    timeout = int(payload.get("timeout") or 600)
    max_files = int(payload.get("max_files") or 6)
    max_diff_chars = int(payload.get("max_diff_chars") or 80_000)
    run_after = str(payload.get("run_after") or "").strip().lower()
    run_timeout = int(payload.get("run_timeout") or 900)
    run_runner = str(payload.get("runner") or os.getenv("CODEX_GATEWAY_WORKSPACE_RUNNER", "container")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    if not task or len(task) > 4000:
        raise ValueError("task must be 1..4000 characters")
    if model not in {"qwen2.5-coder:14b", "qwen2.5-coder:32b"}:
        raise ValueError("Unsupported edit model")
    if timeout < 30 or timeout > 1800:
        raise ValueError("timeout must be between 30 and 1800")
    if max_files < 1 or max_files > 20:
        raise ValueError("max_files must be between 1 and 20")
    if max_diff_chars < 1000 or max_diff_chars > 300_000:
        raise ValueError("max_diff_chars must be between 1000 and 300000")
    if run_after and run_after not in {"install", "verify", "smoke", "test", "build", "lint"}:
        raise ValueError("run_after must be one of install, verify, smoke, test, build, lint")
    if run_timeout < 1 or run_timeout > 3600:
        raise ValueError("run_timeout must be between 1 and 3600")
    if run_runner not in {"container", "host"}:
        raise ValueError("runner must be container or host")

    root = workspace_root(workspace)
    if not (root / ".git").exists():
        raise ValueError("workspace edit currently requires a git repository")

    snapshot = workspace_edit_snapshot(root)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an autonomous local coding agent. Produce exactly one unified diff for the requested repository edit. "
                "Do not explain, do not use markdown except an optional ```diff fenced block, and do not include commands. "
                "Touch only files needed for the task. Do not edit secrets, .git, runtime state, audit logs, dependency folders, or build outputs. "
                "For a simple standalone web visual request in an otherwise empty/minimal repo, create or update index.html with complete runnable HTML/CSS/JS."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Workspace: {workspace}\n"
                f"Root: {root}\n"
                f"Task:\n{task}\n\n"
                f"Repository snapshot:\n{snapshot}\n\n"
                "Return only a unified diff."
            ),
        },
    ]
    started = time.time()
    resp = ollama_chat(model, messages, timeout=timeout)
    model_text = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    diff = extract_unified_diff(model_text)
    if len(diff) > max_diff_chars:
        raise ValueError(f"Patch is too large: {len(diff)} > {max_diff_chars}")
    paths = validate_edit_paths(root, diff_target_paths(diff), max_files)

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        cwd=root,
        input=diff,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    if check.returncode != 0:
        return {
            "ok": False,
            "action": "workspace_edit",
            "workspace": workspace,
            "root": str(root),
            "model": model,
            "files": paths,
            "status": "patch_check_failed",
            "error": check.stdout.strip(),
            "diff": diff,
            "model_text": model_text,
            "usage": resp.get("usage", {}),
            "duration_ms": int((time.time() - started) * 1000),
        }

    apply = subprocess.run(
        ["git", "apply", "--whitespace=nowarn"],
        cwd=root,
        input=diff,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    action_result = None
    if apply.returncode == 0 and run_after:
        action_result = admin_workspace_action({
            "workspace": workspace,
            "action": run_after,
            "timeout": run_timeout,
            "runner": run_runner,
        })
    status = run_ro(["git", "status", "--short", "--branch"], root, 10)
    return {
        "ok": apply.returncode == 0 and (action_result is None or bool(action_result.get("ok"))),
        "action": "workspace_edit",
        "workspace": workspace,
        "root": str(root),
        "model": model,
        "files": paths,
        "status": "applied" if apply.returncode == 0 else "apply_failed",
        "run_after": run_after,
        "run_result": action_result,
        "apply_output": apply.stdout.strip(),
        "diff": diff,
        "model_text": model_text,
        "git_status": status,
        "usage": resp.get("usage", {}),
        "duration_ms": int((time.time() - started) * 1000),
    }

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


def admin_workspace_ssh_key(payload):
    workspace = str(payload.get("workspace") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("Unsafe workspace name")
    _, workspaces = load_registry()
    if workspace not in workspaces:
        raise ValueError(f"Unknown workspace '{workspace}'. Allowed: {', '.join(sorted(workspaces))}")
    mode = str(payload.get("mode") or "create").strip().lower()
    if mode not in {"create", "show_public"}:
        raise ValueError("mode must be create or show_public")
    comment = str(payload.get("comment") or f"{workspace}@local").strip() or f"{workspace}@local"
    key = generate_repo_ssh_key(f"github-{workspace}", comment)
    status = str(key.get("status") or "")
    if mode == "show_public" and status == "SSH_KEY_READY":
        summary = "Workspace SSH public key was missing and has been created now."
    elif mode == "show_public":
        summary = "Workspace SSH public key is ready."
    elif status == "SSH_KEY_READY":
        summary = "Workspace SSH key has been created."
    else:
        summary = "Workspace SSH key already exists."
    return {
        "ok": True,
        "action": f"workspace_ssh_key_{mode}",
        "workspace": workspace,
        "comment": comment,
        "summary": summary,
        "created": status == "SSH_KEY_READY",
        "ssh_key": key,
        "public_key": key.get("public_key", ""),
    }


def workspace_runtime_home_dir(workspace):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("Unsafe workspace name")
    return REPO_ROOT / "codex/state" / f"opencode-home-{workspace}"


def ensure_workspace_runtime_ssh_key(workspace, comment):
    key = generate_repo_ssh_key(f"github-{workspace}", comment)
    source_private = REPO_ROOT / key["private_key_path"]
    source_public = REPO_ROOT / key["public_key_path"]
    runtime_home = workspace_runtime_home_dir(workspace)
    ssh_dir = runtime_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ssh_dir, 0o700)
    except OSError:
        pass
    runtime_private = ssh_dir / source_private.name
    runtime_public = ssh_dir / source_public.name
    runtime_private.write_text(source_private.read_text(encoding="utf-8"), encoding="utf-8")
    runtime_public.write_text(source_public.read_text(encoding="utf-8"), encoding="utf-8")
    config_text = (
        "Host github.com\n"
        f"  IdentityFile /home/opencode/.ssh/{source_private.name}\n"
        "  IdentitiesOnly yes\n"
        "  StrictHostKeyChecking accept-new\n"
    )
    (ssh_dir / "config").write_text(config_text, encoding="utf-8")
    for path, mode in ((runtime_private, 0o600), (runtime_public, 0o644), (ssh_dir / "config", 0o600)):
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    return {
        "workspace": workspace,
        "private_key_path": runtime_private.as_posix(),
        "public_key_path": runtime_public.as_posix(),
        "container_private_key": f"/home/opencode/.ssh/{source_private.name}",
        "container_public_key": f"/home/opencode/.ssh/{source_public.name}",
        "public_key": key.get("public_key", ""),
        "source_key": key,
    }


def git_push_auth_failed(text):
    lower = str(text or "").lower()
    needles = (
        "permission denied (publickey)",
        "could not read from remote repository",
        "repository not found",
        "fatal: could not",
        "git@github.com: permission denied",
        "authentication failed",
    )
    return any(needle in lower for needle in needles)


def admin_workspace_git_publish(payload):
    workspace = str(payload.get("workspace") or "").strip()
    remote_url = str(payload.get("remote_url") or "").strip()
    branch = str(payload.get("branch") or "main").strip() or "main"
    commit_message = str(payload.get("commit_message") or "chore: publish workspace").strip() or "chore: publish workspace"
    comment = str(payload.get("comment") or f"{workspace}@local").strip() or f"{workspace}@local"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("Unsafe workspace name")
    if not remote_url:
        return {
            "ok": False,
            "action": "workspace_git_publish",
            "workspace": workspace,
            "status": "NEEDS_ATTENTION",
            "marker": "WORKSPACE_GIT_REMOTE_URL_MISSING",
            "recovery": "Doplň přesnou remote URL, například git@github.com:owner/repo.git.",
        }
    _, workspaces = load_registry()
    if workspace not in workspaces:
        raise ValueError(f"Unknown workspace '{workspace}'. Allowed: {', '.join(sorted(workspaces))}")

    ssh_runtime = ensure_workspace_runtime_ssh_key(workspace, comment)
    env = {
        "HOME": "/home/opencode",
        "GIT_SSH_COMMAND": (
            f"ssh -i {ssh_runtime['container_private_key']} "
            "-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        ),
    }

    setup_script = (
        "set -eu\n"
        "git rev-parse --is-inside-work-tree >/dev/null 2>&1 || "
        "(git init -b main >/dev/null 2>&1 || (git init >/dev/null 2>&1 && git branch -M main >/dev/null 2>&1))\n"
        "git config user.email 'ai-sandbox@local'\n"
        "git config user.name 'AI Sandbox'\n"
        f"git branch -M {shlex.quote(branch)}\n"
        "if git remote get-url origin >/dev/null 2>&1; then\n"
        f"  git remote set-url origin {shlex.quote(remote_url)}\n"
        "else\n"
        f"  git remote add origin {shlex.quote(remote_url)}\n"
        "fi\n"
        "git add -A\n"
        "if [ -n \"$(git status --porcelain)\" ]; then\n"
        f"  git commit -m {shlex.quote(commit_message)}\n"
        "fi\n"
        f"git push -u origin HEAD:{shlex.quote(branch)}\n"
    )
    execution = admin_run_workspace({
        "workspace": workspace,
        "command": ["sh", "-lc", setup_script],
        "timeout": int(payload.get("timeout", 900)),
        "runner": "container",
        "env": env,
    })
    result = {
        "ok": bool(execution.get("ok")),
        "action": "workspace_git_publish",
        "workspace": workspace,
        "remote_url": remote_url,
        "branch": branch,
        "commit_message": commit_message,
        "runner_result": execution,
        "ssh_key": ssh_runtime["source_key"],
        "runtime_ssh": ssh_runtime,
    }
    if execution.get("ok"):
        result["status"] = "WORKSPACE_GIT_PUBLISH_OK"
        result["summary"] = "Workspace git init/origin/commit/push completed."
        return result
    output = str(execution.get("output") or execution.get("error") or "").strip()
    if git_push_auth_failed(output):
        result.update({
            "status": "MANUAL_STEP_REQUIRED",
            "ok": False,
            "summary": "Git remote/push narazil na SSH nebo GitHub autentizaci.",
            "public_key": ssh_runtime["public_key"],
            "public_key_path": ssh_runtime["source_key"]["public_key_path"],
            "recovery": (
                "Přidej tento public key do GitHubu pro cílový repozitář nebo účet, "
                "ověř že remote URL míří na existující repo, a potom zopakuj workspace_git_publish."
            ),
        })
        return result
    result.update({
        "status": "WORKSPACE_GIT_PUBLISH_FAILED",
        "summary": "Workspace git publish selhal z jiného důvodu než SSH auth.",
    })
    return result


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
    workspace_registered = bool(workspace_result.get("workspace_registered")) or workspace_result.get("exit_code") == 0
    restart_ok = workspace_result.get("restart_ok")
    restart_blocked = restart and restart_ok is False and workspace_registered
    github_ok = (not github) or bool(github_result.get("ok"))
    repo_ok = rc == 0 and workspace_registered and github_ok
    ok = repo_ok and not restart_blocked
    partial_ok = repo_ok and restart_blocked
    next_step = ""
    if partial_ok:
        next_step = "Repository, SSH key, and workspace registration are ready; run GATEWAY_ADMIN_DEPLOY_STACK or start_codex_stack.sh as root to start the new OpenCode container."
    elif ok and not restart:
        next_step = "Repository and workspace are ready. Restart/deploy only if you need the OpenCode container for this workspace immediately."
    return {
        "ok": ok,
        "partial_ok": partial_ok,
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
        "next_step": next_step,
        "commands": commands,
    }

def workspace_run_state_dir():
    path = REPO_ROOT / "codex/state/workspace-runs"
    path.mkdir(parents=True, exist_ok=True)
    return path

def workspace_run_job_path(job_id):
    return workspace_run_state_dir() / f"{job_id}.json"

def workspace_run_write(job_id, payload):
    data = dict(payload)
    data["updated_at"] = int(time.time())
    workspace_run_job_path(job_id).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def workspace_run_read(job_id):
    path = workspace_run_job_path(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def parse_run_check_json_from_log(log_path):
    try:
        raw = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None

def admin_run_workspace(payload):
    workspace = str(payload.get("workspace") or "").strip()
    command = payload.get("command") or []
    timeout = int(payload.get("timeout", 300))
    env_map = payload.get("env") or {}
    runner = str(payload.get("runner") or os.getenv("CODEX_GATEWAY_WORKSPACE_RUNNER", "container")).strip()
    background = bool(payload.get("background", False))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("Unsafe workspace name")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a non-empty string list")
    if not isinstance(env_map, dict):
        raise ValueError("env must be an object")
    if runner not in {"container", "host"}:
        raise ValueError("runner must be container or host")
    if runner == "host" and not bool(payload.get("allow_host")):
        return {
            "ok": False,
            "action": "workspace_run",
            "workspace": workspace,
            "runner": runner,
            "command": command,
            "error": "host_runner_requires_explicit_capability",
            "marker": "WORKSPACE_RUN_HOST_REQUIRES_EXPLICIT_CAPABILITY",
            "recovery": (
                "Použij container runner, nebo explicitně povol host diagnostiku přes capability/admin vrstvu. "
                "Codex-local agent loop nesmí tiše spouštět workspace příkazy na hostu."
            ),
        }

    rescued = rescue_nested_workspace_helper(workspace, command)
    if rescued is not None:
        return rescued

    script = REPO_ROOT / "codex/bin/run_check.py"
    if not script.is_file():
        raise FileNotFoundError("codex/bin/run_check.py is missing")

    cmd = [os.environ.get("PYTHON", "python3"), str(script), "--timeout", str(timeout), "--runner", runner, "--json"]
    for key, value in env_map.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("env keys and values must be strings")
        cmd.extend(["--env", f"{key}={value}"])
    cmd.append(workspace)
    cmd.append("--")
    cmd.extend(command)
    if background:
        audit_dir = REPO_ROOT / "codex/audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        safe_workspace = re.sub(r"[^A-Za-z0-9_.-]", "-", workspace)[:80] or "workspace"
        nonce = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        job_id = f"{safe_workspace}-{nonce}"
        log_file = audit_dir / f"workspace-run-{job_id}.log"
        workspace_run_write(job_id, {
            "job_id": job_id,
            "workspace": workspace,
            "runner": runner,
            "command": command,
            "executed_command": cmd,
            "log": str(log_file),
            "status": "scheduled",
            "running": True,
        })
        with open(log_file, "ab") as log:
            log.write(("scheduled_command=" + " ".join(command) + "\n").encode("utf-8", "replace"))
            child_env = os.environ.copy()
            child_env.update(env_map)
            proc = subprocess.Popen(
                cmd,
                cwd=REPO_ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=child_env,
                start_new_session=True,
            )
        workspace_run_write(job_id, {
            "job_id": job_id,
            "workspace": workspace,
            "runner": runner,
            "command": command,
            "executed_command": cmd,
            "log": str(log_file),
            "status": "running",
            "running": True,
            "pid": proc.pid,
        })
        return {
            "ok": True,
            "action": "workspace_run_scheduled",
            "background": True,
            "job_id": job_id,
            "workspace": workspace,
            "runner": runner,
            "command": command,
            "executed_command": cmd,
            "pid": proc.pid,
            "log": str(log_file),
            "duration_ms": 0,
            "output": "",
        }
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

def admin_workspace_run_status(payload):
    job_id = str(payload.get("job_id") or "").strip()
    workspace = str(payload.get("workspace") or "").strip()
    if not job_id and not workspace:
        raise ValueError("job_id or workspace is required")
    if job_id:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", job_id):
            raise ValueError("Unsafe job_id")
        job = workspace_run_read(job_id)
    else:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
            raise ValueError("Unsafe workspace")
        job = None
        for path in sorted(workspace_run_state_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            item = workspace_run_read(path.stem)
            if item and item.get("workspace") == workspace:
                job = item
                break
    if not job:
        return {"ok": False, "action": "workspace_run_status", "error": "job_not_found", "job_id": job_id, "workspace": workspace}

    pid = int(job.get("pid") or 0)
    log_path = str(job.get("log") or "")
    running = bool(pid and pid_running(pid))
    tail = tail_text(log_path) if log_path else ""
    parsed = parse_run_check_json_from_log(log_path) if log_path else None
    result = dict(job)
    result.update({
        "ok": True,
        "action": "workspace_run_status",
        "running": running,
        "tail": tail,
        "result": parsed or {},
    })
    if parsed:
        result["exit_code"] = parsed.get("exit_code")
        result["runner_exit_code"] = parsed.get("runner_exit_code")
        result["duration_ms"] = parsed.get("duration_ms")
    return result

def admin_workspace_action(payload):
    workspace = str(payload.get("workspace") or "").strip()
    action = str(payload.get("action") or "").strip()
    timeout = int(payload.get("timeout") or 900)
    env_map = payload.get("env") or {}
    dry_run = bool(payload.get("dry_run", False))
    runner = str(payload.get("runner") or os.getenv("CODEX_GATEWAY_WORKSPACE_RUNNER", "container")).strip()

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    if action not in {"install", "test", "build", "lint", "verify", "smoke"}:
        raise ValueError("action must be one of install, test, build, lint, verify, smoke")
    if timeout < 1 or timeout > 3600:
        raise ValueError("timeout must be between 1 and 3600")
    if not isinstance(env_map, dict):
        raise ValueError("env must be an object")
    if runner not in {"container", "host"}:
        raise ValueError("runner must be container or host")
    if runner == "host" and not bool(payload.get("allow_host")):
        return {
            "ok": False,
            "workspace": workspace,
            "action": action,
            "runner": runner,
            "error": "host_runner_requires_explicit_capability",
            "marker": "WORKSPACE_ACTION_HOST_REQUIRES_EXPLICIT_CAPABILITY",
            "recovery": (
                "Použij container runner, nebo explicitně povol host diagnostiku přes capability/admin vrstvu. "
                "Běžné workspace akce nemají tiše padat zpět na host."
            ),
        }

    script = REPO_ROOT / "codex/bin/workspace_action.py"
    if not script.is_file():
        raise FileNotFoundError("codex/bin/workspace_action.py is missing")

    cmd = [os.environ.get("PYTHON", "python3"), str(script), action, "--timeout", str(timeout), "--runner", runner, "--json"]
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
        root = load_workspace(Path(WORKSPACES_FILE), workspace)
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

def workspace_action_failure_recommendation(workspace: str, action: str, action_result: dict | None) -> dict:
    registry = load_workspace_action_registry()
    spec = registry.get(action, {}) if isinstance(registry, dict) else {}
    generic_hint = str(spec.get("recovery_hint", "")).strip()
    retry_runner = str(spec.get("runner", "container") or "container").strip() or "container"
    retry_timeout = int(spec.get("timeout", 900) or 900)

    def build(text: str, patch_target: str, patch_hint: str, patch_summary: str) -> dict:
        read_command = f"GATEWAY_ADMIN_READ_NUMBERED {patch_target} 1 220" if patch_target else ""
        return {
            "text": text,
            "patch_target": patch_target,
            "patch_hint": patch_hint,
            "patch_summary": patch_summary,
            "read_command": read_command,
            "retry_action": action,
            "retry_runner": retry_runner,
            "retry_timeout": retry_timeout,
        }

    try:
        root = load_workspace(Path(WORKSPACES_FILE), workspace)
        scan = collect(root, 80)
    except Exception as exc:
        return build(
            f"Action {action} failed and workspace scan is unavailable: {exc}",
            "",
            generic_hint or "Inspect the failing action output and the nearest project manifest before retrying.",
            f"Review the failing {action} step and prepare the smallest manifest/config patch that unblocks it.",
        )

    manifests = scan.get("manifests") or []
    manifest_names = {Path(rel).name for rel in manifests}
    output = str((action_result or {}).get("output", "")).strip()
    output_lower = output.lower()

    def default_patch_target() -> str:
        if "package.json" in manifest_names:
            return "package.json"
        if "pyproject.toml" in manifest_names:
            return "pyproject.toml"
        if manifests:
            return manifests[0]
        return ""

    def pick_patch_target(preferred: str) -> str:
        preferred = str(preferred or "").strip()
        if preferred:
            if preferred in manifests or Path(preferred).name in manifest_names:
                return preferred
            if preferred == "package.json" and "package.json" in manifest_names:
                return "package.json"
            if preferred == "pyproject.toml" and "pyproject.toml" in manifest_names:
                return "pyproject.toml"
            if preferred == "requirements.txt" and "requirements.txt" in manifest_names:
                return "requirements.txt"
        return default_patch_target()

    def from_recovery_rules() -> dict | None:
        rules = spec.get("recovery_rules") or []
        if not isinstance(rules, list):
            return None
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            needles = [str(item).lower() for item in (rule.get("contains_any") or []) if str(item).strip()]
            if needles and not any(needle in output_lower for needle in needles):
                continue
            patch_target = pick_patch_target(str(rule.get("patch_target", "")).strip())
            patch_hint = str(rule.get("patch_hint", "")).strip() or generic_hint
            patch_summary = str(rule.get("patch_summary", "")).strip() or f"Prepare the smallest patch that unblocks {action}."
            text = str(rule.get("text", "")).strip() or f"Action {action} failed; inspect {patch_target or 'the main manifest'} and apply the smallest fix before retrying."
            return build(text, patch_target, patch_hint, patch_summary)
        return None

    patch_target = default_patch_target()

    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    reason = first_line or str((action_result or {}).get("error", "") or "").strip()
    reason_suffix = f" Failure summary: {reason}" if reason else ""

    matched = from_recovery_rules()
    if matched:
        if reason_suffix and matched.get("text"):
            matched["text"] = str(matched["text"]).rstrip(".") + "." + reason_suffix
        return matched

    if not patch_target:
        return build(
            f"Action {action} failed and the project still lacks a clear manifest target.{reason_suffix}",
            "",
            generic_hint or "Add or repair the primary project manifest before retrying the failed capability.",
            f"Create or repair the primary project manifest to unblock {action}.",
        )

    patch_hint = generic_hint or f"Inspect {patch_target} and adjust the config or script that blocks {action}."
    patch_summary = f"Prepare the smallest patch in {patch_target} that unblocks the failed {action} capability."
    text = f"Action {action} failed; inspect {patch_target} and apply the smallest fix before retrying.{reason_suffix}"
    return build(text, patch_target, patch_hint, patch_summary)

def workspace_autopilot_order(allow_actions: list[str]) -> list[str]:
    registry = load_workspace_action_registry()
    default_order = ["install", "verify", "smoke", "test", "build", "lint"]
    default_rank = {action: idx for idx, action in enumerate(default_order)}

    def sort_key(action: str):
        spec = registry.get(action, {}) if isinstance(registry, dict) else {}
        priority = int(spec.get("autopilot_priority", 1000))
        return (priority, default_rank.get(action, 999), action)

    return sorted(allow_actions, key=sort_key)


def workspace_autopilot_candidate_messages(
    workspace: str,
    task: str,
    desired_end_state: str,
    allow_actions: list[str],
    executed_actions: list[dict],
    candidates: list[dict],
    verify_steps: list[dict],
    action_probes: dict,
):
    candidate_lines = []
    for candidate in candidates:
        action = str(candidate.get("action") or "").strip().lower()
        reason = str(candidate.get("reason") or "").strip()
        probe = action_probes.get(action) if isinstance(action_probes, dict) else {}
        command = probe.get("command", []) if isinstance(probe, dict) else []
        resolved_from = str(probe.get("resolved_from") or "").strip() if isinstance(probe, dict) else ""
        candidate_lines.append(
            json.dumps(
                {
                    "action": action,
                    "reason": reason,
                    "command": command,
                    "resolved_from": resolved_from,
                },
                ensure_ascii=False,
            )
        )
    executed_text = json.dumps(executed_actions or [], ensure_ascii=False)
    verify_text = json.dumps(verify_steps or [], ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": (
                "You are the bounded next-step planner for a local Codex-like workspace autopilot. "
                "Reply with one compact JSON object and nothing else.\n"
                'Schema: {"action":"install|verify|smoke|test|build|lint","reason":"short reason"}\n'
                "Choose only one action from the provided candidate list. "
                "Optimize for the user's desired end state, avoid repeating already executed actions, "
                "and prefer the smallest useful next step that increases confidence. "
                "Do not invent actions outside the provided candidates."
            ),
        },
        {
            "role": "user",
            "content": (
                f"workspace={workspace}\n"
                f"user_task={task or '(none provided)'}\n"
                f"desired_end_state={desired_end_state or '(unspecified)'}\n"
                f"allow_actions={','.join(allow_actions)}\n"
                f"executed_actions={executed_text}\n"
                f"verify_steps={verify_text}\n"
                "candidates:\n"
                + "\n".join(candidate_lines)
            ),
        },
    ]


def workspace_autopilot_choose_candidate(
    workspace: str,
    task: str,
    desired_end_state: str,
    allow_actions: list[str],
    executed_actions: list[dict],
    candidates: list[dict],
    verify_steps: list[dict],
    action_probes: dict,
):
    if not candidates:
        return None, "none", "No candidate actions were available."
    fallback = candidates[0]
    allowed = {str(item.get("action") or "").strip().lower() for item in candidates}
    try:
        response = ollama_chat(
            codex_local_runtime_model_name(task=task, role=ROLE_RECOVERY),
            workspace_autopilot_candidate_messages(
                workspace,
                task,
                desired_end_state,
                allow_actions,
                executed_actions,
                candidates,
                verify_steps,
                action_probes,
            ),
            timeout=180,
        )
        raw = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        parsed = extract_json_object(raw)
        action = str(parsed.get("action") or "").strip().lower()
        if action not in allowed:
            return fallback, "fallback", f"Planner suggested unsupported action {action!r}; using priority fallback."
        reason = str(parsed.get("reason") or "").strip()
        for candidate in candidates:
            if str(candidate.get("action") or "").strip().lower() == action:
                chosen = dict(candidate)
                if reason:
                    chosen["reason"] = reason
                return chosen, "llm", reason or str(candidate.get("reason") or "").strip()
    except Exception as exc:
        return fallback, "fallback", f"Planner failed ({type(exc).__name__}: {exc}); using priority fallback."
    return fallback, "fallback", "Planner did not return a usable action; using priority fallback."


def admin_workspace_autopilot(payload):
    workspace = str(payload.get("workspace") or "").strip()
    timeout = int(payload.get("timeout") or 1800)
    env_map = payload.get("env") or {}
    recommend_only = bool(payload.get("recommend_only", False))
    allow_actions = payload.get("allow_actions") or ["install", "verify", "smoke", "test", "build", "lint"]
    max_steps = int(payload.get("max_steps") or 1)
    task = str(payload.get("task") or "").strip()
    desired_end_state = str(payload.get("desired_end_state") or "").strip()

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
        verify_step_map = {}
        for step in verify_steps_local:
            action = str(step.get("action") or "").strip().lower()
            if action and step.get("supported"):
                verify_step_map[action] = step

        ordered_actions = workspace_autopilot_order(allow_actions)
        candidates = []
        probes = {"verify": verify_result}
        for action in ordered_actions:
            if action in executed_names:
                continue
            if action == "verify":
                if verify_result.get("ok") and verify_step_map:
                    candidates.append({"action": action, "reason": "verify dry-run found supported verification steps"})
                continue
            if action in verify_step_map:
                probes[action] = {
                    "ok": True,
                    "planned_only": True,
                    "action": action,
                    "command": verify_step_map[action].get("command", []),
                    "resolved_from": verify_step_map[action].get("resolved_from", ""),
                    "output": "",
                }
                candidates.append({"action": action, "reason": f"verify dry-run exposed a supported {action} step"})
                continue
            probe_result = admin_workspace_action({
                "workspace": workspace,
                "action": action,
                "timeout": timeout,
                "env": env_map,
                "dry_run": True,
            })
            probes[action] = probe_result
            if probe_result.get("ok"):
                candidates.append({"action": action, "reason": f"{action} dry-run is supported in this workspace"})
        return verify_result, verify_steps_local, candidates, probes

    verify, verify_steps, candidate_actions, action_probes = plan_candidates(set())
    wants_preview = (
        "workspace_expose_preview" in canonicalize_agent_capabilities(payload.get("required_capabilities") or [])
        or "preview" in desired_end_state.lower()
        or "preview" in task.lower()
        or "expose" in desired_end_state.lower()
        or "expose" in task.lower()
    )

    chosen, planner_source, planner_reason = workspace_autopilot_choose_candidate(
        workspace,
        task,
        desired_end_state,
        allow_actions,
        [],
        candidate_actions,
        verify_steps,
        action_probes,
    )
    chosen_action = chosen["action"] if chosen else None
    chosen_reason = planner_reason or (chosen["reason"] if chosen else "")

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
            "planner_source": planner_source,
            "planner_reason": planner_reason,
            "recommendation": recommendation.get("text", ""),
            "patch_target": recommendation.get("patch_target", ""),
            "patch_hint": recommendation.get("patch_hint", ""),
            "patch_summary": recommendation.get("patch_summary", ""),
            "read_command": recommendation.get("read_command", ""),
            "retry_action": recommendation.get("retry_action", ""),
            "retry_runner": recommendation.get("retry_runner", ""),
            "retry_timeout": recommendation.get("retry_timeout", ""),
            "duration_ms": verify.get("duration_ms", 0),
            "verify_steps": verify_steps,
            "action_probes": action_probes,
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
            "planner_source": planner_source,
            "planner_reason": planner_reason,
            "recommendation": "",
            "patch_target": "",
            "patch_hint": "",
            "patch_summary": "",
            "read_command": "",
            "retry_action": "",
            "retry_runner": "",
            "retry_timeout": "",
            "duration_ms": verify.get("duration_ms", 0),
            "verify_steps": verify_steps,
            "action_probes": action_probes,
            "executed_actions": [],
            "stop_reason": "recommend_only",
            "output": "",
        }

    total_started = time.time()
    executed_actions = []
    last_result = None
    current_verify_steps = verify_steps
    current_probes = action_probes
    stop_reason = "max_steps_reached"
    step_planner_source = planner_source
    step_planner_reason = planner_reason
    recommendation = {
        "text": "",
        "patch_target": "",
        "patch_hint": "",
        "patch_summary": "",
        "read_command": "",
        "retry_action": "",
        "retry_runner": "",
        "retry_timeout": "",
    }
    preview_urls = []
    for idx in range(max_steps):
        remaining_names = {step["action"] for step in executed_actions if step.get("action")}
        if idx == 0:
            next_candidates = candidate_actions
        else:
            _, current_verify_steps, next_candidates, current_probes = plan_candidates(remaining_names)
        if not next_candidates:
            stop_reason = "no_more_supported_actions"
            recommendation = workspace_autopilot_recommendation(workspace)
            break
        next_choice, step_planner_source, step_planner_reason = workspace_autopilot_choose_candidate(
            workspace,
            task,
            desired_end_state,
            allow_actions,
            executed_actions,
            next_candidates,
            current_verify_steps,
            current_probes,
        )
        if not next_choice:
            stop_reason = "no_more_supported_actions"
            recommendation = workspace_autopilot_recommendation(workspace)
            break
        action_name = next_choice["action"]
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
            "planner_source": step_planner_source,
            "planner_reason": step_planner_reason,
        })
        for url in _string_list(last_result.get("preview_urls")):
            if url not in preview_urls:
                preview_urls.append(url)
        first_preview_url = str(last_result.get("preview_url") or "").strip()
        if first_preview_url and first_preview_url not in preview_urls:
            preview_urls.append(first_preview_url)
        if not last_result.get("ok"):
            stop_reason = "step_failed"
            recommendation = workspace_action_failure_recommendation(workspace, action_name, last_result)
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
        "planner_source": step_planner_source,
        "planner_reason": step_planner_reason,
        "recommendation": recommendation.get("text", ""),
        "wants_preview": wants_preview,
        "preview_urls": preview_urls,
        "preview_url": preview_urls[0] if preview_urls else "",
        "patch_target": recommendation.get("patch_target", ""),
        "patch_hint": recommendation.get("patch_hint", ""),
        "patch_summary": recommendation.get("patch_summary", ""),
        "read_command": recommendation.get("read_command", ""),
        "retry_action": recommendation.get("retry_action", ""),
        "retry_runner": recommendation.get("retry_runner", ""),
        "retry_timeout": recommendation.get("retry_timeout", ""),
        "duration_ms": int((time.time() - total_started) * 1000),
        "verify_steps": current_verify_steps,
        "action_probes": current_probes,
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

def deploy_runtime_gate_status():
    script = REPO_ROOT / "codex/bin/gateway_runtime_fingerprint_check.py"
    if not script.is_file():
        return {
            "ok": False,
            "marker": "CODEX_LOCAL_RUNTIME_GATE_MISSING",
            "recovery": "codex/bin/gateway_runtime_fingerprint_check.py is missing from the runtime checkout.",
        }

    raw = run_ro(
        [
            sys.executable,
            str(script),
            "--base-url",
            "http://127.0.0.1:9101",
            "--json",
        ],
        REPO_ROOT,
        12,
    )
    try:
        parsed = json.loads(raw)
    except Exception:
        return {
            "ok": False,
            "marker": "CODEX_LOCAL_RUNTIME_GATE_PARSE_FAILED",
            "recovery": "Run python3 codex/bin/gateway_runtime_fingerprint_check.py --base-url http://127.0.0.1:9101 --json from the runtime checkout.",
            "raw": trim_response_text(raw, 3000),
        }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "marker": "CODEX_LOCAL_RUNTIME_GATE_INVALID",
            "recovery": "gateway_runtime_fingerprint_check.py returned a non-object payload.",
            "raw": trim_response_text(raw, 3000),
        }
    return parsed

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
    upstream = run_ro(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], REPO_ROOT, 8)
    origin_head = run_ro(["git", "rev-parse", "--short", "origin/main"], REPO_ROOT, 8)
    remote_url = run_ro(["git", "remote", "get-url", "origin"], REPO_ROOT, 8)
    status = run_ro(["git", "status", "--short", "--branch"], REPO_ROOT, 8)
    tail = tail_text(log_file)
    deploy_pairs = re.findall(r"(?m)^before=([0-9a-fA-F]+)\s*$\n^after=([0-9a-fA-F]+)\s*$", tail)
    last_before = deploy_pairs[-1][0] if deploy_pairs else ""
    last_after = deploy_pairs[-1][1] if deploy_pairs else ""
    blocker = ""
    for marker in (
        "DEPLOY_BLOCKED_ROOT_RESTART_REQUIRED",
        "DEPLOY_BLOCKED_RUNTIME_METADATA_CONFLICT",
        "DEPLOY_BLOCKED_DIRTY_TRACKED_FILES",
        "DEPLOY_BLOCKED_ROOT_REQUIRED",
        "DEPLOY_BLOCKED_GATEWAY_RUNTIME_DRIFT",
    ):
        if marker in tail:
            blocker = marker
    runtime_gate = deploy_runtime_gate_status()
    runtime_gate_marker = str(runtime_gate.get("marker") or "").strip()
    if not blocker and runtime_gate.get("ok") is not True:
        blocker = runtime_gate_marker or "CODEX_LOCAL_RUNTIME_GATE_FAILED"
    restart_required = blocker in {
        "DEPLOY_BLOCKED_ROOT_RESTART_REQUIRED",
        "DEPLOY_BLOCKED_GATEWAY_RUNTIME_DRIFT",
        "CODEX_LOCAL_GATEWAY_SOURCE_EPOCH_DRIFT",
        "CODEX_LOCAL_RUNTIME_FINGERPRINT_MISSING",
        "CODEX_LOCAL_RUNTIME_SPLIT_BRAIN",
        "CODEX_LOCAL_AGENT_ROUTE_DEGRADED",
        "CODEX_LOCAL_RUNTIME_GATE_PARSE_FAILED",
        "CODEX_LOCAL_RUNTIME_GATE_INVALID",
    }
    user = run_ro(["id", "-un"], REPO_ROOT, 4)
    script = REPO_ROOT / "codex/bin/deploy_ai_stack.sh"
    sudoers_helper = REPO_ROOT / "codex/bin/install_deploy_sudoers.sh"
    sudoers_entry = (
        f"{user} ALL=(root) NOPASSWD: {script} --restart-only, {script} --sudoers-probe"
        if restart_required and user
        else ""
    )
    return {
        "ok": True,
        "action": "deploy_status",
        "pid": pid,
        "running": running,
        "head": head,
        "upstream": upstream,
        "origin_head": origin_head,
        "remote_url": remote_url,
        "git_status": status,
        "last_before": last_before,
        "last_after": last_after,
        "deployment_blocker": blocker,
        "restart_required": restart_required,
        "runtime_gate": runtime_gate,
        "manual_restart_command": f"sudo {script} --restart-only" if restart_required else "",
        "sudoers_entry": sudoers_entry,
        "sudoers_install_command": f"sudo {sudoers_helper} --install" if restart_required else "",
        "log": str(log_file),
        "tail": tail,
    }

def admin_agent_self_improve(payload):
    workspace = str(payload.get("workspace") or "ai-stack").strip() or "ai-stack"
    mode = str(payload.get("mode") or "diagnose").strip().lower()
    dry_run = bool(payload.get("dry_run", True))
    max_cycles = int(payload.get("max_cycles") or 1)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    if mode not in {"diagnose", "reproduce", "propose_patch", "generate_unified_diff", "patch", "verify", "deploy", "e2e", "capability_develop", "full"}:
        raise ValueError("mode must be diagnose|reproduce|propose_patch|generate_unified_diff|patch|verify|deploy|e2e|capability_develop|full")
    max_cycles = max(1, min(max_cycles, 3))

    script = REPO_ROOT / "codex/bin/agent_self_improve.py"
    if not script.is_file():
        raise FileNotFoundError("codex/bin/agent_self_improve.py is missing")

    cmd = [
        sys.executable,
        str(script),
        "--workspace",
        workspace,
        "--mode",
        mode,
        "--max-cycles",
        str(max_cycles),
        "--audit-root",
        str(REPO_ROOT / "codex/audit/self-improve"),
        "--json",
    ]
    if dry_run:
        cmd.append("--dry-run")
    for key, flag in (
        ("chat_id", "--chat-id"),
        ("chat_url", "--chat-url"),
        ("failure_marker", "--failure-marker"),
        ("expected_behavior", "--expected-behavior"),
        ("prompt", "--prompt"),
        ("patch_file", "--patch-file"),
        ("e2e_prompt", "--e2e-prompt"),
        ("capability_name", "--capability-name"),
        ("target_capability_name", "--target-capability-name"),
        ("feature_request", "--feature-request"),
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            cmd.extend([flag, value])

    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=int(payload.get("timeout") or 900),
    )
    raw = proc.stdout or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {
            "ok": False,
            "raw_output": trim_response_text(raw, 8000),
        }
    return {
        "ok": proc.returncode == 0 and bool(parsed.get("ok", proc.returncode == 0)),
        "action": "agent_self_improve",
        "workspace": workspace,
        "mode": mode,
        "dry_run": dry_run,
        "exit_code": proc.returncode,
        "duration_ms": int((time.time() - started) * 1000),
        "command": [cmd[0], "codex/bin/agent_self_improve.py", *cmd[2:]],
        "result": parsed,
        "artifact_dir": str(parsed.get("artifact_dir") or ""),
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


def runtime_fingerprint():
    """Hash loaded gateway source text to detect runtime/repo split-brain.

    Source text is intentionally used instead of marshal.dumps(code objects).
    Marshal output can differ across Python versions even when the loaded source
    is identical, which makes remote-vs-local checks noisy from a different
    workstation.
    """
    digest = hashlib.sha256()
    digest.update(GATEWAY_SOURCE_EPOCH.encode("utf-8"))
    targets = [
        ("runtime_health", runtime_health),
        ("completion", completion),
        ("codex_local_agent_loop_payload", codex_local_agent_loop_payload),
        ("normal_chat_requires_tool", normal_chat_requires_tool),
        ("canonicalize_agent_capability", canonicalize_agent_capability),
        ("split_agent_capabilities", split_agent_capabilities),
        ("agent_capability_registry_issues", agent_capability_registry_issues),
        ("agent_plan", agent_plan),
        ("normalize_agent_taskspec", normalize_agent_taskspec),
        ("agent_taskspec_to_plan", agent_taskspec_to_plan),
        ("normalize_agent_plan", normalize_agent_plan),
        ("admin_agent_meta", admin_agent_meta),
        ("admin_workspace_search", admin_workspace_search),
        ("admin_agent_loop", admin_agent_loop),
        ("admin_agent_self_improve", admin_agent_self_improve),
        ("admin_run_workspace", admin_run_workspace),
        ("admin_workspace_git_publish", admin_workspace_git_publish),
        ("gateway_admin_text", gateway_admin_text),
        ("fallback_response_text", fallback_response_text),
        ("agent_loop_human_answer", agent_loop_human_answer),
        ("agent_loop_changed_files", agent_loop_changed_files),
        ("agent_loop_verify_status", agent_loop_verify_status),
        ("agent_loop_response_text", agent_loop_response_text),
        ("trim_response_text", trim_response_text),
        ("preview_text", preview_text),
    ]
    for name, fn in targets:
        digest.update(name.encode("utf-8"))
        try:
            source = inspect.getsource(fn)
        except (OSError, TypeError):
            source = repr(getattr(fn, "__code__", ""))
        digest.update(source.replace("\r\n", "\n").strip().encode("utf-8"))
    return digest.hexdigest()[:24]


def agent_requested_workspace_from_text(text, messages=None):
    resolved = resolve_workspace_context(text, messages or [], WORKSPACES_FILE, fallback_workspace="ai-stack")
    return resolved.workspace


def trim_response_text(text, limit=14000):
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated {len(value) - limit} chars]"


def preview_text(value, limit=220):
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


def shell_join_safe(command):
    if not isinstance(command, list):
        return str(command or "")
    try:
        return shlex.join([str(item) for item in command])
    except Exception:
        return " ".join(str(item) for item in command)


def agent_loop_human_answer(result):
    workflow = str(result.get("workflow") or "").strip()
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    followup = result.get("followup") if isinstance(result.get("followup"), dict) else {}
    recovery = result.get("recovery") if isinstance(result.get("recovery"), dict) else {}
    workspace = str(result.get("requested_workspace") or result.get("controller_workspace") or "").strip()

    if workflow in {"meta", "review", "clarify"}:
        return str(result.get("answer") or "").strip()

    if workflow == "direct_answer":
        return str(result.get("answer") or execution.get("answer") or "").strip()

    if workflow == "workspace_search":
        query = str(execution.get("query") or "").strip()
        count = int(execution.get("match_count") or 0)
        if result.get("ok"):
            matches = execution.get("matches") if isinstance(execution.get("matches"), list) else []
            text = f"Ve workspace {workspace} jsem prohledal repo na `{query}` a našel {count} shod."
            if matches:
                preview = "\n".join(str(item) for item in matches[:12])
                text += f"\n\n```text\n{preview}\n```"
            return text
        return (
            f"Search ve workspace {workspace} pro `{query}` selhal. "
            + preview_text(execution.get("output") or execution.get("error") or result.get("summary"))
        ).strip()

    if workflow == "web_answer":
        return str(execution.get("answer") or result.get("answer") or "").strip()

    if workflow == "web_search":
        return str(execution.get("answer") or result.get("answer") or "").strip()

    if workflow == "web_fetch":
        final_url = str(execution.get("final_url") or execution.get("url") or "").strip()
        title = str(execution.get("title") or "").strip()
        text = preview_text(execution.get("text") or "")
        if result.get("ok"):
            pieces = [f"Načetl jsem veřejný web {final_url or '(unknown url)'}."]
            if title:
                pieces.append(f"Titul: {title}.")
            if text:
                pieces.append(f"Stručný výtah: {text}")
            return " ".join(pieces).strip()
        return f"Nepodařilo se načíst veřejný web {final_url or '(unknown url)'}."

    if workflow == "deploy":
        if result.get("ok"):
            pid = execution.get("pid")
            return (
                f"Nasazení ai-stack jsem naplánoval. "
                f"Běží na pozadí{f' pod PID {pid}' if pid else ''}; stav zkontroluješ přes deploy status."
            ).strip()
        return (
            "Nasazení ai-stack se nepodařilo naplánovat. "
            + preview_text(execution.get("tail") or execution.get("error") or result.get("summary"))
        ).strip()

    if workflow == "self_improve":
        artifact = str(execution.get("artifact_dir") or "").strip()
        exit_code = execution.get("exit_code")
        mode = str(execution.get("mode") or "").strip()
        if result.get("ok"):
            return (
                f"Self-improve rutina doběhla v režimu `{mode or 'diagnose'}`. "
                f"Artifact je v `{artifact}`. exit_code={exit_code}."
            ).strip()
        return (
            f"Self-improve rutina narazila v režimu `{mode or 'diagnose'}`. "
            f"Artifact: `{artifact or '(missing)'}`. "
            + preview_text((execution.get("result") or {}).get("raw_output") or result.get("summary"))
        ).strip()

    if workflow in {"ssh_key_create", "ssh_key_show_public"}:
        ssh_key = execution.get("ssh_key") if isinstance(execution.get("ssh_key"), dict) else {}
        public_key = str(execution.get("public_key") or ssh_key.get("public_key") or "").strip()
        public_key_path = str(ssh_key.get("public_key_path") or "").strip()
        if result.get("ok"):
            text = f"Ve workspace {workspace} je SSH key připravený."
            if public_key_path:
                text += f" Public key je v `{public_key_path}`."
            if public_key:
                text += f" Public key: {public_key}"
            return text
        return (
            f"SSH key capability ve workspace {workspace} selhala. "
            + preview_text(execution.get("error") or result.get("summary"))
        ).strip()

    if workflow == "workspace_git_publish":
        remote_url = str(execution.get("remote_url") or (result.get("plan") or {}).get("remote_url") or "").strip()
        if result.get("ok"):
            return f"Ve workspace {workspace} jsem připravil git, nastavil origin na `{remote_url}` a pushnul branch `main`."
        if execution.get("status") == "MANUAL_STEP_REQUIRED":
            public_key = str(execution.get("public_key") or "").strip()
            public_key_path = str(execution.get("public_key_path") or "").strip()
            text = f"Ve workspace {workspace} jsem připravil git publish na `{remote_url}`, ale GitHub/SSH ještě potřebuje ruční krok."
            if public_key_path:
                text += f" Public key je v `{public_key_path}`."
            if public_key:
                text += f" Public key: {public_key}"
            return text
        runner_output = execution.get("runner_result") if isinstance(execution.get("runner_result"), dict) else {}
        return (
            f"Git publish ve workspace {workspace} selhal. "
            + preview_text(runner_output.get("output") or execution.get("summary") or result.get("summary"))
        ).strip()

    if workflow == "run":
        command = shell_join_safe(execution.get("command") or execution.get("executed_command") or [])
        runner = str(execution.get("runner") or "container")
        if result.get("ok"):
            output = preview_text(execution.get("output") or "")
            text = f"Ve workspace {workspace} jsem spustil `{command}` přes {runner} runner a příkaz doběhl."
            if output:
                text += f" Výstup: {output}"
            return text
        marker = str(execution.get("marker") or "").strip()
        if marker:
            return (
                f"Příkaz `{command}` ve workspace {workspace} neproběhl. "
                + preview_text(execution.get("recovery") or execution.get("output") or result.get("summary"))
            ).strip()
        return (
            f"Příkaz `{command}` ve workspace {workspace} selhal. "
            + preview_text(execution.get("output") or execution.get("error") or result.get("summary"))
        ).strip()

    if workflow == "action":
        action = str((result.get("plan") or {}).get("action") or execution.get("action") or "").strip() or "verify"
        runner = str(execution.get("runner") or "container").strip() or "container"
        if result.get("ok"):
            output = preview_text(execution.get("output") or "")
            text = f"Ve workspace {workspace} jsem provedl akci `{action}` přes {runner} runner."
            if output:
                text += f" Výstup: {output}"
            return text
        return (
            f"Akce `{action}` ve workspace {workspace} selhala. "
            + preview_text((recovery.get("text") or execution.get("recovery") or execution.get("output") or result.get("summary")))
        ).strip()

    if workflow == "edit":
        files = execution.get("files") if isinstance(execution.get("files"), list) else []
        file_text = ", ".join(str(item) for item in files[:4]) if files else "relevantní soubory"
        run_after = str((result.get("plan") or {}).get("run_after") or execution.get("run_after") or "").strip()
        if result.get("ok"):
            text = f"Ve workspace {workspace} jsem upravil {file_text}."
            if run_after:
                text += f" Následné ověření `{run_after}` proběhlo."
            return text
        return (
            f"Editace ve workspace {workspace} narazila na blocker. "
            + preview_text(recovery.get("text") or execution.get("error") or execution.get("apply_output") or result.get("summary"))
        ).strip()

    if workflow == "autopilot":
        steps = execution.get("executed_actions") if isinstance(execution.get("executed_actions"), list) else []
        step_names = [str(step.get("action")) for step in steps if isinstance(step, dict) and step.get("action")]
        preview_url = str(execution.get("preview_url") or "").strip()
        wants_preview = bool(execution.get("wants_preview"))
        if result.get("ok"):
            if step_names:
                text = f"Autopilot ve workspace {workspace} dokončil kroky: {', '.join(step_names)}."
                if preview_url:
                    text += f" Preview běží na `{preview_url}`."
                elif wants_preview:
                    text += " Preview krok byl součástí cíle, ale runtime zatím nevrátil konkrétní URL."
                return text
            return f"Autopilot ve workspace {workspace} doběhl bez chyby."
        if step_names:
            text = (
                f"Autopilot ve workspace {workspace} se zastavil po krocích {', '.join(step_names)}. "
                + preview_text(recovery.get("text") or execution.get("recommendation") or result.get("summary"))
            ).strip()
            if preview_url:
                text += f" Poslední nalezené preview URL: `{preview_url}`."
            return text
        return (
            f"Autopilot ve workspace {workspace} nenašel bezpečný další krok. "
            + preview_text(recovery.get("text") or execution.get("recommendation") or result.get("summary"))
        ).strip()

    if workflow == "bootstrap":
        repo_name = str(execution.get("name") or (result.get("plan") or {}).get("repo_name") or workspace).strip()
        ssh_key = execution.get("ssh_key") if isinstance(execution.get("ssh_key"), dict) else {}
        ssh_pub = str(ssh_key.get("public_key_path") or "").strip()
        github_requested = bool(execution.get("github_requested"))
        if result.get("ok"):
            text = f"Bootstrap workspace `{repo_name}` doběhl."
            if ssh_pub:
                text += f" SSH public key je v `{ssh_pub}`."
            if github_requested:
                text += " GitHub část byla zahrnutá."
            if followup:
                follow_steps = followup.get("executed_actions") if isinstance(followup.get("executed_actions"), list) else []
                names = [str(step.get('action')) for step in follow_steps if isinstance(step, dict) and step.get("action")]
                if names:
                    text += f" Následně proběhly kroky: {', '.join(names)}."
            return text
        if execution.get("partial_ok"):
            return (
                f"Bootstrap workspace `{repo_name}` je částečně hotový. "
                + preview_text(execution.get("next_step") or recovery.get("text") or result.get("summary"))
            ).strip()
        return (
            f"Bootstrap workspace `{repo_name}` selhal. "
            + preview_text(execution.get("next_step") or execution.get("github_note") or result.get("summary"))
        ).strip()

    return str(result.get("answer") or "").strip()


def agent_loop_changed_files(result):
    files = []
    seen = set()

    def add(value):
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            files.append(text)

    for container_key in ("execution", "followup"):
        container = result.get(container_key)
        if isinstance(container, dict):
            for key in ("files", "paths", "changed_files", "changed_paths", "safe_apply_candidate_paths"):
                value = container.get(key)
                if isinstance(value, list):
                    for item in value:
                        add(item)
            for step in container.get("executed_actions") or []:
                if not isinstance(step, dict):
                    continue
                for key in ("files", "paths", "changed_files", "changed_paths"):
                    value = step.get(key)
                    if isinstance(value, list):
                        for item in value:
                            add(item)
    return files


def agent_loop_verify_status(result):
    if result.get("ok"):
        return "OK"
    recovery = result.get("recovery") if isinstance(result.get("recovery"), dict) else {}
    if recovery:
        return "needs attention"
    return "not verified"


def agent_loop_response_text(result):
    status = "AGENT_LOOP_OK" if result.get("ok") else "AGENT_LOOP_NEEDS_ATTENTION"
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    workflow = str(result.get("workflow", plan.get("workflow", "")))
    answer_visibility = str(result.get("answer_visibility") or plan.get("answer_visibility") or "summary").strip().lower()
    if answer_visibility not in {"summary", "details", "hidden_debug"}:
        answer_visibility = "summary"
    answer = agent_loop_human_answer(result)
    lines = []
    if answer:
        lines.append(answer)
    else:
        lines.append(str(result.get("summary") or "Agent loop doběhl bez detailní odpovědi.").strip())

    workspace = str(result.get("requested_workspace") or plan.get("workspace") or "").strip()
    progress = [
        f"Stav: {'hotovo' if result.get('ok') else 'potřebuji pozornost'} · workflow `{workflow or '-'}`",
    ]
    if workspace:
        progress.append(f"Workspace: `{workspace}`")
    changed_files = agent_loop_changed_files(result)
    if changed_files:
        progress.append(
            "Změněné soubory: "
            + ", ".join(f"`{item}`" for item in changed_files[:8])
            + f" · verify: {agent_loop_verify_status(result)}"
        )
    elif not result.get("ok"):
        recovery = result.get("recovery") if isinstance(result.get("recovery"), dict) else {}
        recovery_text = preview_text(recovery.get("text") or result.get("summary") or "", 220)
        if recovery_text:
            progress.append(f"Recovery: {recovery_text}")
    lines.extend(progress[:3])

    debug_lines = [
        status,
        f"requested_workspace={result.get('requested_workspace', '')}",
        f"controller_workspace={result.get('controller_workspace', '')}",
        f"planner_source={result.get('planner_source', '')}",
        f"routing_provenance={result.get('routing_provenance', '')}",
        f"workflow={workflow}",
        f"read_only={result.get('read_only', plan.get('read_only', ''))}",
        f"summary={result.get('summary', '')}",
    ]
    if plan.get("reason"):
        debug_lines.append(f"reason={plan.get('reason')}")
    debug_payload = {}
    for key in ("model_runtime", "execution", "followup", "recovery", "plan", "taskspec"):
        value = result.get(key)
        if value in (None, {}, []):
            continue
        debug_payload[key] = value
    debug_rendered = "\n".join(debug_lines)
    if debug_payload:
        try:
            rendered = json.dumps(debug_payload, ensure_ascii=False, indent=2)
        except TypeError:
            rendered = str(debug_payload)
        debug_rendered = debug_rendered + "\n\n" + trim_response_text(rendered, 12000)
    if env_truthy("CODEX_LOCAL_SHOW_DEBUG"):
        lines.append("")
        lines.append("Debug:")
        lines.append("```text")
        lines.append(debug_rendered)
        lines.append("```")
    elif answer_visibility == "details":
        collapsed_debug = debug_rendered.replace("</", "<\\/")
        lines.append("")
        lines.append("<details><summary>Technické detaily</summary>")
        lines.append("")
        lines.append("```text")
        lines.append(trim_response_text(collapsed_debug, 5000))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
    else:
        # Keep machine-readable markers for helper/test correlation without
        # making normal OpenWebUI replies look like raw runtime logs.
        safe_debug = debug_rendered.replace("--", "- -")
        lines.append(f"\n<!-- CODEX_DEBUG\n{safe_debug}\n-->")
    return "\n".join(lines).strip()


def explicit_agent_loop_request(text):
    parsed = parse_agent_loop_request_text(text)
    if not parsed:
        return None
    workspace, task = parsed
    return {"workspace": workspace, "task": task}

def codex_local_model_requested(payload):
    model_name = str((payload or {}).get("model") or "").strip()
    return is_codex_local_model_name(model_name)

def codex_local_agent_loop_payload(payload):
    """Route natural codex-local prompts through the agent loop by default.

    The local Codex surface should be capability-first, not a plain snapshot
    chat with a heuristic "tool-like" detector. We still allow explicit admin
    markers and fixed gateway responses to bypass this path, but otherwise a
    codex-local model request is treated as an agent-loop request.
    """
    if not codex_local_model_requested(payload):
        return None
    admin_text = gateway_admin_text(payload)
    text = strip_routing(admin_text).strip() or str(admin_text or "").strip()
    if not text or "GATEWAY_ADMIN_" in text:
        return None
    messages = payload.get("messages") or []
    return {
        "workspace": agent_requested_workspace_from_text(admin_text, messages),
        "task": text[:6000],
        "model": str(payload.get("model") or DEFAULT_MODEL_ALIAS),
        "messages": messages,
    }

def normal_chat_requires_tool(payload):
    if codex_local_agent_loop_payload(payload):
        return True
    text = strip_routing(gateway_admin_text(payload)).strip()
    if "GATEWAY_ADMIN_" in text:
        return False
    # Non-codex models should not be pulled into the gateway by natural-language
    # keywords. Only concrete URLs are structural enough to route without the
    # codex-local TaskSpec planner.
    return bool(re.search(r"https?://", text))

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
    runtime = codex_local_model_runtime(
        model_name,
        task=gateway_admin_text(payload),
        role=ROLE_DIRECT,
    )
    workspace_name, workspace = select_workspace(payload.get("messages", []))
    snapshot = repo_snapshot(workspace_name, workspace)
    spec = {
        "model": str(runtime.get("model") or CODEX_LOCAL_CONFIG.default_model),
        "mode": str(runtime.get("role") or ROLE_DIRECT),
        "resolved_alias": str(runtime.get("resolved_alias") or DEFAULT_MODEL_ALIAS),
    }
    return spec, direct_messages(payload.get("messages", []), workspace_name, snapshot, spec["mode"])

def completion(payload):
    model_name = payload.get("model") or DEFAULT_MODEL_ALIAS
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

    explicit_loop = explicit_agent_loop_request(admin_text)
    if explicit_loop:
        try:
            explicit_loop["messages"] = payload.get("messages") or []
            text = agent_loop_response_text(admin_agent_loop(explicit_loop))
        except Exception as exc:
            text = f"CODEX_LOCAL_AGENT_LOOP_FAILED\nerror={type(exc).__name__}: {exc}"
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

    natural_loop = codex_local_agent_loop_payload(payload)
    if natural_loop:
        try:
            result = admin_agent_loop(natural_loop)
            text = agent_loop_response_text(result)
        except Exception as exc:
            text = (
                "CODEX_LOCAL_AGENT_LOOP_FAILED\n"
                f"error={type(exc).__name__}: {exc}\n"
                "recovery=Zkontroluj gateway health, aktivitu OpenWebUI filtrů a capability executor."
            )
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

    if normal_chat_requires_tool(payload):
        admin_text = gateway_admin_text(payload)
        task = strip_routing(admin_text).strip() or admin_text.strip()
        try:
            result = admin_agent_loop({
                "workspace": agent_requested_workspace_from_text(admin_text, payload.get("messages") or []),
                "task": task,
                "model": str(payload.get("model") or DEFAULT_MODEL_ALIAS),
                "messages": payload.get("messages") or [],
            })
            text = agent_loop_response_text(result)
        except Exception as exc:
            text = (
                "CODEX_LOCAL_AGENT_LOOP_FAILED\n"
                f"error={type(exc).__name__}: {exc}\n"
                "recovery=Zkontroluj gateway health, aktivitu OpenWebUI filtrů a capability executor."
            )
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

    if codex_local_model_requested(payload):
        text = (
            "CODEX_LOCAL_AGENT_LOOP_UNROUTED\n"
            "recovery=Zkontroluj capability-first routing ve gateway a aktivitu OpenWebUI codex filtrů; "
            "codex-local prompt nesmí tiše spadnout do plain LLM režimu."
        )
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

    def _write_sse_chunk(self, result_id, created, model_name, content="", finish_reason=None):
        self.wfile.write(
            f"data: {json.dumps(sse_chunk(result_id, created, model_name, content, finish_reason), ensure_ascii=False)}\n\n".encode()
        )
        self.wfile.flush()

    def stream_completion_with_heartbeat(self, payload):
        model_name = payload.get("model") or DEFAULT_MODEL_ALIAS
        result_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())
        state = {"result": None, "error": None}

        def worker():
            try:
                state["result"] = completion(payload)
            except Exception as exc:
                state["error"] = exc

        thread = threading.Thread(target=worker, name="codex-gateway-stream-completion", daemon=True)
        thread.start()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        progress_messages = [
            "Pracuji na tom pres codex-local agent loop...\n\n",
            "\n[progress] Gateway porad bezi, cekam na planner/capability vysledek...\n",
            "\n[progress] Request je stale aktivni; drzim spojeni zive, aby OpenWebUI nespadlo na fetch timeout...\n",
        ]
        sent_progress = False
        next_progress_at = time.monotonic()
        progress_idx = 0

        while thread.is_alive():
            thread.join(timeout=0.5)
            now = time.monotonic()
            if now < next_progress_at:
                continue
            try:
                self._write_sse_chunk(result_id, created, model_name, progress_messages[min(progress_idx, len(progress_messages) - 1)])
            except (BrokenPipeError, ConnectionResetError):
                return
            sent_progress = True
            progress_idx += 1
            next_progress_at = now + 12

        error = state.get("error")
        if error is not None:
            final_text = (
                "CODEX_LOCAL_STREAM_FAILED\n"
                f"error={type(error).__name__}: {error}\n"
                "recovery=Zkontroluj gateway log a zopakuj request; spojeni uz dostalo heartbeat, chyba vznikla uvnitr executor vrstvy."
            )
        else:
            result = state.get("result") or {}
            final_text = str(
                ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ).strip()
            if not final_text:
                final_text = fallback_response_text(payload)

        if sent_progress:
            final_text = "\n" + final_text
        try:
            self._write_sse_chunk(result_id, created, model_name, final_text)
            self._write_sse_chunk(result_id, created, model_name, finish_reason="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def proxy_ollama_sse(self, payload):
        model_name = payload.get("model") or DEFAULT_MODEL_ALIAS
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
            payload = {"ok": True, "default": default, "workspaces": sorted(workspaces)}
            payload.update(runtime_health())
            return self.sendj(payload)
        if self.path == "/v1/models":
            now = int(time.time())
            return self.sendj({"object": "list", "data": [{"id": k, "object": "model", "created": now, "owned_by": "local"} for k in MODELS]})
        if self.path == "/v1/workspaces":
            default, workspaces = load_registry()
            return self.sendj({"default": default, "workspaces": workspaces})
        self.sendj({"error": "not found"}, 404)

    def do_POST(self):
        try:
            if self.path == "/v1/admin/web/fetch":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_web_fetch(payload))
            if self.path == "/v1/admin/web/answer":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_web_answer(payload))
            if self.path == "/v1/admin/file/explain":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_explain_file(payload))
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
            if self.path == "/v1/admin/agent/loop":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_agent_loop(payload))
            if self.path == "/v1/admin/agent/self-improve":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_agent_self_improve(payload))
            if self.path == "/v1/admin/workspace/run":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_run_workspace(payload))
            if self.path == "/v1/admin/workspace/run/status":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_workspace_run_status(payload))
            if self.path == "/v1/admin/workspace/edit":
                if not admin_ok(self):
                    return self.sendj({"error": "forbidden"}, 403)
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self.sendj(admin_workspace_edit(payload))
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
            if (
                payload.get("stream")
                and not gateway_fixed_response_requested(payload)
                and not explicit_agent_loop_request(gateway_admin_text(payload))
                and not codex_local_agent_loop_payload(payload)
                and not normal_chat_requires_tool(payload)
            ):
                return self.proxy_ollama_sse(payload)
            if (
                payload.get("stream")
                and (
                    explicit_agent_loop_request(gateway_admin_text(payload))
                    or codex_local_agent_loop_payload(payload)
                    or normal_chat_requires_tool(payload)
                )
            ):
                return self.stream_completion_with_heartbeat(payload)
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
