"""Sandbox verification: apply patch and run tests inside Docker only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autopatch.mcp_tools.sandbox_tool import SandboxTools
from autopatch.sandbox.docker_runner import ExecResult
from autopatch.tracing.logger import StructuredLogger


@dataclass
class VerifyResult:
    """Outcome of sandboxed test execution."""

    passed: bool
    apply_ok: bool
    result: ExecResult
    feedback: str


class Verifier:
    """Runs the act→observe test step inside Docker."""

    def __init__(
        self,
        sandbox: SandboxTools,
        *,
        test_command: str = "python -m pytest -q",
        logger: StructuredLogger | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.test_command = test_command
        self.logger = logger

    def verify(self, patch_text: str) -> VerifyResult:
        result = self.sandbox.apply_patch_and_test(
            patch_text,
            test_command=self.test_command,
        )
        # Heuristic: git apply failures often exit non-zero before tests.
        apply_ok = "FAILED" not in result.stderr and "corrupt patch" not in result.stderr.lower()
        if result.exit_code == 127 or "Neither git nor patch" in result.stderr:
            apply_ok = False

        feedback = result.summary()
        passed = result.ok
        if self.logger:
            self.logger.log(
                "verify_result",
                message="passed" if passed else "failed",
                data={
                    "passed": passed,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "duration_seconds": result.duration_seconds,
                },
                level="info" if passed else "error",
            )
        return VerifyResult(
            passed=passed,
            apply_ok=apply_ok or passed,
            result=result,
            feedback=feedback,
        )

    def verify_host_apply_only(self, workspace: Path, patch_text: str) -> None:
        """Apply patch as pure text on host (no code execution) for dry-runs."""
        from autopatch.sandbox.docker_runner import DockerRunner

        DockerRunner().apply_patch_host_safe(workspace, patch_text)
