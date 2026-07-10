"""Structured JSON logging and per-run token/cost tracking."""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

# USD per 1M tokens — keep in sync with known public list prices.
# Product default is Claude Sonnet; OpenAI GPT-4.1-class used as swap path.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4o": (2.5, 10.0),
}


@dataclass
class TokenUsage:
    """Token counts and estimated USD cost for a single model call or aggregate."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0

    def add(self, other: TokenUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        if other.model and not self.model:
            self.model = other.model

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from model id and token counts."""
    input_rate, output_rate = MODEL_PRICING_USD_PER_MTOK.get(model, (3.0, 15.0))
    return (input_tokens / 1_000_000.0) * input_rate + (output_tokens / 1_000_000.0) * output_rate


@dataclass
class RunTrace:
    """In-memory summary of a single agent run."""

    run_id: str
    started_at: str
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    ended_at: str | None = None
    status: str = "running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "usage": asdict(self.usage),
            "events": self.events,
        }


class StructuredLogger:
    """Emit one JSON object per line for every tool/model/retry event."""

    def __init__(
        self,
        log_dir: Path | None = None,
        *,
        run_id: str | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.started_at = datetime.now(UTC).isoformat()
        self.trace = RunTrace(run_id=self.run_id, started_at=self.started_at)
        self._stream = stream if stream is not None else sys.stderr
        self._file: TextIO | None = None
        self.log_path: Path | None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / f"run-{self.run_id}.jsonl"
            self._file = path.open("a", encoding="utf-8")
            self.log_path = path
        else:
            self.log_path = None

    def log(
        self,
        event_type: str,
        *,
        message: str = "",
        data: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "level": level,
            "event": event_type,
            "message": message,
            "data": data or {},
        }
        self.trace.events.append(record)
        line = json.dumps(record, default=str, ensure_ascii=False)
        print(line, file=self._stream, flush=True)
        if self._file is not None:
            self._file.write(line + "\n")
            self._file.flush()

    def log_model_call(
        self,
        *,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        extra: dict[str, Any] | None = None,
    ) -> TokenUsage:
        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            cost_usd=cost,
        )
        self.trace.usage.add(usage)
        payload = {
            "model": model,
            "purpose": purpose,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
            "run_cost_usd": round(self.trace.usage.cost_usd, 6),
        }
        if extra:
            payload.update(extra)
        self.log("model_call", message=purpose, data=payload)
        return usage

    def log_tool_call(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        result_summary: str = "",
        success: bool = True,
    ) -> None:
        self.log(
            "tool_call",
            message=tool_name,
            data={
                "tool": tool_name,
                "arguments": arguments or {},
                "result_summary": result_summary,
                "success": success,
            },
            level="info" if success else "error",
        )

    def finish(self, status: str = "completed") -> RunTrace:
        self.trace.status = status
        self.trace.ended_at = datetime.now(UTC).isoformat()
        self.log(
            "run_finished",
            message=status,
            data={
                "status": status,
                "usage": asdict(self.trace.usage),
            },
        )
        if self._file is not None:
            self._file.close()
            self._file = None
        return self.trace
