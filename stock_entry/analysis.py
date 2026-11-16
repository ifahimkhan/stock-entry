"""One place that runs everything on a stock's bars: entry signals + indicators + screens."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from stock_entry.indicators import indicator_columns
from stock_entry.signals import add_entry_signals, clean_bars
from stock_entry.smc import smc_columns, smc_summary
from stock_entry.strategy import evaluate_screens
from stock_entry.summary import summarize_latest
from stock_entry.swings import empty_swing_summary, swing_summary
from stock_entry.tickers import Ticker

logger = logging.getLogger(__name__)


def analyze(prices: pd.DataFrame) -> pd.DataFrame:
    """Bars (oldest first) with the entry-signal columns and the indicator columns."""
    bars = clean_bars(prices)
    return pd.concat([add_entry_signals(bars), indicator_columns(bars), smc_columns(bars)], axis=1)


def build_row(ticker: Ticker, analyzed: pd.DataFrame, swings: pd.DataFrame | None = None) -> dict[str, Any]:
    """Latest-bar summary of one stock: entry status, indicator values, screen results and, from the
    analysed 2-hour bars in `swings`, the TradingView.ipynb swing signals."""
    return {
        **summarize_latest(ticker.symbol, analyzed, ticker.name, ticker.sector),
        **evaluate_screens(analyzed, ticker.market_cap_cr, ticker.debt_to_equity),
        **smc_summary(analyzed),
        **_swing_fields(ticker.symbol, swings),
    }


def _swing_fields(symbol: str, swings: pd.DataFrame | None) -> dict[str, Any]:
    """Swing summary, or empty fields: a swing problem never drops the stock from the scan."""
    if swings is None:
        return empty_swing_summary()
    try:
        return swing_summary(swings)
    except Exception:
        logger.exception("Swing summary failed for %s", symbol)
        return empty_swing_summary()
