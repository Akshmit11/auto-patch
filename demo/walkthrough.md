# AutoPatch Demo Walkthrough (Day 1–3)

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

### Day 3 — Eval, polish, ship
1. **Eval set**: 15+ real closed GitHub issues + local smoke targets with golden diffs
2. **Harness** (`eval/run_eval.py` / `autopatch eval`): resolve rate, cost, time, edit distance
3. **README** with architecture diagram, eval table, failure modes, non-goals
4. **Honest metrics** pipeline — inventory baseline checked in; live numbers from real runs

## Demo target

`demo/sample_target` is a tiny Python package with a deliberate bug in `clamp()`.

Local eval targets (isolated packages) live under `eval/targets/` (`clamp_bug`, `even_bug`, `percent_bug`, `reverse_bug`).

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

# Eval harness
uv run autopatch eval --list
uv run python eval/run_eval.py --local-only
uv run python eval/run_eval.py --only local_clamp --skip-sandbox
uv run python eval/run_eval.py --dry-run
```

## Suggested 2-minute demo video script

1. **0:00–0:20** — Problem: backlog of small issues; show README one-liner + architecture Mermaid.
2. **0:20–0:45** — `uv run autopatch index demo/sample_target` (symbols appear).
3. **0:45–1:30** — `autopatch run --repo demo/sample_target ... --html-trace` (plan → patch → Docker tests).
4. **1:30–1:50** — Open HTML trace + result JSON (cost, attempts).
5. **1:50–2:00** — `autopatch eval --list` + point at `eval/results/report.md` (honest metrics).

## MCP servers (stdio)

```bash
uv run autopatch mcp filesystem --workspace demo/sample_target
uv run autopatch mcp codebase --workspace demo/sample_target
uv run autopatch mcp github
uv run autopatch mcp sandbox --workspace /path/to/repo
```
