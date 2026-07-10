"""Pure scoring helpers for the AutoPatch eval harness.

Scores a single agent run against an optional golden expected_diff / expected files.
No network, Docker, or LLM calls — safe for unit tests.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any


@dataclass
class FixtureScore:
    """Per-fixture metrics used for aggregate resolve-rate reporting."""

    fixture_id: str
    resolved: bool
    tests_passed: bool | None
    attempts: int
    cost_usd: float
    duration_seconds: float
    files_touched: list[str] = field(default_factory=list)
    has_tests_in_patch: bool = False
    edit_distance: float | None = None  # 0 = identical to golden; None = not annotated
    similarity: float | None = None  # 1 = identical normalized diff
    expected_files_hit: float | None = None  # fraction of expected files touched
    error: str | None = None
    run_id: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalSummary:
    """Aggregate metrics over a full eval run."""

    total: int
    ran: int
    skipped: int
    resolved: int
    resolve_rate: float
    median_duration_seconds: float | None
    mean_cost_usd: float | None
    mean_attempts: float | None
    mean_edit_distance: float | None
    mean_similarity: float | None
    tests_in_patch_rate: float | None
    fixture_scores: list[FixtureScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "ran": self.ran,
            "skipped": self.skipped,
            "resolved": self.resolved,
            "resolve_rate": self.resolve_rate,
            "median_duration_seconds": self.median_duration_seconds,
            "mean_cost_usd": self.mean_cost_usd,
            "mean_attempts": self.mean_attempts,
            "mean_edit_distance": self.mean_edit_distance,
            "mean_similarity": self.mean_similarity,
            "tests_in_patch_rate": self.tests_in_patch_rate,
            "fixtures": [s.to_dict() for s in self.fixture_scores],
        }


_HUNK_RE = re.compile(r"^@@.*@@")


def normalize_diff_lines(diff: str) -> list[str]:
    """Normalize a unified diff to comparable change lines.

    Drops headers (diff/index/---/+++/@@) and file mode noise so edit distance
    measures semantic patch content rather than git metadata.
    """
    lines: list[str] = []
    for raw in (diff or "").splitlines():
        line = raw.rstrip("\n")
        if not line:
            continue
        if line.startswith(("diff --git", "index ", "new file mode", "deleted file mode")):
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            # Keep path only, normalized
            path = line[4:].strip()
            if "\t" in path:
                path = path.split("\t", 1)[0]
            if path.startswith(("a/", "b/")):
                path = path[2:]
            if path == "/dev/null":
                continue
            lines.append(f"FILE:{path.replace(chr(92), '/')}")
            continue
        if _HUNK_RE.match(line):
            continue
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            # Drop pure context-less noise whitespace differences lightly
            lines.append(line[0] + line[1:].rstrip())
        # ignore context lines (space prefix) for distance
    return lines


def diff_similarity(actual: str, expected: str) -> float:
    """Return SequenceMatcher ratio in [0, 1] over normalized diff lines."""
    a = normalize_diff_lines(actual)
    b = normalize_diff_lines(expected)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def edit_distance_score(actual: str, expected: str) -> float:
    """Human-edit-distance proxy: 0 = identical, 1 = completely different."""
    return round(1.0 - diff_similarity(actual, expected), 4)


def expected_files_hit_rate(files_touched: list[str], expected_files: list[str]) -> float | None:
    """Fraction of annotated expected files that appear in the agent patch."""
    if not expected_files:
        return None
    touched = {_norm_path(p) for p in files_touched}
    expected = [_norm_path(p) for p in expected_files]
    if not expected:
        return None
    hits = sum(
        1 for p in expected if p in touched or any(p.endswith(t) or t.endswith(p) for t in touched)
    )
    return round(hits / len(expected), 4)


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def is_test_path(path: str) -> bool:
    normalized = _norm_path(path).lower()
    name = normalized.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{normalized}"
        or "/test/" in f"/{normalized}"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def patch_includes_tests(files_touched: list[str]) -> bool:
    return any(is_test_path(p) for p in files_touched)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def score_agent_result(
    *,
    fixture_id: str,
    source: str,
    success: bool,
    tests_passed: bool | None,
    attempts: int,
    cost_usd: float,
    duration_seconds: float,
    files_touched: list[str],
    actual_diff: str | None,
    expected_diff: str | None,
    expected_files: list[str] | None,
    error: str | None,
    run_id: str | None,
) -> FixtureScore:
    """Score one completed agent run (or failed run with partial fields)."""
    similarity: float | None = None
    edit_dist: float | None = None
    if expected_diff and actual_diff:
        similarity = round(diff_similarity(actual_diff, expected_diff), 4)
        edit_dist = edit_distance_score(actual_diff, expected_diff)

    return FixtureScore(
        fixture_id=fixture_id,
        resolved=bool(success),
        tests_passed=tests_passed,
        attempts=attempts,
        cost_usd=round(cost_usd, 6),
        duration_seconds=round(duration_seconds, 3),
        files_touched=list(files_touched),
        has_tests_in_patch=patch_includes_tests(files_touched),
        edit_distance=edit_dist,
        similarity=similarity,
        expected_files_hit=expected_files_hit_rate(files_touched, expected_files or []),
        error=error,
        run_id=run_id,
        source=source,
    )


def aggregate_scores(scores: list[FixtureScore]) -> EvalSummary:
    """Compute honest aggregate metrics (skipped fixtures excluded from rates)."""
    ran = [s for s in scores if not s.skipped]
    skipped = [s for s in scores if s.skipped]
    resolved = [s for s in ran if s.resolved]

    durations = [s.duration_seconds for s in ran]
    costs = [s.cost_usd for s in ran]
    attempts = [float(s.attempts) for s in ran]
    edits = [s.edit_distance for s in ran if s.edit_distance is not None]
    sims = [s.similarity for s in ran if s.similarity is not None]
    with_tests = [1.0 if s.has_tests_in_patch else 0.0 for s in ran]

    ran_n = len(ran)
    resolve_rate = (len(resolved) / ran_n) if ran_n else 0.0

    return EvalSummary(
        total=len(scores),
        ran=ran_n,
        skipped=len(skipped),
        resolved=len(resolved),
        resolve_rate=round(resolve_rate, 4),
        median_duration_seconds=round(median(durations) or 0.0, 3) if durations else None,
        mean_cost_usd=round(mean(costs) or 0.0, 6) if costs else None,
        mean_attempts=round(mean(attempts) or 0.0, 3) if attempts else None,
        mean_edit_distance=round(mean(edits) or 0.0, 4) if edits else None,
        mean_similarity=round(mean(sims) or 0.0, 4) if sims else None,
        tests_in_patch_rate=round(mean(with_tests) or 0.0, 4) if with_tests else None,
        fixture_scores=scores,
    )


def render_report_md(summary: EvalSummary, *, title: str = "AutoPatch Eval Report") -> str:
    """Human-readable Markdown table for README / results/report.md."""
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fixtures total | {summary.total} |",
        f"| Ran | {summary.ran} |",
        f"| Skipped | {summary.skipped} |",
        f"| Resolved | {summary.resolved} |",
        f"| **Resolve rate** | **{summary.resolve_rate * 100:.1f}%** |",
        f"| Median duration (s) | {summary.median_duration_seconds if summary.median_duration_seconds is not None else '—'} |",
        f"| Mean cost (USD) | {summary.mean_cost_usd if summary.mean_cost_usd is not None else '—'} |",
        f"| Mean attempts | {summary.mean_attempts if summary.mean_attempts is not None else '—'} |",
        f"| Mean edit distance (vs golden) | {summary.mean_edit_distance if summary.mean_edit_distance is not None else '—'} |",
        f"| Mean diff similarity | {summary.mean_similarity if summary.mean_similarity is not None else '—'} |",
        f"| Patches including tests | {summary.tests_in_patch_rate if summary.tests_in_patch_rate is not None else '—'} |",
        "",
        "> Resolve rate = fixtures where the agent produced a patch that passed sandbox tests "
        "(or succeeded under skip-sandbox dry-run). Edit distance is only computed when a "
        "golden `expected_diff` is annotated. Numbers are honest — imperfect scores are expected.",
        "",
        "## Per-fixture results",
        "",
        "| ID | Source | Resolved | Attempts | Cost $ | Time s | Edit dist | Tests in patch | Error |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summary.fixture_scores:
        if s.skipped:
            lines.append(
                f"| {s.fixture_id} | {s.source} | skipped | — | — | — | — | — | {s.skip_reason or ''} |"
            )
            continue
        err = (s.error or "").replace("|", "/").replace("\n", " ")[:80]
        lines.append(
            f"| {s.fixture_id} | {s.source} | {'yes' if s.resolved else 'no'} | "
            f"{s.attempts} | {s.cost_usd:.4f} | {s.duration_seconds:.1f} | "
            f"{s.edit_distance if s.edit_distance is not None else '—'} | "
            f"{'yes' if s.has_tests_in_patch else 'no'} | {err} |"
        )
    lines.append("")
    return "\n".join(lines)
