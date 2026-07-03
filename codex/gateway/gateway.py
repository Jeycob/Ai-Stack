# openwebui-admin-filter-smoke: 2026-07-02T14:05:00
# secure_gateway marker added by admin filter
# openwebui-chat-deploy-test: 2026-07-02T15-18-local
# gateway-change-via-openwebui-chat: ok
# gateway-scheduled-chat-patch: ok
# gateway-chat-no-error-patch: ok
# gateway-chat-fast-ack-patch: ok
import html, ipaddress, json, os, re, socket, subprocess, sys, time, uuid, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from workspace_scan import collect, load_workspace

WORKSPACES_FILE = os.getenv("CODEX_WORKSPACES_FILE", "/mnt/c/Repositories/ai-stack/codex/workspaces.json")
OLLAMA_OPENAI_URL = os.getenv("OLLAMA_OPENAI_URL", "http://192.168.0.48:11434/v1")
OPENWEBUI_HEALTH_URL = os.getenv("OPENWEBUI_HEALTH_URL", "http://127.0.0.1:9090/")
OPENWEBUI_LOADER_URL = os.getenv("OPENWEBUI_LOADER_URL", "http://127.0.0.1:9090/static/loader.js")
REPO_ROOT = Path(WORKSPACES_FILE).resolve().parents[1]
CAPABILITY_ROADMAP_FILE = REPO_ROOT / "docs" / "codex-local-capability-roadmap.json"
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
WORKSPACE_LABEL_RE = r"(?:repo|repository|repositar|repozitar|repozitář|projekt|project|workspace)"
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
    with open(WORKSPACES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("default", "smoke"), data.get("workspaces", {})

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

def content_to_text(content):
    if isinstance(content, list):
        return "\n".join(str(x.get("text", x)) for x in content)
    return str(content)

def select_workspace(messages):
    default, workspaces = load_registry()
    full = "\n".join(content_to_text(m.get("content", "")) for m in messages)
    m = re.search(rf"(?im)^\s*{WORKSPACE_LABEL_RE}\s*:\s*([A-Za-z0-9_.-]+)\s*$", full)
    name = m.group(1) if m else default
    if name not in workspaces:
        raise ValueError(f"Unknown workspace '{name}'. Allowed: {', '.join(sorted(workspaces))}")
    return name, workspaces[name]

def strip_routing(text):
    return re.sub(rf"(?im)^\s*{WORKSPACE_LABEL_RE}\s*:?\s*[A-Za-z0-9_.-]+\s*$", "", text).strip()

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

def runtime_health():
    root = http_probe(OPENWEBUI_HEALTH_URL, timeout=2, max_bytes=4096)
    loader = http_probe(OPENWEBUI_LOADER_URL, timeout=2, max_bytes=8192)
    return {
        "openwebui": {
            "ok": bool(root.get("ok")) and bool(loader.get("ok")),
            "root": root,
            "loader": loader,
        }
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
        response = ollama_chat(MODELS["codex-local-plan-qwen14b"]["model"], messages, timeout=180)
        answer = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not answer:
            answer = "Model nevrátil odpověď nad načteným zdrojem."
    result = dict(fetch)
    result["question"] = question
    result["answer"] = answer
    return result


AGENT_LOOP_WORKFLOWS = {
    "review",
    "edit",
    "action",
    "run",
    "autopilot",
    "bootstrap",
    "web_answer",
    "web_fetch",
    "deploy",
    "clarify",
}
AGENT_LOOP_ACTIONS = {"install", "verify", "smoke", "test", "build", "lint"}


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
        "read only",
        "readonly",
        "do not edit",
        "don't edit",
    )
    return any(cue in lower for cue in cues)


def agent_extract_repo_name(task):
    text = str(task or "").strip()
    patterns = (
        r"(?i)\b(?:vytvor|vytvoř|zaloz|založ|create)\b\s+(?:mi\s+)?(?:(?:novy|nový|nove|nové|new)\s+)?(?:repo|repository|repozitar|repozitář|repositar|workspace|projekt|project)\s*:\s*([A-Za-z0-9_.-]{1,80})\b",
        r"(?i)\b(?:repo|repository|repozitar|repozitář|repositar)\s+([A-Za-z0-9_.-]{1,80})\b",
        r"(?i)\b(?:workspace|projekt|project)\s+([A-Za-z0-9_.-]{1,80})\b",
        r"(?i)\b(?:vytvor|vytvoř|zaloz|založ)\s+(?:nove|nové|new)?\s*(?:repo|repository|repozitar|repozitář)\s+([A-Za-z0-9_.-]{1,80})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def agent_controller_workspace(requested_workspace):
    default, workspaces = load_registry()
    if requested_workspace in workspaces:
        return requested_workspace, True, workspaces
    if "ai-stack" in workspaces:
        return "ai-stack", False, workspaces
    return default, False, workspaces


def agent_capability_catalog():
    registry = load_workspace_action_registry()
    lines = [
        "- review: read-only analysis over repository snapshot; never edits",
        "- edit: safe repository edit through audited unified diff application; optional verify/test/build/smoke follow-up",
        "- action: one audited workspace action from {install, verify, smoke, test, build, lint}",
        "- run: execute one explicit short command inside codex-opencode-<workspace> and return output",
        "- autopilot: recovery/verify loop over install/verify/smoke/test/build/lint",
        "- bootstrap: create local repository/workspace, init git, generate SSH key, optionally continue with follow-up actions",
        "- web_answer: answer a question from a public HTTP/HTTPS source",
        "- web_fetch: fetch text from a public HTTP/HTTPS source",
        "- deploy: ai-stack deploy/restart flow",
        "- clarify: ask for one missing piece of information instead of pretending to execute",
        "",
        "Workspace actions:",
    ]
    for action in ("install", "verify", "smoke", "test", "build", "lint"):
        spec = registry.get(action) or {}
        summary = str(spec.get("summary", "")).strip()
        lines.append(f"- {action}: {summary or 'audited workspace action'}")
    return "\n".join(lines)


def agent_infer_action_from_task(task):
    lower = str(task or "").lower()
    registry = load_workspace_action_registry()
    for action, spec in registry.items():
        if not isinstance(spec, dict):
            continue
        cues = spec.get("cues") or []
        if any(isinstance(cue, str) and cue.lower() in lower for cue in cues):
            return str(action).strip().lower()
    return ""


def agent_edit_requested(task):
    lower = str(task or "").lower()
    cues = (
        "uprav",
        "edituj",
        "pridej",
        "přidej",
        "vytvor soubor",
        "vytvoř soubor",
        "vytvor",
        "vytvoř",
        "dopln",
        "doplň",
        "zmen",
        "změň",
        "append",
        "modify",
        "update",
        "create file",
    )
    return any(cue in lower for cue in cues)


def agent_bootstrap_requested(task):
    lower = str(task or "").lower()
    cues = (
        "vytvor repo",
        "vytvoř repo",
        "vytvor repository",
        "vytvoř repository",
        "zaloz repo",
        "založ repo",
        "create repo",
        "create repository",
        "nove repository",
        "nové repository",
        "nove repo",
        "nové repo",
        "vytvor workspace",
        "vytvoř workspace",
        "initni git",
        "init git",
        "ssh klic",
        "ssh klíč",
    )
    return any(cue in lower for cue in cues)


def agent_public_url_from_task(task):
    text = str(task or "").strip()
    match = re.search(r"https?://[^\s<>'\")]+", text)
    if match:
        return match.group(0).rstrip(".,;:!?)]}")
    lower = text.lower()
    if "seznam.cz" in lower and ("svatek" in lower or "svátek" in lower):
        return "https://search.seznam.cz/?q=" + urllib.parse.quote_plus("kdo má dnes svátek")
    known = {
        "seznam.cz": "https://www.seznam.cz/",
        "novinky.cz": "https://www.novinky.cz/",
        "idnes.cz": "https://www.idnes.cz/",
        "github.com": "https://github.com/",
        "example.com": "https://example.com/",
    }
    for domain, url in known.items():
        if domain in lower:
            return url
    return ""


def agent_web_question_requested(task):
    lower = str(task or "").lower()
    cues = (
        "?",
        "kdo ",
        "co ",
        "jaky ",
        "jaký ",
        "jaka ",
        "jaká ",
        "jake ",
        "jaké ",
        "kdy ",
        "kde ",
        "proc ",
        "proč ",
        "who ",
        "what ",
        "when ",
        "where ",
        "why ",
        "svatek",
        "svátek",
        "dneska",
        "dnes ",
    )
    return any(cue in lower for cue in cues)


def agent_run_requested(task):
    lower = str(task or "").lower()
    cues = (
        "spust prikaz",
        "spusť příkaz",
        "spust command",
        "spusť command",
        "run command",
        "execute command",
        "shell command",
        "terminal command",
        "vypis verzi",
        "vypiš verzi",
        "ukaz verzi",
        "ukaž verzi",
    )
    return any(cue in lower for cue in cues)


def agent_infer_command_from_task(task):
    text = str(task or "").strip()
    lower = text.lower()
    fenced = re.search(r"(?is)```(?:bash|sh|shell)?\s*\n(.+?)\n```", text)
    if fenced:
        line = next((item.strip() for item in fenced.group(1).splitlines() if item.strip()), "")
        if line:
            return ["sh", "-lc", line]
    inline = re.search(r"`([^`\n]{1,300})`", text)
    if inline:
        return ["sh", "-lc", inline.group(1).strip()]
    command_match = re.search(
        r"(?is)\b(?:spust|spusť|run|execute)\s+(?:prikaz|příkaz|command)\s*:?\s*(.+)$",
        text,
    )
    if command_match:
        candidate = command_match.group(1).strip()
        if candidate:
            return ["sh", "-lc", candidate[:500]]
    if "git status" in lower:
        return ["git", "status", "--short", "--branch"]
    if re.search(r"\b(pwd|kde jsem|working directory)\b", lower):
        return ["pwd"]
    if re.search(r"\b(ls|vypis soubory|vypiš soubory|seznam souboru|seznam souborů)\b", lower):
        return ["ls", "-la"]
    if "python" in lower and ("verzi" in lower or "version" in lower):
        return ["python3", "--version"]
    if "node" in lower and ("verzi" in lower or "version" in lower):
        return ["node", "--version"]
    if "npm" in lower and ("verzi" in lower or "version" in lower):
        return ["npm", "--version"]
    return []


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


def agent_plan_messages(requested_workspace, controller_workspace, workspace_exists, task, snapshot):
    registry = load_registry()[1]
    workspace_list = ", ".join(sorted(registry))
    return [
        {
            "role": "system",
            "content": (
                "You are the intent planner for a local Codex-like engineering agent. "
                "Return JSON only. Do not explain. Choose the single best next workflow, not every possible one.\n\n"
                "Output schema:\n"
                "{\n"
                '  "workflow": "review|edit|action|run|autopilot|bootstrap|web_answer|web_fetch|deploy|clarify",\n'
                '  "reason": "short reason",\n'
                '  "read_only": true,\n'
                '  "workspace": "workspace-name",\n'
                '  "action": "install|verify|smoke|test|build|lint or empty",\n'
                '  "command": ["short","command","args"],\n'
                '  "run_after": "install|verify|smoke|test|build|lint or empty",\n'
                '  "followup_actions": ["install","verify"],\n'
                '  "repo_name": "name or empty",\n'
                '  "github": false,\n'
                '  "url": "public url or empty",\n'
                '  "question": "question for public web answer or empty",\n'
                '  "confidence": "high|medium|low"\n'
                "}\n\n"
                "Planning rules:\n"
                "- If the user explicitly says no edits, choose review with read_only=true.\n"
                "- For analysis/explanation/review prompts, choose review.\n"
                "- For file or code changes, choose edit. If the prompt also asks to verify/test/build/smoke after the edit, set run_after.\n"
                "- For direct install/verify/smoke/test/build/lint requests, choose action.\n"
                "- For an explicit one-off shell/terminal command or a small runtime inspection not covered by named actions, choose run and set command as a JSON string array.\n"
                "- For 'do what is needed', 'continue autonomously', recovery, or multi-step runtime stabilization, choose autopilot.\n"
                "- For creating a new repository/workspace/git init/SSH key bootstrap flow, choose bootstrap. "
                "Put the new repository name in repo_name and any post-bootstrap runtime steps into followup_actions.\n"
                "- If GitHub push/remote is mentioned during bootstrap, set github=true, but do not assume push is already confirmed.\n"
                "- For public website questions, choose web_answer; for plain fetch, choose web_fetch.\n"
                "- For ai-stack self-update/deploy/restart prompts, choose deploy.\n"
                "- If the request is materially ambiguous, choose clarify.\n"
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
                f"User task:\n{task}\n\n"
                f"Repository snapshot for the controller workspace:\n{snapshot[:18000]}"
            ),
        },
    ]


def normalize_agent_plan(plan, requested_workspace, controller_workspace, workspace_exists, task):
    if not isinstance(plan, dict):
        plan = {}
    workflow = str(plan.get("workflow") or "").strip().lower() or "clarify"
    if workflow not in AGENT_LOOP_WORKFLOWS:
        workflow = "clarify"
    requested_read_only = agent_read_only_requested(task)
    read_only = requested_read_only
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
    if workflow in {"review", "edit", "action", "run", "autopilot", "deploy"}:
        workspace = requested_workspace if workspace_exists else controller_workspace
    confidence = str(plan.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    inferred_action = agent_infer_action_from_task(task)
    bootstrap_requested = agent_bootstrap_requested(task)
    run_requested = agent_run_requested(task)
    if workflow == "bootstrap" and not bootstrap_requested:
        if agent_edit_requested(task):
            workflow = "edit"
        elif inferred_action:
            workflow = "action"
            action = action or inferred_action
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
    if not read_only and workflow == "review" and inferred_action:
        workflow = "action"
        action = action or inferred_action
    if not read_only and workflow == "review" and run_requested:
        workflow = "run"
    if not read_only and workflow == "review" and agent_edit_requested(task):
        workflow = "edit"
    if workflow == "edit" and not run_after and inferred_action:
        run_after = inferred_action
    if workflow == "run" and not command:
        command = agent_infer_command_from_task(task)
    if workflow == "run" and not command:
        workflow = "clarify"
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
        "github": bool(plan.get("github")),
        "url": str(plan.get("url") or "").strip(),
        "question": str(plan.get("question") or "").strip(),
        "confidence": confidence,
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
    response = ollama_chat(MODELS["codex-local-plan-qwen14b"]["model"], messages, timeout=240)
    return response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


def agent_plan(task, requested_workspace, controller_workspace, workspace_exists):
    default, workspaces = load_registry()
    cfg = workspaces.get(controller_workspace) or workspaces.get(default)
    try:
        snapshot = repo_snapshot(controller_workspace, cfg) if cfg else ""
    except Exception as exc:
        snapshot = f"SNAPSHOT_UNAVAILABLE: {exc}"
    response = ollama_chat(
        MODELS["codex-local-plan-qwen14b"]["model"],
        agent_plan_messages(requested_workspace, controller_workspace, workspace_exists, task, snapshot),
        timeout=240,
    )
    raw = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    plan = normalize_agent_plan(extract_json_object(raw), requested_workspace, controller_workspace, workspace_exists, task)
    return plan, raw


def admin_agent_loop(payload):
    requested_workspace = str(payload.get("workspace") or "").strip() or "ai-stack"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", requested_workspace):
        raise ValueError("workspace must match [A-Za-z0-9_.-]{1,80}")
    task = str(payload.get("task") or "").strip()
    if not task or len(task) > 6000:
        raise ValueError("task must be 1..6000 characters")
    controller_workspace, workspace_exists, workspaces = agent_controller_workspace(requested_workspace)
    plan, raw_plan = agent_plan(task, requested_workspace, controller_workspace, workspace_exists)

    result = {
        "ok": False,
        "requested_workspace": requested_workspace,
        "controller_workspace": controller_workspace,
        "workspace_exists": workspace_exists,
        "task": task,
        "plan": plan,
        "raw_plan": raw_plan,
        "workflow": plan["workflow"],
        "read_only": plan["read_only"],
    }

    workflow = plan["workflow"]
    if workflow == "clarify":
        result["ok"] = True
        result["summary"] = "Potřebuju upřesnit zadání nebo chybějící cílový workspace."
        result["answer"] = (
            "Nejsem si ještě jistý správným workflow. Upřesni prosím cílový workspace nebo konkrétní akci "
            "(např. review, edit, install, verify, bootstrap repo)."
        )
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

    if workflow == "deploy":
        deploy = admin_deploy_stack({"branch": "main"})
        result["ok"] = bool(deploy.get("ok"))
        result["summary"] = "ai-stack deploy scheduled." if result["ok"] else "ai-stack deploy was not scheduled."
        result["execution"] = deploy
        return result

    if workflow in {"edit", "action", "autopilot"} and not workspace_exists:
        result["summary"] = f"Workspace '{requested_workspace}' zatím není registrovaný."
        result["recovery"] = {
            "text": "Nejdřív vytvoř nebo zaregistruj workspace, případně použij bootstrap workflow.",
            "suggested_workflow": "bootstrap",
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
        root = load_workspace(WORKSPACES_FILE, workspace)
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

    chosen = candidate_actions[0] if candidate_actions else None
    chosen_action = chosen["action"] if chosen else None
    chosen_reason = chosen["reason"] if chosen else ""

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
        action_name = next_candidates[0]["action"]
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
        "recommendation": recommendation.get("text", ""),
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


def agent_requested_workspace_from_text(text):
    default, workspaces = load_registry()
    match = re.search(rf"(?im)^\s*{WORKSPACE_LABEL_RE}\s*:?\s*([A-Za-z0-9_.-]{{1,80}})\s*$", str(text or ""))
    if match:
        return match.group(1)
    inferred = agent_extract_repo_name(text)
    if inferred:
        return inferred
    if "ai-stack" in workspaces:
        return "ai-stack"
    return default


def trim_response_text(text, limit=14000):
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated {len(value) - limit} chars]"


def agent_loop_response_text(result):
    status = "AGENT_LOOP_OK" if result.get("ok") else "AGENT_LOOP_NEEDS_ATTENTION"
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    lines = [
        status,
        f"requested_workspace={result.get('requested_workspace', '')}",
        f"controller_workspace={result.get('controller_workspace', '')}",
        f"workflow={result.get('workflow', plan.get('workflow', ''))}",
        f"read_only={result.get('read_only', plan.get('read_only', ''))}",
        f"summary={result.get('summary', '')}",
    ]
    if plan.get("reason"):
        lines.append(f"reason={plan.get('reason')}")
    answer = str(result.get("answer") or "").strip()
    if answer:
        lines.append("")
        lines.append(answer)
    for key in ("execution", "followup", "recovery", "plan"):
        value = result.get(key)
        if value in (None, {}, []):
            continue
        try:
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            rendered = str(value)
        lines.append("")
        lines.append(f"{key}:")
        lines.append("```json")
        lines.append(trim_response_text(rendered, 10000))
        lines.append("```")
    return "\n".join(lines).strip()


def explicit_agent_loop_request(text):
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
    idx = parts.index("--")
    task = " ".join(parts[idx + 1 :]).strip()
    if not task:
        return None
    return {"workspace": workspace, "task": task}

def normal_chat_requires_tool(payload):
    text = strip_routing(gateway_admin_text(payload)).strip()
    if "GATEWAY_ADMIN_" in text:
        return False
    tool_intent_re = re.compile(
        r"(?is)\b("
        r"ssh|github|push|pushni|install|nainstaluj|spust|spusť|run|shell|command|terminal|"
        r"st[aá]hni|st[aá]hnout|fetch|download|web|internet|http|https|seznam\.cz|"
        r"vygeneruj\s+.*(?:klic|klíč|key)|generate\s+.*key|"
        r"(?:vytvor|vytvoř|zaloz|založ|inituj|inicializuj)\s+.*"
        r"(?:repo|repository|repozitar|repozitář|workspace|projekt)|"
        r"(?:precti|přečti|vysvetli|vysvětli|show|read)\s+.*"
        r"(?:soubor|file|docker-compose|compose|README|gateway\.py)"
        r")\b"
    )
    return bool(tool_intent_re.search(text))

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

    explicit_loop = explicit_agent_loop_request(admin_text)
    if explicit_loop:
        try:
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

    if normal_chat_requires_tool(payload):
        admin_text = gateway_admin_text(payload)
        task = strip_routing(admin_text).strip() or admin_text.strip()
        try:
            result = admin_agent_loop({
                "workspace": agent_requested_workspace_from_text(admin_text),
                "task": task,
            })
            text = agent_loop_response_text(result)
        except Exception as exc:
            text = fallback_response_text(payload) + f"\n\nAGENT_LOOP_ERROR: {type(exc).__name__}: {exc}"
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
