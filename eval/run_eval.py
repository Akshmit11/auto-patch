"""Eval harness stub (Day 3).

Loads issue fixtures from eval/issues/ and will score resolve rate, cost, and time.
Day 1: stub only — full harness lands with the eval set.
"""

from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    issues_dir = Path(__file__).parent / "issues"
    fixtures = sorted(p for p in issues_dir.glob("*.json") if p.is_file())
    print(
        json.dumps(
            {
                "status": "stub",
                "message": "Eval harness will be implemented on Day 3.",
                "fixture_count": len(fixtures),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
