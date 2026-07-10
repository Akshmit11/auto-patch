"""Unit tests for tree-sitter symbol indexing and retrieval."""

from __future__ import annotations

from pathlib import Path

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
