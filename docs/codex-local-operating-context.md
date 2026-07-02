# Codex Local Operating Context

This document captures the current local-ai workflow so future sessions can resume without reconstructing the setup from chat history.

## Primary rule

Use the visible OpenWebUI audit chat for repository work whenever possible:

- OpenWebUI base URL: http://192.168.0.48:9090
- Audit chat ID: 57529037-84b9-42e1-8bae-9eab35b601bd
- Default model alias: codex-local-plan-qwen14b
- Use OWUI_API_KEY from the local environment. Never write API keys, private SSH keys, passwords, `.env`, `codex/state/`, or `codex/audit/` contents into git.

## Live helper workflow

The preferred helper is `codex/bin/owui_chat_turn.py`. It writes the user instruction into the visible OpenWebUI chat immediately, creates a running assistant placeholder, updates it with heartbeat progress, and then replaces it with the final response.

Typical invocation from a trusted shell:

    OWUI_API_KEY=... codex/bin/owui_chat_turn.py --model codex-local-plan-qwen14b --prompt-file /tmp/prompt.txt --status-interval 3 --quiet

Supporting helpers:

- `codex/bin/http_retry.py`: standard-library HTTP retries with proxy disabled by default.
- `codex/bin/owui_request.sh`: wrapper for OpenWebUI REST calls using `OWUI_API_KEY`.
- `codex/bin/owui_chat_append.py`: offline chat JSON append helper, useful for diagnostics or repairs.
- `codex/bin/openwebui_gateway_admin_filter.py`: source copy of the OpenWebUI admin filter that is installed in OpenWebUI.

## Gateway and workspace flow

OpenWebUI calls the local OpenAI-compatible gateway on port 9101. The gateway routes prompts by a leading line such as:

    repo: ai-stack
    repo: Odysseus-Lite

The gateway injects a read-only repository snapshot into the local model. Admin changes are intentionally narrow and must go through explicit whitelisted commands handled by the OpenWebUI admin filter.

Useful admin commands in the visible chat:

- `GATEWAY_ADMIN_GIT_STATUS`: show safe git status, allowed paths, blocked paths, and sensitive-path detection.
- `GATEWAY_ADMIN_READ <path>`: read a whitelisted file.
- `GATEWAY_ADMIN_APPLY_NOW` followed by a unified diff: apply a whitelisted patch immediately.
- `GATEWAY_ADMIN_GIT_PUSH main <message>`: commit allowed paths and push to GitHub via the runtime SSH key.

## Marker safety

Admin markers should be interpreted only when they appear as standalone command lines. This matters because the source code itself contains marker strings. The gateway and filter were hardened for this after direct-response and source-versioning work.

## Current GitHub flow

The ai-stack repository remote is:

    git@github.com:Jeycob/Ai-Stack.git

The public deploy key was added by the user on GitHub. The private key remains under ignored runtime state and is copied to an OpenWebUI runtime path with strict permissions when pushing. Do not print or commit private key material.

## Recovery notes

If OpenWebUI package state is lost after container recreation, `openssh-client` may need to be reinstalled in the running OpenWebUI container before `GATEWAY_ADMIN_GIT_PUSH` can push. The admin filter has `GATEWAY_ADMIN_INSTALL_SSH_CLIENT`, but image-level persistence should eventually be solved in Dockerfile or compose.
