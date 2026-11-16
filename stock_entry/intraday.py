"""2-hour bars for the swing scan (TradingView.ipynb uses 2-hour TradingView bars).

Yahoo has no 2-hour interval, so hourly bars are combined. NSE hourly bars start at 09:15, 10:15, ...
15:15; pairs from the session open give the same 2-hour candles TradingView draws on NSE:
09:15 (09:15-11:15), 11:15, 13:15 and the short 15:15 candle (15:15-15:30).
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from stock_entry.swings import swing_columns

SESSION_OPEN_MINUTES = 9 * 60 + 15
SLOT_MINUTES = 120
Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]


def to_two_hour(hourly: pd.DataFrame) -> pd.DataFrame:
    """Combine hourly bars (Date with time, OHLCV) into 2-hour bars from the 09:15 open, oldest first."""
    if hourly.empty:
        return hourly.copy()
    dates = pd.DatetimeIndex(hourly["Date"])
    minutes = (dates.hour * 60 + dates.minute - SESSION_OPEN_MINUTES).to_numpy()
    slot = np.maximum(minutes // SLOT_MINUTES, 0)   # a stray pre-open tick joins the first candle
    start = dates.normalize() + pd.to_timedelta(SESSION_OPEN_MINUTES + slot * SLOT_MINUTES, unit="min")
    rules = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    present = {column: rule for column, rule in rules.items() if column in hourly.columns}
    grouped = hourly.assign(Date=start).groupby("Date", sort=True).agg(present)
    return grouped.reset_index()


def fetch_two_hour(symbols: list[str], fetch_hourly: Fetch, max_bars: int) -> dict[str, pd.DataFrame]:
    """{symbol: latest `max_bars` 2-hour bars}. Symbols without hourly data are left out."""
    frames = {symbol: to_two_hour(frame).tail(max_bars).reset_index(drop=True)
              for symbol, frame in fetch_hourly(symbols).items()}
    return {symbol: frame for symbol, frame in frames.items() if not frame.empty}


def analyze_swings(bars: pd.DataFrame) -> pd.DataFrame:
    """2-hour bars (oldest first) with the swing columns added."""
    clean = bars.dropna(subset=["High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)
    return pd.concat([clean, swing_columns(clean)], axis=1)
