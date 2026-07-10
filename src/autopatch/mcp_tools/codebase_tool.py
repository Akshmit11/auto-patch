"""Codebase MCP tools — tree-sitter symbol lookup and context retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autopatch.retrieval.symbol_index import SymbolIndex
from autopatch.tracing.logger import StructuredLogger


class CodebaseTools:
    """Symbol index + keyword/symbol retrieval over a cloned workspace."""

    def __init__(
        self,
        workspace: Path,
        logger: StructuredLogger | None = None,
        index: SymbolIndex | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.logger = logger
        self.index = index or SymbolIndex()
        self._built = False

    def build_index(self) -> int:
        count = self.index.build(self.workspace)
        self._built = True
        if self.logger:
            self.logger.log_tool_call(
                "codebase_build_index",
                arguments={"workspace": str(self.workspace)},
                result_summary=f"{count} symbols",
            )
        return count

    def ensure_index(self) -> None:
        if not self._built:
            self.build_index()

    def search_symbols(self, query: str, *, limit: int = 20) -> list[dict[str, object]]:
        self.ensure_index()
        results = [s.to_dict() for s in self.index.search(query, limit=limit)]
        if self.logger:
            self.logger.log_tool_call(
                "codebase_search_symbols",
                arguments={"query": query, "limit": limit},
                result_summary=f"{len(results)} hits",
            )
        return results

    def relevant_files(self, query: str, *, limit: int = 10) -> list[str]:
        self.ensure_index()
        files = self.index.relevant_files(query, limit=limit)
        if self.logger:
            self.logger.log_tool_call(
                "codebase_relevant_files",
                arguments={"query": query, "limit": limit},
                result_summary=f"{len(files)} files",
            )
        return files

    def get_context_bundle(
        self,
        query: str,
        *,
        max_files: int = 8,
        max_chars_per_file: int = 12_000,
    ) -> dict[str, Any]:
        """Retrieve ranked files + symbols for an issue query."""
        self.ensure_index()
        symbols = self.index.search(query, limit=30)
        files = self.index.relevant_files(query, limit=max_files)

        # Also keyword-scan file paths if symbol search is thin.
        if len(files) < max_files:
            for path in sorted(self.workspace.rglob("*.py")):
                rel = path.relative_to(self.workspace).as_posix()
                if rel in files:
                    continue
                name = path.name.lower()
                if any(tok in name for tok in query.lower().split() if len(tok) > 3):
                    files.append(rel)
                if len(files) >= max_files:
                    break

        file_contents: dict[str, str] = {}
        for rel in files[:max_files]:
            full = self.workspace / rel
            if not full.is_file():
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_chars_per_file:
                text = text[:max_chars_per_file] + "\n...[truncated]"
            file_contents[rel] = text

        bundle = {
            "symbols": [s.to_dict() for s in symbols[:20]],
            "files": file_contents,
            "file_list": list(file_contents.keys()),
        }
        if self.logger:
            self.logger.log_tool_call(
                "codebase_get_context",
                arguments={"query": query[:200], "max_files": max_files},
                result_summary=f"{len(file_contents)} files, {len(symbols)} symbols",
            )
        return bundle


def create_codebase_mcp_server(workspace: Path, logger: StructuredLogger | None = None) -> Any:
    """Build a FastMCP server for codebase/symbol tools."""
    from mcp.server.fastmcp import FastMCP

    tools = CodebaseTools(workspace, logger=logger)
    mcp = FastMCP("autopatch_codebase_mcp")

    @mcp.tool(name="codebase_build_index")
    def codebase_build_index() -> str:
        """Build a tree-sitter symbol index for the workspace."""
        count = tools.build_index()
        return json.dumps({"symbol_count": count})

    @mcp.tool(name="codebase_search_symbols")
    def codebase_search_symbols(query: str, limit: int = 20) -> str:
        """Search indexed symbols by keyword / name match."""
        return json.dumps(tools.search_symbols(query, limit=limit), indent=2)

    @mcp.tool(name="codebase_relevant_files")
    def codebase_relevant_files(query: str, limit: int = 10) -> str:
        """List the most relevant source files for a natural-language query."""
        return json.dumps(tools.relevant_files(query, limit=limit), indent=2)

    return mcp
