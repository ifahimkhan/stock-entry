"""Recompute the 2-hour swing fields of the saved scan from TBC_all_stocks_bars.csv.

    python -m stock_entry.apply_two_hour_csv

Runs in seconds (no downloads), so a freshly committed CSV shows on the site without a full rescan.
The GitHub workflow "Apply 2-hour CSV" runs it whenever the CSV is pushed. Daily fields stay as they are;
stocks the CSV does not list keep the 2-hour fields they had.
"""
from __future__ import annotations

import logging
import sys
from collections import Counter
from typing import Any

import pandas as pd

from stock_entry.config import Settings
from stock_entry.intraday import analyze_swings
from stock_entry.store import ScanStore
from stock_entry.swings import empty_swing_summary, swing_summary
from stock_entry.two_hour_csv import read_bars

logger = logging.getLogger(__name__)
SWING_KEYS = tuple(empty_swing_summary())   # the 2-hour fields of a scan row (daily ones carry "d1_")


def swing_fields(frame: pd.DataFrame) -> dict[str, Any]:
    return swing_summary(analyze_swings(frame))


def apply_bars(scan: dict[str, Any], frames: dict[str, pd.DataFrame]) -> tuple[dict[str, Any], Counter]:
    """A new scan whose rows take their 2-hour fields from `frames`; the input scan is unchanged."""
    counts: Counter = Counter()
    rows = []
    for row in scan["rows"]:
        frame = frames.get(row["ticker"])
        if frame is None:
            counts["not in csv"] += 1
            rows.append(row)
            continue
        try:
            fields = swing_fields(frame)
        except Exception:  # one bad stock keeps its old fields; the rest are still updated
            logger.exception("2-hour swings failed for %s", row["ticker"])
            counts["failed"] += 1
            rows.append(row)
            continue
        counts["updated"] += 1
        rows.append({**row, **{key: fields.get(key) for key in SWING_KEYS}})
    return {**scan, "rows": rows}, counts


def run(settings: Settings) -> int:
    store = ScanStore(settings.scan_path)
    scan = store.load()
    if scan is None:
        logger.error("No saved scan at %s: run a scan first", settings.scan_path)
        return 1
    if not settings.two_hour_csv_path.is_file():
        logger.error("%s not found", settings.two_hour_csv_path)
        return 1
    frames = read_bars(settings.two_hour_csv_path)
    updated, counts = apply_bars(scan, frames)
    store.save(updated)
    logger.info("2-hour fields from %s: %s (csv has %d stocks)", settings.two_hour_csv_path.name, dict(counts), len(frames))
    return 0 if counts["updated"] else 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run(Settings())


if __name__ == "__main__":
    sys.exit(main())
