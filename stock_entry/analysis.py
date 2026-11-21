"""One place that runs everything on a stock's bars: entry signals + indicators + screens."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from stock_entry.channel import parallel_channel
from stock_entry.indicators import indicator_columns
from stock_entry.intraday import analyze_swings
from stock_entry.signals import add_entry_signals, clean_bars
from stock_entry.smc import smc_columns, smc_summary
from stock_entry.strategy import evaluate_screens
from stock_entry.summary import summarize_latest
from stock_entry.swings import daily_summary, empty_swing_summary, swing_summary
from stock_entry.tickers import Ticker
from stock_entry.tight_range import tight_fields

logger = logging.getLogger(__name__)
DAILY_SWING_BARS = 500   # same as the notebook's n_bars; the 2-year daily history has about this many
PRICE_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")


def analyze(prices: pd.DataFrame) -> pd.DataFrame:
    """Bars (oldest first) with the entry-signal columns and the indicator columns."""
    bars = clean_bars(prices)
    return pd.concat([add_entry_signals(bars), indicator_columns(bars), smc_columns(bars)], axis=1)


def daily_swings(analyzed: pd.DataFrame) -> pd.DataFrame | None:
    """The TradingView.ipynb swing columns on the daily bars (latest DAILY_SWING_BARS), or None on any
    failure: a daily swing problem never drops the stock from the scan."""
    if not {"High", "Low"} <= set(analyzed.columns):
        return None   # close-only prices: no swing pivots to find
    try:
        prices = analyzed[[column for column in PRICE_COLUMNS if column in analyzed.columns]]
        return analyze_swings(prices.tail(DAILY_SWING_BARS))
    except Exception:
        logger.exception("Daily swing signals failed")
        return None


def at_closing_high(prices: pd.DataFrame, bars: int = DAILY_SWING_BARS) -> bool:
    """True when the latest close is >= every earlier close of the last `bars` daily candles (a new
    closing high). Such a stock gets no swing analysis: it is not in a pullback / zone entry."""
    closes = prices["Close"].astype(float).dropna().tail(bars)
    return len(closes) > 1 and bool(closes.iloc[-1] >= closes.iloc[:-1].max())


def build_row(ticker: Ticker, analyzed: pd.DataFrame, swings: pd.DataFrame | None = None,
              daily: pd.DataFrame | None = None) -> dict[str, Any]:
    """Latest-bar summary of one stock: entry status, indicator values, screen results and the
    TradingView.ipynb swing signals of the analysed 2-hour bars in `swings` and of the daily bars
    (`daily`, computed from `analyzed` when not given; fields prefixed d1_).
    At a new closing high (see at_closing_high) both swing parts are skipped and left empty."""
    high = at_closing_high(analyzed)
    if high:
        swings, daily = None, None
    else:
        daily = daily_swings(analyzed) if daily is None else daily
    return {
        **summarize_latest(ticker.symbol, analyzed, ticker.name, ticker.sector),
        **evaluate_screens(analyzed, ticker.market_cap_cr, ticker.debt_to_equity),
        **smc_summary(analyzed),
        "at_closing_high": high,
        **tight_fields(analyzed),
        **parallel_channel(analyzed),
        **_swing_fields(ticker.symbol, swings),
        **daily_summary(_swing_fields(ticker.symbol, daily)),
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
