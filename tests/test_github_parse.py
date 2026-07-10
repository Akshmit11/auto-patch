"""GitHub URL parsing unit tests (no network)."""

from __future__ import annotations

import pytest

from autopatch.mcp_tools.github_tool import parse_issue_url, parse_repo_url


def test_parse_issue_url() -> None:
    ref = parse_issue_url("https://github.com/octocat/Hello-World/issues/42")
    assert ref.owner == "octocat"
    assert ref.repo == "Hello-World"
    assert ref.number == 42


def test_parse_issue_url_invalid() -> None:
    with pytest.raises(ValueError):
        parse_issue_url("https://example.com/not-github")


def test_parse_repo_url() -> None:
    owner, repo = parse_repo_url("https://github.com/octocat/Hello-World")
    assert owner == "octocat"
    assert repo == "Hello-World"


def test_parse_repo_slug() -> None:
    owner, repo = parse_repo_url("octocat/Hello-World")
    assert owner == "octocat"
    assert repo == "Hello-World"
