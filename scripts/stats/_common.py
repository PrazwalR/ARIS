"""Shared paths for the review statistics scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parents[2] / "docs" / "results"


def write(name: str, payload: Any) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / name
    path.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {path}")
    return path
