"""AutoPatch eval harness (Day 3).

Loads issue fixtures from ``eval/issues/``, runs the full agent pipeline against
each (local packages or real GitHub issues), scores resolve rate / cost / time /
optional edit-distance vs a golden merged fix, and writes:

- ``eval/results/results.json``
- ``eval/results/report.md``

Usage::

    # Offline / CI-safe: load fixtures and score only (no agent)
    uv run python eval/run_eval.py --list
    uv run python eval/run_eval.py --score-only eval/results/results.json

    # Local smoke fixtures only (needs LLM + Docker unless --skip-sandbox)
    uv run python eval/run_eval.py --local-only

    # Full set including real GitHub issues (needs GITHUB_TOKEN + LLM + Docker)
    uv run python eval/run_eval.py

    # Subset
    uv run python eval/run_eval.py --only local_clamp,local_is_even --limit 2

Be honest in scoring — imperfect numbers are expected and preferred.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow ``from fixtures import ...`` / ``from scoring import ...`` when executed
# as ``python eval/run_eval.py`` (repo root or eval/ as cwd).
_EVAL_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _EVAL_DIR.parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from fixtures import EvalFixture, load_fixtures  # noqa: E402
from scoring import (  # noqa: E402
    EvalSummary,
    FixtureScore,
    aggregate_scores,
    render_report_md,
    score_agent_result,
)


def _default_issues_dir() -> Path:
    return _EVAL_DIR / "issues"


def _default_results_dir() -> Path:
    return _EVAL_DIR / "results"


def run_fixture(
    fixture: EvalFixture,
    *,
    repo_root: Path,
    skip_sandbox: bool = False,
    dry_run: bool = False,
    agent_factory: Callable[[], Any] | None = None,
) -> FixtureScore:
    """Run one fixture through the agent (or dry-run) and return scores."""
    if dry_run:
        return FixtureScore(
            fixture_id=fixture.id,
            resolved=False,
            tests_passed=None,
            attempts=0,
            cost_usd=0.0,
            duration_seconds=0.0,
            skipped=True,
            skip_reason="dry_run",
            source=fixture.source,
        )

    expected_diff = fixture.resolve_expected_diff(repo_root)

    # Late imports so --list works without optional runtime deps failing import
    from autopatch.agent.loop import AgentLoop, RunRequest
    from autopatch.config import get_settings
    from autopatch.tracing.logger import StructuredLogger

    settings = get_settings()
    settings.ensure_dirs()

    need_github = fixture.source == "github" or bool(fixture.issue_url)
    try:
        settings.require_for_run(need_github=need_github, need_llm=True)
    except ValueError as exc:
        return FixtureScore(
            fixture_id=fixture.id,
            resolved=False,
            tests_passed=None,
            attempts=0,
            cost_usd=0.0,
            duration_seconds=0.0,
            error=str(exc),
            skipped=True,
            skip_reason="missing_secrets",
            source=fixture.source,
        )

    if fixture.source == "local":
        repo_path = fixture.resolve_repo_path(repo_root)
        if repo_path is None or not repo_path.is_dir():
            return FixtureScore(
                fixture_id=fixture.id,
                resolved=False,
                tests_passed=None,
                attempts=0,
                cost_usd=0.0,
                duration_seconds=0.0,
                error=f"repo_path missing or not a directory: {fixture.repo_path}",
                skipped=True,
                skip_reason="missing_repo",
                source=fixture.source,
            )
        body = fixture.resolve_body(repo_root)
        title = fixture.title or fixture.id
        request = RunRequest(
            issue_url=None,
            issue_title=title,
            issue_body=body,
            repo_path=repo_path,
            test_command=fixture.test_command,
            skip_sandbox=skip_sandbox,
            create_pr=False,
            work_subdir=f"eval-{fixture.id}",
        )
    else:
        request = RunRequest(
            issue_url=fixture.issue_url,
            test_command=fixture.test_command,
            skip_sandbox=skip_sandbox,
            create_pr=False,
            work_subdir=f"eval-{fixture.id}",
        )

    run_id = f"eval-{fixture.id}-{uuid.uuid4().hex[:8]}"
    logger = StructuredLogger(settings.log_dir, run_id=run_id)
    agent = agent_factory() if agent_factory is not None else AgentLoop(settings, logger=logger)

    try:
        result = agent.run(request)
    except Exception as exc:  # noqa: BLE001 — surface into score row
        cost = 0.0
        try:
            cost = float(logger.trace.usage.cost_usd)
        except Exception:  # noqa: BLE001
            cost = 0.0
        return FixtureScore(
            fixture_id=fixture.id,
            resolved=False,
            tests_passed=None,
            attempts=0,
            cost_usd=cost,
            duration_seconds=0.0,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}",
            run_id=getattr(logger, "run_id", None),
            source=fixture.source,
        )

    files_touched: list[str] = []
    actual_diff: str | None = None
    if result.patch is not None:
        files_touched = list(result.patch.files_touched or [])
        actual_diff = result.patch.diff

    if skip_sandbox:
        # Plan+patch only: resolved means the agent produced a non-rejected patch.
        tests_passed: bool | None = None
        resolved_success = bool(result.success)
    elif result.verify is not None:
        tests_passed = bool(result.verify.passed)
        resolved_success = bool(result.success and tests_passed)
    else:
        tests_passed = False
        resolved_success = False

    return score_agent_result(
        fixture_id=fixture.id,
        source=fixture.source,
        success=resolved_success,
        tests_passed=tests_passed,
        attempts=len(result.attempts),
        cost_usd=float(result.cost_usd),
        duration_seconds=float(result.duration_seconds),
        files_touched=files_touched,
        actual_diff=actual_diff,
        expected_diff=expected_diff,
        expected_files=fixture.expected_files,
        error=result.error,
        run_id=result.run_id,
    )


def write_outputs(
    summary: EvalSummary,
    results_dir: Path,
    *,
    meta: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Write results.json + report.md; return their paths."""
    results_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "meta": meta or {},
        "summary": {k: v for k, v in summary.to_dict().items() if k != "fixtures"},
        "fixtures": [s.to_dict() for s in summary.fixture_scores],
    }
    json_path = results_dir / "results.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    report = render_report_md(summary)
    if meta:
        extra = ["", "## Run metadata", ""]
        for k, v in meta.items():
            extra.append(f"- **{k}**: {v}")
        extra.append("")
        report = report + "\n".join(extra)

    md_path = results_dir / "report.md"
    md_path.write_text(report, encoding="utf-8")
    return json_path, md_path


def load_scores_from_results_json(path: Path) -> list[FixtureScore]:
    """Rehydrate FixtureScore rows from a previous results.json (for --score-only)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("fixtures") or data.get("summary", {}).get("fixtures") or []
    scores: list[FixtureScore] = []
    for row in rows:
        scores.append(
            FixtureScore(
                fixture_id=str(row.get("fixture_id") or row.get("id") or "unknown"),
                resolved=bool(row.get("resolved")),
                tests_passed=row.get("tests_passed"),
                attempts=int(row.get("attempts") or 0),
                cost_usd=float(row.get("cost_usd") or 0.0),
                duration_seconds=float(row.get("duration_seconds") or 0.0),
                files_touched=list(row.get("files_touched") or []),
                has_tests_in_patch=bool(row.get("has_tests_in_patch")),
                edit_distance=row.get("edit_distance"),
                similarity=row.get("similarity"),
                expected_files_hit=row.get("expected_files_hit"),
                error=row.get("error"),
                run_id=row.get("run_id"),
                skipped=bool(row.get("skipped")),
                skip_reason=row.get("skip_reason"),
                source=str(row.get("source") or "unknown"),
            )
        )
    return scores


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AutoPatch eval harness — resolve rate, cost, time, edit distance.",
    )
    p.add_argument(
        "--issues-dir",
        type=Path,
        default=_default_issues_dir(),
        help="Directory of fixture JSON files (default: eval/issues)",
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=_default_results_dir(),
        help="Output directory for results.json + report.md",
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root for resolving relative fixture paths",
    )
    p.add_argument("--only", type=str, default=None, help="Comma-separated fixture ids")
    p.add_argument("--limit", type=int, default=None, help="Max fixtures to run")
    p.add_argument("--local-only", action="store_true", help="Only local fixtures")
    p.add_argument("--github-only", action="store_true", help="Only github fixtures")
    p.add_argument(
        "--skip-sandbox",
        action="store_true",
        help="Agent dry-run (plan+patch only). Resolve = patch produced, not tests.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Load fixtures and write empty skipped scores (no agent calls)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        dest="list_fixtures",
        help="List fixtures and exit",
    )
    p.add_argument(
        "--score-only",
        type=Path,
        default=None,
        help="Re-aggregate an existing results.json into report.md (no agent)",
    )
    p.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include fixtures with enabled=false",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    issues_dir = args.issues_dir
    if not issues_dir.is_absolute():
        issues_dir = (repo_root / issues_dir).resolve()
    results_dir = args.results_dir
    if not results_dir.is_absolute():
        results_dir = (repo_root / results_dir).resolve()

    if args.score_only is not None:
        path = args.score_only
        if not path.is_file():
            print(f"results file not found: {path}", file=sys.stderr)
            return 2
        scores = load_scores_from_results_json(path)
        summary = aggregate_scores(scores)
        json_path, md_path = write_outputs(
            summary,
            results_dir,
            meta={"mode": "score_only", "source": str(path)},
        )
        print(render_report_md(summary))
        print(f"\nWrote {json_path} and {md_path}")
        return 0

    only: set[str] | None = None
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}

    source = None
    if args.local_only and args.github_only:
        print("Use only one of --local-only / --github-only", file=sys.stderr)
        return 2
    if args.local_only:
        source = "local"
    elif args.github_only:
        source = "github"

    fixtures = load_fixtures(
        issues_dir,
        only=only,
        source=source,  # type: ignore[arg-type]
        include_disabled=args.include_disabled,
    )
    if args.limit is not None:
        fixtures = fixtures[: max(0, args.limit)]

    if args.list_fixtures:
        print(
            json.dumps(
                [
                    {
                        "id": f.id,
                        "source": f.source,
                        "title": f.title,
                        "issue_url": f.issue_url,
                        "repo_path": f.repo_path,
                        "difficulty": f.difficulty,
                        "enabled": f.enabled,
                        "tags": f.tags,
                    }
                    for f in fixtures
                ],
                indent=2,
            )
        )
        print(f"\n{len(fixtures)} fixture(s)", file=sys.stderr)
        return 0

    if not fixtures:
        print("No fixtures matched filters.", file=sys.stderr)
        return 2

    scores: list[FixtureScore] = []
    for i, fix in enumerate(fixtures, start=1):
        print(f"[{i}/{len(fixtures)}] {fix.id} ({fix.source}) ...", flush=True)
        score = run_fixture(
            fix,
            repo_root=repo_root,
            skip_sandbox=args.skip_sandbox,
            dry_run=args.dry_run,
        )
        status = "skip" if score.skipped else ("OK" if score.resolved else "FAIL")
        print(
            f"  -> {status} attempts={score.attempts} cost=${score.cost_usd:.4f} "
            f"time={score.duration_seconds:.1f}s"
            + (f" err={score.error[:100]}" if score.error else ""),
            flush=True,
        )
        scores.append(score)

    summary = aggregate_scores(scores)
    meta = {
        "mode": "dry_run" if args.dry_run else ("skip_sandbox" if args.skip_sandbox else "full"),
        "fixture_count": len(fixtures),
        "local_only": args.local_only,
        "github_only": args.github_only,
        "repo_root": str(repo_root),
    }
    json_path, md_path = write_outputs(summary, results_dir, meta=meta)
    print()
    print(render_report_md(summary))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    # Exit 0 even with low resolve rate — eval is a measurement tool, not a gate
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
