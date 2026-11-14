"""Breakout -> pullback -> reversal entry signals. Same rules as Fahim.ipynb.

valid         True  : up day whose Close is above the highest Close of the previous 4 days
              False : up day that did not clear that high
              NaN   : down/flat day, or fewer than 4 earlier bars
entry_status  wait  : down/flat day after a valid=True breakout
              entry : first up day after a wait
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BREAKOUT_WINDOW = 4
PRICE_DECIMALS = 2
WAIT, ENTRY, BLANK = "wait", "entry", ""
REQUIRED_COLUMNS = ("Date", "Close")


def build_entry_status(valid: pd.Series, angle: pd.Series) -> pd.Series:
    """Walk the bars in order: breakout arms the setup, pullback -> 'wait', reversal -> 'entry'."""
    statuses = []
    armed = False      # a valid=True breakout has been seen
    waiting = False    # pullback after the breakout is in progress
    for is_valid, ang in zip(valid.eq(True), angle):
        is_up = ang > 0            # NaN angle counts as not up
        status = BLANK
        if waiting and is_up:      # reversal after pullback
            status = ENTRY
            armed = waiting = False
        elif armed and not is_up:  # pullback after breakout
            status = WAIT
            waiting = True
        if is_valid:               # breakout day (re)arms the setup
            armed = True
        statuses.append(status)
    return pd.Series(statuses, index=valid.index, dtype=object)


def clean_bars(prices: pd.DataFrame) -> pd.DataFrame:
    """Usable bars only, oldest first, index 0..n-1. `prices` is not modified."""
    missing = [column for column in REQUIRED_COLUMNS if column not in prices.columns]
    if missing:
        raise ValueError(f"price data is missing column(s): {', '.join(missing)}")
    priced = prices.dropna(subset=["Close"])
    # A zero or negative Close is a bad tick: it would turn the % change into infinity.
    bars = priced[priced["Close"].astype(float) > 0].sort_values("Date").reset_index(drop=True)
    return bars[~_filler_bars(bars)].reset_index(drop=True)


def _filler_bars(bars: pd.DataFrame) -> pd.Series:
    """Yahoo sometimes returns a placeholder for a day it has no data for: open = high = low = close =
    the previous close. It is not a real candle, so it is dropped."""
    if not {"Open", "High", "Low"} <= set(bars.columns):
        return pd.Series(False, index=bars.index)
    close = bars["Close"].astype(float)
    flat = (bars["Open"] == close) & (bars["High"] == close) & (bars["Low"] == close)
    return flat & (close == close.shift(1))


def add_entry_signals(prices: pd.DataFrame, window: int = BREAKOUT_WINDOW) -> pd.DataFrame:
    """Return a new frame (oldest bar first) with the signal columns added. `prices` is not modified."""
    if window < 1:
        raise ValueError("window must be at least 1")
    bars = clean_bars(prices)
    raw_close = bars["Close"].astype(float)
    pct_change = raw_close.pct_change(fill_method=None) * 100
    angle = np.degrees(np.arctan(pct_change)).round(PRICE_DECIMALS)
    close = raw_close.round(PRICE_DECIMALS)

    prev_max_close = close.shift(1).rolling(window).max().where(angle > 0)  # NaN on down/flat days
    valid = (close > prev_max_close).astype(object).where(prev_max_close.notna())
    entry_status = build_entry_status(valid, angle)
    return bars.assign(
        Close=close,
        pct_change=pct_change.round(PRICE_DECIMALS),
        angle=angle,
        prev4_max_close=prev_max_close,
        valid=valid,
        entry_status=entry_status,
        entry_price=close.where(entry_status == ENTRY),  # Close of the entry day
    )
