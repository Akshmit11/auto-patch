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
    help="AutoPatch — GitHub issue → sandboxed patch → draft PR (human review).",
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
    create_pr: bool = typer.Option(
        False,
        "--create-pr",
        help="After success, open a draft PR (requires issue URL + GITHUB_TOKEN).",
    ),
    pr_base: str | None = typer.Option(
        None,
        "--pr-base",
        help="Base branch for the draft PR (default: repo default branch).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write result JSON to this path.",
    ),
    html_trace: bool = typer.Option(
        False,
        "--html-trace",
        help="Also write an HTML trace viewer next to the JSONL log.",
    ),
) -> None:
    """Run plan → patch → test generation → sandbox verify → capped retries → optional draft PR."""
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

    if create_pr and not issue_url:
        typer.secho(
            "--create-pr requires a GitHub issue URL.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    need_github = issue_url is not None or create_pr
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
        create_pr=create_pr,
        pr_base=pr_base,
    )
    result = agent.run(request)

    out_path = output or (settings.log_dir / f"result-{result.run_id}.json")
    write_result_json(result, out_path)

    if html_trace and logger.log_path is not None:
        from autopatch.tracing.viewer import write_html_report

        html_path = write_html_report(logger.log_path)
        typer.echo(f"HTML trace: {html_path}")

    typer.echo(json.dumps(result.to_dict(), indent=2, default=str))
    typer.secho(
        f"\nRun {result.run_id}: {'SUCCESS' if result.success else 'FAILED'} "
        f"| attempts={len(result.attempts)} "
        f"| cost=${result.cost_usd:.4f} | {result.duration_seconds:.1f}s "
        f"| log={logger.log_path} | result={out_path}",
        fg=typer.colors.GREEN if result.success else typer.colors.RED,
    )
    if result.plan:
        typer.echo(f"Plan: {result.plan.summary}")
    if result.patch and result.patch.diff:
        typer.echo(f"Files touched: {', '.join(result.patch.files_touched) or '(none)'}")
    if result.pr:
        typer.secho(
            f"Draft PR: {result.pr.html_url} (draft={result.pr.draft})",
            fg=typer.colors.CYAN,
        )
    if result.error:
        typer.secho(f"Error: {result.error}", fg=typer.colors.RED, err=True)

    raise typer.Exit(code=0 if result.success else 1)


@app.command("trace")
def trace_cmd(
    log_path: Path = typer.Argument(..., help="Path to a run-*.jsonl log file."),
    html: bool = typer.Option(
        False,
        "--html",
        help="Write an HTML report (default path: same name with .html).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="HTML output path (implies --html).",
    ),
    max_events: int = typer.Option(
        200,
        "--max-events",
        help="Max events to show in the terminal view.",
    ),
) -> None:
    """View a structured JSONL run trace in the terminal (and optionally as HTML)."""
    from autopatch.tracing.viewer import format_terminal, load_events, write_html_report

    if not log_path.is_file():
        typer.secho(f"Log not found: {log_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    events = load_events(log_path)
    typer.echo(format_terminal(events, max_events=max_events))

    if html or output is not None:
        html_path = write_html_report(log_path, output)
        typer.secho(f"\nHTML report written to {html_path}", fg=typer.colors.GREEN)


@app.command("pr")
def pr_cmd(
    action: str = typer.Argument(
        ...,
        help="Action: ready — promote a draft PR to ready-for-review (never merges).",
    ),
    pr_url: str = typer.Argument(..., help="GitHub pull request URL."),
) -> None:
    """Human review gate helpers for AutoPatch draft PRs."""
    if action not in {"ready", "promote", "ready-for-review"}:
        typer.secho(
            f"Unknown pr action {action!r}. Use: autopatch pr ready <pr-url>",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    settings = get_settings()
    try:
        settings.require_for_run(need_github=True, need_llm=False)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    from autopatch.mcp_tools.github_tool import GitHubTools

    tools = GitHubTools(token=settings.github_token)
    try:
        result = tools.mark_ready_for_review(pr_url)
    except Exception as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(result.to_dict(), indent=2))
    if result.draft:
        typer.secho(
            "PR is still marked draft — check permissions / GitHub API response.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            f"Promoted to ready for review: {result.html_url}",
            fg=typer.colors.GREEN,
        )


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
