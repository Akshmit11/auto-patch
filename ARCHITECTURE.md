# AutoPatch Architecture

## Design principles

1. **Hand-rolled loop** — `plan → act → observe → retry` (capped). No LangChain/LangGraph as core.
2. **MCP tools** — filesystem, sandbox exec, GitHub, codebase/symbol lookup via official `mcp` Python SDK (FastMCP servers + in-process callables).
3. **Docker only for untrusted code** — target-repo tests never run on the host.
4. **Unified diffs** — patches are reviewable diffs, not full-file rewrites.
5. **Swappable LLM** — all model calls go through `LLMProvider` (`ClaudeProvider` default, `OpenAIProvider` swap).
6. **Explainability** — every tool call, model call, retry, and outcome is structured JSON (JSONL per run) + HTML/terminal trace viewer.
7. **Human review gate** — draft PRs only; never auto-merge; explicit promote to ready-for-review.

## Day 2 pipeline

```
┌─────────────┐   ┌──────────────┐   ┌────────────────┐
│ Issue ingest│──►│ Clone/index  │──►│ Retrieve ctx   │
│ GitHub/local│   │ tree-sitter  │   │ keyword+symbol │
└─────────────┘   └──────────────┘   └───────┬────────┘
                                             │
                      ┌──────────────────────▼────────┐
                      │ Guardrails (vague precheck)   │
                      └──────────────────────┬────────┘
                                             │
                      ┌──────────────────────▼────────┐
                      │ Planner (LLM → JSON plan)     │
                      │ vague → stop & ask clarify    │
                      └──────────────────────┬────────┘
                                             │
                 ┌───────────────────────────▼──────────────────────────┐
                 │ Retry loop (attempt 1 .. 1+max_retries)              │
                 │  ┌────────────┐  ┌─────────────┐  ┌───────────────┐  │
                 │  │ Patcher    │─►│ Test gen    │─►│ Verifier      │  │
                 │  │ unified    │  │ if no tests │  │ Docker apply  │  │
                 │  │ diff       │  │ in patch    │  │ + pytest      │  │
                 │  └────────────┘  └─────────────┘  └───────┬───────┘  │
                 │         ▲  reset workspace on fail         │         │
                 │         └──────── failure feedback ────────┘         │
                 └───────────────────────────┬──────────────────────────┘
                                             │ success
                      ┌──────────────────────▼────────┐
                      │ Optional draft PR (never merge)│
                      │ plan + tests + cost in body    │
                      └──────────────────────┬────────┘
                                             │
                      ┌──────────────────────▼────────┐
                      │ JSONL log + HTML/terminal view │
                      └───────────────────────────────┘
```

Day 3 adds: eval harness + metrics + polish.

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | Thin Typer entrypoint (`run`, `trace`, `pr`, `index`, `mcp`) |
| `config.py` | `pydantic-settings` from `.env` |
| `agent/loop.py` | Orchestration + retry + draft PR |
| `agent/planner.py` | Structured plan |
| `agent/patcher.py` | Unified-diff generation + file cap + merge |
| `agent/test_generator.py` | Issue-specific test unified diff |
| `agent/verifier.py` | Sandbox test observation |
| `agent/guardrails.py` | Vague filter, file cap, retries, deadlines |
| `mcp_tools/*` | MCP servers + tool classes |
| `retrieval/symbol_index.py` | tree-sitter Python index |
| `sandbox/docker_runner.py` | Container lifecycle |
| `llm/provider.py` | Provider interface + Claude/OpenAI |
| `tracing/logger.py` | JSONL + token/cost |
| `tracing/viewer.py` | Terminal + HTML trace viewer |

## MCP tool surface

| Server | Tools |
|---|---|
| `filesystem` | `fs_read_file`, `fs_write_file`, `fs_list_dir` |
| `sandbox` | `sandbox_exec`, `sandbox_apply_patch_and_test` |
| `github` | `github_get_issue`, `github_create_draft_pr`, `github_mark_ready_for_review` |
| `codebase` | `codebase_build_index`, `codebase_search_symbols`, `codebase_relevant_files` |

Run: `uv run autopatch mcp <server> [--workspace PATH]`

The agent loop uses the same tool classes in-process (identical behavior, lower latency). MCP stdio servers exist for external hosts and demos.

## Guardrails

| Guardrail | Default | Behavior |
|---|---|---|
| Max files per patch | 5 (`MAX_FILES_PER_PATCH`) | Reject / retry if exceeded |
| Max retries | 3 (`MAX_RETRIES`) | Cap attempts at `1 + max_retries` |
| Sandbox timeout | 300s | Kill container |
| Run timeout | 1800s | Abort whole run |
| Vague issue | heuristic + planner | Fail closed; no guessing large changes |
| Draft PR only | always | Never merge; promote via `autopatch pr ready` |

## Sandbox safety

- Target code executes only in Docker (`DockerRunner`).
- Default image: `python:3.11-slim`.
- Network disabled for generic `exec`; briefly enabled for `pip install` during test setup.
- Memory/CPU limits, `no-new-privileges`.
- Host path: pure text patch apply only (`apply_patch_host_safe`) — never imports target modules.
- Workspace reset via `git reset --hard` between retry attempts.

## Retrieval strategy (v1)

1. Parse all `*.py` with tree-sitter → functions, methods, classes, imports.
2. Score symbols against issue tokens (name equality, substring, path).
3. Return top files + contents as LLM context.
4. Embeddings / vector DB are stretch only.

## Cost tracking

Each model call records `input_tokens`, `output_tokens`, and estimated USD via static price tables in `tracing/logger.py`. Aggregates are stored on the run trace, written to result JSON, and surfaced in draft PR descriptions. Retries log `cost_delta_usd` per attempt.

## Trace viewer

```bash
uv run autopatch trace .autopatch/logs/run-<id>.jsonl
uv run autopatch trace .autopatch/logs/run-<id>.jsonl --html
```

Self-contained HTML (no CDN). Terminal view prints a compact timeline of model/tool/retry events.
