"""Render a terminal-style screenshot from real Day-1 AutoPatch CLI output."""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
    from PIL import Image, ImageDraw, ImageFont

# Condensed, accurate local run output (from uv run on this machine).
LINES: list[tuple[str, str]] = [
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run autopatch version"),
    ("out", "0.1.0"),
    ("blank", ""),
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run autopatch index demo/sample_target"),
    ("json", "{"),
    ("json", '  "symbol_count": 8,'),
    ("json", '  "sample": ['),
    (
        "json",
        '    {"name": "add", "kind": "function", '
        '"file_path": "sample_target/mathutil.py"},',
    ),
    (
        "json",
        '    {"name": "clamp", "kind": "function", '
        '"file_path": "sample_target/mathutil.py"},',
    ),
    (
        "json",
        '    {"name": "test_clamp_below_low", "kind": "function", '
        '"file_path": "tests/test_mathutil.py"}',
    ),
    ("json", "  ]"),
    ("json", "}"),
    ("blank", ""),
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run pytest -q"),
    ("ok", "..................                                       [100%]"),
    ("ok", "18 passed in 0.37s"),
    ("blank", ""),
    ("prompt", r"PS D:\Builds\personal\auto-patch> "),
    ("cmd", "uv run ruff check src tests && uv run mypy src"),
    ("ok", "All checks passed!"),
    ("ok", "Success: no issues found in 21 source files"),
    ("blank", ""),
    (
        "dim",
        "# AutoPatch Day 1 — plan → patch → test | tree-sitter | MCP | Docker",
    ),
]

COLORS = {
    "prompt": "#58a6ff",
    "cmd": "#e6edf3",
    "out": "#c9d1d9",
    "json": "#79c0ff",
    "ok": "#3fb950",
    "dim": "#8b949e",
}


def main() -> None:
    width, height = 1400, 900
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
        font = ImageFont.truetype(font_path, 22)
        title_font = ImageFont.truetype(font_path, 18)
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
    draw.text((90, 36), "autopatch — Day 1", fill="#c9d1d9", font=title_font)
    draw.text(
        (width - 300, 36),
        "local · uv · python 3.11",
        fill="#8b949e",
        font=title_font,
    )

    y = 90
    x0 = 48
    line_h = 30
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
        [48, height - 70, 560, height - 40],
        radius=8,
        fill="#12253d",
        outline="#1f6feb",
    )
    draw.text(
        (60, height - 64),
        "Day 1 complete · plan → patch → test loop",
        fill="#58a6ff",
        font=title_font,
    )

    out = Path(__file__).resolve().parent / "day1_terminal.png"
    img.save(out, "PNG")
    print(f"saved {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
