"""Structured logger + cost estimation tests."""

from __future__ import annotations

from pathlib import Path

from autopatch.tracing.logger import StructuredLogger, estimate_cost_usd


def test_estimate_cost_sonnet() -> None:
    cost = estimate_cost_usd("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 18.0


def test_logger_accumulates_usage(tmp_path: Path) -> None:
    logger = StructuredLogger(tmp_path)
    logger.log_model_call(
        model="claude-sonnet-4-6",
        purpose="plan",
        input_tokens=1000,
        output_tokens=500,
    )
    logger.log_tool_call("fs_read_file", arguments={"path": "a.py"}, result_summary="ok")
    trace = logger.finish("completed")
    assert trace.usage.input_tokens == 1000
    assert trace.usage.output_tokens == 500
    assert trace.usage.cost_usd > 0
    assert logger.log_path is not None
    assert logger.log_path.exists()
