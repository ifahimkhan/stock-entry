"""Turn a signal frame into JSON-friendly rows: one summary per stock, plus the bar history."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from stock_entry.signals import ENTRY, PRICE_DECIMALS, WAIT

HISTORY_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume", "pct_change", "angle",
                   "prev4_max_close", "valid", "entry_status", "entry_price", "cci", "cci_ma", "rsi",
                   "mri_mark", "smc_event", "ma_signal")


def clean_value(value: Any) -> Any:
    """NaN/NaT -> None, timestamps -> ISO date, numpy scalars -> plain Python values."""
    if value is None or (not isinstance(value, (str, bool)) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bool, str)):
        return value
    plain = value.item() if hasattr(value, "item") else value
    if isinstance(plain, float) and not math.isfinite(plain):
        return None  # +/-infinity is not valid JSON
    return plain


def _pct(current: float, reference: float | None) -> float | None:
    if reference is None or reference == 0:
        return None
    return round((current / reference - 1) * 100, PRICE_DECIMALS)


def history_records(frame: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    """Bars as dicts, oldest first. `limit` keeps only the most recent bars."""
    columns = [column for column in HISTORY_COLUMNS if column in frame.columns]
    tail = frame[columns] if limit is None else frame[columns].tail(limit)
    return [{key: clean_value(value) for key, value in row.items()} for row in tail.to_dict("records")]


def _trailing_wait_days(statuses: pd.Series) -> int:
    count = 0
    for status in reversed(statuses.tolist()):
        if status != WAIT:
            break
        count += 1
    return count


def summarize_latest(symbol: str, frame: pd.DataFrame, name: str = "", sector: str = "") -> dict[str, Any]:
    """Summary of the most recent bar of one stock, with context about its last breakout and entry."""
    if frame.empty:
        raise ValueError(f"no price bars for {symbol}")
    last = frame.iloc[-1]
    close = float(last["Close"])

    entries = frame[frame["entry_status"] == ENTRY]
    last_entry = entries.iloc[-1] if len(entries) else None
    breakouts = frame[frame["valid"].eq(True)]
    last_breakout = breakouts.iloc[-1] if len(breakouts) else None

    last_entry_price = None if last_entry is None else float(last_entry["Close"])
    breakout_close = None if last_breakout is None else float(last_breakout["Close"])
    return {
        "ticker": symbol,
        "name": name,
        "sector": sector,
        "date": clean_value(last["Date"]),
        "close": close,
        "pct_change": clean_value(last["pct_change"]),
        "valid": clean_value(last["valid"]),
        "status": last["entry_status"],
        "entry_price": clean_value(last["entry_price"]),
        "prev4_max_close": clean_value(last["prev4_max_close"]),
        "wait_days": _trailing_wait_days(frame["entry_status"]),
        "breakout_date": None if last_breakout is None else clean_value(last_breakout["Date"]),
        "breakout_close": breakout_close,
        "pullback_pct": _pct(close, breakout_close),
        "last_entry_date": None if last_entry is None else clean_value(last_entry["Date"]),
        "last_entry_price": last_entry_price,
        "sessions_since_entry": None if last_entry is None else int(len(frame) - 1 - last_entry.name),
        "since_entry_pct": _pct(close, last_entry_price),
    }
