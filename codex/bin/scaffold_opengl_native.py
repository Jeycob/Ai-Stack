#!/usr/bin/env python3
"""Create a minimal OpenGL starter scaffold in the target workspace."""

from __future__ import annotations

import argparse
from pathlib import Path


CMAKELISTS = """cmake_minimum_required(VERSION 3.20)
project(OpenGLStarter LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenGL REQUIRED)
find_package(glfw3 REQUIRED)

add_executable(opengl_starter
    src/main.cpp
)

target_link_libraries(opengl_starter PRIVATE OpenGL::GL glfw)
"""


MAIN_CPP = """#include <GLFW/glfw3.h>

#include <iostream>

int main() {
    if (!glfwInit()) {
        std::cerr << "glfwInit failed\\n";
        return 1;
    }

    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 3);
    glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);

    GLFWwindow* window = glfwCreateWindow(800, 600, "OpenGL Starter", nullptr, nullptr);
    if (!window) {
        std::cerr << "glfwCreateWindow failed\\n";
        glfwTerminate();
        return 1;
    }

    glfwMakeContextCurrent(window);

    while (!glfwWindowShouldClose(window)) {
        glClearColor(0.1f, 0.12f, 0.16f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        glfwSwapBuffers(window);
        glfwPollEvents();
    }

    glfwDestroyWindow(window);
    glfwTerminate();
    return 0;
}
"""


README_BLOCK = """
## OpenGL starter notes

- Build example:
  - `cmake -S . -B build`
  - `cmake --build build`
- Runtime dependencies are expected from the host system, especially `glfw3` and
  OpenGL development packages.
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
    if "## OpenGL starter notes" in text:
        return "kept"
    suffix = README_BLOCK if text.endswith("\n") else "\n" + README_BLOCK
    path.write_text(text + suffix, encoding="utf-8")
    return "updated"


def ensure_gitignore(path: Path) -> str:
    entries = ["build/", ".vscode/"]
    if not path.exists():
        path.write_text("build/\n.vscode/\n", encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8", errors="replace")
    changed = False
    lines = text.splitlines()
    for entry in entries:
        if entry.rstrip("/") not in lines and entry not in lines:
            text = text.rstrip("\n") + "\n" + entry + "\n"
            changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
        return "updated"
    return "kept"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal OpenGL native starter scaffold.")
    parser.add_argument("target", nargs="?", default=".")
    args = parser.parse_args()

    root = Path(args.target).resolve()
    root.mkdir(parents=True, exist_ok=True)

    cmake_status = write_if_missing(root / "CMakeLists.txt", CMAKELISTS)
    main_status = write_if_missing(root / "src/main.cpp", MAIN_CPP)
    gitignore_status = ensure_gitignore(root / ".gitignore")
    readme_status = append_readme_notes(root / "README.md")

    print("OPENGL_SCAFFOLD_OK")
    print(f"root={root}")
    print(f"cmakelists={cmake_status}")
    print(f"main_cpp={main_status}")
    print(f"gitignore={gitignore_status}")
    print(f"readme={readme_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
