"""Trace viewer unit tests."""

from __future__ import annotations

from pathlib import Path

from autopatch.tracing.logger import StructuredLogger
from autopatch.tracing.viewer import (
    format_terminal,
    load_events,
    render_html,
    summarize_trace,
    write_html_report,
)


def test_load_and_summarize(tmp_path: Path) -> None:
    logger = StructuredLogger(tmp_path)
    logger.log_model_call(
        model="claude-sonnet-4-6",
        purpose="plan",
        input_tokens=100,
        output_tokens=50,
    )
    logger.log_tool_call("sandbox_exec", arguments={"command": "pytest"}, result_summary="ok")
    logger.log(
        "retry",
        message="attempt 1 failed",
        level="warning",
        data={"attempt": 1, "cost_delta_usd": 0.01},
    )
    logger.finish("completed")
    assert logger.log_path is not None

    events = load_events(logger.log_path)
    summary = summarize_trace(events)
    assert summary["run_id"] == logger.run_id
    assert summary["status"] == "completed"
    assert summary["model_calls"] >= 1
    assert summary["tool_calls"] >= 1
    assert summary["retries"] >= 1
    assert summary["cost_usd"] > 0

    text = format_terminal(events)
    assert logger.run_id in text
    assert "model_call" in text

    html = render_html(events)
    assert "<!DOCTYPE html>" in html
    assert logger.run_id in html
    assert "model_call" in html

    out = write_html_report(logger.log_path)
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
