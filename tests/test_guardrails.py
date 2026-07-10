"""Guardrail unit tests (no LLM / Docker)."""

from __future__ import annotations

import time

import pytest

from autopatch.agent.guardrails import (
    GuardrailError,
    RunDeadline,
    enforce_file_cap,
    enforce_retry_budget,
    is_test_path,
    issue_looks_vague,
    patch_includes_tests,
)
from autopatch.agent.planner import Plan


def test_issue_looks_vague_short_body() -> None:
    reason = issue_looks_vague(title="Fix", body="pls")
    assert reason is not None


def test_issue_looks_clear_enough() -> None:
    reason = issue_looks_vague(
        title="clamp() returns wrong value for low bound",
        body=(
            "When value is below low, clamp should return low. "
            "Currently it returns high. Expected: clamp(-1, 0, 10) == 0."
        ),
    )
    assert reason is None


def test_is_test_path() -> None:
    assert is_test_path("tests/test_mathutil.py")
    assert is_test_path("sample_target/test_foo.py")
    assert is_test_path("pkg/foo_test.py")
    assert not is_test_path("sample_target/mathutil.py")


def test_patch_includes_tests() -> None:
    assert patch_includes_tests(["src/a.py", "tests/test_a.py"])
    assert not patch_includes_tests(["src/a.py", "src/b.py"])


def test_enforce_file_cap_ok() -> None:
    diff = """\
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-x
+y
"""
    files = enforce_file_cap(diff, max_files=2)
    assert files == ["a.py"]


def test_enforce_file_cap_rejects() -> None:
    diff = """\
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-x
+y
--- a/b.py
+++ b/b.py
@@ -1 +1 @@
-x
+y
--- a/c.py
+++ b/c.py
@@ -1 +1 @@
-x
+y
"""
    with pytest.raises(GuardrailError) as exc:
        enforce_file_cap(diff, max_files=2)
    assert exc.value.code == "max_files"


def test_enforce_retry_budget() -> None:
    enforce_retry_budget(1, max_retries=3)
    enforce_retry_budget(4, max_retries=3)  # initial + 3 retries
    with pytest.raises(GuardrailError) as exc:
        enforce_retry_budget(5, max_retries=3)
    assert exc.value.code == "max_retries"


def test_run_deadline_timeout() -> None:
    deadline = RunDeadline(timeout_seconds=0)
    time.sleep(0.01)
    with pytest.raises(GuardrailError) as exc:
        deadline.check()
    assert exc.value.code == "run_timeout"


def test_plan_is_vague_property() -> None:
    plan = Plan(summary="x", issue_clarity="vague", clarification_needed="need repro")
    assert plan.is_vague
