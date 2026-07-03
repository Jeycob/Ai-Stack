#!/usr/bin/env python3
"""Create a minimal FastAPI service starter scaffold in the target workspace."""

from __future__ import annotations

import argparse
from pathlib import Path


MAIN_PY = """from fastapi import FastAPI

from app.config import Settings

settings = Settings()
app = FastAPI(title=settings.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
"""


CONFIG_PY = """from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FastAPI Starter"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
"""


INIT_PY = ""


TEST_HEALTH = """from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
"""


REQUIREMENTS = """fastapi
uvicorn
pydantic-settings
pytest
httpx
"""


README_BLOCK = """
## FastAPI starter notes

- Create and activate a virtual environment before install:
  - `python -m venv .venv`
  - `. .venv/bin/activate`
- Install and run example:
  - `python -m pip install -r requirements.txt`
  - `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
"""


def write_if_missing(path: Path, content: str) -> str:
    if path.exists():
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "created"


def append_readme_notes(path: Path) -> str:
    if not path.exists():
        path.write_text("# Project\n", encoding="utf-8")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "## FastAPI starter notes" in text:
        return "kept"
    suffix = README_BLOCK if text.endswith("\n") else "\n" + README_BLOCK
    path.write_text(text + suffix, encoding="utf-8")
    return "updated"


def ensure_gitignore(path: Path) -> str:
    entries = [".venv/", "__pycache__/", "*.pyc"]
    if not path.exists():
        path.write_text(".venv/\n__pycache__/\n*.pyc\n", encoding="utf-8")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal FastAPI service starter scaffold.")
    parser.add_argument("target", nargs="?", default=".")
    args = parser.parse_args()

    root = Path(args.target).resolve()
    root.mkdir(parents=True, exist_ok=True)

    statuses = {
        "app_main": write_if_missing(root / "app/main.py", MAIN_PY),
        "app_config": write_if_missing(root / "app/config.py", CONFIG_PY),
        "app_init": write_if_missing(root / "app/__init__.py", INIT_PY),
        "test_health": write_if_missing(root / "tests/test_health.py", TEST_HEALTH),
        "requirements": write_if_missing(root / "requirements.txt", REQUIREMENTS),
        "gitignore": ensure_gitignore(root / ".gitignore"),
        "readme": append_readme_notes(root / "README.md"),
    }

    print("FASTAPI_SCAFFOLD_OK")
    print(f"root={root}")
    for key, value in statuses.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
