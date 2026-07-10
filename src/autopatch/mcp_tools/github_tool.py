"""GitHub MCP tools — issue read, clone, draft PR create, mark ready-for-review."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopatch.tracing.logger import StructuredLogger

_ISSUE_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)",
    re.IGNORECASE,
)
_REPO_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IssueRef:
    owner: str
    repo: str
    number: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/issues/{self.number}"


@dataclass(frozen=True)
class IssueData:
    ref: IssueRef
    title: str
    body: str
    labels: list[str]
    comments: list[str]
    state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.ref.owner,
            "repo": self.ref.repo,
            "number": self.ref.number,
            "url": self.ref.html_url,
            "title": self.title,
            "body": self.body,
            "labels": self.labels,
            "comments": self.comments,
            "state": self.state,
        }

    def as_prompt_text(self) -> str:
        parts = [
            f"Issue: {self.ref.html_url}",
            f"Title: {self.title}",
            f"State: {self.state}",
            f"Labels: {', '.join(self.labels) if self.labels else '(none)'}",
            "",
            "Body:",
            self.body or "(empty)",
        ]
        if self.comments:
            parts.append("")
            parts.append("Comments:")
            for idx, comment in enumerate(self.comments, start=1):
                parts.append(f"--- comment {idx} ---")
                parts.append(comment)
        return "\n".join(parts)


@dataclass(frozen=True)
class PullRequestResult:
    """Outcome of opening or updating a pull request (always draft by default)."""

    owner: str
    repo: str
    number: int
    html_url: str
    draft: bool
    title: str
    head: str
    base: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "number": self.number,
            "html_url": self.html_url,
            "draft": self.draft,
            "title": self.title,
            "head": self.head,
            "base": self.base,
        }


def parse_issue_url(url: str) -> IssueRef:
    """Parse a GitHub issue URL into owner/repo/number."""
    match = _ISSUE_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            f"Invalid GitHub issue URL: {url!r}. "
            "Expected https://github.com/<owner>/<repo>/issues/<n>"
        )
    return IssueRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=int(match.group("number")),
    )


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub pull request URL into owner, repo, number."""
    match = _PR_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            f"Invalid GitHub PR URL: {url!r}. "
            "Expected https://github.com/<owner>/<repo>/pull/<n>"
        )
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def parse_repo_url(url: str) -> tuple[str, str]:
    match = _REPO_URL_RE.match(url.strip())
    if not match:
        # Accept owner/repo shorthand
        if re.fullmatch(r"[^/\s]+/[^/\s]+", url.strip()):
            owner, repo = url.strip().split("/", 1)
            return owner, repo.removesuffix(".git")
        raise ValueError(f"Invalid GitHub repo URL or slug: {url!r}")
    return match.group("owner"), match.group("repo")


def build_pr_body(
    *,
    issue_url: str | None,
    plan_summary: str,
    plan_approach: list[str],
    files_touched: list[str],
    test_passed: bool | None,
    test_feedback: str | None,
    attempts: int,
    cost_usd: float,
    run_id: str,
    extra_notes: str = "",
) -> str:
    """Render the standard AutoPatch draft PR description (human review gate)."""
    approach = "\n".join(f"- {step}" for step in plan_approach) or "- (see plan log)"
    files = "\n".join(f"- `{p}`" for p in files_touched) or "- (none)"
    test_status = (
        "passed"
        if test_passed is True
        else "failed"
        if test_passed is False
        else "not run"
    )
    issue_line = f"Fixes {issue_url}" if issue_url else "Local / fixture issue (no GitHub link)"
    feedback_block = ""
    if test_feedback:
        clipped = test_feedback[-2500:]
        feedback_block = f"\n### Sandbox output (truncated)\n```\n{clipped}\n```\n"

    notes = f"\n{extra_notes.strip()}\n" if extra_notes.strip() else ""

    return f"""## AutoPatch draft PR

> **Human review required.** This PR was opened as a **draft** and will never auto-merge.

{issue_line}

### Summary
{plan_summary or "(no plan summary)"}

### Plan
{approach}

### Files touched
{files}

### Test results
- Status: **{test_status}**
- Attempts: {attempts}
{feedback_block}
### Cost & run
- Estimated LLM cost: **${cost_usd:.4f}**
- Run id: `{run_id}`
{notes}
---
*Generated by [AutoPatch](https://github.com) — promote to ready-for-review only after human review.*
"""


def _with_github_retries(
    fn: Callable[[], Any],
    *,
    logger: StructuredLogger | None,
    operation: str,
    max_attempts: int = 3,
) -> Any:
    """Retry transient GitHub/API failures with simple exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classify below
            last_exc = exc
            msg = str(exc).lower()
            # Auth: fail closed immediately for bad credentials / 401 on first attempt.
            # Other not-found / permission-like errors still get retries (eventual consistency).
            if ("bad credentials" in msg or "401" in msg) and attempt == 1:
                raise RuntimeError(f"GitHub auth failed for {operation}: {exc}") from exc
            if attempt >= max_attempts:
                break
            delay = 0.5 * (2 ** (attempt - 1))
            if logger:
                logger.log(
                    "github_retry",
                    message=f"{operation} attempt {attempt} failed: {exc}",
                    level="warning",
                    data={"attempt": attempt, "delay_s": delay, "operation": operation},
                )
            time.sleep(delay)
    assert last_exc is not None
    raise RuntimeError(f"GitHub {operation} failed after {max_attempts} attempts: {last_exc}") from last_exc


class GitHubTools:
    """GitHub operations for issue ingestion, clone, and draft PR lifecycle."""

    def __init__(self, token: str | None = None, logger: StructuredLogger | None = None) -> None:
        self.token = token
        self.logger = logger
        self._gh: Any | None = None

    def _client(self) -> Any:
        if self._gh is None:
            from github import Auth, Github

            if self.token:
                self._gh = Github(auth=Auth.Token(self.token))
            else:
                self._gh = Github()
        return self._gh

    def get_issue(self, issue_url: str) -> IssueData:
        ref = parse_issue_url(issue_url)

        def _fetch() -> IssueData:
            gh = self._client()
            repo = gh.get_repo(ref.full_name)
            issue = repo.get_issue(ref.number)
            comments = [c.body or "" for c in issue.get_comments()]
            labels = [label.name for label in issue.labels]
            return IssueData(
                ref=ref,
                title=issue.title or "",
                body=issue.body or "",
                labels=labels,
                comments=comments,
                state=issue.state,
            )

        data = _with_github_retries(_fetch, logger=self.logger, operation="get_issue")
        if self.logger:
            self.logger.log_tool_call(
                "github_get_issue",
                arguments={"url": issue_url},
                result_summary=f"{ref.full_name}#{ref.number}: {data.title[:80]}",
            )
        return data

    def clone_repo(
        self,
        owner: str,
        repo: str,
        dest: Path,
        *,
        depth: int = 1,
        ref: str | None = None,
    ) -> Path:
        """Shallow-clone a GitHub repository into ``dest`` using git CLI."""
        dest = dest.resolve()
        if dest.exists() and any(dest.iterdir()):
            if self.logger:
                self.logger.log_tool_call(
                    "github_clone_repo",
                    arguments={"repo": f"{owner}/{repo}", "dest": str(dest)},
                    result_summary="already exists",
                )
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.token:
            clone_url = f"https://x-access-token:{self.token}@github.com/{owner}/{repo}.git"
        else:
            clone_url = f"https://github.com/{owner}/{repo}.git"

        cmd = ["git", "clone", f"--depth={depth}"]
        if ref:
            cmd.extend(["--branch", ref])
        cmd.extend([clone_url, str(dest)])

        # Redact token from logs
        log_cmd = [
            c.replace(self.token, "***") if self.token and self.token in c else c for c in cmd
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as exc:
            if self.logger:
                self.logger.log_tool_call(
                    "github_clone_repo",
                    arguments={"repo": f"{owner}/{repo}", "cmd": log_cmd},
                    result_summary=exc.stderr[-500:] if exc.stderr else str(exc),
                    success=False,
                )
            raise RuntimeError(
                f"git clone failed for {owner}/{repo}: {exc.stderr or exc}"
            ) from exc

        if self.logger:
            self.logger.log_tool_call(
                "github_clone_repo",
                arguments={"repo": f"{owner}/{repo}", "dest": str(dest)},
                result_summary="cloned",
            )
        return dest

    def get_default_branch(self, owner: str, repo: str) -> str:
        def _fetch() -> str:
            gh = self._client()
            return str(gh.get_repo(f"{owner}/{repo}").default_branch)

        return str(_with_github_retries(_fetch, logger=self.logger, operation="get_default_branch"))

    def push_branch(
        self,
        workspace: Path,
        *,
        owner: str,
        repo: str,
        branch: str,
        commit_message: str,
    ) -> str:
        """Commit all changes in ``workspace`` and push ``branch`` to origin.

        Returns the branch name. Requires ``GITHUB_TOKEN`` with contents write.
        Never merges. Caller is responsible for creating a draft PR afterward.
        """
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN is required to push a branch")

        workspace = workspace.resolve()
        remote = f"https://x-access-token:{self.token}@github.com/{owner}/{repo}.git"

        def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                args,
                cwd=workspace,
                check=check,
                capture_output=True,
                text=True,
                timeout=120,
            )

        try:
            _run(["git", "config", "user.email", "autopatch@users.noreply.github.com"])
            _run(["git", "config", "user.name", "AutoPatch"])
            # Ensure we have a remote that can push (tokenized)
            remotes = _run(["git", "remote"], check=False)
            if "origin" in (remotes.stdout or "").split():
                _run(["git", "remote", "set-url", "origin", remote])
            else:
                _run(["git", "remote", "add", "origin", remote])

            _run(["git", "checkout", "-B", branch])
            _run(["git", "add", "-A"])
            status = _run(["git", "status", "--porcelain"], check=False)
            if status.stdout.strip():
                _run(["git", "commit", "-m", commit_message])
            else:
                # Empty commit still useful if patch already committed — allow no-op push tip
                _run(["git", "commit", "--allow-empty", "-m", commit_message])
            _run(["git", "push", "-u", "origin", branch, "--force-with-lease"])
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or exc.stdout or str(exc))[-800:]
            # Redact token if it leaked into stderr
            if self.token:
                err = err.replace(self.token, "***")
            if self.logger:
                self.logger.log_tool_call(
                    "github_push_branch",
                    arguments={"repo": f"{owner}/{repo}", "branch": branch},
                    result_summary=err,
                    success=False,
                )
            raise RuntimeError(f"git push failed for {owner}/{repo}@{branch}: {err}") from exc

        if self.logger:
            self.logger.log_tool_call(
                "github_push_branch",
                arguments={"repo": f"{owner}/{repo}", "branch": branch},
                result_summary="pushed",
            )
        return branch

    def create_draft_pr(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str | None = None,
        issue_number: int | None = None,
    ) -> PullRequestResult:
        """Open a **draft** pull request. Never merges and never opens as ready-by-default."""
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN is required to create a pull request")

        def _create() -> PullRequestResult:
            gh = self._client()
            repository = gh.get_repo(f"{owner}/{repo}")
            base_branch = base or repository.default_branch
            pr_body = body
            if issue_number is not None and f"#{issue_number}" not in body:
                pr_body = f"Fixes #{issue_number}\n\n{body}"
            pr = repository.create_pull(
                title=title,
                body=pr_body,
                head=head,
                base=base_branch,
                draft=True,
            )
            return PullRequestResult(
                owner=owner,
                repo=repo,
                number=int(pr.number),
                html_url=str(pr.html_url),
                draft=bool(getattr(pr, "draft", True)),
                title=str(pr.title),
                head=head,
                base=base_branch,
            )

        result = _with_github_retries(_create, logger=self.logger, operation="create_draft_pr")
        if self.logger:
            self.logger.log_tool_call(
                "github_create_draft_pr",
                arguments={
                    "repo": f"{owner}/{repo}",
                    "head": head,
                    "title": title[:120],
                    "draft": True,
                },
                result_summary=result.html_url,
            )
        # Hard invariant: AutoPatch only creates drafts
        if not result.draft:
            if self.logger:
                self.logger.log(
                    "pr_not_draft_warning",
                    message="API returned non-draft PR; converting to draft",
                    level="warning",
                    data=result.to_dict(),
                )
            try:
                self._convert_to_draft(owner, repo, result.number)
                result = PullRequestResult(
                    owner=result.owner,
                    repo=result.repo,
                    number=result.number,
                    html_url=result.html_url,
                    draft=True,
                    title=result.title,
                    head=result.head,
                    base=result.base,
                )
            except Exception as exc:  # noqa: BLE001
                if self.logger:
                    self.logger.log(
                        "pr_convert_draft_failed",
                        message=str(exc),
                        level="error",
                    )
        return result

    def mark_ready_for_review(self, pr_url: str) -> PullRequestResult:
        """Explicit human-gate promotion: draft → ready for review. Never merges."""
        owner, repo, number = parse_pr_url(pr_url)
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN is required to mark a PR ready for review")

        def _ready() -> PullRequestResult:
            gh = self._client()
            repository = gh.get_repo(f"{owner}/{repo}")
            pr = repository.get_pull(number)
            # PyGithub: mark_ready_for_review()
            if hasattr(pr, "mark_ready_for_review"):
                pr.mark_ready_for_review()
            else:
                # GraphQL fallback
                self._mark_ready_graphql(pr.node_id)
            pr = repository.get_pull(number)
            return PullRequestResult(
                owner=owner,
                repo=repo,
                number=int(pr.number),
                html_url=str(pr.html_url),
                draft=bool(getattr(pr, "draft", False)),
                title=str(pr.title),
                head=str(pr.head.ref),
                base=str(pr.base.ref),
            )

        result = _with_github_retries(_ready, logger=self.logger, operation="mark_ready_for_review")
        if self.logger:
            self.logger.log_tool_call(
                "github_mark_ready_for_review",
                arguments={"url": pr_url},
                result_summary=f"draft={result.draft} {result.html_url}",
            )
        return result

    def _convert_to_draft(self, owner: str, repo: str, number: int) -> None:
        gh = self._client()
        pr = gh.get_repo(f"{owner}/{repo}").get_pull(number)
        if hasattr(pr, "convert_to_draft"):
            pr.convert_to_draft()
            return
        # GraphQL mutation convertPullRequestToDraft
        node_id = pr.node_id
        mutation = """
        mutation($id: ID!) {
          convertPullRequestToDraft(input: {pullRequestId: $id}) {
            pullRequest { id isDraft }
          }
        }
        """
        gh.requester.graphql_query(mutation, {"id": node_id})

    def _mark_ready_graphql(self, node_id: str) -> None:
        gh = self._client()
        mutation = """
        mutation($id: ID!) {
          markPullRequestReadyForReview(input: {pullRequestId: $id}) {
            pullRequest { id isDraft }
          }
        }
        """
        gh.requester.graphql_query(mutation, {"id": node_id})


def create_github_mcp_server(token: str | None = None, logger: StructuredLogger | None = None) -> Any:
    """Build a FastMCP server for GitHub read + draft PR operations."""
    import json

    from mcp.server.fastmcp import FastMCP

    tools = GitHubTools(token=token, logger=logger)
    mcp = FastMCP("autopatch_github_mcp")

    @mcp.tool(name="github_get_issue")
    def github_get_issue(issue_url: str) -> str:
        """Fetch a GitHub issue title, body, labels, and comments by URL."""
        data = tools.get_issue(issue_url)
        return json.dumps(data.to_dict(), indent=2)

    @mcp.tool(name="github_create_draft_pr")
    def github_create_draft_pr(
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "",
        issue_number: int = 0,
    ) -> str:
        """Open a draft pull request (never merges). Leave base empty for default branch."""
        result = tools.create_draft_pr(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=head,
            base=base or None,
            issue_number=issue_number or None,
        )
        return json.dumps(result.to_dict(), indent=2)

    @mcp.tool(name="github_mark_ready_for_review")
    def github_mark_ready_for_review(pr_url: str) -> str:
        """Promote a draft PR to ready-for-review (explicit human gate; never merges)."""
        result = tools.mark_ready_for_review(pr_url)
        return json.dumps(result.to_dict(), indent=2)

    return mcp
