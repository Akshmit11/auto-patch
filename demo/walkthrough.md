# AutoPatch Demo Walkthrough (Day 1–2)

## What is implemented

### Day 1 — Core loop + tools
1. Repo scaffold (`src/autopatch`, config, structured logging, CI)
2. Docker sandbox that runs a target repo’s tests safely
3. tree-sitter Python symbol index + keyword/symbol retrieval
4. MCP tool wrappers: filesystem, sandbox, GitHub read, codebase
5. Hand-rolled **plan → patch → test** loop

### Day 2 — Robustness + PR flow
1. Capped **retry** loop with failure feedback + cost delta logging
2. **Test generation** step (ensures at least one issue-covering test when possible)
3. **Draft PR** creation (`--create-pr`) with plan, tests, cost — never merges
4. **Guardrails**: vague issues, max files, sandbox + run timeouts
5. **Trace viewer**: terminal + HTML (`autopatch trace`)

## Demo target

`demo/sample_target` is a tiny Python package with a deliberate bug in `clamp()`.

## Commands

```bash
# Install
uv sync --all-extras
cp .env.example .env   # fill ANTHROPIC_API_KEY (and GITHUB_TOKEN for real issues/PRs)

# Index only (no LLM / Docker)
uv run autopatch index demo/sample_target

# Full loop against the local sample (needs ANTHROPIC_API_KEY + Docker)
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md \
  --html-trace

# Plan+patch only (no Docker)
uv run autopatch run \
  --repo demo/sample_target \
  --title "Fix clamp() lower bound" \
  --issue-file demo/sample_issue.md \
  --skip-sandbox

# Real GitHub issue + draft PR (needs GITHUB_TOKEN with repo write)
uv run autopatch run https://github.com/owner/repo/issues/123 --create-pr

# Promote draft → ready for review (human gate; never merges)
uv run autopatch pr ready https://github.com/owner/repo/pull/456

# View structured trace
uv run autopatch trace .autopatch/logs/run-<id>.jsonl
uv run autopatch trace .autopatch/logs/run-<id>.jsonl --html
```

## MCP servers (stdio)

```bash
uv run autopatch mcp filesystem --workspace demo/sample_target
uv run autopatch mcp codebase --workspace demo/sample_target
uv run autopatch mcp github
uv run autopatch mcp sandbox --workspace /path/to/repo
```

## Day 3 next

- Eval harness (15–20 real closed issues)
- Honest metrics (resolve rate, cost, time)
- README polish + demo video
