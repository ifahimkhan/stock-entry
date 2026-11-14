"""Builds the objects both front doors share: the Flask API (app.py) and the Streamlit bridge (bridge.py)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

import pandas as pd

from stock_entry.config import Settings
from stock_entry.fundamentals import FetchOne, FundamentalsService, fetch_from_yahoo
from stock_entry.history import HistoryService
from stock_entry.market_data import download_history
from stock_entry.scanner import Scanner
from stock_entry.store import ScanStore
from stock_entry.tickers import Ticker
from stock_entry.tickers import load_universe as read_universe

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]


@dataclass(frozen=True)
class Services:
    settings: Settings
    load_universe: Callable[[], list[Ticker]]
    store: ScanStore
    fundamentals: FundamentalsService
    scanner: Scanner
    history: HistoryService

    def known_ticker(self, symbol: str) -> Ticker:
        """The ticker with its name / sector when listed; a bare one otherwise."""
        try:
            return next((ticker for ticker in self.load_universe() if ticker.symbol == symbol), Ticker(symbol))
        except (FileNotFoundError, ValueError):
            return Ticker(symbol)  # the single-stock view still works without a ticker list


def build_services(settings: Settings, fetch: Fetch | None = None,
                   fetch_fundamentals: FetchOne | None = None) -> Services:
    """`fetch` (symbols -> {symbol: bars}) and `fetch_fundamentals` can be replaced in tests."""
    # The re-ask for stocks a batch dropped makes one attempt only: those are often delisted symbols.
    refetch = fetch or partial(download_history, period=settings.history_period, interval=settings.interval,
                               attempts=1)
    fetch = fetch or partial(download_history, period=settings.history_period, interval=settings.interval)
    load_universe = partial(read_universe, (settings.universe_path, settings.tickers_path),
                            settings.default_suffix, settings.excluded_sectors)
    store = ScanStore(settings.scan_path)
    fundamentals = FundamentalsService(settings.fundamentals_path, fetch_fundamentals or fetch_from_yahoo,
                                       settings.fundamentals_max_age_days, settings.fundamentals_workers)
    scanner = Scanner(load_universe, fetch, store, settings.batch_size, settings.max_failed_share,
                      enrich=fundamentals.enrich, refetch=refetch)
    history = HistoryService(fetch, settings.history_cache_seconds, settings.detail_rows)
    return Services(settings=settings, load_universe=load_universe, store=store, fundamentals=fundamentals,
                    scanner=scanner, history=history)
