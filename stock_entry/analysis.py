"""One place that runs everything on a stock's bars: entry signals + indicators + screens."""
from __future__ import annotations

from typing import Any

import pandas as pd

from stock_entry.indicators import indicator_columns
from stock_entry.signals import add_entry_signals, clean_bars
from stock_entry.smc import smc_columns, smc_summary
from stock_entry.strategy import evaluate_screens
from stock_entry.summary import summarize_latest
from stock_entry.tickers import Ticker


def analyze(prices: pd.DataFrame) -> pd.DataFrame:
    """Bars (oldest first) with the entry-signal columns and the indicator columns."""
    bars = clean_bars(prices)
    return pd.concat([add_entry_signals(bars), indicator_columns(bars), smc_columns(bars)], axis=1)


def build_row(ticker: Ticker, analyzed: pd.DataFrame) -> dict[str, Any]:
    """Latest-bar summary of one stock: entry status plus indicator values and screen results."""
    return {
        **summarize_latest(ticker.symbol, analyzed, ticker.name, ticker.sector),
        **evaluate_screens(analyzed, ticker.market_cap_cr, ticker.debt_to_equity),
        **smc_summary(analyzed),
    }
