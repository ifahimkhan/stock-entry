"""Market cap (crore) and debt/equity (ratio) per stock, fetched from Yahoo Finance and cached on disk.

Values already present in tickers.csv always win; Yahoo only fills the gaps. The cache is refreshed
when an entry is older than `max_age_days`, because these numbers change slowly.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from stock_entry.tickers import Ticker

logger = logging.getLogger(__name__)

RUPEES_PER_CRORE = 1e7
PERCENT = 100  # Yahoo reports debt/equity as a percentage: 15.6 means a ratio of 0.156
OnProgress = Callable[[int, int], None]


@dataclass(frozen=True)
class Fundamentals:
    market_cap_cr: float | None = None
    debt_to_equity: float | None = None

    @property
    def is_empty(self) -> bool:
        return self.market_cap_cr is None and self.debt_to_equity is None


FetchOne = Callable[[str], Fundamentals]


def _non_negative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def from_yahoo_info(info: dict[str, Any]) -> Fundamentals:
    """Convert Yahoo's raw fields (rupees, percent) to crore and a plain ratio."""
    cap = _non_negative(info.get("marketCap"))
    debt = _non_negative(info.get("debtToEquity"))
    return Fundamentals(
        market_cap_cr=None if cap is None else round(cap / RUPEES_PER_CRORE),
        debt_to_equity=None if debt is None else round(debt / PERCENT, 4),
    )


def fetch_from_yahoo(symbol: str) -> Fundamentals:
    import yfinance as yf  # imported here so the rest of the app (and the tests) work offline

    info = yf.Ticker(symbol).info
    return from_yahoo_info(info if isinstance(info, dict) else {})


class FundamentalsService:
    def __init__(self, path: Path, fetch_one: FetchOne, max_age_days: int, workers: int,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._path = path
        self._fetch_one = fetch_one
        self._max_age = timedelta(days=max_age_days)
        self._workers = workers
        self._now = now
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as handle:
                cache = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Fundamentals cache %s could not be read: %s", self._path, exc)
            return {}
        return cache if isinstance(cache, dict) else {}

    def _save(self, cache: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(cache, handle, allow_nan=False)
        os.replace(temp_path, self._path)

    def _is_fresh(self, entry: Any) -> bool:
        try:
            return self._now() - datetime.fromisoformat(entry["fetched_at"]) < self._max_age
        except (KeyError, TypeError, ValueError):
            return False

    def _safe_fetch(self, symbol: str) -> Fundamentals:
        try:
            return self._fetch_one(symbol)
        except Exception as exc:  # Yahoo fails in many ways; a missing value only disables a filter
            logger.warning("No fundamentals for %s: %s", symbol, exc)
            return Fundamentals()

    @staticmethod
    def _merge(ticker: Ticker, entry: Any) -> Ticker:
        """Fill the ticker's missing values from a cache entry. CSV values are never overwritten."""
        if not isinstance(entry, dict):
            return ticker
        return replace(
            ticker,
            market_cap_cr=ticker.market_cap_cr if ticker.market_cap_cr is not None else _non_negative(entry.get("market_cap_cr")),
            debt_to_equity=ticker.debt_to_equity if ticker.debt_to_equity is not None else _non_negative(entry.get("debt_to_equity")),
        )

    def enrich(self, tickers: list[Ticker], on_progress: OnProgress | None = None) -> list[Ticker]:
        """Return tickers with market cap and debt/equity filled in, fetching what is missing or stale."""
        with self._lock:
            cache = self._load()
        needed = [t.symbol for t in tickers
                  if (t.market_cap_cr is None or t.debt_to_equity is None) and not self._is_fresh(cache.get(t.symbol))]
        done = len(tickers) - len(needed)
        if on_progress is not None:
            on_progress(done, len(tickers))
        # The download runs outside the lock so a single-stock lookup never waits behind a full scan.
        fetched: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for symbol, result in zip(needed, pool.map(self._safe_fetch, needed)):
                done += 1
                if on_progress is not None:
                    on_progress(done, len(tickers))
                if not result.is_empty:  # failures are not cached, so they are retried next scan
                    fetched[symbol] = {"market_cap_cr": result.market_cap_cr, "debt_to_equity": result.debt_to_equity,
                                       "fetched_at": self._now().isoformat(timespec="seconds")}
        merged = {**cache, **fetched}
        if fetched:
            with self._lock:
                merged = {**self._load(), **fetched}  # re-read: another request may have saved meanwhile
                self._save(merged)
        return [self._merge(ticker, merged.get(ticker.symbol)) for ticker in tickers]

    def enrich_one(self, ticker: Ticker) -> Ticker:
        return self.enrich([ticker])[0]
