"""Planning step: structured plan before any code is written."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from autopatch.llm.provider import LLMMessage, LLMProvider
from autopatch.tracing.logger import StructuredLogger

PLAN_SYSTEM = """You are AutoPatch's planning module for a coding agent.
Given a GitHub issue and retrieved codebase context, produce a concise structured plan.
Do NOT write code or diffs. Output ONLY valid JSON matching this schema:
{
  "summary": "one-sentence approach",
  "files_to_touch": ["relative/path.py"],
  "approach": ["step 1", "step 2"],
  "risks": ["optional risk notes"],
  "test_plan": "how we will verify",
  "issue_clarity": "clear" | "vague",
  "clarification_needed": null or "what is unclear"
}
Rules:
- Touch as few files as possible (localized fix).
- Prefer existing test locations.
- If the issue is too vague to implement safely, set issue_clarity to "vague".
"""


@dataclass
class Plan:
    """Structured plan produced before patch generation."""

    summary: str
    files_to_touch: list[str] = field(default_factory=list)
    approach: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    test_plan: str = ""
    issue_clarity: str = "clear"
    clarification_needed: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @property
    def is_vague(self) -> bool:
        return self.issue_clarity.lower() == "vague" or bool(self.clarification_needed)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # Prefer fenced block
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        parsed: object = json.loads(fence.group(1))
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"Could not parse plan JSON from model output: {text[:300]}")
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Plan JSON must be an object")
    return parsed


class Planner:
    """LLM-backed planner with explainability logging."""

    def __init__(self, provider: LLMProvider, logger: StructuredLogger | None = None) -> None:
        self.provider = provider
        self.logger = logger

    def plan(
        self,
        *,
        issue_text: str,
        context_bundle: dict[str, Any],
        max_files: int = 5,
    ) -> Plan:
        file_list = context_bundle.get("file_list") or list(
            (context_bundle.get("files") or {}).keys()
        )
        symbols = context_bundle.get("symbols") or []
        files = context_bundle.get("files") or {}

        context_sections: list[str] = []
        context_sections.append("## Retrieved symbols\n" + json.dumps(symbols[:25], indent=2))
        context_sections.append("## Candidate files\n" + "\n".join(f"- {p}" for p in file_list))
        for path, content in list(files.items())[:8]:
            context_sections.append(f"## File: {path}\n```python\n{content}\n```")

        user_prompt = (
            f"## Issue\n{issue_text}\n\n"
            f"## Constraints\n- max files to touch: {max_files}\n"
            f"- language: Python\n\n"
            + "\n\n".join(context_sections)
            + "\n\nProduce the JSON plan now."
        )

        response = self.provider.complete(
            [LLMMessage(role="user", content=user_prompt)],
            purpose="plan",
            max_tokens=4096,
            system=PLAN_SYSTEM,
        )
        raw = _extract_json(response.content)
        plan = Plan(
            summary=str(raw.get("summary") or ""),
            files_to_touch=[str(p) for p in (raw.get("files_to_touch") or [])][:max_files],
            approach=[str(s) for s in (raw.get("approach") or [])],
            risks=[str(s) for s in (raw.get("risks") or [])],
            test_plan=str(raw.get("test_plan") or ""),
            issue_clarity=str(raw.get("issue_clarity") or "clear"),
            clarification_needed=raw.get("clarification_needed"),
            raw_json=raw,
        )
        if self.logger:
            self.logger.log(
                "plan_created",
                message=plan.summary,
                data=plan.to_dict(),
            )
        return plan
