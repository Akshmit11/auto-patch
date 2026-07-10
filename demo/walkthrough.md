# AutoPatch Day-1 Demo Walkthrough

## What Day 1 delivers

1. Repo scaffold (`src/autopatch`, config, structured logging, CI stubs)
2. Docker sandbox that runs a target repo’s tests safely
3. tree-sitter Python symbol index + keyword/symbol retrieval
4. MCP tool wrappers: filesystem, sandbox, GitHub read, codebase
5. Hand-rolled **plan → patch → test** loop (no retry / no draft PR yet)

## Demo target

`demo/sample_target` is a tiny Python package with a deliberate bug in `clamp()`.

## Commands

```bash
# Install
uv sync --all-extras

# Index only (no LLM / Docker)
uv run autopatch index demo/sample_target

# Full Day-1 loop against the local sample (needs ANTHROPIC_API_KEY + Docker)
cp .env.example .env   # fill ANTHROPIC_API_KEY
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md

# Plan+patch only (no Docker)
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md \
  --skip-sandbox

# Against a real GitHub issue (needs GITHUB_TOKEN + ANTHROPIC_API_KEY + Docker)
uv run autopatch run https://github.com/owner/repo/issues/123
```

## MCP servers (stdio)

```bash
uv run autopatch mcp filesystem --workspace demo/sample_target
uv run autopatch mcp codebase --workspace demo/sample_target
uv run autopatch mcp github
uv run autopatch mcp sandbox --workspace /path/to/repo
```

## Day 2 next

- Capped retry loop on test failure
- Test generation step
- Draft PR creation with plan + cost
- Guardrails polish + minimal trace viewer
