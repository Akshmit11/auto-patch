"""GitHub MCP tools — issue read (Day 1) and clone helpers."""

from __future__ import annotations

import re
import subprocess
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


def parse_repo_url(url: str) -> tuple[str, str]:
    match = _REPO_URL_RE.match(url.strip())
    if not match:
        # Accept owner/repo shorthand
        if re.fullmatch(r"[^/\s]+/[^/\s]+", url.strip()):
            owner, repo = url.strip().split("/", 1)
            return owner, repo.removesuffix(".git")
        raise ValueError(f"Invalid GitHub repo URL or slug: {url!r}")
    return match.group("owner"), match.group("repo")


class GitHubTools:
    """GitHub operations for issue ingestion and shallow clone."""

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
        gh = self._client()
        repo = gh.get_repo(ref.full_name)
        issue = repo.get_issue(ref.number)
        comments = [c.body or "" for c in issue.get_comments()]
        labels = [label.name for label in issue.labels]
        data = IssueData(
            ref=ref,
            title=issue.title or "",
            body=issue.body or "",
            labels=labels,
            comments=comments,
            state=issue.state,
        )
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


def create_github_mcp_server(token: str | None = None, logger: StructuredLogger | None = None) -> Any:
    """Build a FastMCP server for GitHub read operations."""
    import json

    from mcp.server.fastmcp import FastMCP

    tools = GitHubTools(token=token, logger=logger)
    mcp = FastMCP("autopatch_github_mcp")

    @mcp.tool(name="github_get_issue")
    def github_get_issue(issue_url: str) -> str:
        """Fetch a GitHub issue title, body, labels, and comments by URL."""
        data = tools.get_issue(issue_url)
        return json.dumps(data.to_dict(), indent=2)

    return mcp
