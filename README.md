# AutoPatch

**Autonomous coding agent that turns a GitHub issue into a sandboxed, human-reviewed patch.**

AutoPatch ingests an issue, indexes the target repo with tree-sitter, plans a fix, generates a unified diff, and verifies tests inside Docker. Draft PRs (Day 2) are never auto-merged — a human always reviews.

> **Status:** Day 1 complete — core loop + tools. Retry, draft PR, eval harness, and polish land on Days 2–3.

## Non-goals (v1)

- Large multi-file architectural rewrites
- Auto-merging PRs
- Fine-tuning models (orchestration + retrieval + tool use only)
- Supporting every language (Python first; TypeScript is stretch)

## Architecture (Day 1)

```text
Issue URL / local issue
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
  Patcher (LLM) ── unified diff only
        │
        ▼
  Docker sandbox ── apply patch + pytest
        │
        ▼
  Result JSON + structured JSONL trace + cost
```

Hand-rolled **plan → act → observe** loop (no LangChain core). Tools are exposed as MCP servers (filesystem, sandbox, GitHub, codebase) and used in-process by the agent.

## Quickstart

```bash
# Prerequisites: Python 3.11+, uv, Docker (for sandbox verify)
uv sync --all-extras
cp .env.example .env   # set ANTHROPIC_API_KEY (and GITHUB_TOKEN for real issues)

# Symbol index only
uv run autopatch index demo/sample_target

# Day-1 loop on the included buggy sample (needs Docker + API key)
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md

# Plan + patch without Docker
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md \
  --skip-sandbox

# Real GitHub issue
uv run autopatch run https://github.com/owner/repo/issues/123
```

See [demo/walkthrough.md](demo/walkthrough.md) for a fuller walkthrough.

## Project layout

Matches `AGENTS.md` / `PRD.md` §4:

```text
src/autopatch/
  agent/          # plan → act → observe loop
  mcp_tools/      # filesystem, sandbox, GitHub, codebase (MCP)
  retrieval/      # tree-sitter symbol index
  sandbox/        # DockerRunner
  llm/            # LLMProvider (Claude default, OpenAI swap)
  tracing/        # structured JSON logs + cost
```

## Configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude (default provider) |
| `OPENAI_API_KEY` | OpenAI swap path |
| `GITHUB_TOKEN` | Issue read / clone / (Day 2) draft PR |
| `LLM_PROVIDER` | `claude` \| `openai` |
| `LLM_MODEL` | default `claude-sonnet-4-6` |
| `MAX_FILES_PER_PATCH` | safety cap (default 5) |
| `SANDBOX_TIMEOUT_SECONDS` | container exec timeout |

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
