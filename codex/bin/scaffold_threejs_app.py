#!/usr/bin/env python3
"""Create a minimal Three.js + Vite + TypeScript starter scaffold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PACKAGE_JSON = {
    "name": "threejs-app-starter",
    "version": "0.1.0",
    "private": True,
    "type": "module",
    "scripts": {
        "dev": "vite --host 127.0.0.1",
        "build": "tsc -b && vite build",
        "smoke": "vite --host 127.0.0.1",
    },
    "dependencies": {
        "three": "^0.179.1",
    },
    "devDependencies": {
        "@types/three": "^0.179.0",
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
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
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
  }
});
"""


INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Three.js Starter</title>
  </head>
  <body>
    <div id="app">
      <div class="hud">
        <p class="eyebrow">Three.js Starter</p>
        <h1>Local Codex 3D workspace is ready.</h1>
      </div>
    </div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
"""


MAIN_TS = """import "./styles.css";
import * as THREE from "three";

const mount = document.getElementById("app");
if (!mount) {
  throw new Error("Missing #app mount");
}

const scene = new THREE.Scene();
scene.background = new THREE.Color("#0b1020");

const camera = new THREE.PerspectiveCamera(
  60,
  window.innerWidth / window.innerHeight,
  0.1,
  100
);
camera.position.set(0, 0.4, 3.2);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
mount.appendChild(renderer.domElement);

const geometry = new THREE.TorusKnotGeometry(0.7, 0.22, 160, 24);
const material = new THREE.MeshStandardMaterial({
  color: "#60a5fa",
  metalness: 0.35,
  roughness: 0.2
});
const mesh = new THREE.Mesh(geometry, material);
scene.add(mesh);

const key = new THREE.DirectionalLight("#ffffff", 2.3);
key.position.set(3, 2, 4);
scene.add(key);

const fill = new THREE.AmbientLight("#8ec5ff", 1.1);
scene.add(fill);

const clock = new THREE.Clock();

function resize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

window.addEventListener("resize", resize);

function animate() {
  const elapsed = clock.getElapsedTime();
  mesh.rotation.x = elapsed * 0.55;
  mesh.rotation.y = elapsed * 0.8;
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}

animate();
"""


STYLES_CSS = """:root {
  color: #f8fafc;
  background: #020617;
  font-family: Inter, "Segoe UI", sans-serif;
}

html,
body,
#app {
  width: 100%;
  height: 100%;
  margin: 0;
  overflow: hidden;
}

canvas {
  display: block;
}

.hud {
  position: fixed;
  inset: 20px auto auto 20px;
  z-index: 1;
  max-width: 320px;
  pointer-events: none;
}

.eyebrow {
  margin: 0 0 8px;
  color: #93c5fd;
  font-size: 14px;
  font-weight: 600;
}

h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.1;
}
"""


README_BLOCK = """
## Three.js starter notes

- Install dependencies:
  - `npm install`
- Useful scripts:
  - `npm run dev`
  - `npm run build`
  - `npm run smoke`
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
    if "## Three.js starter notes" in text:
        return "kept"
    suffix = README_BLOCK if text.endswith("\n") else "\n" + README_BLOCK
    path.write_text(text + suffix, encoding="utf-8")
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal Three.js starter scaffold.")
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
        "main_ts": write_if_missing(root / "src/main.ts", MAIN_TS),
        "styles_css": write_if_missing(root / "src/styles.css", STYLES_CSS),
        "gitignore": ensure_gitignore(root / ".gitignore"),
        "readme": append_readme_notes(root / "README.md"),
    }

    print("THREEJS_APP_SCAFFOLD_OK")
    print(f"root={root}")
    for key, value in statuses.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
