"""Load and validate eval issue fixtures from ``eval/issues/*.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SourceKind = Literal["local", "github"]


@dataclass
class EvalFixture:
    """One eval case: local buggy package or a real closed GitHub issue."""

    id: str
    source: SourceKind
    title: str = ""
    body: str = ""
    body_file: str | None = None
    issue_url: str | None = None
    expected_pr_url: str | None = None
    repo_path: str | None = None
    test_command: str = "python -m pytest -q"
    expected_diff: str | None = None
    expected_diff_file: str | None = None
    expected_files: list[str] = field(default_factory=list)
    difficulty: str = "easy"
    language: str = "python"
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    path: Path | None = None  # original JSON path

    def resolve_body(self, repo_root: Path) -> str:
        if self.body:
            return self.body
        if self.body_file:
            p = (repo_root / self.body_file).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"body_file not found for fixture {self.id}: {p}")
            return p.read_text(encoding="utf-8")
        if self.issue_url:
            return ""  # agent will fetch from GitHub
        return ""

    def resolve_expected_diff(self, repo_root: Path) -> str | None:
        if self.expected_diff:
            return self.expected_diff
        if self.expected_diff_file:
            p = (repo_root / self.expected_diff_file).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"expected_diff_file not found for fixture {self.id}: {p}")
            return p.read_text(encoding="utf-8")
        return None

    def resolve_repo_path(self, repo_root: Path) -> Path | None:
        if not self.repo_path:
            return None
        p = Path(self.repo_path)
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        return p


def _require(data: dict[str, Any], key: str, fixture_path: Path) -> Any:
    if key not in data:
        raise ValueError(f"Fixture {fixture_path.name} missing required field {key!r}")
    return data[key]


def load_fixture(path: Path) -> EvalFixture:
    """Parse a single fixture JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Fixture {path} must be a JSON object")

    fid = str(_require(raw, "id", path))
    source = str(_require(raw, "source", path)).lower()
    if source not in {"local", "github"}:
        raise ValueError(f"Fixture {path.name}: source must be 'local' or 'github', got {source!r}")

    if source == "local" and not raw.get("repo_path"):
        raise ValueError(f"Fixture {path.name}: local fixtures require repo_path")
    if source == "github" and not raw.get("issue_url"):
        raise ValueError(f"Fixture {path.name}: github fixtures require issue_url")

    return EvalFixture(
        id=fid,
        source=source,  # type: ignore[arg-type]
        title=str(raw.get("title") or ""),
        body=str(raw.get("body") or ""),
        body_file=raw.get("body_file"),
        issue_url=raw.get("issue_url"),
        expected_pr_url=raw.get("expected_pr_url"),
        repo_path=raw.get("repo_path"),
        test_command=str(raw.get("test_command") or "python -m pytest -q"),
        expected_diff=raw.get("expected_diff"),
        expected_diff_file=raw.get("expected_diff_file"),
        expected_files=list(raw.get("expected_files") or []),
        difficulty=str(raw.get("difficulty") or "easy"),
        language=str(raw.get("language") or "python"),
        notes=str(raw.get("notes") or ""),
        tags=list(raw.get("tags") or []),
        enabled=bool(raw.get("enabled", True)),
        path=path,
    )


def load_fixtures(
    issues_dir: Path,
    *,
    only: set[str] | None = None,
    source: SourceKind | None = None,
    include_disabled: bool = False,
) -> list[EvalFixture]:
    """Load fixtures from ``issues_dir/*.json`` (sorted by id)."""
    if not issues_dir.is_dir():
        raise FileNotFoundError(f"Issues directory not found: {issues_dir}")

    fixtures: list[EvalFixture] = []
    for path in sorted(issues_dir.glob("*.json")):
        if not path.is_file():
            continue
        fix = load_fixture(path)
        if not include_disabled and not fix.enabled:
            continue
        if only is not None and fix.id not in only:
            continue
        if source is not None and fix.source != source:
            continue
        fixtures.append(fix)

    fixtures.sort(key=lambda f: f.id)
    return fixtures
