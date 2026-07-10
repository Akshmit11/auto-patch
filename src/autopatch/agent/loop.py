"""Hand-rolled agent loop: plan → act → observe (no retry on Day 1)."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autopatch.agent.patcher import Patcher, PatchResult
from autopatch.agent.planner import Plan, Planner
from autopatch.agent.verifier import Verifier, VerifyResult
from autopatch.config import Settings
from autopatch.llm.provider import LLMProvider, create_provider
from autopatch.mcp_tools.codebase_tool import CodebaseTools
from autopatch.mcp_tools.filesystem_tool import FilesystemTools
from autopatch.mcp_tools.github_tool import GitHubTools, IssueData
from autopatch.mcp_tools.sandbox_tool import SandboxTools
from autopatch.sandbox.docker_runner import DockerRunner
from autopatch.tracing.logger import StructuredLogger


@dataclass
class RunRequest:
    """Inputs for a single agent run."""

    issue_url: str | None = None
    issue_title: str | None = None
    issue_body: str | None = None
    repo_path: Path | None = None  # local repo (skip clone)
    repo_clone_url: str | None = None  # optional override
    test_command: str = "python -m pytest -q"
    skip_sandbox: bool = False  # unit/dev dry-run only
    work_subdir: str | None = None


@dataclass
class AgentResult:
    """Final outcome of Day-1 plan → patch → test loop."""

    success: bool
    run_id: str
    issue_text: str
    plan: Plan | None
    patch: PatchResult | None
    verify: VerifyResult | None
    workspace: Path | None
    cost_usd: float
    duration_seconds: float
    error: str | None = None
    events_summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "run_id": self.run_id,
            "issue_text": self.issue_text[:500],
            "plan": self.plan.to_dict() if self.plan else None,
            "patch": {
                "files_touched": self.patch.files_touched if self.patch else [],
                "rejected": self.patch.rejected if self.patch else None,
                "reject_reason": self.patch.reject_reason if self.patch else None,
                "diff": self.patch.diff if self.patch else None,
            },
            "verify": {
                "passed": self.verify.passed if self.verify else None,
                "feedback": self.verify.feedback if self.verify else None,
            },
            "workspace": str(self.workspace) if self.workspace else None,
            "cost_usd": self.cost_usd,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


class AgentLoop:
    """Core orchestration: ingest → index → plan → patch → sandbox test.

    Day 1: single pass (no retry). Day 2 will add capped retries + test gen + PR.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        provider: LLMProvider | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger or StructuredLogger(settings.log_dir)
        self.provider = provider or create_provider(settings, logger=self.logger)
        self.planner = Planner(self.provider, logger=self.logger)
        self.patcher = Patcher(
            self.provider,
            max_files=settings.max_files_per_patch,
            logger=self.logger,
        )
        self.github = GitHubTools(token=settings.github_token, logger=self.logger)

    def run(self, request: RunRequest) -> AgentResult:
        started = time.monotonic()
        self.settings.ensure_dirs()
        self.logger.log(
            "run_started",
            message="Day-1 plan→patch→test",
            data={
                "issue_url": request.issue_url,
                "repo_path": str(request.repo_path) if request.repo_path else None,
            },
        )

        plan: Plan | None = None
        patch: PatchResult | None = None
        verify: VerifyResult | None = None
        workspace: Path | None = None
        issue_text = ""

        try:
            issue_text, issue_data = self._ingest_issue(request)
            workspace = self._prepare_workspace(request, issue_data)

            fs = FilesystemTools(workspace, logger=self.logger)
            codebase = CodebaseTools(workspace, logger=self.logger)
            symbol_count = codebase.build_index()
            self.logger.log(
                "index_built",
                message=f"{symbol_count} symbols",
                data={"workspace": str(workspace), "symbol_count": symbol_count},
            )

            context = codebase.get_context_bundle(
                issue_text,
                max_files=min(8, self.settings.max_files_per_patch + 3),
            )
            # Ensure plan files are readable via fs tool for logging parity
            _ = fs.list_dir(".")

            plan = self.planner.plan(
                issue_text=issue_text,
                context_bundle=context,
                max_files=self.settings.max_files_per_patch,
            )
            if plan.is_vague:
                self.logger.finish(status="rejected_vague")
                return AgentResult(
                    success=False,
                    run_id=self.logger.run_id,
                    issue_text=issue_text,
                    plan=plan,
                    patch=None,
                    verify=None,
                    workspace=workspace,
                    cost_usd=self.logger.trace.usage.cost_usd,
                    duration_seconds=time.monotonic() - started,
                    error=plan.clarification_needed
                    or "Issue is too vague; clarification needed before patching.",
                )

            patch = self.patcher.generate(
                issue_text=issue_text,
                plan=plan,
                context_bundle=context,
            )
            if patch.rejected or not patch.diff:
                self.logger.finish(status="patch_rejected")
                return AgentResult(
                    success=False,
                    run_id=self.logger.run_id,
                    issue_text=issue_text,
                    plan=plan,
                    patch=patch,
                    verify=None,
                    workspace=workspace,
                    cost_usd=self.logger.trace.usage.cost_usd,
                    duration_seconds=time.monotonic() - started,
                    error=patch.reject_reason or "Empty patch",
                )

            # Persist patch for inspection
            patch_out = workspace / ".autopatch_generated.diff"
            patch_out.write_text(patch.diff, encoding="utf-8")

            if request.skip_sandbox:
                self.logger.log(
                    "sandbox_skipped",
                    message="skip_sandbox=True (dev/dry-run)",
                )
                self.logger.finish(status="completed_no_sandbox")
                return AgentResult(
                    success=True,
                    run_id=self.logger.run_id,
                    issue_text=issue_text,
                    plan=plan,
                    patch=patch,
                    verify=None,
                    workspace=workspace,
                    cost_usd=self.logger.trace.usage.cost_usd,
                    duration_seconds=time.monotonic() - started,
                )

            runner = DockerRunner(
                image=self.settings.sandbox_image,
                timeout_seconds=self.settings.sandbox_timeout_seconds,
                network_disabled=self.settings.docker_network_disabled,
                logger=self.logger,
            )
            sandbox = SandboxTools(runner, workspace, logger=self.logger)
            verifier = Verifier(
                sandbox,
                test_command=request.test_command,
                logger=self.logger,
            )
            verify = verifier.verify(patch.diff)
            status = "completed" if verify.passed else "tests_failed"
            self.logger.finish(status=status)
            return AgentResult(
                success=verify.passed,
                run_id=self.logger.run_id,
                issue_text=issue_text,
                plan=plan,
                patch=patch,
                verify=verify,
                workspace=workspace,
                cost_usd=self.logger.trace.usage.cost_usd,
                duration_seconds=time.monotonic() - started,
                error=None if verify.passed else "Sandbox tests failed",
            )
        except Exception as exc:
            self.logger.log("run_error", message=str(exc), level="error")
            self.logger.finish(status="error")
            return AgentResult(
                success=False,
                run_id=self.logger.run_id,
                issue_text=issue_text,
                plan=plan,
                patch=patch,
                verify=verify,
                workspace=workspace,
                cost_usd=self.logger.trace.usage.cost_usd,
                duration_seconds=time.monotonic() - started,
                error=str(exc),
            )

    def _ingest_issue(self, request: RunRequest) -> tuple[str, IssueData | None]:
        if request.issue_url:
            data = self.github.get_issue(request.issue_url)
            return data.as_prompt_text(), data
        title = request.issue_title or "Local issue"
        body = request.issue_body or ""
        text = f"Title: {title}\n\nBody:\n{body}"
        return text, None

    def _prepare_workspace(
        self,
        request: RunRequest,
        issue_data: IssueData | None,
    ) -> Path:
        run_root = self.settings.work_dir / (request.work_subdir or self.logger.run_id)
        run_root.mkdir(parents=True, exist_ok=True)

        if request.repo_path is not None:
            src = request.repo_path.resolve()
            if not src.is_dir():
                raise FileNotFoundError(f"repo_path does not exist: {src}")
            dest = run_root / "repo"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(
                src,
                dest,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    ".mypy_cache",
                    ".ruff_cache",
                    "node_modules",
                    ".autopatch",
                ),
            )
            # Re-init git so git apply works inside sandbox
            _git_init_snapshot(dest)
            return dest

        if issue_data is not None:
            dest = run_root / "repo"
            self.github.clone_repo(issue_data.ref.owner, issue_data.ref.repo, dest)
            return dest

        raise ValueError("Provide issue_url or repo_path for a runnable workspace")


def _git_init_snapshot(repo: Path) -> None:
    """Create a minimal git repo so `git apply` works after copytree without .git."""
    import subprocess

    try:
        subprocess.run(
            ["git", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "autopatch@local"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "AutoPatch"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "autopatch snapshot", "--allow-empty"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Host without git: pure-text apply path still works inside Docker after apt install.
        pass


def write_result_json(result: AgentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
