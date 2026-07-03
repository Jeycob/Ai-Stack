#!/usr/bin/env python3
"""Create a minimal React/Vite TypeScript starter scaffold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PACKAGE_JSON = {
    "name": "react-app-starter",
    "version": "0.1.0",
    "private": True,
    "type": "module",
    "scripts": {
        "dev": "vite",
        "build": "tsc -b && vite build",
        "test": "vitest run",
        "smoke": "vite build",
    },
    "dependencies": {
        "react": "^19.1.1",
        "react-dom": "^19.1.1",
    },
    "devDependencies": {
        "@testing-library/jest-dom": "^6.8.0",
        "@testing-library/react": "^16.3.0",
        "@types/react": "^19.1.10",
        "@types/react-dom": "^19.1.7",
        "@vitejs/plugin-react": "^5.0.0",
        "jsdom": "^26.1.0",
        "typescript": "^5.9.2",
        "vite": "^7.1.3",
        "vitest": "^3.2.4",
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
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
"""


VITE_CONFIG_TS = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts"
  }
});
"""


INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>React Starter</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""


MAIN_TSX = """import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
"""


APP_TSX = """export default function App() {
  return (
    <main className="app-shell">
      <section className="panel">
        <p className="eyebrow">React Starter</p>
        <h1>Local Codex workspace is ready.</h1>
        <p className="body">
          Install dependencies, run tests, and continue implementation from this
          small but production-shaped baseline.
        </p>
      </section>
    </main>
  );
}
"""


STYLES_CSS = """:root {
  font-family: Inter, "Segoe UI", sans-serif;
  color: #111827;
  background: #f6f7fb;
}

body {
  margin: 0;
}

.app-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
}

.panel {
  width: min(560px, 100%);
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 24px;
  box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08);
}

.eyebrow {
  margin: 0 0 12px;
  color: #2563eb;
  font-size: 14px;
  font-weight: 600;
}

h1 {
  margin: 0 0 12px;
  font-size: 32px;
  line-height: 1.1;
}

.body {
  margin: 0;
  color: #4b5563;
  line-height: 1.6;
}
"""


APP_TEST_TSX = """import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("App", () => {
  it("renders the starter headline", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /local codex workspace is ready/i })).toBeTruthy();
  });
});
"""


TEST_SETUP_TS = """import "@testing-library/jest-dom";
"""


README_BLOCK = """
## React starter notes

- Install dependencies:
  - `npm install`
- Useful scripts:
  - `npm run dev`
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
    if "## React starter notes" in text:
        return "kept"
    suffix = README_BLOCK if text.endswith("\n") else "\n" + README_BLOCK
    path.write_text(text + suffix, encoding="utf-8")
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal React/Vite TypeScript starter scaffold.")
    parser.add_argument("target", nargs="?", default=".")
    args = parser.parse_args()

    root = Path(args.target).resolve()
    root.mkdir(parents=True, exist_ok=True)

    statuses = {
        "package_json": write_json_if_missing(root / "package.json", PACKAGE_JSON),
        "tsconfig_json": write_if_missing(root / "tsconfig.json", TSCONFIG_JSON),
        "tsconfig_app_json": write_if_missing(root / "tsconfig.app.json", TSCONFIG_APP_JSON),
        "vite_config": write_if_missing(root / "vite.config.ts", VITE_CONFIG_TS),
        "index_html": write_if_missing(root / "index.html", INDEX_HTML),
        "main_tsx": write_if_missing(root / "src/main.tsx", MAIN_TSX),
        "app_tsx": write_if_missing(root / "src/App.tsx", APP_TSX),
        "styles_css": write_if_missing(root / "src/styles.css", STYLES_CSS),
        "app_test_tsx": write_if_missing(root / "src/App.test.tsx", APP_TEST_TSX),
        "test_setup_ts": write_if_missing(root / "src/test/setup.ts", TEST_SETUP_TS),
        "gitignore": ensure_gitignore(root / ".gitignore"),
        "readme": append_readme_notes(root / "README.md"),
    }

    print("REACT_APP_SCAFFOLD_OK")
    print(f"root={root}")
    for key, value in statuses.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
