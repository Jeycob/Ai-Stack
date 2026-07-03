#!/usr/bin/env python3
"""Create a minimal Node/TypeScript service starter scaffold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PACKAGE_JSON = {
    "name": "node-service-starter",
    "version": "0.1.0",
    "private": True,
    "type": "module",
    "scripts": {
        "build": "tsc -p tsconfig.json",
        "test": "vitest run",
        "smoke": "tsx src/index.ts",
    },
    "dependencies": {
        "express": "^4.21.2",
    },
    "devDependencies": {
        "@types/express": "^5.0.3",
        "@types/node": "^24.3.0",
        "tsx": "^4.20.5",
        "typescript": "^5.9.2",
        "vitest": "^3.2.4",
    },
}


TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "outDir": "dist",
    "types": ["node"]
  },
  "include": ["src", "tests"]
}
"""


APP_TS = """export function healthPayload(): { status: string } {
  return { status: "ok" };
}
"""


INDEX_TS = """import express from "express";

import { healthPayload } from "./app.js";

const app = express();
const port = Number(process.env.PORT || 3000);

app.get("/health", (_req, res) => {
  res.json(healthPayload());
});

app.listen(port, "127.0.0.1", () => {
  console.log(`listening on http://127.0.0.1:${port}`);
});
"""


TEST_TS = """import { describe, expect, it } from "vitest";

import { healthPayload } from "../src/app.js";

describe("healthPayload", () => {
  it("returns ok status", () => {
    expect(healthPayload()).toEqual({ status: "ok" });
  });
});
"""


README_BLOCK = """
## Node service starter notes

- Install dependencies:
  - `npm install`
- Useful scripts:
  - `npm run smoke`
  - `npm test`
  - `npm run build`
"""


def write_if_missing(path: Path, content: str) -> str:
    if path.exists():
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "created"


def write_json_if_missing(path: Path, payload: dict) -> str:
    if path.exists():
        return "kept"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "created"


def ensure_gitignore(path: Path) -> str:
    entries = ["node_modules/", "dist/"]
    if not path.exists():
        path.write_text("node_modules/\ndist/\n", encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    changed = False
    for entry in entries:
        if entry not in lines:
            text = text.rstrip("\n") + "\n" + entry + "\n"
            changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
        return "updated"
    return "kept"


def append_readme_notes(path: Path) -> str:
    if not path.exists():
        path.write_text("# Project\n", encoding="utf-8")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "## Node service starter notes" in text:
        return "kept"
    suffix = README_BLOCK if text.endswith("\n") else "\n" + README_BLOCK
    path.write_text(text + suffix, encoding="utf-8")
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal Node service starter scaffold.")
    parser.add_argument("target", nargs="?", default=".")
    args = parser.parse_args()

    root = Path(args.target).resolve()
    root.mkdir(parents=True, exist_ok=True)

    statuses = {
        "package_json": write_json_if_missing(root / "package.json", PACKAGE_JSON),
        "tsconfig": write_if_missing(root / "tsconfig.json", TSCONFIG),
        "app_ts": write_if_missing(root / "src/app.ts", APP_TS),
        "index_ts": write_if_missing(root / "src/index.ts", INDEX_TS),
        "test_ts": write_if_missing(root / "tests/health.test.ts", TEST_TS),
        "gitignore": ensure_gitignore(root / ".gitignore"),
        "readme": append_readme_notes(root / "README.md"),
    }

    print("NODE_SERVICE_SCAFFOLD_OK")
    print(f"root={root}")
    for key, value in statuses.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
