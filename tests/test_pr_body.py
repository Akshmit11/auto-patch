"""Draft PR description + URL parsing tests (no network)."""

from __future__ import annotations

import pytest

from autopatch.mcp_tools.github_tool import build_pr_body, parse_pr_url


def test_build_pr_body_contains_cost_and_draft_gate() -> None:
    body = build_pr_body(
        issue_url="https://github.com/octocat/Hello-World/issues/1",
        plan_summary="Fix clamp lower bound",
        plan_approach=["Update clamp", "Add regression test"],
        files_touched=["mathutil.py", "tests/test_mathutil.py"],
        test_passed=True,
        test_feedback="exit_code=0",
        attempts=2,
        cost_usd=0.1234,
        run_id="abc123",
    )
    assert "draft" in body.lower()
    assert "never auto-merge" in body.lower() or "Human review required" in body
    assert "$0.1234" in body
    assert "abc123" in body
    assert "mathutil.py" in body
    assert "Fixes https://github.com/octocat/Hello-World/issues/1" in body
    assert "Attempts: 2" in body


def test_parse_pr_url() -> None:
    owner, repo, number = parse_pr_url("https://github.com/octocat/Hello-World/pull/99")
    assert owner == "octocat"
    assert repo == "Hello-World"
    assert number == 99


def test_parse_pr_url_invalid() -> None:
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/octocat/Hello-World/issues/1")
