"""Thin CLI entrypoint for AutoPatch."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from autopatch import __version__
from autopatch.agent.loop import AgentLoop, RunRequest, write_result_json
from autopatch.config import get_settings
from autopatch.tracing.logger import StructuredLogger

app = typer.Typer(
    name="autopatch",
    help="AutoPatch — GitHub issue → sandboxed patch → (draft PR on Day 2).",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """AutoPatch CLI."""


@app.command("version")
def version_cmd() -> None:
    """Print package version."""
    typer.echo(__version__)


@app.command("run")
def run_cmd(
    issue_url: str | None = typer.Argument(
        None,
        help="GitHub issue URL (https://github.com/owner/repo/issues/N).",
    ),
    repo_path: Path | None = typer.Option(
        None,
        "--repo",
        help="Local target repository path (skips clone; for demos/fixtures).",
    ),
    issue_title: str | None = typer.Option(
        None,
        "--title",
        help="Local issue title when not using --issue URL.",
    ),
    issue_body: str | None = typer.Option(
        None,
        "--body",
        help="Local issue body when not using issue URL.",
    ),
    issue_file: Path | None = typer.Option(
        None,
        "--issue-file",
        help="Path to a text file containing the issue body (local mode).",
    ),
    test_command: str = typer.Option(
        "python -m pytest -q",
        "--test-command",
        help="Test command executed inside the Docker sandbox.",
    ),
    skip_sandbox: bool = typer.Option(
        False,
        "--skip-sandbox",
        help="Stop after plan+patch (no Docker). Dev/dry-run only.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write result JSON to this path.",
    ),
) -> None:
    """Run Day-1 plan → patch → sandbox test loop on one issue."""
    settings = get_settings()
    settings.ensure_dirs()

    body = issue_body
    if issue_file is not None:
        body = issue_file.read_text(encoding="utf-8")

    if not issue_url and repo_path is None:
        typer.secho(
            "Provide an issue URL and/or --repo for a local target.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    need_github = issue_url is not None
    need_llm = True
    try:
        settings.require_for_run(need_github=need_github, need_llm=need_llm)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    logger = StructuredLogger(settings.log_dir)
    agent = AgentLoop(settings, logger=logger)
    request = RunRequest(
        issue_url=issue_url,
        issue_title=issue_title,
        issue_body=body,
        repo_path=repo_path,
        test_command=test_command,
        skip_sandbox=skip_sandbox,
    )
    result = agent.run(request)

    out_path = output or (settings.log_dir / f"result-{result.run_id}.json")
    write_result_json(result, out_path)

    typer.echo(json.dumps(result.to_dict(), indent=2, default=str))
    typer.secho(
        f"\nRun {result.run_id}: {'SUCCESS' if result.success else 'FAILED'} "
        f"| cost=${result.cost_usd:.4f} | {result.duration_seconds:.1f}s "
        f"| log={logger.log_path} | result={out_path}",
        fg=typer.colors.GREEN if result.success else typer.colors.RED,
    )
    if result.plan:
        typer.echo(f"Plan: {result.plan.summary}")
    if result.patch and result.patch.diff:
        typer.echo(f"Files touched: {', '.join(result.patch.files_touched) or '(none)'}")
    if result.error:
        typer.secho(f"Error: {result.error}", fg=typer.colors.RED, err=True)

    raise typer.Exit(code=0 if result.success else 1)


@app.command("index")
def index_cmd(
    repo_path: Path = typer.Argument(..., help="Repository root to index."),
) -> None:
    """Build and print a tree-sitter symbol index for a local repo (no LLM)."""
    from autopatch.mcp_tools.codebase_tool import CodebaseTools

    tools = CodebaseTools(repo_path)
    count = tools.build_index()
    symbols = [s.to_dict() for s in tools.index.symbols[:50]]
    typer.echo(json.dumps({"symbol_count": count, "sample": symbols}, indent=2))


@app.command("mcp")
def mcp_cmd(
    server: str = typer.Argument(
        ...,
        help="MCP server to run: filesystem | sandbox | github | codebase",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root for filesystem/codebase/sandbox servers.",
    ),
) -> None:
    """Run an AutoPatch MCP tool server over stdio."""
    settings = get_settings()
    if server == "filesystem":
        from autopatch.mcp_tools.filesystem_tool import create_filesystem_mcp_server

        create_filesystem_mcp_server(workspace).run()
    elif server == "codebase":
        from autopatch.mcp_tools.codebase_tool import create_codebase_mcp_server

        create_codebase_mcp_server(workspace).run()
    elif server == "github":
        from autopatch.mcp_tools.github_tool import create_github_mcp_server

        create_github_mcp_server(token=settings.github_token).run()
    elif server == "sandbox":
        from autopatch.mcp_tools.sandbox_tool import create_sandbox_mcp_server
        from autopatch.sandbox.docker_runner import DockerRunner

        runner = DockerRunner(
            image=settings.sandbox_image,
            timeout_seconds=settings.sandbox_timeout_seconds,
            network_disabled=settings.docker_network_disabled,
        )
        create_sandbox_mcp_server(runner, workspace).run()
    else:
        typer.secho(
            f"Unknown server {server!r}. Choose filesystem|sandbox|github|codebase.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
