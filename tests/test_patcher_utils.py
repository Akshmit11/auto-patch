"""Unit tests for patch extraction and file-count parsing (no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autopatch.agent.patcher import extract_unified_diff, files_in_diff
from autopatch.sandbox.docker_runner import _apply_unified_diff

SAMPLE_DIFF = """\
--- a/sample_target/mathutil.py
+++ b/sample_target/mathutil.py
@@ -18,5 +18,5 @@ def clamp(value: float, low: float, high: float) -> float:
     if high < low:
         low, high = high, low
-    # BUG: should be max(low, min(high, value))
-    return min(low, max(high, value))
+    # Fixed: clamp into [low, high]
+    return max(low, min(high, value))
"""


def test_extract_unified_diff_from_fence() -> None:
    fenced = f"Here is the fix:\n```diff\n{SAMPLE_DIFF}\n```\nThanks"
    diff = extract_unified_diff(fenced)
    assert "--- a/sample_target/mathutil.py" in diff
    assert "+++ b/sample_target/mathutil.py" in diff


def test_files_in_diff() -> None:
    assert files_in_diff(SAMPLE_DIFF) == ["sample_target/mathutil.py"]


def test_apply_unified_diff_host_safe(tmp_path: Path) -> None:
    target = tmp_path / "sample_target"
    target.mkdir()
    py = target / "mathutil.py"
    py.write_text(
        "def clamp(value, low, high):\n"
        "    if high < low:\n"
        "        low, high = high, low\n"
        "    # BUG: should be max(low, min(high, value))\n"
        "    return min(low, max(high, value))\n",
        encoding="utf-8",
    )
    diff = """\
--- a/sample_target/mathutil.py
+++ b/sample_target/mathutil.py
@@ -1,5 +1,5 @@
 def clamp(value, low, high):
     if high < low:
         low, high = high, low
-    # BUG: should be max(low, min(high, value))
-    return min(low, max(high, value))
+    # Fixed
+    return max(low, min(high, value))
"""
    _apply_unified_diff(tmp_path, diff)
    text = py.read_text(encoding="utf-8")
    assert "return max(low, min(high, value))" in text
    assert "min(low, max(high, value))" not in text


def test_extract_diff_raises_without_headers() -> None:
    with pytest.raises(ValueError):
        extract_unified_diff("no diff here, just prose")


def test_merge_unified_diffs() -> None:
    from autopatch.agent.patcher import PatchResult, combine_patch_results, merge_unified_diffs

    a = """\
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-x
+y
"""
    b = """\
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1 @@
-old
+new
"""
    merged = merge_unified_diffs(a, b)
    assert "a/a.py" in merged or "+++ b/a.py" in merged
    assert "tests/test_a.py" in merged
    assert files_in_diff(merged) == ["a.py", "tests/test_a.py"]

    combined = combine_patch_results(
        PatchResult(diff=a, files_touched=["a.py"]),
        PatchResult(diff=b, files_touched=["tests/test_a.py"]),
    )
    assert not combined.rejected
    assert set(combined.files_touched) == {"a.py", "tests/test_a.py"}


def test_combine_rejects_if_any_rejected() -> None:
    from autopatch.agent.patcher import PatchResult, combine_patch_results

    good = PatchResult(diff="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n", files_touched=["a.py"])
    bad = PatchResult(diff="", files_touched=[], rejected=True, reject_reason="nope")
    combined = combine_patch_results(good, bad)
    assert combined.rejected
    assert combined.reject_reason == "nope"
