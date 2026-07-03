#!/usr/bin/env python3
"""Create a minimal Electron + Vite starter scaffold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PACKAGE_JSON = {
    "name": "electron-app-starter",
    "version": "0.1.0",
    "private": True,
    "type": "module",
    "main": "main.js",
    "scripts": {
        "dev": "vite --host 127.0.0.1",
        "electron": "electron .",
        "build": "tsc -b && vite build",
        "smoke": "node scripts/smoke.mjs",
    },
    "dependencies": {
        "electron": "^37.2.6",
    },
    "devDependencies": {
        "typescript": "^5.9.2",
        "vite": "^7.1.3",
    },
}


TSCONFIG_JSON = """{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" }
  ]
}
"""


TSCONFIG_APP_JSON = """{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "strict": true
  },
  "include": ["src"]
}
"""


VITE_CONFIG_TS = """import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: "127.0.0.1",
    port: 5173
  },
  build: {
    outDir: "dist"
  }
});
"""


MAIN_JS = """import { BrowserWindow, app } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    webPreferences: {
      preload: path.join(__dirname, "preload.js")
    }
  });

  if (process.env.ELECTRON_START_URL) {
    win.loadURL(process.env.ELECTRON_START_URL);
    win.webContents.openDevTools({ mode: "detach" });
    return;
  }

  win.loadFile(path.join(__dirname, "dist/index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
"""


PRELOAD_JS = """import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("desktopMeta", {
  runtime: "electron"
});
"""


INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Electron Starter</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
"""


MAIN_TS = """import "./styles.css";

const mount = document.getElementById("app");
if (!mount) {
  throw new Error("Missing #app mount");
}

const runtime = (window as typeof window & { desktopMeta?: { runtime?: string } }).desktopMeta?.runtime ?? "browser";

mount.innerHTML = `
  <main class="shell">
    <section class="panel">
      <p class="eyebrow">Electron Starter</p>
      <h1>Local desktop workspace is ready.</h1>
      <p class="body">
        This starter keeps Electron thin and uses Vite for the renderer so codex-local can
        continue with a familiar frontend workflow.
      </p>
      <p class="meta">runtime: ${runtime}</p>
    </section>
  </main>
`;
"""


STYLES_CSS = """:root {
  font-family: Inter, "Segoe UI", sans-serif;
  color: #e5eefb;
  background: #08111f;
}

html,
body,
#app {
  width: 100%;
  min-height: 100%;
  margin: 0;
}

body {
  min-height: 100vh;
}

.shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
}

.panel {
  width: min(560px, 100%);
  background: rgba(15, 23, 42, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 8px;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 10px;
  color: #93c5fd;
  font-size: 14px;
  font-weight: 600;
}

h1 {
  margin: 0 0 12px;
  font-size: 30px;
  line-height: 1.1;
}

.body,
.meta {
  margin: 0;
  color: #cbd5e1;
  line-height: 1.6;
}

.meta {
  margin-top: 12px;
}
"""


SMOKE_MJS = """import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const required = [
  "package.json",
  "main.js",
  "preload.js",
  "index.html",
  "src/main.ts",
  "src/styles.css",
  "vite.config.ts"
];

const missing = required.filter((rel) => !fs.existsSync(path.join(root, rel)));
if (missing.length > 0) {
  console.error(`missing: ${missing.join(", ")}`);
  process.exit(1);
}

console.log("electron starter smoke ok");
"""


README_BLOCK = """
## Electron starter notes

- Install dependencies:
  - `npm install`
- Useful scripts:
  - `npm run dev`
  - `npm run build`
  - `npm run smoke`
  - `npm run electron`
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
    if "## Electron starter notes" in text:
        return "kept"
    suffix = README_BLOCK if text.endswith("\n") else "\n" + README_BLOCK
    path.write_text(text + suffix, encoding="utf-8")
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal Electron + Vite starter scaffold.")
    parser.add_argument("target", nargs="?", default=".")
    args = parser.parse_args()

    root = Path(args.target).resolve()
    root.mkdir(parents=True, exist_ok=True)

    statuses = {
        "package_json": write_json_if_missing(root / "package.json", PACKAGE_JSON),
        "tsconfig_json": write_if_missing(root / "tsconfig.json", TSCONFIG_JSON),
        "tsconfig_app_json": write_if_missing(root / "tsconfig.app.json", TSCONFIG_APP_JSON),
        "vite_config": write_if_missing(root / "vite.config.ts", VITE_CONFIG_TS),
        "main_js": write_if_missing(root / "main.js", MAIN_JS),
        "preload_js": write_if_missing(root / "preload.js", PRELOAD_JS),
        "index_html": write_if_missing(root / "index.html", INDEX_HTML),
        "main_ts": write_if_missing(root / "src/main.ts", MAIN_TS),
        "styles_css": write_if_missing(root / "src/styles.css", STYLES_CSS),
        "smoke_script": write_if_missing(root / "scripts/smoke.mjs", SMOKE_MJS),
        "gitignore": ensure_gitignore(root / ".gitignore"),
        "readme": append_readme_notes(root / "README.md"),
    }

    print("ELECTRON_APP_SCAFFOLD_OK")
    print(f"root={root}")
    for key, value in statuses.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
