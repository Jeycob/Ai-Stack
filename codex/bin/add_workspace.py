#!/usr/bin/env python3
import argparse, json, os, re
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("name")
parser.add_argument("path")
parser.add_argument("--port", type=int)
parser.add_argument("--cpus", type=int, default=8)
parser.add_argument("--memory", default="16g")
parser.add_argument("--default", action="store_true")
args = parser.parse_args()

if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.name):
    raise SystemExit("Invalid workspace name. Use letters, numbers, dot, underscore, dash.")

root = Path(os.getenv("CODEX_WORKSPACES_FILE", "/mnt/c/Repositories/ai-stack/codex/workspaces.json"))
data = json.loads(root.read_text())
workspaces = data.setdefault("workspaces", {})

existing = workspaces.get(args.name)
normalized_path = Path(args.path).as_posix()

if existing:
    existing_path = Path(str(existing.get("path", ""))).as_posix()
    if existing_path != normalized_path:
        raise SystemExit(
            f"Workspace {args.name} already exists with different path: {existing_path} != {normalized_path}"
        )
    port = int(existing.get("port", 4095))
    workspaces[args.name] = {
        "path": normalized_path,
        "port": port,
        "cpus": args.cpus,
        "memory": args.memory,
    }
    if args.default:
        data["default"] = args.name
    root.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Workspace {args.name} already registered on port {port}: {normalized_path}")
    raise SystemExit(0)

if args.port is None:
    used = [int(v.get("port", 4095)) for v in workspaces.values()]
    args.port = max(used + [4095]) + 1

workspaces[args.name] = {
    "path": normalized_path,
    "port": args.port,
    "cpus": args.cpus,
    "memory": args.memory,
}

if args.default:
    data["default"] = args.name

root.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(f"Added workspace {args.name} on port {args.port}: {normalized_path}")
