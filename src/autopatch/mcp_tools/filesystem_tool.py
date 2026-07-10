"""Filesystem MCP tools — read/write within a rooted workspace only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autopatch.tracing.logger import StructuredLogger


class FilesystemTools:
    """Safe filesystem operations confined to a workspace root."""

    def __init__(self, workspace: Path, logger: StructuredLogger | None = None) -> None:
        self.workspace = workspace.resolve()
        self.logger = logger

    def _resolve(self, rel_path: str) -> Path:
        candidate = (self.workspace / rel_path).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {rel_path} -> {candidate}") from exc
        return candidate

    def read_file(self, path: str, *, max_chars: int = 100_000) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if self.logger:
            self.logger.log_tool_call(
                "fs_read_file",
                arguments={"path": path},
                result_summary=f"{len(text)} chars",
            )
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n...[truncated, total {len(text)} chars]"
        return text

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if self.logger:
            self.logger.log_tool_call(
                "fs_write_file",
                arguments={"path": path, "bytes": len(content.encode("utf-8"))},
                result_summary="written",
            )
        return f"Wrote {path} ({len(content)} chars)"

    def list_dir(self, path: str = ".", *, max_entries: int = 200) -> list[str]:
        target = self._resolve(path)
        if not target.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
        entries: list[str] = []
        for child in sorted(target.iterdir()):
            suffix = "/" if child.is_dir() else ""
            rel = child.relative_to(self.workspace).as_posix()
            entries.append(rel + suffix)
            if len(entries) >= max_entries:
                break
        if self.logger:
            self.logger.log_tool_call(
                "fs_list_dir",
                arguments={"path": path},
                result_summary=f"{len(entries)} entries",
            )
        return entries

    def file_exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def read_files(self, paths: list[str], *, max_chars_each: int = 40_000) -> dict[str, str]:
        return {p: self.read_file(p, max_chars=max_chars_each) for p in paths}


def create_filesystem_mcp_server(workspace: Path, logger: StructuredLogger | None = None) -> Any:
    """Build a FastMCP server exposing filesystem tools (stdio-capable)."""
    from mcp.server.fastmcp import FastMCP

    tools = FilesystemTools(workspace, logger=logger)
    mcp = FastMCP("autopatch_filesystem_mcp")

    @mcp.tool(name="fs_read_file")
    def fs_read_file(path: str) -> str:
        """Read a text file relative to the workspace root."""
        return tools.read_file(path)

    @mcp.tool(name="fs_write_file")
    def fs_write_file(path: str, content: str) -> str:
        """Write a text file relative to the workspace root."""
        return tools.write_file(path, content)

    @mcp.tool(name="fs_list_dir")
    def fs_list_dir(path: str = ".") -> str:
        """List directory entries relative to the workspace root."""
        return json.dumps(tools.list_dir(path), indent=2)

    return mcp
