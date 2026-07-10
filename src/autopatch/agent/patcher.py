"""Patch generation: unified diffs only (no full-file rewrites)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from autopatch.agent.planner import Plan
from autopatch.llm.provider import LLMMessage, LLMProvider
from autopatch.tracing.logger import StructuredLogger

PATCH_SYSTEM = """You are AutoPatch's patch generator.
Generate a unified diff (git-style) that implements the plan for the issue.
Rules:
- Output ONLY a unified diff (optional ```diff fence is OK).
- Do NOT rewrite entire large files if a small change works.
- Paths in the diff must be repository-relative (e.g. src/foo.py).
- Use --- a/path and +++ b/path headers.
- Prefer minimal, reviewable changes.
- Prefer including at least one pytest that covers the issue (tests/test_*.py).
- Do not touch more files than listed in the plan unless strictly necessary.
"""


@dataclass
class PatchResult:
    """Generated patch plus guardrail metadata."""

    diff: str
    files_touched: list[str]
    rejected: bool = False
    reject_reason: str | None = None


def extract_unified_diff(text: str) -> str:
    """Pull a unified diff out of model output (fenced or raw)."""
    text = text.strip()
    fence = re.search(r"```(?:diff|patch)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    # Find first diff header
    for marker in ("diff --git ", "--- "):
        idx = text.find(marker)
        if idx >= 0:
            text = text[idx:]
            break
    if "--- " not in text or "+++ " not in text:
        raise ValueError("Model output did not contain a unified diff with ---/+++ headers")
    return text.strip() + ("\n" if not text.endswith("\n") else "")


def files_in_diff(diff: str) -> list[str]:
    """Extract target file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if "\t" in path:
                path = path.split("\t", 1)[0]
            if path.startswith("b/"):
                path = path[2:]
            if path == "/dev/null":
                continue
            if path not in seen:
                seen.add(path)
                files.append(path)
    return files


class Patcher:
    """LLM-backed unified-diff generator with file-count guardrail."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_files: int = 5,
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
        failure_feedback: str | None = None,
    ) -> PatchResult:
        files = context_bundle.get("files") or {}
        sections = [
            f"## Issue\n{issue_text}",
            f"## Plan\n{json_dumps_plan(plan)}",
        ]
        for path in plan.files_to_touch:
            if path in files:
                sections.append(f"## File: {path}\n```python\n{files[path]}\n```")
        # Include any other retrieved files not listed
        for path, content in files.items():
            if path not in plan.files_to_touch:
                sections.append(f"## File: {path}\n```python\n{content}\n```")

        if failure_feedback:
            sections.append(f"## Previous attempt failed\n{failure_feedback}")

        sections.append(
            f"\nGenerate a unified diff touching at most {self.max_files} files."
        )
        response = self.provider.complete(
            [LLMMessage(role="user", content="\n\n".join(sections))],
            purpose="patch",
            max_tokens=8192,
            system=PATCH_SYSTEM,
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
                self.logger.log("patch_rejected", message=str(exc), level="error")
            return result

        touched = files_in_diff(diff)
        if len(touched) > self.max_files:
            reason = (
                f"Patch touches {len(touched)} files (cap is {self.max_files}): "
                + ", ".join(touched)
            )
            result = PatchResult(
                diff=diff,
                files_touched=touched,
                rejected=True,
                reject_reason=reason,
            )
            if self.logger:
                self.logger.log("patch_rejected", message=reason, level="error", data={"files": touched})
            return result

        result = PatchResult(diff=diff, files_touched=touched)
        if self.logger:
            self.logger.log(
                "patch_generated",
                message=f"{len(touched)} files",
                data={"files": touched, "diff_chars": len(diff)},
            )
        return result


def json_dumps_plan(plan: Plan) -> str:
    import json

    return json.dumps(
        {
            "summary": plan.summary,
            "files_to_touch": plan.files_to_touch,
            "approach": plan.approach,
            "test_plan": plan.test_plan,
        },
        indent=2,
    )


def merge_unified_diffs(*diffs: str) -> str:
    """Concatenate non-empty unified diffs into one multi-file patch text.

    Git apply accepts multiple file hunks in sequence. Empty / whitespace-only
    inputs are skipped. Does not attempt semantic conflict resolution.
    """
    parts: list[str] = []
    for diff in diffs:
        text = (diff or "").strip()
        if not text:
            continue
        if not text.endswith("\n"):
            text += "\n"
        parts.append(text)
    if not parts:
        return ""
    return "".join(parts)


def combine_patch_results(*results: PatchResult) -> PatchResult:
    """Merge multiple PatchResults, rejecting if any component was rejected."""
    for result in results:
        if result.rejected:
            return result
    merged_diff = merge_unified_diffs(*(r.diff for r in results))
    if not merged_diff:
        return PatchResult(diff="", files_touched=[], rejected=True, reject_reason="Empty combined patch")
    touched = files_in_diff(merged_diff)
    return PatchResult(diff=merged_diff, files_touched=touched)
