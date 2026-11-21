"""HH / HL entries page data: the file written by analysis/hh_hl_entries.py (Entry 1 / Entry 2 study)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATUS_KEYS = ("data_date", "status", "status_2h")   # status: daily stage per stock; status_2h: 2-hour


def results(path: Path, status_only: bool = False) -> dict[str, Any]:
    """The saved analysis; with `status_only`, just each stock's current stage (small, loaded with the page).

    Raises OSError when the file is missing and ValueError when it is not the expected JSON object."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("status"), dict):
        raise ValueError(f"{path.name} is not an HH/HL analysis file")
    return {key: data.get(key) for key in STATUS_KEYS} if status_only else data
