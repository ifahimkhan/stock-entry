"""Builds the objects both front doors share: the Flask API (app.py) and the Streamlit bridge (bridge.py)."""
from __future__ import annotations

import logging
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
from stock_entry.tv_login import TradingViewLogin
from stock_entry.two_hour_csv import CsvFirstTwoHourBars, CsvTwoHourBars

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Services:
    settings: Settings
    load_universe: Callable[[], list[Ticker]]
    store: ScanStore
    fundamentals: FundamentalsService
    scanner: Scanner
    history: HistoryService
    tv_login: TradingViewLogin | None = None   # None when tests pass their own bars

    def known_ticker(self, symbol: str) -> Ticker:
        """The ticker with its name / sector when listed; a bare one otherwise."""
        try:
            return next((ticker for ticker in self.load_universe() if ticker.symbol == symbol), Ticker(symbol))
        except (FileNotFoundError, ValueError):
            return Ticker(symbol)  # the single-stock view still works without a ticker list


def _two_hour_fetch(settings: Settings) -> TradingViewLogin:
    """2-hour bars from TradingView only: TV_USERNAME / TV_PASSWORD at start, or a login typed into the
    page later. A stock TradingView does not return gets empty 2-hour swing fields; without a login every
    stock does."""
    return TradingViewLogin(n_bars=settings.swing_bars)


def build_services(settings: Settings, fetch: Fetch | None = None,
                   fetch_fundamentals: FetchOne | None = None, swing_fetch: Fetch | None = None,
                   tv_login: TradingViewLogin | None = None) -> Services:
    """`fetch` (symbols -> {symbol: daily bars}), `swing_fetch` (symbols -> {symbol: 2-hour bars}) and
    `fetch_fundamentals` can be replaced in tests. Passing `fetch` without `swing_fetch` turns the
    2-hour swing scan off, so tests never reach TradingView by accident. `tv_login` (tests) replaces the
    TradingView login built from the environment."""
    if tv_login is None and swing_fetch is None and fetch is None:
        tv_login = _two_hour_fetch(settings)
    swing_fetch = tv_login if tv_login is not None else swing_fetch
    if fetch is None and settings.two_hour_csv_path.is_file():
        # The notebook's bars first; TradingView only for the stocks the file does not list.
        swing_fetch = CsvFirstTwoHourBars(CsvTwoHourBars(settings.two_hour_csv_path), swing_fetch)
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
                      enrich=fundamentals.enrich, refetch=refetch, swing_fetch=swing_fetch)
    history = HistoryService(fetch, settings.history_cache_seconds, settings.detail_rows,
                             swing_fetch=swing_fetch, swing_points=settings.swing_detail_points)
    return Services(settings=settings, load_universe=load_universe, store=store, fundamentals=fundamentals,
                    scanner=scanner, history=history, tv_login=tv_login)
