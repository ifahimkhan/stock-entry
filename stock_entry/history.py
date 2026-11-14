"""Single-stock history with signals, cached briefly so repeated clicks do not re-download."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import pandas as pd

from stock_entry.analysis import analyze, build_row
from stock_entry.summary import history_records
from stock_entry.tickers import Ticker

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]


class StockNotFoundError(LookupError):
    """Raised when Yahoo Finance has no price data for a symbol."""


class HistoryService:
    def __init__(self, fetch: Fetch, cache_seconds: int, detail_rows: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch
        self._cache_seconds = cache_seconds
        self._detail_rows = detail_rows
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, ticker: Ticker) -> dict[str, Any]:
        """Return {summary, history} for one stock, downloading it when not cached."""
        symbol = ticker.symbol
        now = self._clock()
        with self._lock:
            cached = self._cache.get(symbol)
        if cached is not None and now - cached[0] < self._cache_seconds:
            return cached[1]

        frame = self._fetch([symbol]).get(symbol)
        if frame is None or frame.empty:
            raise StockNotFoundError(f"No price data found for {symbol}. Check the symbol (NSE stocks end in .NS).")
        analyzed = analyze(frame)
        detail = {"summary": build_row(ticker, analyzed), "history": history_records(analyzed, self._detail_rows)}
        with self._lock:
            fresh = {key: value for key, value in self._cache.items() if now - value[0] < self._cache_seconds}
            self._cache = {**fresh, symbol: (now, detail)}  # expired entries are dropped on every insert
        return detail

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
