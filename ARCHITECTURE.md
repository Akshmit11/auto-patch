# AutoPatch Architecture

## Design principles

1. **Hand-rolled loop** — `plan → act → observe` (retry on Day 2). No LangChain/LangGraph as core.
2. **MCP tools** — filesystem, sandbox exec, GitHub, codebase/symbol lookup via official `mcp` Python SDK (FastMCP servers + in-process callables).
3. **Docker only for untrusted code** — target-repo tests never run on the host.
4. **Unified diffs** — patches are reviewable diffs, not full-file rewrites.
5. **Swappable LLM** — all model calls go through `LLMProvider` (`ClaudeProvider` default, `OpenAIProvider` swap).
6. **Explainability** — every tool call, model call, and outcome is structured JSON (JSONL per run).

## Day 1 pipeline

```
┌─────────────┐   ┌──────────────┐   ┌────────────────┐
│ Issue ingest│──►│ Clone/index  │──►│ Retrieve ctx   │
│ GitHub/local│   │ tree-sitter  │   │ keyword+symbol │
└─────────────┘   └──────────────┘   └───────┬────────┘
                                             │
                      ┌──────────────────────▼────────┐
                      │ Planner (LLM → JSON plan)     │
                      │ logged as plan_created event  │
                      └──────────────────────┬────────┘
                                             │
                      ┌──────────────────────▼────────┐
                      │ Patcher (LLM → unified diff)  │
                      │ max_files guardrail           │
                      └──────────────────────┬────────┘
                                             │
                      ┌──────────────────────▼────────┐
                      │ Verifier (Docker sandbox)     │
                      │ apply patch + pytest          │
                      └───────────────────────────────┘
```

Day 2 adds: capped retries, test generation, draft PR, richer guardrails, trace viewer.  
Day 3 adds: eval harness + metrics + polish.

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | Thin Typer entrypoint (`run`, `index`, `mcp`) |
| `config.py` | `pydantic-settings` from `.env` |
| `agent/loop.py` | Orchestration |
| `agent/planner.py` | Structured plan |
| `agent/patcher.py` | Unified-diff generation + file cap |
| `agent/verifier.py` | Sandbox test observation |
| `mcp_tools/*` | MCP servers + tool classes |
| `retrieval/symbol_index.py` | tree-sitter Python index |
| `sandbox/docker_runner.py` | Container lifecycle |
| `llm/provider.py` | Provider interface + Claude/OpenAI |
| `tracing/logger.py` | JSONL + token/cost |

## MCP tool surface

| Server | Tools |
|---|---|
| `filesystem` | `fs_read_file`, `fs_write_file`, `fs_list_dir` |
| `sandbox` | `sandbox_exec`, `sandbox_apply_patch_and_test` |
| `github` | `github_get_issue` |
| `codebase` | `codebase_build_index`, `codebase_search_symbols`, `codebase_relevant_files` |

Run: `uv run autopatch mcp <server> [--workspace PATH]`

The agent loop uses the same tool classes in-process (identical behavior, lower latency). MCP stdio servers exist for external hosts and demos.

## Sandbox safety

- Target code executes only in Docker (`DockerRunner`).
- Default image: `python:3.11-slim`.
- Network disabled for generic `exec`; briefly enabled for `pip install` during test setup.
- Memory/CPU limits, `no-new-privileges`.
- Host path: pure text patch apply only (`apply_patch_host_safe`) — never imports target modules.

## Retrieval strategy (v1)

1. Parse all `*.py` with tree-sitter → functions, methods, classes, imports.
2. Score symbols against issue tokens (name equality, substring, path).
3. Return top files + contents as LLM context.
4. Embeddings / vector DB are stretch only.

## Cost tracking

Each model call records `input_tokens`, `output_tokens`, and estimated USD via static price tables in `tracing/logger.py`. Aggregates are stored on the run trace and written to result JSON.
