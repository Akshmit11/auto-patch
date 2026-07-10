# AGENTS.md — AutoPatch Agent Guidelines

This file is the first thing every coding agent must read at the start of a new session.

## Mandatory Session Preflight

At the start of **every new agent session**, before planning, answering project questions, or editing files:

1. Read `PRD.md` completely — it is the canonical product scope, feature list, tech stack, and decision record.
2. Read this `AGENTS.md` completely.
3. Inspect relevant existing code and configuration before changing anything.
4. Treat `PRD.md` as the source of truth. It overrides outdated plans, comments, or remembered context where they conflict.

Do not continue from remembered project context alone. Re-read current files because product decisions may have changed between sessions.

## What We Are Building

**AutoPatch** is an open-source autonomous coding agent that:

1. Ingests a GitHub issue
2. Clones the target repo and builds an AST-level symbol index (tree-sitter)
3. Retrieves relevant files/functions (keyword + symbol matching first)
4. Produces a structured plan via LLM (logged for explainability)
5. Generates a unified-diff patch (not full file rewrites)
6. Applies the patch and runs tests inside an isolated **Docker** sandbox
7. Self-verifies with capped retries when tests fail
8. Writes at least one new test covering the issue
9. Opens a **draft** PR (never auto-merges) with plan, summary, test results, and cost

**Target user:** maintainers of small-to-mid OSS repos and eng leads triaging well-scoped / "good first issue" tickets.

**Positioning:** lightweight, self-hostable, transparent alternative to closed coding agents — human always reviews.

## Non-Goals (v1)

- Large multi-file architectural rewrites
- Auto-merging PRs
- Fine-tuning models (orchestration + retrieval + tool use only)
- Supporting every language (v1: **Python first**; TypeScript is stretch)

## Hard Requirements

- **Hand-rolled agent loop** (`plan → act → observe → retry`) — do **not** wrap LangChain/LangGraph as the core. Optional thin adapter is stretch only.
- **MCP tools** for filesystem, sandbox exec, GitHub, and codebase/symbol lookup via official `mcp` Python SDK.
- **Swappable `LLMProvider`** — Claude (Sonnet) default; GPT-class models without touching agent logic.
- **Docker sandbox is non-negotiable** — never execute arbitrary target-repo code on the host.
- **Guardrails:** reject vague issues, cap files touched per patch, enforce timeouts.
- **Structured JSON logging** for every tool call, model call, and retry (trace viewer + eval harness).
- **Token/cost tracking** per run; surface in logs and PR description.
- **Draft PRs only** — human review gate; promote to ready-for-review explicitly.

## Tech Stack (use only this)

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Packaging | `pyproject.toml` + **uv** (preferred) |
| LLM | Anthropic Claude (Sonnet) via API; swappable OpenAI GPT via `LLMProvider` |
| Tool protocol | MCP — official `mcp` Python SDK |
| Code parsing | `tree-sitter` + `tree-sitter-python` (TS later) |
| Sandboxing | Docker + `docker` Python SDK |
| GitHub | PyGithub or raw REST + `httpx` |
| Orchestration | Hand-rolled plan → act → observe → retry loop |
| Agent tests | `pytest` |
| Logging | Structured JSON logs + minimal HTML/terminal trace viewer |
| Config | `.env` + `pydantic-settings` |
| Lint / types | `ruff`, `mypy` |
| CI | GitHub Actions (ruff, mypy, pytest) |
| Containers | `Dockerfile` (agent) + `docker-compose.yml` (agent + sandbox) |

**Do not introduce:** LangChain as core runtime, Next.js/React/frontend stacks, Clerk, Supabase, Drizzle, Tailwind, shadcn, Streamlit (unless stretch dashboard), vector DBs (unless stretch embedding retrieval), or unrelated cloud SDKs.

## Development Workflow

1. **Read PRD** — `PRD.md` first every new session.
2. **Explore** — read relevant code; respect nested `AGENTS.md` if any.
3. **Load skills** — load guides from `.agents/skills` before technology-specific work (especially MCP, planning, TDD, debugging).
4. **Retrieve current docs** — for MCP, Anthropic/OpenAI APIs, tree-sitter, Docker, PyGithub: use official docs / `llms.txt` / skills, not model memory alone.
5. **Implement existing patterns** — match repo style once code exists.
6. **Stop after implementation** — do not run tests, lint, builds, or Docker unless the user explicitly asks in the current request.

## First-Principles Building

- Prefer the smallest correct design. Code only what the PRD requires for the current milestone.
- If a module, dependency, abstraction, or file is not needed for a working feature, do not add it.
- Prefer unified diffs over full rewrites; keyword/symbol retrieval before embeddings; hand-rolled loop before frameworks.
- Treat complexity as a cost. Add structure only when it clearly improves correctness, safety, or required product capability.
- Scope by day plan in `PRD.md` (Day 1 core loop → Day 2 robustness/PR → Day 3 eval/polish). Do not silently expand into stretch goals.

## Testing and Verification

- Do **not** run tests, lint, type checks, builds, Docker, or eval harness automatically.
- The user will say when testing/verification is required.
- Do not add test frameworks beyond `pytest` (already the stack choice) or create verification artifacts unless requested.
- Static inspection of source is allowed and expected.

## Development Commands (reference only)

Run only when the user explicitly requests testing or verification:

```bash
# Setup
uv sync                          # install deps from pyproject.toml

# Quality
uv run ruff check .              # lint
uv run mypy src                  # type-check
uv run pytest                    # agent unit tests

# Agent
uv run autopatch run <issue-url>
uv run autopatch run <issue-url> --create-pr
uv run autopatch pr ready <pr-url>          # draft → ready-for-review (never merges)
uv run autopatch trace .autopatch/logs/run-<id>.jsonl --html
docker compose up --build
```

## Project Structure

Prefer this layout (from PRD — keep `PRD.md` §4 identical). Adjust only if a simpler split is clearly better — do not invent parallel apps or frontend trees.

```
.
├── README.md
├── ARCHITECTURE.md              # agent loop, MCP tools, retrieval
├── PRD.md                       # product source of truth
├── AGENTS.md                    # this file
├── pyproject.toml
├── Dockerfile                   # agent image
├── docker-compose.yml           # agent + sandbox
├── .env.example
├── .github/workflows/ci.yml
├── src/autopatch/
│   ├── __init__.py
│   ├── cli.py                   # entrypoint (thin): run, trace, pr, index, mcp
│   ├── config.py                # pydantic-settings
│   ├── agent/
│   │   ├── loop.py              # plan → act → observe → retry + draft PR
│   │   ├── planner.py
│   │   ├── patcher.py
│   │   ├── test_generator.py    # issue-covering test unified diff
│   │   ├── verifier.py
│   │   └── guardrails.py        # vague filter, file cap, retries, deadlines
│   ├── mcp_tools/               # MCP tool servers / wrappers
│   │   ├── github_tool.py       # issue read + draft PR + mark ready
│   │   ├── filesystem_tool.py
│   │   ├── sandbox_tool.py
│   │   └── codebase_tool.py     # tree-sitter powered
│   ├── retrieval/
│   │   └── symbol_index.py
│   ├── sandbox/
│   │   └── docker_runner.py
│   ├── llm/
│   │   └── provider.py          # LLMProvider interface + Claude/OpenAI
│   └── tracing/
│       ├── logger.py            # structured JSON + cost tracking
│       └── viewer.py            # terminal + HTML trace viewer
├── eval/
│   ├── issues/                  # real issue fixtures
│   ├── run_eval.py
│   └── results/
├── tests/                       # agent unit/integration tests
└── demo/
    ├── walkthrough.md           # steps for demo video
    ├── sample_issue.md          # local issue text
    └── sample_target/           # tiny buggy Python package for E2E
```

**Structure principles:**

- Keep orchestration in `agent/`; keep side effects behind MCP tools.
- `sandbox/docker_runner.py` owns container lifecycle; agent never shells out to host for target code.
- `llm/provider.py` is the only place provider SDKs are called.
- `tracing/` owns JSON logs, token counts, cost estimates, and the minimal HTML/terminal trace viewer.
- No frontend app in v1 (optional minimal static HTML trace viewer only).

## Core Pipeline (implementation order)

Build in this order unless the user directs otherwise:

1. Scaffold + config + logging stubs ✅
2. Docker sandbox can run a target repo test suite safely ✅
3. tree-sitter symbol index for Python ✅
4. MCP tools: filesystem, sandbox, GitHub read ✅
5. Plan → patch → test loop (no retry) ✅
6. Retry loop + test generation ✅ (Day 2)
7. Draft PR creation + cost in description ✅ (Day 2)
8. Guardrails + trace viewer ✅ (Day 2)
9. Eval harness + honest metrics (Day 3)

## Guardrails (always enforce when implementing agent behavior)

- Cap max files touched per patch (configurable N)
- Cap max retry attempts (default 3)
- Enforce sandbox and overall run timeouts
- Reject or ask for clarification on vague issues — do not guess large changes
- Draft PR only; never merge
- Never execute untrusted target-repo code outside Docker

## Naming and Code Style (Python)

- Modules/files: `snake_case.py`
- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Prefer type hints throughout; keep mypy clean once CI exists
- Prefer small pure functions; isolate I/O
- Early returns over deep nesting
- Structured logs over ad-hoc `print` for agent runtime paths
- No secrets in code — use `.env` / pydantic-settings

## Error Handling

- Fail closed on missing required env vars (GitHub token, LLM API key)
- Sandbox failures must surface stdout/stderr into the retry loop, not silent pass
- GitHub API errors: retry with backoff for transient failures; fail clearly for auth/permission
- Log every retry attempt with attempt number, failure reason, and cost delta

## Skills

Project skills live in `.agents/skills`. Load the relevant skill before specialized work.

### Tech stack skills (load when implementing that layer)

| Skill | Stack area | When to load |
|---|---|---|
| `claude-api` | Anthropic Claude / `LLMProvider` | Implementing or changing Claude API calls, tool use, streaming, token usage |
| `mcp-builder` | MCP tool protocol | Designing/implementing MCP tools (filesystem, sandbox, GitHub, codebase) |
| `python-project-structure` | Python packaging / `src/` layout | Scaffold, `pyproject.toml`, module boundaries |
| `python-code-style` | Python style / typing | Module APIs, naming, type hints |
| `python-testing-patterns` | pytest | Agent unit tests, fixtures, mocking Docker/GitHub |
| `docker-patterns` | Docker / Compose | Sandbox images, `docker-compose.yml`, volumes/networking |
| `multi-stage-dockerfile` | Dockerfile hardening | Agent/sandbox multi-stage images, slim runtime stages |
| `github-actions-templates` | CI | `.github/workflows/ci.yml` (ruff, mypy, pytest) |

### Workflow skills (load when executing multi-step agent work)

| Skill | When to load |
|---|---|
| `find-skills` | Discovering/installing additional skills |
| `writing-plans` | Before multi-step implementation |
| `executing-plans` | Incremental plan execution with checkpoints |
| `systematic-debugging` | Sandbox/test failures, flaky loops |
| `test-driven-development` | Tests alongside implementation |
| `verification-before-completion` | Before declaring a task done (when user asks for verification) |
| `requesting-code-review` / `receiving-code-review` | Pre-publish quality pass |
| `using-git-worktrees` | Isolated feature branches |
| `dispatching-parallel-agents` | Parallel independent workstreams |

### Gaps (no high-quality skill found — use official docs)

- **tree-sitter**: only low-install skills; retrieve official tree-sitter / tree-sitter-python docs
- **uv packaging**: only low-install skills; use [docs.astral.sh/uv](https://docs.astral.sh/uv)
- **OpenAI swap path**: no solid official skill; use OpenAI official API docs when implementing the second `LLMProvider`
- **PyGithub / GitHub REST**: no high-signal skill; use PyGithub or GitHub REST docs
- **pydantic-settings**: no dedicated skill; use pydantic-settings docs (do **not** install pydantic-ai agent skills — we hand-roll the loop)

## When Adding Features

1. Confirm the feature is in `PRD.md` core or differentiators (or user-approved stretch). Do not expand scope silently.
2. Load the relevant skill from `.agents/skills`.
3. Retrieve current official docs for MCP / LLM APIs / Docker / tree-sitter as needed.
4. Implement the smallest change that satisfies the requirement.
5. Do not run lint, build, tests, or Docker unless the user explicitly requests verification.

## Open Source Notes

- License: MIT or Apache-2.0 (document in README when shipping).
- README must state non-goals, honest eval numbers, architecture, and quickstart.
- Never commit secrets, tokens, or real `.env` files.
- Prefer reproducible local setup: `uv sync` + `docker compose` + sample issue path.
