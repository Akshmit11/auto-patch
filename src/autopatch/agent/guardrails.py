"""Agent guardrails: vague-issue rejection, file caps, timeouts, retry limits."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from autopatch.agent.patcher import files_in_diff
from autopatch.agent.planner import Plan


class GuardrailError(Exception):
    """Raised when a hard guardrail rejects the run or attempt."""

    def __init__(self, reason: str, *, code: str = "guardrail") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


# Heuristic: very short bodies without concrete symbols often mean "vague".
_VAGUE_TITLE_RE = re.compile(
    r"^(fix|improve|update|refactor|handle|something|misc|cleanup)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardrailConfig:
    """Configurable safety limits for a single agent run."""

    max_files_per_patch: int = 5
    max_retries: int = 3
    sandbox_timeout_seconds: int = 300
    run_timeout_seconds: int = 1800


class RunDeadline:
    """Wall-clock deadline for an entire agent run."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.started_at = time.monotonic()

    def remaining(self) -> float:
        return self.timeout_seconds - (time.monotonic() - self.started_at)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def check(self) -> None:
        if self.remaining() <= 0:
            raise GuardrailError(
                f"Run exceeded overall timeout of {self.timeout_seconds}s",
                code="run_timeout",
            )


def issue_looks_vague(*, title: str, body: str) -> str | None:
    """Return a clarification message if the issue is too thin to implement safely.

    Used as a pre-LLM filter. The planner can still mark issues vague after retrieval.
    """
    title = (title or "").strip()
    body = (body or "").strip()
    # Strip common markdown noise for length checks
    body_plain = re.sub(r"[#*`>\-\d\.]", " ", body)
    body_plain = re.sub(r"\s+", " ", body_plain).strip()

    if not title and len(body_plain) < 40:
        return "Issue has no title and almost no body — please describe the bug or desired change."
    if len(body_plain) < 20 and _VAGUE_TITLE_RE.match(title):
        return (
            f"Issue title {title!r} is vague and the body is too short "
            f"({len(body_plain)} chars). Add expected vs actual behavior, steps, or a failing test."
        )
    if len(body_plain) < 8 and len(title) < 12:
        return "Issue is too short to implement safely. Add more detail before re-running."
    return None


def enforce_plan_clarity(plan: Plan) -> None:
    """Reject plans the model marked as too vague."""
    if plan.is_vague:
        raise GuardrailError(
            plan.clarification_needed
            or "Issue is too vague; clarification needed before patching.",
            code="vague_issue",
        )


def enforce_file_cap(diff: str, max_files: int) -> list[str]:
    """Return touched files or raise if the diff exceeds the file-count cap."""
    touched = files_in_diff(diff)
    if len(touched) > max_files:
        raise GuardrailError(
            f"Patch touches {len(touched)} files (cap is {max_files}): " + ", ".join(touched),
            code="max_files",
        )
    return touched


def enforce_retry_budget(attempt: int, max_retries: int) -> None:
    """``attempt`` is 1-based; allow attempts 1..max_retries+1 (initial + retries)."""
    max_attempts = max_retries + 1
    if attempt > max_attempts:
        raise GuardrailError(
            f"Exceeded max attempts ({max_attempts} = 1 + {max_retries} retries)",
            code="max_retries",
        )


def is_test_path(path: str) -> bool:
    """Heuristic: path looks like a test module or under a tests/ tree."""
    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{normalized}"
        or "/test/" in f"/{normalized}"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def patch_includes_tests(files_touched: list[str]) -> bool:
    return any(is_test_path(p) for p in files_touched)
