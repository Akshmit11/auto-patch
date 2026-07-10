# Project 1: AutoPatch — GitHub Issue-to-PR Coding Agent

**Target roles:** AI Agent Developer, LLM Engineer, Applied AI Engineer (dev-tools companies)
**Timeline:** 3 days (10 hrs/day = ~30 hrs)
**Positioning line for resume:** *"Built an autonomous coding agent that resolves GitHub issues end-to-end — parses codebase context with tree-sitter, generates a patch + tests in a sandboxed Docker environment, and opens a human-reviewed PR. Achieves X% resolve rate on a self-built eval set of Y real-world issues."*

---

## 1. PRD (Product Requirements Document)

### 1.1 Problem Statement
Engineering teams accumulate a backlog of small-to-medium bugs and well-scoped feature requests faster than they can triage and fix them. Existing "AI coding agent" demos (Devin, SWE-agent, etc.) are closed-source or hard to inspect. There's no lightweight, self-hostable, transparent version an engineer can point at their own repo, watch reason step-by-step, and trust enough to review a PR from.

### 1.2 Goal
Given a GitHub issue on a real repository, the agent should:
1. Understand the relevant parts of the codebase (not the whole repo — targeted retrieval).
2. Propose and implement a fix.
3. Write/update tests that prove the fix works.
4. Run the tests in an isolated sandbox to self-verify before proposing anything.
5. Open a draft PR with a clear description of what changed and why, and **never merge automatically** — a human always reviews.

### 1.3 Non-Goals (write these explicitly in your README — signals maturity)
- Not attempting large, multi-file architectural changes (v1 scope: single-issue, localized fixes).
- Not auto-merging — this is a supervised agent, not autonomous-merge.
- Not fine-tuning any model — orchestration + retrieval + tool use only.
- Not supporting every language in v1 — **Python first**; TypeScript is stretch (not required for Day 1–2).

### 1.4 Target User
A maintainer of a small-to-mid open source repo, or an eng team lead who wants to triage a backlog of "good first issue"-tagged tickets automatically.

### 1.5 Success Metrics (this is what makes it resume-worthy, not a toy)
- **Resolve rate**: % of issues in your eval set where the agent produces a patch that passes existing + new tests. Target: report an honest number (even 40-60% is fine — document it).
- **Time to patch**: median wall-clock time from issue ingestion to PR draft.
- **Test coverage delta**: does the patch add meaningful test coverage, not just pass a trivial assertion.
- **Human edit distance**: how much a reviewer had to change before merging (measure this yourself on a sample — huge credibility signal).
- **Cost per resolved issue**: token cost tracked and reported. Hiring managers care about this a lot in 2026 — cost-awareness is a differentiator.

### 1.6 Why this project (interview talking points to build in from day 1)
- Multi-step tool use with real failure recovery (retry logic, backoff).
- Codebase understanding at the AST level (tree-sitter), not naive text search — shows you understand retrieval isn't just embeddings.
- Sandboxed, safe code execution (Docker) — shows production/security awareness.
- Human-in-the-loop design — shows judgment about where autonomy should stop.
- Self-built eval set — the single highest-signal artifact per the research (see below).

---

## 2. Full Feature List

### Core (must-have — build these in order)
1. **Issue ingestion** — pull issue title/body/comments via GitHub API (PyGithub or raw REST).
2. **Repo cloning & indexing** — shallow clone target repo into a working dir.
3. **Codebase understanding via tree-sitter** — parse the repo into an AST-level symbol map (functions, classes, imports) so the agent can navigate by symbol, not just grep.
4. **Context retrieval** — given the issue text, retrieve the most relevant files/functions (start simple: keyword + symbol-name matching; you can add embedding-based retrieval as a stretch goal).
5. **Planning step** — LLM call that outputs a structured plan (files to touch, expected changes) *before* writing any code. Log this — it's your "explainability" artifact.
6. **Patch generation** — LLM generates a diff/patch, not a full file rewrite (cleaner, more reviewable, closer to how real devs work).
7. **Sandboxed execution** — apply the patch inside a Docker container, run the existing test suite + any new tests.
8. **Self-verification loop** — if tests fail, feed the failure back to the model, allow up to N retries (this is your "error recovery" signal — cap it and log every attempt).
9. **Test generation** — agent writes at least one new test that specifically covers the issue being fixed.
10. **PR creation** — open a draft PR via GitHub API with: linked issue, summary of the change, the plan from step 5, test results, and a diff.
11. **Human review gate** — PR is opened as **draft**, never auto-merged. CLI/dashboard flag to explicitly promote to "ready for review."

### Differentiators (do these — they're what separate this from every tutorial clone on GitHub)
12. **Cost & token tracking** — log tokens + $ cost per run, surface it in the PR description.
13. **Structured logging / trace viewer** — every tool call, retrieval, and model call logged as structured JSON; build a minimal HTML or terminal trace viewer so you can *show* the agent's reasoning in your demo video.
14. **Eval harness (Project 3 overlaps here, but stub it now)** — a folder of 15-20 real closed issues from real open source repos (pick ones with a merged PR you can compare against) with a script that runs the agent against each and scores resolve rate automatically.
15. **Guardrails** — reject issues that are too vague (ask for clarification instead of guessing), reject if diff touches more than N files (safety valve against runaway edits), timeout limits.
16. **MCP-based tool architecture** — expose your tools (file read/write, run tests, git operations, GitHub API) as MCP tools rather than ad hoc function-calling. This is the single most-mentioned 2026 hiring signal in the research — MCP is now the expected pattern, not a nice-to-have.

### Stretch (only if time remains on day 3)
17. Multi-language support (Python + TypeScript).
18. Simple web dashboard (issue queue, run status, trace viewer) — even a basic Streamlit app adds a lot of demo polish.
19. Embedding-based retrieval (swap in for keyword matching) using a lightweight vector store.

---

## 3. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Baseline expectation for AI roles |
| LLM | Claude (Sonnet) via Anthropic API, with an easy swap for GPT-4.1/o-series | Show model-agnostic design — a `LLMProvider` interface, not hardcoded calls |
| Tool protocol | **MCP (Model Context Protocol)** | Industry-standard as of 2026 per hiring research; use official `mcp` Python SDK |
| Code parsing | `tree-sitter` + `tree-sitter-python` / `tree-sitter-typescript` | AST-level code understanding, not regex/grep |
| Sandboxing | Docker (via `docker` Python SDK) | Safe, isolated execution — non-negotiable for a code-executing agent |
| GitHub integration | `PyGithub` or raw REST + `httpx` | Issue/PR/comment management |
| Orchestration | Hand-rolled agent loop (plan → act → observe → retry) — **do not just wrap LangChain** | Research explicitly flags "LangChain on resume, nothing else" as a red flag; build the loop yourself to prove you understand it, optionally show a thin LangGraph adapter as a stretch |
| Testing | `pytest` for your own agent's tests; sandbox runs the target repo's own test suite | |
| Logging/tracing | Structured JSON logs + a minimal trace viewer (plain HTML+JS is fine) | |
| Config/secrets | `.env` + `pydantic-settings` | |
| Packaging | `pyproject.toml` + **uv** (preferred) | Modern tooling signal |
| Lint / types | `ruff`, `mypy` | CI quality gates |
| CI | GitHub Actions — lint, type-check (mypy), run agent's own unit tests | Explicitly called out in research: no CI/CD = credibility loss |
| Containerization | `Dockerfile` for the agent itself too, not just the sandbox | Deployment-readiness signal |

**Canonical stack table also lives in `AGENTS.md`.** Prefer `AGENTS.md` + this section staying identical when either changes.

---

## 4. Repo Structure

Canonical layout (kept in sync with `AGENTS.md`). Paths are relative to the repository root.

```
.
├── README.md
├── ARCHITECTURE.md              # agent loop, MCP tools, retrieval
├── PRD.md                       # this file — product source of truth
├── AGENTS.md                    # agent/session guidelines
├── pyproject.toml
├── Dockerfile                   # agent image
├── docker-compose.yml           # agent + sandbox
├── .env.example
├── .github/workflows/ci.yml
├── src/autopatch/
│   ├── __init__.py
│   ├── cli.py                   # entrypoint (thin)
│   ├── config.py                # pydantic-settings
│   ├── agent/
│   │   ├── loop.py              # plan → act → observe → retry
│   │   ├── planner.py
│   │   ├── patcher.py
│   │   └── verifier.py
│   ├── mcp_tools/               # MCP tool servers / wrappers
│   │   ├── github_tool.py
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
│       └── logger.py            # structured JSON + cost tracking
├── eval/
│   ├── issues/                  # real issue fixtures
│   ├── run_eval.py
│   └── results/
├── tests/                       # agent unit/integration tests
└── demo/
    ├── walkthrough.md           # steps for demo video
    ├── sample_issue.md          # Day-1 local issue text
    └── sample_target/           # tiny buggy Python package for Day-1 E2E
```

**Structure principles:**

- Keep orchestration in `agent/`; keep side effects behind MCP tools.
- `sandbox/docker_runner.py` owns container lifecycle; agent never shells out to host for target code.
- `llm/provider.py` is the only place provider SDKs are called.
- `tracing/` owns JSON logs, token counts, and cost estimates.
- No frontend app in v1 (optional minimal static HTML trace viewer only).

---

## 5. Day-by-Day Plan (3 days x 10 hrs)

**Day 1 — Core loop + tools** ✅ (implemented)
- Repo scaffold, Docker sandbox working (can run arbitrary repo's test suite safely).
- tree-sitter symbol indexing for one target repo.
- MCP tool wrappers: filesystem, sandbox exec, GitHub read (+ codebase).
- Basic plan → patch → test loop (no retry yet), demo target at `demo/sample_target`.

**Day 2 — Robustness + PR flow**
- Add retry/error-recovery loop with capped attempts.
- Add test generation step.
- GitHub PR creation (draft PRs, proper description template).
- Structured logging + trace viewer.
- Guardrails (file-count limits, timeouts, clarification-needed detection).

**Day 3 — Eval, polish, ship**
- Build eval set: 15-20 real closed issues from small OSS repos where you can diff against the real merged fix.
- Run eval, record resolve rate, cost, time honestly (imperfect numbers are fine and expected).
- Write README with architecture diagram, demo GIF, eval table, "what I'd do with more time" section.
- Record 2-minute demo video.
- CI pipeline green, Docker build working, push to GitHub public.

---

## 6. Prompt to Give Your AI Coding Agent (Claude Code / Cursor / etc.)

Copy-paste this as your kickoff prompt:

```
I'm building "AutoPatch" — an autonomous coding agent that resolves GitHub issues
end-to-end and opens human-reviewed PRs. This is a portfolio project for AI Agent
Developer / LLM Engineer job applications, so code quality, architecture clarity,
and production-readiness matter as much as functionality.

GOAL
Given a GitHub issue URL for a target repo, the agent should:
1. Clone the repo and build an AST-level symbol index using tree-sitter
2. Retrieve the files/functions most relevant to the issue
3. Produce a structured plan (files to touch, approach) via an LLM call, and log it
4. Generate a patch (unified diff, not full file rewrites)
5. Apply the patch inside an isolated Docker sandbox and run the existing test suite
6. If tests fail, feed the failure back to the model and retry (max 3 attempts, log every attempt)
7. Write at least one new test covering the issue
8. Open a DRAFT pull request via the GitHub API with: linked issue, the plan, a
   summary of changes, and test results. Never auto-merge.

HARD REQUIREMENTS
- Python 3.11+, use `uv` or `poetry` for packaging
- Expose all tools (filesystem read/write, docker exec, github operations,
  codebase symbol lookup) as MCP tools using the official `mcp` Python SDK —
  do NOT just wrap everything in LangChain. I want to understand and be able to
  explain every part of the agent loop myself, so hand-roll the plan->act->observe
  ->retry orchestration loop rather than using a black-box agent framework.
- LLM calls must go through a swappable `LLMProvider` interface so I can switch
  between Claude and GPT-4.1 without touching agent logic.
- Docker sandboxing is non-negotiable — the agent must never execute arbitrary
  code from a target repo outside a container.
- Add guardrails: reject/flag issues that are too vague, cap the number of files
  a single patch can touch, enforce execution timeouts.
- Structured JSON logging for every tool call, model call, and retry — I need
  this for a trace viewer and for an eval harness later.
- Include a GitHub Actions CI workflow: lint (ruff), type-check (mypy), and run
  the agent's own test suite on every push.
- Write a Dockerfile for the agent itself, plus docker-compose for agent+sandbox.
- Track and log token usage and estimated cost per run.

START WITH
Set up the repo structure below, then implement Day 1 scope only (core loop +
Docker sandbox + tree-sitter indexing + MCP tool wrappers for filesystem/sandbox/
github, working end-to-end on one hand-picked simple issue). Show me the plan
before writing code, and check in with me after each major component instead of
building everything silently.

REPO STRUCTURE
[paste the structure from section 4 above]

Ask me clarifying questions about the target repo I want to test against before
you start, if you need them. Otherwise proceed.
```

Then, before Day 3, give it a second prompt for the eval harness:

```
Now build the eval harness. I need eval/run_eval.py to:
- Load a set of 15-20 (issue_url, expected_diff_or_test) pairs from eval/issues/
- Run the full agent pipeline against each issue
- Score: did tests pass, how many retry attempts needed, token cost, wall-clock
  time, and (where I've manually annotated it) edit distance vs the real merged fix
- Output results/results.json and a human-readable results/report.md table
Be honest in the scoring — I want real numbers for my README, not inflated ones.
```

---

## 7. Skills to Install from skills.sh

I checked the current leaderboard at skills.sh. Most of the top skills (frontend-design, Vercel/React patterns, Azure, marketing skills, Firebase) are irrelevant to a backend agent project — skip those. Install these instead, since they map directly to what this build needs:

### Install now (directly relevant)
| Skill | Repo | Why it helps this project |
|---|---|---|
| `writing-plans` | `obra/superpowers` | Forces the agent to write a structured plan before coding — matches your "planning step" feature exactly, and is good practice for how *you* should scope Day 1-3 too. |
| `executing-plans` | `obra/superpowers` | Keeps a coding agent executing a plan incrementally with checkpoints, instead of going off and rewriting everything — reduces the "silent big-bang PR" failure mode. |
| `systematic-debugging` | `obra/superpowers` | Directly useful when your Docker sandbox tests fail and the coding agent needs a disciplined debug loop rather than guessing — this *is* your retry/self-verification logic pattern. |
| `test-driven-development` / `tdd` | `obra/superpowers` / `mattpocock/skills` | You want tests written alongside the patch, not after — this skill nudges your coding assistant toward TDD discipline, which also strengthens your "test generation" feature. |
| `verification-before-completion` | `obra/superpowers` | Stops the coding agent from declaring a task "done" without running tests/checks first — exactly the self-verification behavior you want AutoPatch itself to have, and useful discipline for the agent building AutoPatch. |
| `requesting-code-review` / `receiving-code-review` | `obra/superpowers` | Useful once you have a working agent and want a second pass on code quality before you publish it publicly. |
| `using-git-worktrees` | `obra/superpowers` | Handy if you want your coding agent to work on isolated branches per feature without messing up your main working tree during the 3-day sprint. |
| `mcp-builder` | `anthropics/skills` | You're building MCP tools as a core architecture requirement — this is the official Anthropic skill for scaffolding MCP servers/tools correctly. Install this one for sure. |
| `dispatching-parallel-agents` | `obra/superpowers` | If you want to parallelize work (e.g., building the tree-sitter indexer and the Docker sandbox simultaneously with sub-agents) to save time in your 3-day window. |

### Skip (not relevant to this project)
- Anything frontend/design (`frontend-design`, `web-design-guidelines`, `shadcn`, Vercel React skills, `taste-skill` family) — this is a backend/CLI project, no UI needed beyond a minimal trace viewer.
- Marketing skills (`coreyhaines31/marketingskills` family) — irrelevant.
- Cloud-provider skills (`azure-skills`, `firebase`) — you're using Docker locally/CI, not deploying to a managed cloud platform for this project.
- `to-prd` / `to-issues` (`mattpocock/skills`) — nice-to-have for planning but you already have this PRD; skip unless you want to self-generate future project PRDs.

### Install command
```bash
npx skills add obra/superpowers/writing-plans
npx skills add obra/superpowers/executing-plans
npx skills add obra/superpowers/systematic-debugging
npx skills add obra/superpowers/test-driven-development
npx skills add obra/superpowers/verification-before-completion
npx skills add anthropics/skills/mcp-builder
```
(Add `requesting-code-review`, `receiving-code-review`, `using-git-worktrees`, `dispatching-parallel-agents` the same way if you want them.)

---

## 8. README Checklist (what makes a recruiter stop scrolling)
- [ ] One-line problem statement at the top
- [ ] Architecture diagram (even a simple Mermaid diagram)
- [ ] Demo GIF or 2-min video link
- [ ] Eval results table (resolve rate, cost, time — honest numbers)
- [ ] "Where it failed and what I learned" section
- [ ] Quickstart: clone, `docker-compose up`, run against a sample issue in <5 min
- [ ] Explicit non-goals / scope boundaries
- [ ] License (MIT/Apache-2.0 for open source)
