"""Tight range: the close has stayed within a small band for several trading days in a row.

Rule: take the close of the first day of the run as the base. Every later close of the run, up to the latest
day, is within +/- TIGHT_PCT percent of that base. A run of at least TIGHT_DAYS days (the base day included)
puts the stock in the "Tight Range" list.

Fall before the range: the base close against the highest close of the FALL_DAYS trading days before the
run (the base day included). A fall of FALL_PCT percent or more puts the stock in "Fall → Tight Range":
a downtrend that has gone flat.

Entry: a breakout up from a tight range. The close is more than TIGHT_PCT percent above the base close, on
the first day after a run of at least TIGHT_DAYS days, and the volume of that day is above the volume of every
day of the run and above the average volume of the VOLUME_DAYS days before it. The latest such day counts
while it is less than ENTRY_MAX_AGE trading days old.

    python -m stock_entry.tight_range

recomputes these fields of the saved scan from data/daily_bars.csv, in seconds and without downloads.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import numpy as np
import pandas as pd

from stock_entry.config import Settings
from stock_entry.daily_store import read_store
from stock_entry.store import ScanStore

logger = logging.getLogger(__name__)
TIGHT_DAYS = 7          # trading days in a row, the base day included
TIGHT_PCT = 2.0         # every close within +/- this percent of the base close
MAX_DAYS = 60           # the run is measured this far back at most
FALL_DAYS = 20          # the downtrend is looked for in this many trading days before the run
FALL_PCT = 8.0          # the base close is at least this percent below the highest close of those days
VOLUME_DAYS = 20        # the breakout volume is compared with the average of this many days before it
ENTRY_MAX_AGE = 5       # a breakout is listed while it is fewer than this many trading days old
PRICE_DECIMALS = 2
ROUNDING = 1e-9         # exactly +/- TIGHT_PCT counts, whatever the float arithmetic says


def empty_tight_range() -> dict[str, Any]:
    return {"tight_range": False, "tight_days": None, "tight_base_date": None, "tight_base_close": None,
            "tight_low_pct": None, "tight_high_pct": None, "tight_change_pct": None,
            "tight_after_fall": False, "tight_fall_pct": None, "tight_peak_date": None, "tight_peak_close": None}


def fall_before(closes: np.ndarray, dates: pd.Series, base: int, days: int = FALL_DAYS,
                pct: float = FALL_PCT) -> dict[str, Any]:
    """The fall into the run whose base bar is `base`: base close against the highest close of the `days`
    bars before it (base included, so a base that is itself the high gives 0)."""
    start = max(0, base - days)
    peak = start + int(np.argmax(closes[start:base + 1]))
    fall = round(float(closes[base] / closes[peak] - 1) * 100, PRICE_DECIMALS) if closes[peak] > 0 else None
    return {
        "tight_after_fall": fall is not None and fall <= -pct, "tight_fall_pct": fall,
        "tight_peak_date": pd.Timestamp(dates.iloc[peak]).strftime("%Y-%m-%d"),
        "tight_peak_close": round(float(closes[peak]), PRICE_DECIMALS),
    }


def tight_range(bars: pd.DataFrame, days: int = TIGHT_DAYS, pct: float = TIGHT_PCT,
                max_days: int = MAX_DAYS) -> dict[str, Any]:
    """Tight-range fields of the latest bar of `bars` (Date, Close; oldest first).

    tight_days is the longest run ending on the latest day (at least `days`, at most `max_days`);
    tight_low_pct / tight_high_pct are the lowest / highest close of the run against the base close."""
    clean = bars.dropna(subset=["Close"])
    closes = clean["Close"].to_numpy(dtype=float)
    best = None
    for length in range(days, min(max_days, len(closes)) + 1):
        base = closes[-length]
        if base > 0 and np.all(np.abs(closes[-length + 1:] / base - 1) * 100 <= pct + ROUNDING):
            best = length   # runs are not nested, so every length is tried and the longest kept
    if best is None:
        return empty_tight_range()
    run, base = closes[-best:], closes[-best]
    change = lambda value: round(float(value / base - 1) * 100, PRICE_DECIMALS)  # noqa: E731
    return {
        "tight_range": True, "tight_days": best,
        "tight_base_date": pd.Timestamp(clean["Date"].iloc[-best]).strftime("%Y-%m-%d"),
        "tight_base_close": round(float(base), PRICE_DECIMALS),
        "tight_low_pct": change(run.min()), "tight_high_pct": change(run.max()), "tight_change_pct": change(run[-1]),
        **fall_before(closes, clean["Date"], len(closes) - best),
    }


def empty_tight_entry() -> dict[str, Any]:
    return {"tight_entry": False, "tight_entry_date": None, "tight_entry_days_ago": None, "tight_entry_close": None,
            "tight_entry_pct": None, "tight_entry_now_pct": None, "tight_entry_volume_x": None,
            "tight_entry_range_days": None, "tight_entry_base_date": None, "tight_entry_base_close": None,
            "tight_entry_after_fall": False, "tight_entry_fall_pct": None}


def tight_entry(bars: pd.DataFrame, max_age: int = ENTRY_MAX_AGE, pct: float = TIGHT_PCT,
                volume_days: int = VOLUME_DAYS) -> dict[str, Any]:
    """The latest breakout up from a tight range within the last `max_age` bars (see the module text).

    tight_entry_pct: the breakout close against the base close; tight_entry_now_pct: the latest close against
    it; tight_entry_volume_x: the breakout volume as a multiple of the average of the `volume_days` days before."""
    if "Volume" not in bars.columns:
        return empty_tight_entry()
    clean = bars.dropna(subset=["Close", "Volume"]).reset_index(drop=True)
    closes, volumes = clean["Close"].to_numpy(dtype=float), clean["Volume"].to_numpy(dtype=float)
    for age in range(max_age):
        day = len(clean) - 1 - age
        if day < volume_days:
            break
        run = tight_range(clean.iloc[:day], pct=pct)   # the range as it stood the day before
        if not run["tight_range"]:
            continue
        length = run["tight_days"]
        base = closes[day - length]
        average = volumes[day - volume_days:day].mean()
        up = (closes[day] / base - 1) * 100 > pct + ROUNDING
        if not (up and volumes[day] > volumes[day - length:day].max() and volumes[day] > average):
            continue
        return {
            "tight_entry": True, "tight_entry_date": pd.Timestamp(clean["Date"].iloc[day]).strftime("%Y-%m-%d"),
            "tight_entry_days_ago": age, "tight_entry_close": round(float(closes[day]), PRICE_DECIMALS),
            "tight_entry_pct": round(float(closes[day] / base - 1) * 100, PRICE_DECIMALS),
            "tight_entry_now_pct": round(float(closes[-1] / base - 1) * 100, PRICE_DECIMALS),
            "tight_entry_volume_x": round(float(volumes[day] / average), PRICE_DECIMALS) if average > 0 else None,
            "tight_entry_range_days": length, "tight_entry_base_date": run["tight_base_date"],
            "tight_entry_base_close": run["tight_base_close"],
            "tight_entry_after_fall": run["tight_after_fall"], "tight_entry_fall_pct": run["tight_fall_pct"],
        }
    return empty_tight_entry()


def tight_fields(bars: pd.DataFrame) -> dict[str, Any]:
    """Every tight-range field of a scan row: the range the stock is in now, and its latest breakout."""
    return {**tight_range(bars), **tight_entry(bars)}


def apply_to_scan(scan: dict[str, Any], frames: dict[str, pd.DataFrame]) -> tuple[dict[str, Any], int]:
    """A new scan whose rows carry the tight-range fields from `frames`; stocks without bars get empty fields."""
    rows = []
    for row in scan["rows"]:
        frame = frames.get(row["ticker"])
        empty = {**empty_tight_range(), **empty_tight_entry()}
        rows.append({**row, **(empty if frame is None else tight_fields(frame.sort_values("Date")))})
    return {**scan, "rows": rows}, sum(1 for row in rows if row["tight_range"])


def run(settings: Settings) -> int:
    store = ScanStore(settings.scan_path)
    scan = store.load()
    if scan is None:
        logger.error("No saved scan at %s: run a scan first", settings.scan_path)
        return 1
    if not settings.daily_bars_path.is_file():
        logger.error("%s not found", settings.daily_bars_path)
        return 1
    updated, found = apply_to_scan(scan, read_store(settings.daily_bars_path))
    store.save(updated)
    after_fall = sum(1 for row in updated["rows"] if row["tight_after_fall"])
    logger.info("Tight range (%d days within +/-%s%%): %d of %d stocks; %d of them after a fall of %s%% or more",
                TIGHT_DAYS, TIGHT_PCT, found, len(updated["rows"]), after_fall, FALL_PCT)
    entries = [row for row in updated["rows"] if row["tight_entry"]]
    logger.info("Breakouts up with volume in the last %d days: %d; %d of them after a fall",
                ENTRY_MAX_AGE, len(entries), sum(1 for row in entries if row["tight_entry_after_fall"]))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(Settings()))
