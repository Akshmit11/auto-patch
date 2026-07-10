"""Filesystem tool confinement tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from autopatch.mcp_tools.filesystem_tool import FilesystemTools


def test_read_write_list(tmp_path: Path) -> None:
    tools = FilesystemTools(tmp_path)
    tools.write_file("pkg/hello.py", "print('hi')\n")
    assert tools.read_file("pkg/hello.py") == "print('hi')\n"
    entries = tools.list_dir("pkg")
    assert any(e.endswith("hello.py") for e in entries)


def test_path_escape_rejected(tmp_path: Path) -> None:
    tools = FilesystemTools(tmp_path)
    with pytest.raises(PermissionError):
        tools.read_file("../outside.txt")
