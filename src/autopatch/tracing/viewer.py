"""Minimal HTML + terminal trace viewer for structured JSONL run logs."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def load_events(path: Path) -> list[dict[str, Any]]:
    """Load JSONL events from a run log file."""
    events: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_no} of {path}: {exc}") from exc
        if isinstance(obj, dict):
            events.append(obj)
    return events


def summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a short summary for terminal display."""
    run_id = ""
    status = "unknown"
    model_calls = 0
    tool_calls = 0
    retries = 0
    cost = 0.0
    levels: dict[str, int] = {}
    for ev in events:
        run_id = str(ev.get("run_id") or run_id)
        et = str(ev.get("event") or "")
        level = str(ev.get("level") or "info")
        levels[level] = levels.get(level, 0) + 1
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if et == "model_call":
            model_calls += 1
            if isinstance(data.get("run_cost_usd"), (int, float)):
                cost = float(data["run_cost_usd"])
            elif isinstance(data.get("cost_usd"), (int, float)):
                cost += float(data["cost_usd"])
        elif et == "tool_call":
            tool_calls += 1
        elif et == "retry":
            retries += 1
        elif et == "run_finished":
            status = str(data.get("status") or ev.get("message") or status)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            if isinstance(usage.get("cost_usd"), (int, float)):
                cost = float(usage["cost_usd"])
    return {
        "run_id": run_id,
        "status": status,
        "events": len(events),
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "retries": retries,
        "cost_usd": round(cost, 6),
        "levels": levels,
    }


def format_terminal(events: list[dict[str, Any]], *, max_events: int = 200) -> str:
    """Render a compact terminal timeline of the run."""
    summary = summarize_trace(events)
    lines = [
        f"AutoPatch trace  run_id={summary['run_id']}  status={summary['status']}",
        f"events={summary['events']}  model_calls={summary['model_calls']}  "
        f"tool_calls={summary['tool_calls']}  retries={summary['retries']}  "
        f"cost=${summary['cost_usd']:.4f}",
        "-" * 72,
    ]
    shown = events[-max_events:] if len(events) > max_events else events
    if len(events) > max_events:
        lines.append(f"... ({len(events) - max_events} earlier events omitted)")
    for ev in shown:
        ts = str(ev.get("ts") or "")[:19]
        level = str(ev.get("level") or "info")
        et = str(ev.get("event") or "")
        msg = str(ev.get("message") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        extra = ""
        if et == "model_call":
            extra = (
                f" model={data.get('model')} in={data.get('input_tokens')} "
                f"out={data.get('output_tokens')} ${data.get('cost_usd', 0)}"
            )
        elif et == "tool_call":
            extra = f" tool={data.get('tool')} ok={data.get('success')}"
        elif et == "retry":
            extra = f" attempt={data.get('attempt')} delta=${data.get('cost_delta_usd', 0)}"
        lines.append(f"{ts}  {level:7}  {et:22}  {msg}{extra}")
    return "\n".join(lines)


def render_html(events: list[dict[str, Any]], *, title: str | None = None) -> str:
    """Build a self-contained HTML page for the trace (no external assets)."""
    summary = summarize_trace(events)
    page_title = title or f"AutoPatch trace {summary['run_id']}"
    rows: list[str] = []
    for idx, ev in enumerate(events):
        ts = html.escape(str(ev.get("ts") or ""))
        level = html.escape(str(ev.get("level") or "info"))
        et = html.escape(str(ev.get("event") or ""))
        msg = html.escape(str(ev.get("message") or ""))
        data_raw = json.dumps(ev.get("data") or {}, indent=2, default=str)
        data_esc = html.escape(data_raw)
        level_class = {
            "error": "lvl-error",
            "warning": "lvl-warn",
            "info": "lvl-info",
        }.get(str(ev.get("level") or "info"), "lvl-info")
        rows.append(
            f'<tr class="{level_class}">'
            f"<td>{idx}</td><td>{ts}</td><td>{level}</td><td>{et}</td>"
            f"<td>{msg}</td><td><pre>{data_esc}</pre></td></tr>"
        )

    body_rows = "\n".join(rows) if rows else "<tr><td colspan=6>(no events)</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(page_title)}</title>
<style>
  :root {{
    --bg: #0f1419;
    --panel: #1a2332;
    --text: #e7ecf3;
    --muted: #8b9bb4;
    --accent: #3d9cf0;
    --error: #f07178;
    --warn: #e6b450;
    --ok: #7fd962;
    --border: #2a3548;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.45;
  }}
  header {{
    padding: 1.25rem 1.5rem;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #152033 0%, var(--bg) 100%);
  }}
  h1 {{ margin: 0 0 0.35rem; font-size: 1.35rem; font-weight: 600; }}
  .meta {{ color: var(--muted); font-size: 0.9rem; display: flex; flex-wrap: wrap; gap: 1rem; }}
  .meta strong {{ color: var(--text); font-weight: 600; }}
  .pill {{
    display: inline-block;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    background: var(--panel);
    border: 1px solid var(--border);
    font-size: 0.8rem;
  }}
  .status-ok {{ color: var(--ok); }}
  .status-bad {{ color: var(--error); }}
  main {{ padding: 1rem 1.5rem 2rem; overflow-x: auto; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }}
  th, td {{
    text-align: left;
    vertical-align: top;
    padding: 0.55rem 0.6rem;
    border-bottom: 1px solid var(--border);
  }}
  th {{
    position: sticky;
    top: 0;
    background: var(--panel);
    color: var(--muted);
    font-weight: 600;
    z-index: 1;
  }}
  tr.lvl-error {{ background: rgba(240, 113, 120, 0.08); }}
  tr.lvl-warn {{ background: rgba(230, 180, 80, 0.06); }}
  pre {{
    margin: 0;
    max-width: 36rem;
    max-height: 10rem;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.75rem;
  }}
  td:nth-child(4) {{ color: var(--accent); font-family: ui-monospace, monospace; }}
  footer {{
    padding: 0.75rem 1.5rem 1.5rem;
    color: var(--muted);
    font-size: 0.8rem;
  }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(page_title)}</h1>
  <div class="meta">
    <span>run_id <strong>{html.escape(str(summary["run_id"]))}</strong></span>
    <span>status <strong class="{"status-ok" if summary["status"] in ("completed", "completed_no_sandbox") else "status-bad"}">{html.escape(str(summary["status"]))}</strong></span>
    <span class="pill">events {summary["events"]}</span>
    <span class="pill">model {summary["model_calls"]}</span>
    <span class="pill">tools {summary["tool_calls"]}</span>
    <span class="pill">retries {summary["retries"]}</span>
    <span class="pill">cost ${summary["cost_usd"]:.4f}</span>
  </div>
</header>
<main>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Time (UTC)</th><th>Level</th><th>Event</th><th>Message</th><th>Data</th>
      </tr>
    </thead>
    <tbody>
      {body_rows}
    </tbody>
  </table>
</main>
<footer>AutoPatch structured trace viewer — local JSONL only; no data leaves your machine.</footer>
</body>
</html>
"""


def write_html_report(log_path: Path, output_path: Path | None = None) -> Path:
    """Load a JSONL log and write an HTML report next to it (or to ``output_path``)."""
    events = load_events(log_path)
    out = output_path or log_path.with_suffix(".html")
    out.write_text(render_html(events, title=f"AutoPatch {log_path.name}"), encoding="utf-8")
    return out
