"""Config / settings unit tests."""

from __future__ import annotations

import pytest

from autopatch.config import Settings


def test_defaults() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        anthropic_api_key=None,
        github_token=None,
    )
    assert s.llm_provider == "claude"
    assert s.llm_model == "claude-sonnet-4-6"
    assert s.max_files_per_patch == 5
    assert s.max_retries == 3


def test_require_for_run_fails_closed() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        anthropic_api_key=None,
        github_token=None,
    )
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        s.require_for_run(need_github=False, need_llm=True)


def test_require_for_run_github() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        anthropic_api_key="sk-test",
        github_token=None,
    )
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        s.require_for_run(need_github=True, need_llm=True)
