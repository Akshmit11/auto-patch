"""Sandbox MCP tools — execute tests only inside Docker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autopatch.sandbox.docker_runner import DockerRunner, ExecResult
from autopatch.tracing.logger import StructuredLogger


class SandboxTools:
    """Thin MCP-facing wrapper around DockerRunner."""

    def __init__(self, runner: DockerRunner, workspace: Path, logger: StructuredLogger | None = None) -> None:
        self.runner = runner
        self.workspace = workspace.resolve()
        self.logger = logger

    def exec(self, command: str, *, timeout_seconds: int | None = None) -> ExecResult:
        result = self.runner.run_command(
            self.workspace,
            command,
            timeout_seconds=timeout_seconds,
        )
        return result

    def apply_patch(self, patch_text: str) -> ExecResult:
        return self.runner.apply_patch(self.workspace, patch_text)

    def apply_patch_and_test(
        self,
        patch_text: str,
        *,
        test_command: str | None = None,
        install_command: str | None = None,
    ) -> ExecResult:
        return self.runner.apply_patch_and_test(
            self.workspace,
            patch_text,
            test_command=test_command,
            install_command=install_command,
        )

    def result_to_json(self, result: ExecResult) -> str:
        return json.dumps(
            {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": result.timed_out,
                "duration_seconds": result.duration_seconds,
                "ok": result.ok,
            },
            indent=2,
        )


def create_sandbox_mcp_server(
    runner: DockerRunner,
    workspace: Path,
    logger: StructuredLogger | None = None,
) -> Any:
    """Build a FastMCP server for sandbox execution."""
    from mcp.server.fastmcp import FastMCP

    tools = SandboxTools(runner, workspace, logger=logger)
    mcp = FastMCP("autopatch_sandbox_mcp")

    @mcp.tool(name="sandbox_exec")
    def sandbox_exec(command: str, timeout_seconds: int | None = None) -> str:
        """Run a shell command inside the Docker sandbox (workspace mounted at /workspace)."""
        result = tools.exec(command, timeout_seconds=timeout_seconds)
        return tools.result_to_json(result)

    @mcp.tool(name="sandbox_apply_patch_and_test")
    def sandbox_apply_patch_and_test(
        patch_text: str,
        test_command: str = "python -m pytest -q",
    ) -> str:
        """Apply a unified diff and run tests inside Docker. Never runs on host."""
        result = tools.apply_patch_and_test(patch_text, test_command=test_command)
        return tools.result_to_json(result)

    return mcp
