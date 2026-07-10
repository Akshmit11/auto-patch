# AutoPatch

**Autonomous coding agent that turns a GitHub issue into a sandboxed, human-reviewed draft PR.**

AutoPatch ingests an issue, indexes the target repo with tree-sitter, plans a fix, generates a unified diff plus tests, verifies inside Docker with capped retries, and can open a **draft** PR with plan, test results, and cost. Never auto-merges — a human always reviews.

> **Status:** Day 2 complete — retry loop, test generation, draft PRs, guardrails, trace viewer. Day 3: eval harness + polish.

## Non-goals (v1)

- Large multi-file architectural rewrites
- Auto-merging PRs
- Fine-tuning models (orchestration + retrieval + tool use only)
- Supporting every language (Python first; TypeScript is stretch)

## Architecture (Day 2)

```text
Issue URL / local issue
        │
        ▼
  Guardrails (vague precheck)
        │
        ▼
  GitHub read (MCP) ──► shallow clone / local repo
        │
        ▼
  tree-sitter symbol index + keyword/symbol retrieval
        │
        ▼
  Planner (LLM) ── structured plan (logged)
        │
        ▼
  ┌─ Retry loop (max_retries + 1 attempts) ─┐
  │  Patcher → Test generator → Docker verify │
  │  failure feedback + cost delta per try    │
  └──────────────────┬───────────────────────┘
                     │ success
                     ▼
  Optional draft PR (plan + tests + cost) ── never merge
                     │
                     ▼
  Result JSON + JSONL log + HTML/terminal trace
```

Hand-rolled **plan → act → observe → retry** loop (no LangChain core). Tools are MCP servers (filesystem, sandbox, GitHub, codebase) used in-process by the agent.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full module map and guardrail table.

## Quickstart

```bash
# Prerequisites: Python 3.11+, uv, Docker (for sandbox verify)
uv sync --all-extras
cp .env.example .env   # set ANTHROPIC_API_KEY (and GITHUB_TOKEN for real issues/PRs)

# Symbol index only
uv run autopatch index demo/sample_target

# Day-2 loop on the included buggy sample (needs Docker + API key)
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md \
  --html-trace

# Plan + patch without Docker
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md \
  --skip-sandbox

# Real GitHub issue → draft PR
uv run autopatch run https://github.com/owner/repo/issues/123 --create-pr

# Human gate: promote draft to ready-for-review (never merges)
uv run autopatch pr ready https://github.com/owner/repo/pull/456

# Trace viewer
uv run autopatch trace .autopatch/logs/run-<id>.jsonl --html
```

See [demo/walkthrough.md](demo/walkthrough.md) for a fuller walkthrough.

## Project layout

Matches `AGENTS.md` / `PRD.md` §4:

```text
src/autopatch/
  agent/          # plan → act → observe → retry (+ test gen, guardrails)
  mcp_tools/      # filesystem, sandbox, GitHub, codebase (MCP)
  retrieval/      # tree-sitter symbol index
  sandbox/        # DockerRunner
  llm/            # LLMProvider (Claude default, OpenAI swap)
  tracing/        # structured JSON logs + cost + HTML/terminal viewer
```

## Configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude (default provider) |
| `OPENAI_API_KEY` | OpenAI swap path |
| `GITHUB_TOKEN` | Issue read / clone / draft PR / mark ready |
| `LLM_PROVIDER` | `claude` \| `openai` |
| `LLM_MODEL` | default `claude-sonnet-4-6` |
| `MAX_FILES_PER_PATCH` | safety cap (default 5) |
| `MAX_RETRIES` | sandbox failure retries after first attempt (default 3) |
| `SANDBOX_TIMEOUT_SECONDS` | container exec timeout |
| `RUN_TIMEOUT_SECONDS` | overall agent run timeout |

## Development

```bash
uv sync --all-extras
uv run ruff check .
uv run mypy src
uv run pytest
```

CI: GitHub Actions runs ruff, mypy, and pytest on every push/PR.

## License

MIT
