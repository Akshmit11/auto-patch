"""Unit tests for tree-sitter symbol indexing and retrieval."""

from __future__ import annotations

import shutil
from pathlib import Path

from autopatch.mcp_tools.codebase_tool import CodebaseTools
from autopatch.retrieval.symbol_index import SymbolIndex, SymbolKind


def test_build_index_on_sample_target() -> None:
    root = Path(__file__).resolve().parents[1] / "demo" / "sample_target"
    index = SymbolIndex()
    count = index.build(root)
    assert count > 0
    names = {s.name for s in index.symbols}
    assert "clamp" in names
    assert "add" in names
    kinds = {s.kind for s in index.symbols if s.name == "clamp"}
    assert SymbolKind.FUNCTION in kinds


def test_build_index_when_workspace_lives_under_autopatch_dir(tmp_path: Path) -> None:
    """Regression: agent workspaces are `.autopatch/work/<run>/repo`.

    Skip rules must use paths relative to the workspace root, not absolute
    path parts — otherwise `.autopatch` in the parent path zeros the index.
    """
    sample = Path(__file__).resolve().parents[1] / "demo" / "sample_target"
    # Mirror the real layout used by AgentLoop._prepare_workspace
    dest = tmp_path / ".autopatch" / "work" / "testrun" / "repo"
    shutil.copytree(sample, dest)

    index = SymbolIndex()
    count = index.build(dest)
    assert count > 0
    names = {s.name for s in index.symbols}
    assert "clamp" in names

    tools = CodebaseTools(dest)
    tools.build_index()
    bundle = tools.get_context_bundle(
        "Fix clamp() lower bound in sample_target/mathutil.py",
        max_files=8,
    )
    assert bundle["file_list"]
    assert any("mathutil.py" in f for f in bundle["file_list"])
    assert "clamp" in (bundle["files"].get("sample_target/mathutil.py") or "")


def test_search_ranks_clamp_for_issue_text() -> None:
    root = Path(__file__).resolve().parents[1] / "demo" / "sample_target"
    index = SymbolIndex()
    index.build(root)
    hits = index.search("clamp lower bound value below low", limit=10)
    assert hits
    assert any(h.name == "clamp" for h in hits)


def test_relevant_files_finds_mathutil() -> None:
    root = Path(__file__).resolve().parents[1] / "demo" / "sample_target"
    index = SymbolIndex()
    index.build(root)
    files = index.relevant_files("fix clamp function in mathutil", limit=5)
    assert any("mathutil.py" in f for f in files)
