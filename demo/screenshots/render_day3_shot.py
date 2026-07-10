"""Render a terminal-style screenshot from real Day-3 AutoPatch CLI output."""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
    from PIL import Image, ImageDraw, ImageFont

# Condensed, accurate local run output (Day 3: eval harness + polish).
# Source: demo/screenshots/day3_terminal_output.txt (captured 2026-07-10).
LINES: list[tuple[str, str]] = [
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run autopatch eval --list"),
    ("json", '  { "id": "local_clamp", "source": "local", "difficulty": "easy" },'),
    ("json", '  { "id": "gh_click_3487", "source": "github", ... },'),
    ("ok", "23 fixture(s)"),
    ("blank", ""),
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run pytest tests/test_eval_harness.py -q"),
    ("ok", "............                                                             [100%]"),
    ("ok", "12 passed in 0.18s"),
    ("blank", ""),
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run python eval/run_eval.py --dry-run --local-only"),
    ("out", "[1/5] local_clamp (local) ..."),
    ("dim", "  -> skip attempts=0 cost=$0.0000 time=0.0s"),
    ("out", "[2/5] local_demo_sample (local) ..."),
    ("dim", "  -> skip attempts=0 cost=$0.0000 time=0.0s"),
    ("out", "[3/5] local_is_even (local) ..."),
    ("dim", "  -> skip attempts=0 cost=$0.0000 time=0.0s"),
    ("out", "[4/5] local_percent (local) ..."),
    ("dim", "  -> skip attempts=0 cost=$0.0000 time=0.0s"),
    ("out", "[5/5] local_reverse_words (local) ..."),
    ("dim", "  -> skip attempts=0 cost=$0.0000 time=0.0s"),
    ("blank", ""),
    ("dim", "# AutoPatch Eval Report"),
    ("ok", "| Fixtures total | 5 |  Ran | 0 |  Skipped | 5 |"),
    ("ok", "| **Resolve rate** | **0.0%**  (dry-run — honest inventory, not inflated)"),
    ("out", "Wrote eval/results/results.json"),
    ("out", "Wrote eval/results/report.md"),
    ("blank", ""),
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run ruff check .  &&  uv run mypy src  &&  uv run pytest -q"),
    ("ok", "All checks passed!"),
    ("ok", "Success: no issues found in 24 source files"),
    ("ok", ".............................................                            [100%]"),
    ("ok", "45 passed in 0.84s"),
    ("blank", ""),
    (
        "dim",
        "# Day 3 — eval harness · 23 fixtures · golden diffs · honest metrics · ship",
    ),
]

COLORS = {
    "prompt": "#58a6ff",
    "cmd": "#e6edf3",
    "out": "#c9d1d9",
    "json": "#79c0ff",
    "ok": "#3fb950",
    "warn": "#d29922",
    "dim": "#8b949e",
}


def main() -> None:
    width, height = 1500, 1080
    img = Image.new("RGB", (width, height), "#0d1117")
    draw = ImageDraw.Draw(img)

    font_path = None
    for candidate in (
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\CascadiaMono.ttf",
        r"C:\Windows\Fonts\lucon.ttf",
    ):
        if Path(candidate).exists():
            font_path = candidate
            break

    if font_path:
        font = ImageFont.truetype(font_path, 19)
        title_font = ImageFont.truetype(font_path, 17)
    else:
        font = ImageFont.load_default()
        title_font = font

    # Window frame
    draw.rounded_rectangle(
        [24, 24, width - 24, height - 24],
        radius=14,
        fill="#0d1117",
        outline="#30363d",
        width=2,
    )
    draw.rectangle([24, 24, width - 24, 68], fill="#161b22")
    for i, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        x = 48 + i * 22
        draw.ellipse([x, 40, x + 12, 52], fill=color)
    draw.text((90, 36), "autopatch — Day 3", fill="#c9d1d9", font=title_font)
    draw.text(
        (width - 460, 36),
        "eval harness · metrics · polish · ship",
        fill="#8b949e",
        font=title_font,
    )

    y = 90
    x0 = 48
    line_h = 26
    i = 0
    while i < len(LINES):
        kind, text = LINES[i]
        if kind == "prompt" and i + 1 < len(LINES) and LINES[i + 1][0] == "cmd":
            draw.text((x0, y), text, fill=COLORS["prompt"], font=font)
            bbox = draw.textbbox((x0, y), text, font=font)
            draw.text((bbox[2], y), LINES[i + 1][1], fill=COLORS["cmd"], font=font)
            y += line_h
            i += 2
            continue
        if kind == "blank":
            y += line_h // 2
            i += 1
            continue
        draw.text((x0, y), text, fill=COLORS.get(kind, "#c9d1d9"), font=font)
        y += line_h
        i += 1

    draw.rounded_rectangle(
        [48, height - 70, 820, height - 40],
        radius=8,
        fill="#12253d",
        outline="#1f6feb",
    )
    draw.text(
        (60, height - 64),
        "Day 3 complete · eval harness + honest metrics",
        fill="#58a6ff",
        font=title_font,
    )

    out = Path(__file__).resolve().parent / "day3_terminal.png"
    img.save(out, "PNG")
    print(f"saved {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
