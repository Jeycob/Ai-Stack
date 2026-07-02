#!/usr/bin/env python3
import argparse, json, re
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

root = Path("/mnt/c/Repositories/ai-stack/codex/workspaces.json")
data = json.loads(root.read_text())
workspaces = data.setdefault("workspaces", {})

if args.port is None:
    used = [int(v.get("port", 4095)) for v in workspaces.values()]
    args.port = max(used + [4095]) + 1

workspaces[args.name] = {
    "path": args.path,
    "port": args.port,
    "cpus": args.cpus,
    "memory": args.memory,
}

if args.default:
    data["default"] = args.name

root.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(f"Added workspace {args.name} on port {args.port}: {args.path}")
