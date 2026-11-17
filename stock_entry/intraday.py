"""Swing analysis of 2-hour bars. The bars come from TradingView (stock_entry/tradingview.py)."""
from __future__ import annotations

import pandas as pd

from stock_entry.swings import swing_columns


def analyze_swings(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars (oldest first) with the swing columns added."""
    clean = bars.dropna(subset=["High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)
    return pd.concat([clean, swing_columns(clean)], axis=1)
