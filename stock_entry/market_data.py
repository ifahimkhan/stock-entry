"""Price history from Yahoo Finance, normalized to one tidy frame per symbol."""
from __future__ import annotations

from typing import Callable

import pandas as pd

PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
Downloader = Callable[..., pd.DataFrame]


class MarketDataError(RuntimeError):
    """Raised when price data could not be downloaded."""


def _yfinance_download(symbols: list[str], period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf  # imported here so the rest of the app (and the tests) work offline

    # auto_adjust=False: traded prices, as an exchange chart (e.g. TradingView) shows them. Dividend-adjusted
    # prices shift every level (order blocks, gaps, SPV high / low) by a little.
    return yf.download(symbols, period=period, interval=interval, group_by="ticker",
                       auto_adjust=False, progress=False, threads=True)


def _tidy(frame: pd.DataFrame) -> pd.DataFrame:
    """Columns Date + OHLCV, rows without a Close dropped, oldest bar first."""
    columns = [column for column in PRICE_COLUMNS if column in frame.columns]
    if "Close" not in columns:
        return pd.DataFrame(columns=["Date", *PRICE_COLUMNS])
    kept = frame[columns].dropna(subset=["Close"])
    dates = pd.DatetimeIndex(pd.to_datetime(kept.index))
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    # Drop the "Date" index before adding the Date column: having both makes sorting ambiguous.
    tidy = kept.reset_index(drop=True)
    tidy.insert(0, "Date", dates.normalize())
    tidy.columns.name = None
    return tidy.sort_values("Date").reset_index(drop=True)


def split_by_symbol(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Split a yfinance download into {symbol: tidy frame}. Symbols with no bars are left out."""
    if raw is None or raw.empty:
        return {}
    if not isinstance(raw.columns, pd.MultiIndex):
        candidates = {symbols[0]: raw} if len(symbols) == 1 else {}
    else:
        wanted = set(symbols)
        level = next((i for i in range(raw.columns.nlevels) if wanted & set(raw.columns.get_level_values(i))), None)
        if level is None:
            return {}
        present = set(raw.columns.get_level_values(level))
        candidates = {symbol: raw.xs(symbol, axis=1, level=level) for symbol in symbols if symbol in present}
    tidied = {symbol: _tidy(frame) for symbol, frame in candidates.items()}
    return {symbol: frame for symbol, frame in tidied.items() if not frame.empty}


def download_history(symbols: list[str], period: str, interval: str = "1d",
                     downloader: Downloader = _yfinance_download) -> dict[str, pd.DataFrame]:
    """Download daily bars for `symbols`. Returns only the symbols that came back with data."""
    if not symbols:
        return {}
    try:
        raw = downloader(symbols, period=period, interval=interval)
    except Exception as exc:  # yfinance raises many unrelated error types; surface one clear error
        raise MarketDataError(f"Could not download prices from Yahoo Finance: {exc}") from exc
    return split_by_symbol(raw, symbols)
