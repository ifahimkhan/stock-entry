"""Market cap (crore), debt/equity (ratio) and book value per share per stock, fetched from Yahoo Finance
and cached on disk.

Values already present in tickers.csv always win; Yahoo only fills the gaps. The cache is refreshed
when an entry is older than `max_age_days`, because these numbers change slowly.

Book value per share (for the P/B ratio) is never in the stock list, so every stock needs one Yahoo
request for it each time its cache entry goes stale. When Yahoo gives no book value, it is worked out from
the balance sheet: (total assets - total liabilities) / shares outstanding. It is negative when the
liabilities exceed the assets, and so is the P/B ratio then.
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

from stock_entry.clock import now_ist
from stock_entry.tickers import Ticker

logger = logging.getLogger(__name__)

RUPEES_PER_CRORE = 1e7
PERCENT = 100  # Yahoo reports debt/equity as a percentage: 15.6 means a ratio of 0.156
OnProgress = Callable[[int, int], None]


@dataclass(frozen=True)
class Fundamentals:
    market_cap_cr: float | None = None
    debt_to_equity: float | None = None
    book_value: float | None = None   # book value per share, rupees

    @property
    def is_empty(self) -> bool:
        return self.market_cap_cr is None and self.debt_to_equity is None and self.book_value is None


FetchOne = Callable[[str], Fundamentals]


def _non_negative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _positive(value: Any) -> float | None:
    number = _non_negative(value)
    return number if number else None


def _number(value: Any) -> float | None:
    """A finite number of either sign, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _book_value(value: Any) -> float | None:
    """Book value per share, 2 decimals: negative when liabilities exceed assets, None when missing or zero."""
    number = _number(value)
    return round(number, 2) if number else None


def book_value_per_share(total_assets: Any, total_liabilities: Any, shares: Any) -> float | None:
    """BVPS = (total assets - total liabilities) / shares outstanding; None when a figure is missing."""
    assets, liabilities, count = _number(total_assets), _number(total_liabilities), _positive(shares)
    if assets is None or liabilities is None or count is None:
        return None
    return _book_value((assets - liabilities) / count)


def price_to_book(price: Any, book_value: Any) -> float | None:
    """P/B ratio = market price per share / book value per share (negative for a negative book value);
    None when either is missing or zero."""
    price, book_value = _positive(price), _book_value(book_value)
    return None if price is None or book_value is None else round(price / book_value, 2)


def from_yahoo_info(info: dict[str, Any], balance: dict[str, Any] | None = None) -> Fundamentals:
    """Convert Yahoo's raw fields (rupees, percent) to crore, a plain ratio and book value per share.
    `balance` (total_assets, total_liabilities, shares) is used when Yahoo's own book value is missing."""
    cap = _non_negative(info.get("marketCap"))
    debt = _non_negative(info.get("debtToEquity"))
    book = _book_value(info.get("bookValue"))
    if book is None and balance:
        book = book_value_per_share(balance.get("total_assets"), balance.get("total_liabilities"),
                                    balance.get("shares") or info.get("sharesOutstanding"))
    return Fundamentals(
        market_cap_cr=None if cap is None else round(cap / RUPEES_PER_CRORE),
        debt_to_equity=None if debt is None else round(debt / PERCENT, 4),
        book_value=book,
    )


# Balance sheet rows as Yahoo names them.
ASSETS_ROW, LIABILITIES_ROW, SHARES_ROW = "Total Assets", "Total Liabilities Net Minority Interest", "Ordinary Shares Number"


def latest_balance(sheet: Any) -> dict[str, Any] | None:
    """Total assets, total liabilities and share count of the newest period of a Yahoo balance sheet
    (rows = items, columns = period ends); None when the sheet is empty or has no assets / liabilities."""
    if sheet is None or getattr(sheet, "empty", True):
        return None
    if ASSETS_ROW not in sheet.index or LIABILITIES_ROW not in sheet.index:
        return None
    newest = sheet[max(sheet.columns)]

    def cell(row: str) -> float | None:
        return _number(float(newest[row])) if row in newest.index else None

    return {"total_assets": cell(ASSETS_ROW), "total_liabilities": cell(LIABILITIES_ROW), "shares": cell(SHARES_ROW)}


def fetch_from_yahoo(symbol: str) -> Fundamentals:
    import yfinance as yf  # imported here so the rest of the app (and the tests) work offline

    ticker = yf.Ticker(symbol)
    info = ticker.info
    info = info if isinstance(info, dict) else {}
    if _book_value(info.get("bookValue")) is not None:
        return from_yahoo_info(info)
    # No book value from Yahoo: one more request, for the balance sheet (the annual one, else the quarterly).
    balance = None
    for read in (lambda: ticker.balance_sheet, lambda: ticker.quarterly_balance_sheet):
        try:
            balance = latest_balance(read())
        except Exception as exc:  # a missing balance sheet only leaves the P/B ratio empty
            logger.warning("No balance sheet for %s: %s", symbol, exc)
        if balance:
            break
    return from_yahoo_info(info, balance)


class FundamentalsService:
    def __init__(self, path: Path, fetch_one: FetchOne, max_age_days: int, workers: int,
                 now: Callable[[], datetime] = now_ist) -> None:
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
            book_value=ticker.book_value if ticker.book_value is not None else _book_value(entry.get("book_value")),
        )

    def _needs_fetch(self, ticker: Ticker, entry: Any) -> bool:
        missing = ticker.market_cap_cr is None or ticker.debt_to_equity is None or ticker.book_value is None
        # An entry saved before book value was stored has no "book_value" key: it is fetched once more.
        return missing and not (self._is_fresh(entry) and "book_value" in entry)

    def enrich(self, tickers: list[Ticker], on_progress: OnProgress | None = None) -> list[Ticker]:
        """Return tickers with market cap, debt/equity and book value filled in, fetching what is missing or stale."""
        with self._lock:
            cache = self._load()
        needed = [t.symbol for t in tickers if self._needs_fetch(t, cache.get(t.symbol))]
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
                                       "book_value": result.book_value,
                                       "fetched_at": self._now().isoformat(timespec="seconds")}
        merged = {**cache, **fetched}
        if fetched:
            with self._lock:
                merged = {**self._load(), **fetched}  # re-read: another request may have saved meanwhile
                self._save(merged)
        return [self._merge(ticker, merged.get(ticker.symbol)) for ticker in tickers]

    def enrich_one(self, ticker: Ticker) -> Ticker:
        return self.enrich([ticker])[0]
