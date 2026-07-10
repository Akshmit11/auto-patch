"""Hand-rolled agent loop: plan → act → observe → retry (capped)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autopatch.agent.guardrails import (
    GuardrailConfig,
    GuardrailError,
    RunDeadline,
    enforce_plan_clarity,
    enforce_retry_budget,
    issue_looks_vague,
    patch_includes_tests,
)
from autopatch.agent.patcher import Patcher, PatchResult, combine_patch_results
from autopatch.agent.planner import Plan, Planner
from autopatch.agent.test_generator import TestGenerator
from autopatch.agent.verifier import Verifier, VerifyResult
from autopatch.config import Settings
from autopatch.llm.provider import LLMProvider, create_provider
from autopatch.mcp_tools.codebase_tool import CodebaseTools
from autopatch.mcp_tools.filesystem_tool import FilesystemTools
from autopatch.mcp_tools.github_tool import (
    GitHubTools,
    IssueData,
    PullRequestResult,
    build_pr_body,
)
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
    create_pr: bool = False  # open draft PR after success (requires issue_url + token)
    pr_base: str | None = None  # base branch override
    branch_prefix: str = "autopatch"


@dataclass
class AttemptRecord:
    """One plan→patch→verify attempt (for logging / PR body)."""

    attempt: int
    success: bool
    failure_reason: str | None = None
    files_touched: list[str] = field(default_factory=list)
    cost_usd_after: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "success": self.success,
            "failure_reason": self.failure_reason,
            "files_touched": self.files_touched,
            "cost_usd_after": self.cost_usd_after,
        }


@dataclass
class AgentResult:
    """Final outcome of the plan → patch → test → (retry) → (draft PR) loop."""

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
    attempts: list[AttemptRecord] = field(default_factory=list)
    pr: PullRequestResult | None = None
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
            "attempts": [a.to_dict() for a in self.attempts],
            "pr": self.pr.to_dict() if self.pr else None,
        }


class AgentLoop:
    """Core orchestration: ingest → index → plan → patch → test gen → sandbox → retry → draft PR."""

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
        self.test_generator = TestGenerator(
            self.provider,
            max_files=min(3, settings.max_files_per_patch),
            logger=self.logger,
        )
        self.github = GitHubTools(token=settings.github_token, logger=self.logger)
        self.guardrails = GuardrailConfig(
            max_files_per_patch=settings.max_files_per_patch,
            max_retries=settings.max_retries,
            sandbox_timeout_seconds=settings.sandbox_timeout_seconds,
            run_timeout_seconds=settings.run_timeout_seconds,
        )

    def run(self, request: RunRequest) -> AgentResult:
        started = time.monotonic()
        deadline = RunDeadline(self.guardrails.run_timeout_seconds)
        self.settings.ensure_dirs()
        self.logger.log(
            "run_started",
            message="plan→patch→test→retry",
            data={
                "issue_url": request.issue_url,
                "repo_path": str(request.repo_path) if request.repo_path else None,
                "max_retries": self.guardrails.max_retries,
                "create_pr": request.create_pr,
            },
        )

        plan: Plan | None = None
        patch: PatchResult | None = None
        verify: VerifyResult | None = None
        workspace: Path | None = None
        issue_text = ""
        issue_data: IssueData | None = None
        attempts: list[AttemptRecord] = []
        pr_result: PullRequestResult | None = None

        try:
            deadline.check()
            issue_text, issue_data = self._ingest_issue(request)
            self._precheck_vague(request, issue_data)

            workspace = self._prepare_workspace(request, issue_data)
            deadline.check()

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
            _ = fs.list_dir(".")

            plan = self.planner.plan(
                issue_text=issue_text,
                context_bundle=context,
                max_files=self.settings.max_files_per_patch,
            )
            try:
                enforce_plan_clarity(plan)
            except GuardrailError as exc:
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
                    error=exc.reason,
                    attempts=attempts,
                )

            max_attempts = self.guardrails.max_retries + 1
            failure_feedback: str | None = None
            last_error: str | None = None

            for attempt in range(1, max_attempts + 1):
                deadline.check()
                enforce_retry_budget(attempt, self.guardrails.max_retries)
                cost_before = self.logger.trace.usage.cost_usd

                self.logger.log(
                    "attempt_started",
                    message=f"attempt {attempt}/{max_attempts}",
                    data={
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "cost_usd_before": round(cost_before, 6),
                        "has_failure_feedback": bool(failure_feedback),
                    },
                )

                # Clean tree so retries apply against baseline
                if attempt > 1:
                    _reset_workspace(workspace)

                code_patch = self.patcher.generate(
                    issue_text=issue_text,
                    plan=plan,
                    context_bundle=context,
                    failure_feedback=failure_feedback,
                )
                if code_patch.rejected or not code_patch.diff:
                    reason = code_patch.reject_reason or "Empty patch"
                    last_error = reason
                    attempts.append(
                        AttemptRecord(
                            attempt=attempt,
                            success=False,
                            failure_reason=reason,
                            cost_usd_after=self.logger.trace.usage.cost_usd,
                        )
                    )
                    self._log_retry(attempt, reason, cost_before)
                    failure_feedback = reason
                    continue

                # Ensure at least one test covers the issue
                if patch_includes_tests(code_patch.files_touched):
                    combined = code_patch
                else:
                    test_patch = self.test_generator.generate(
                        issue_text=issue_text,
                        plan=plan,
                        context_bundle=context,
                        code_diff=code_patch.diff,
                        failure_feedback=failure_feedback,
                    )
                    if test_patch.rejected or not test_patch.diff:
                        # Soft-fail: proceed with code-only but log (still prefer tests)
                        self.logger.log(
                            "test_generation_skipped",
                            message=test_patch.reject_reason or "no test diff",
                            level="warning",
                        )
                        combined = code_patch
                    else:
                        combined = combine_patch_results(code_patch, test_patch)
                        if len(combined.files_touched) > self.guardrails.max_files_per_patch:
                            reason = (
                                f"Combined patch touches {len(combined.files_touched)} files "
                                f"(cap is {self.guardrails.max_files_per_patch})"
                            )
                            last_error = reason
                            attempts.append(
                                AttemptRecord(
                                    attempt=attempt,
                                    success=False,
                                    failure_reason=reason,
                                    files_touched=combined.files_touched,
                                    cost_usd_after=self.logger.trace.usage.cost_usd,
                                )
                            )
                            self._log_retry(attempt, reason, cost_before)
                            failure_feedback = reason
                            continue

                patch = combined
                patch_out = workspace / ".autopatch_generated.diff"
                patch_out.write_text(patch.diff, encoding="utf-8")

                if request.skip_sandbox:
                    attempts.append(
                        AttemptRecord(
                            attempt=attempt,
                            success=True,
                            files_touched=patch.files_touched,
                            cost_usd_after=self.logger.trace.usage.cost_usd,
                        )
                    )
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
                        attempts=attempts,
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

                if verify.passed:
                    attempts.append(
                        AttemptRecord(
                            attempt=attempt,
                            success=True,
                            files_touched=patch.files_touched,
                            cost_usd_after=self.logger.trace.usage.cost_usd,
                        )
                    )
                    self.logger.log(
                        "attempt_succeeded",
                        message=f"attempt {attempt} passed",
                        data={"attempt": attempt},
                    )

                    if request.create_pr:
                        pr_result = self._open_draft_pr(
                            request=request,
                            issue_data=issue_data,
                            workspace=workspace,
                            plan=plan,
                            patch=patch,
                            verify=verify,
                            attempts=attempts,
                        )

                    self.logger.finish(status="completed")
                    return AgentResult(
                        success=True,
                        run_id=self.logger.run_id,
                        issue_text=issue_text,
                        plan=plan,
                        patch=patch,
                        verify=verify,
                        workspace=workspace,
                        cost_usd=self.logger.trace.usage.cost_usd,
                        duration_seconds=time.monotonic() - started,
                        attempts=attempts,
                        pr=pr_result,
                    )

                # Tests failed — feed back into next attempt
                reason = verify.feedback or "Sandbox tests failed"
                last_error = reason
                attempts.append(
                    AttemptRecord(
                        attempt=attempt,
                        success=False,
                        failure_reason=reason[:2000],
                        files_touched=patch.files_touched,
                        cost_usd_after=self.logger.trace.usage.cost_usd,
                    )
                )
                self._log_retry(attempt, reason, cost_before)
                failure_feedback = f"Attempt {attempt} failed sandbox verification.\n\n{reason}"

            self.logger.finish(status="tests_failed")
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
                error=last_error or "All retry attempts exhausted",
                attempts=attempts,
            )

        except GuardrailError as exc:
            self.logger.log("guardrail", message=exc.reason, level="error", data={"code": exc.code})
            self.logger.finish(status=exc.code)
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
                error=exc.reason,
                attempts=attempts,
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
                attempts=attempts,
                pr=pr_result,
            )

    def _log_retry(self, attempt: int, reason: str, cost_before: float) -> None:
        cost_after = self.logger.trace.usage.cost_usd
        self.logger.log(
            "retry",
            message=f"attempt {attempt} failed",
            level="warning",
            data={
                "attempt": attempt,
                "failure_reason": reason[:1500],
                "cost_usd_before": round(cost_before, 6),
                "cost_usd_after": round(cost_after, 6),
                "cost_delta_usd": round(cost_after - cost_before, 6),
            },
        )

    def _precheck_vague(self, request: RunRequest, issue_data: IssueData | None) -> None:
        if issue_data is not None:
            title, body = issue_data.title, issue_data.body
        else:
            title = request.issue_title or ""
            body = request.issue_body or ""
        reason = issue_looks_vague(title=title, body=body)
        if reason:
            raise GuardrailError(reason, code="vague_issue")

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
            _git_init_snapshot(dest)
            return dest

        if issue_data is not None:
            dest = run_root / "repo"
            self.github.clone_repo(issue_data.ref.owner, issue_data.ref.repo, dest)
            return dest

        raise ValueError("Provide issue_url or repo_path for a runnable workspace")

    def _open_draft_pr(
        self,
        *,
        request: RunRequest,
        issue_data: IssueData | None,
        workspace: Path,
        plan: Plan,
        patch: PatchResult,
        verify: VerifyResult | None,
        attempts: list[AttemptRecord],
    ) -> PullRequestResult | None:
        if issue_data is None:
            self.logger.log(
                "pr_skipped",
                message="create_pr requires a GitHub issue URL",
                level="warning",
            )
            return None

        owner = issue_data.ref.owner
        repo = issue_data.ref.repo
        branch = f"{request.branch_prefix}/issue-{issue_data.ref.number}-{self.logger.run_id[:8]}"
        title = f"fix({issue_data.ref.number}): {issue_data.title[:72]}"
        body = build_pr_body(
            issue_url=issue_data.ref.html_url,
            plan_summary=plan.summary,
            plan_approach=plan.approach,
            files_touched=patch.files_touched,
            test_passed=verify.passed if verify else None,
            test_feedback=verify.feedback if verify else None,
            attempts=len(attempts),
            cost_usd=self.logger.trace.usage.cost_usd,
            run_id=self.logger.run_id,
        )

        # Workspace already has the successful patch applied by the sandbox.
        # Strip autopatch helper files before commit.
        for helper in (
            ".autopatch_patch.diff",
            ".autopatch_generated.diff",
        ):
            helper_path = workspace / helper
            if helper_path.exists():
                helper_path.unlink()

        commit_msg = f"fix: resolve issue #{issue_data.ref.number} via AutoPatch\n\n{plan.summary}"
        self.github.push_branch(
            workspace,
            owner=owner,
            repo=repo,
            branch=branch,
            commit_message=commit_msg,
        )
        return self.github.create_draft_pr(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=branch,
            base=request.pr_base,
            issue_number=issue_data.ref.number,
        )


def _reset_workspace(workspace: Path) -> None:
    """Reset working tree to HEAD so the next patch applies on a clean baseline."""
    git_dir = workspace / ".git"
    if not git_dir.exists():
        return
    try:
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _git_init_snapshot(repo: Path) -> None:
    """Create a minimal git repo so `git apply` / reset work after copytree without .git."""
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
