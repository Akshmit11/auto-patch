"""Generate at least one new/updated test covering the issue."""

from __future__ import annotations

import json
from typing import Any

from autopatch.agent.guardrails import is_test_path
from autopatch.agent.patcher import PatchResult, extract_unified_diff, files_in_diff
from autopatch.agent.planner import Plan
from autopatch.llm.provider import LLMMessage, LLMProvider
from autopatch.tracing.logger import StructuredLogger

TESTGEN_SYSTEM = """You are AutoPatch's test generator.
Write or extend pytest tests that specifically cover the GitHub issue being fixed.
Rules:
- Output ONLY a unified diff (optional ```diff fence is OK).
- Prefer adding tests under existing tests/ directories.
- The test must fail without the fix and pass with the fix (behavioral coverage).
- Do NOT re-implement the production fix in the test file alone.
- Paths must be repository-relative with --- a/path and +++ b/path headers.
- Touch as few files as possible (usually one test file).
- Use pytest style (def test_*).
"""


class TestGenerator:
    """LLM-backed generator for issue-specific tests (unified diff only)."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_files: int = 3,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.provider = provider
        self.max_files = max_files
        self.logger = logger

    def generate(
        self,
        *,
        issue_text: str,
        plan: Plan,
        context_bundle: dict[str, Any],
        code_diff: str,
        failure_feedback: str | None = None,
    ) -> PatchResult:
        files = context_bundle.get("files") or {}
        sections = [
            f"## Issue\n{issue_text}",
            f"## Plan\n{json.dumps({'summary': plan.summary, 'test_plan': plan.test_plan, 'files_to_touch': plan.files_to_touch}, indent=2)}",
            f"## Code patch already generated\n```diff\n{code_diff}\n```",
        ]
        # Prefer existing test files in context
        test_files = [p for p in files if is_test_path(p)]
        for path in test_files[:6]:
            sections.append(f"## File: {path}\n```python\n{files[path]}\n```")
        for path, content in files.items():
            if path not in test_files:
                sections.append(f"## File: {path}\n```python\n{content}\n```")

        if failure_feedback:
            sections.append(f"## Previous attempt failed\n{failure_feedback}")

        sections.append("\nGenerate a unified diff that adds or updates tests covering this issue.")
        response = self.provider.complete(
            [LLMMessage(role="user", content="\n\n".join(sections))],
            purpose="test_generation",
            max_tokens=8192,
            system=TESTGEN_SYSTEM,
        )
        try:
            diff = extract_unified_diff(response.content)
        except ValueError as exc:
            result = PatchResult(
                diff="",
                files_touched=[],
                rejected=True,
                reject_reason=str(exc),
            )
            if self.logger:
                self.logger.log("test_patch_rejected", message=str(exc), level="error")
            return result

        touched = files_in_diff(diff)
        if len(touched) > self.max_files:
            reason = (
                f"Test patch touches {len(touched)} files (cap is {self.max_files}): "
                + ", ".join(touched)
            )
            result = PatchResult(
                diff=diff,
                files_touched=touched,
                rejected=True,
                reject_reason=reason,
            )
            if self.logger:
                self.logger.log(
                    "test_patch_rejected",
                    message=reason,
                    level="error",
                    data={"files": touched},
                )
            return result

        result = PatchResult(diff=diff, files_touched=touched)
        if self.logger:
            self.logger.log(
                "test_patch_generated",
                message=f"{len(touched)} files",
                data={"files": touched, "diff_chars": len(diff)},
            )
        return result
