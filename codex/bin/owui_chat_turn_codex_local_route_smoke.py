#!/usr/bin/env python3
"""Stateless OpenWebUI smoke for codex-local natural agent-first routing.

This uses owui_chat_turn.py in --stateless mode so it still exercises the real
OpenWebUI /api/chat/completions path and active filters, but avoids mutating or
polling the visible audit chat. The goal is to catch stale/inactive filter
runtime or plain-LLM fallback with a cheap, deterministic check.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import owui_chat_turn as turn


DEFAULT_PROMPT = (
    "repo: ai-stack\n"
    "Prohlédni architekturu gateway/filter/helper vrstvy. Nic needituj. "
    "Řekni stručný závěr."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test stateless OpenWebUI codex-local natural agent-first routing."
    )
    parser.add_argument("--base-url", default=turn.DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="OWUI_API_KEY")
    parser.add_argument("--api-key-file", default=str(turn.DEFAULT_API_KEY_FILE))
    parser.add_argument("--model", default=turn.DEFAULT_MODEL)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--expect",
        action="append",
        default=["AGENT_LOOP", "workflow=review", "planner_source="],
        help="Substring that must appear in the stateless codex-local response. Can be repeated.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--max-delay", type=float, default=2.0)
    parser.add_argument("--total-timeout", type=float, default=120.0)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script = Path(__file__).resolve().parent / "owui_chat_turn.py"

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as prompt_file:
        prompt_file.write(args.prompt)
        prompt_path = prompt_file.name

    try:
        cmd = [
            sys.executable,
            str(script),
            "--stateless",
            "--base-url",
            args.base_url,
            "--api-key-env",
            args.api_key_env,
            "--api-key-file",
            args.api_key_file,
            "--model",
            args.model,
            "--prompt-file",
            prompt_path,
            "--timeout",
            str(args.timeout),
            "--attempts",
            str(args.attempts),
            "--initial-delay",
            str(args.initial_delay),
            "--max-delay",
            str(args.max_delay),
            "--total-timeout",
            str(args.total_timeout),
        ]
        if args.quiet:
            cmd.append("--quiet")

        try:
            proc = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(30.0, args.total_timeout + 45.0),
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", "replace")
            raise SystemExit(
                "OWUI_CODEX_ROUTE_SMOKE_FAILED\n"
                "reason=owui_chat_turn timeout\n"
                + output.rstrip()
            )

        output = proc.stdout or ""
        if proc.returncode != 0:
            raise SystemExit(
                "OWUI_CODEX_ROUTE_SMOKE_FAILED\n"
                f"reason=owui_chat_turn exit code {proc.returncode}\n"
                + output.rstrip()
            )

        missing = [needle for needle in args.expect if needle not in output]
        if missing:
            raise SystemExit(
                "OWUI_CODEX_ROUTE_SMOKE_FAILED\n"
                f"reason=missing expected markers {missing!r}\n"
                + output.rstrip()
            )

        print("OWUI_CODEX_ROUTE_SMOKE_OK")
        print(f"model={args.model}")
        print(f"expected_count={len(args.expect)}")
        print("output:")
        print(output.rstrip())
        return 0
    finally:
        try:
            Path(prompt_path).unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
