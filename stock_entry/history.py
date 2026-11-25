"""Single-stock history with signals, cached briefly so repeated clicks do not re-download."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import pandas as pd

from stock_entry.analysis import analyze, build_row, daily_swings
from stock_entry.channel_2h import entry_marks
from stock_entry.intraday import analyze_swings
from stock_entry.market_data import MarketDataError
from stock_entry.summary import history_records
from stock_entry.swings import daily_points, swing_points
from stock_entry.tickers import Ticker

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]



def _with_p_final(points: list[dict[str, Any]], marks: dict[str, str]) -> list[dict[str, Any]]:
    """The 2-hour bars with `p_final`: "Entry 1" / "Entry 2" of the rising 2-hour channel (see channel_2h)."""
    return [{**point, "p_final": marks.get(point["time"])} for point in points]


class StockNotFoundError(LookupError):
    """Raised when Yahoo Finance has no price data for a symbol."""


logger = logging.getLogger(__name__)


class HistoryService:
    def __init__(self, fetch: Fetch, cache_seconds: int, detail_rows: int,
                 clock: Callable[[], float] = time.monotonic, swing_fetch: Fetch | None = None,
                 swing_points: int = 30, two_hour_rows: int = 120, daily_rows: int = 500) -> None:
        self._fetch = fetch
        self._swing_fetch = swing_fetch
        self._swing_points = swing_points
        self._two_hour_rows = two_hour_rows   # every 2-hour bar of the latest ~30 sessions, as in the CSV
        self._daily_rows = daily_rows         # every daily bar the daily swings use (none hidden)
        self._cache_seconds = cache_seconds
        self._detail_rows = detail_rows
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _swings(self, symbol: str) -> pd.DataFrame | None:
        """Analysed 2-hour bars, or None when they cannot be loaded (the daily view still works)."""
        if self._swing_fetch is None:
            return None
        try:
            frame = self._swing_fetch([symbol]).get(symbol)
            return None if frame is None or frame.empty else analyze_swings(frame)
        except MarketDataError as exc:
            logger.warning("2-hour bars failed for %s: %s", symbol, exc)
            return None
        except Exception:  # the daily view must still open
            logger.exception("2-hour swings failed for %s", symbol)
            return None

    def get(self, ticker: Ticker) -> dict[str, Any]:
        """Return {summary, history, swings, swings_1d} for one stock, downloading it when not cached."""
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
        swings = self._swings(symbol)
        daily = daily_swings(analyzed)
        detail = {
            "summary": build_row(ticker, analyzed, swings, daily),
            "history": history_records(analyzed, self._detail_rows),
            "swings": [] if swings is None else swing_points(swings, self._swing_points),
            "bars_2h": [] if swings is None else _with_p_final(
                swing_points(swings, self._two_hour_rows, every_bar=True), entry_marks(swings)),
            "swings_1d": [] if daily is None else daily_points(swing_points(daily, self._swing_points)),
            "bars_1d": [] if daily is None else daily_points(swing_points(daily, self._daily_rows, every_bar=True)),
        }
        if swings is None and self._swing_fetch is not None:
            return detail  # 2-hour bars failed: do not cache, so the next click tries again
        with self._lock:
            fresh = {key: value for key, value in self._cache.items() if now - value[0] < self._cache_seconds}
            self._cache = {**fresh, symbol: (now, detail)}  # expired entries are dropped on every insert
        return detail

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
