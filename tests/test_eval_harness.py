"""Unit tests for Day-3 eval harness (no LLM / Docker / network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from fixtures import load_fixture, load_fixtures  # noqa: E402
from run_eval import load_scores_from_results_json, write_outputs  # noqa: E402
from scoring import (  # noqa: E402
    aggregate_scores,
    diff_similarity,
    edit_distance_score,
    expected_files_hit_rate,
    normalize_diff_lines,
    render_report_md,
    score_agent_result,
)


def test_load_local_fixtures() -> None:
    fixtures = load_fixtures(EVAL_DIR / "issues", source="local")
    ids = {f.id for f in fixtures}
    assert "local_clamp" in ids
    assert "local_is_even" in ids
    assert len(fixtures) >= 4


def test_load_all_fixtures_count() -> None:
    fixtures = load_fixtures(EVAL_DIR / "issues")
    assert 15 <= len(fixtures) <= 30
    sources = {f.source for f in fixtures}
    assert "local" in sources
    assert "github" in sources


def test_local_fixture_resolves_paths() -> None:
    fix = load_fixture(EVAL_DIR / "issues" / "local_clamp.json")
    assert fix.source == "local"
    body = fix.resolve_body(REPO_ROOT)
    assert "clamp" in body.lower()
    repo = fix.resolve_repo_path(REPO_ROOT)
    assert repo is not None
    assert repo.is_dir()
    golden = fix.resolve_expected_diff(REPO_ROOT)
    assert golden is not None
    assert "max(low, min(high, value))" in golden


def test_github_fixture_requires_issue_url() -> None:
    fix = load_fixture(EVAL_DIR / "issues" / "gh_click_3487.json")
    assert fix.source == "github"
    assert fix.issue_url and fix.issue_url.startswith("https://github.com/")


def test_normalize_and_similarity_identical() -> None:
    diff = (EVAL_DIR / "issues" / "expected" / "local_clamp.diff").read_text(encoding="utf-8")
    assert diff_similarity(diff, diff) == 1.0
    assert edit_distance_score(diff, diff) == 0.0
    lines = normalize_diff_lines(diff)
    assert any(line.startswith("+") for line in lines)
    assert any(line.startswith("FILE:") for line in lines)


def test_edit_distance_detects_difference() -> None:
    a = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    b = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+other\n"
    dist = edit_distance_score(a, b)
    assert 0.0 < dist <= 1.0
    assert diff_similarity(a, b) < 1.0


def test_expected_files_hit_rate() -> None:
    rate = expected_files_hit_rate(
        ["clamp_bug/mathutil.py", "tests/test_mathutil.py"],
        ["clamp_bug/mathutil.py"],
    )
    assert rate == 1.0
    rate2 = expected_files_hit_rate(["other.py"], ["clamp_bug/mathutil.py"])
    assert rate2 == 0.0


def test_score_and_aggregate() -> None:
    golden = (EVAL_DIR / "issues" / "expected" / "local_clamp.diff").read_text(encoding="utf-8")
    ok = score_agent_result(
        fixture_id="local_clamp",
        source="local",
        success=True,
        tests_passed=True,
        attempts=1,
        cost_usd=0.02,
        duration_seconds=12.5,
        files_touched=["clamp_bug/mathutil.py", "tests/test_extra.py"],
        actual_diff=golden,
        expected_diff=golden,
        expected_files=["clamp_bug/mathutil.py"],
        error=None,
        run_id="abc",
    )
    assert ok.resolved is True
    assert ok.has_tests_in_patch is True
    assert ok.edit_distance == 0.0
    assert ok.similarity == 1.0

    fail = score_agent_result(
        fixture_id="local_is_even",
        source="local",
        success=False,
        tests_passed=False,
        attempts=3,
        cost_usd=0.1,
        duration_seconds=40.0,
        files_touched=["even_bug/numbers.py"],
        actual_diff="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",
        expected_diff=golden,
        expected_files=["even_bug/numbers.py"],
        error="tests failed",
        run_id="def",
    )
    assert fail.resolved is False

    summary = aggregate_scores([ok, fail])
    assert summary.total == 2
    assert summary.ran == 2
    assert summary.resolved == 1
    assert summary.resolve_rate == 0.5
    assert summary.mean_attempts == 2.0
    assert summary.mean_cost_usd is not None
    md = render_report_md(summary)
    assert "Resolve rate" in md
    assert "local_clamp" in md
    assert "50.0%" in md


def test_write_outputs(tmp_path: Path) -> None:
    score = score_agent_result(
        fixture_id="x",
        source="local",
        success=True,
        tests_passed=True,
        attempts=1,
        cost_usd=0.01,
        duration_seconds=1.0,
        files_touched=["a.py"],
        actual_diff=None,
        expected_diff=None,
        expected_files=None,
        error=None,
        run_id="r1",
    )
    summary = aggregate_scores([score])
    json_path, md_path = write_outputs(summary, tmp_path, meta={"mode": "unit"})
    assert json_path.is_file()
    assert md_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["summary"]["resolved"] == 1
    assert "fixtures" in data


def test_score_only_roundtrip(tmp_path: Path) -> None:
    score = score_agent_result(
        fixture_id="y",
        source="github",
        success=False,
        tests_passed=False,
        attempts=2,
        cost_usd=0.05,
        duration_seconds=9.0,
        files_touched=[],
        actual_diff=None,
        expected_diff=None,
        expected_files=None,
        error="boom",
        run_id="r2",
    )
    summary = aggregate_scores([score])
    json_path, _ = write_outputs(summary, tmp_path)
    reloaded = load_scores_from_results_json(json_path)
    assert len(reloaded) == 1
    assert reloaded[0].fixture_id == "y"
    assert reloaded[0].error == "boom"


def test_only_filter() -> None:
    fixtures = load_fixtures(EVAL_DIR / "issues", only={"local_clamp"})
    assert len(fixtures) == 1
    assert fixtures[0].id == "local_clamp"


def test_invalid_fixture_missing_repo(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"id": "bad", "source": "local", "title": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="repo_path"):
        load_fixture(bad)
