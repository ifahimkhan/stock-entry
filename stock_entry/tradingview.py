"""2-hour bars from TradingView (the same feed TradingView.ipynb reads), through tvDatafeed.

Credentials come only from the TV_USERNAME / TV_PASSWORD environment variables (on Streamlit Cloud,
root-level secrets become environment variables; in GitHub Actions they are mapped from repository
secrets). They are never written to disk or logged.

- One sign-in per process: worker clients reuse the token, so a scan does not log in hundreds of times.
- tvDatafeed opens a websocket per request and never closes it; every request closes it here.
- tvDatafeed gives bar times in the machine's local zone; they are converted to IST (NSE time).
- NSE symbols: "BAJAJ-AUTO.NS" -> "BAJAJ_AUTO", "M&M.NS" -> "M_M" (TradingView's names).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
NOLOGIN_TOKEN = "unauthorized_user_token"   # what tvDatafeed uses when the sign-in did not work
SOURCE_COLUMN = "Source"
TRADINGVIEW, TRADINGVIEW_NOLOGIN = "tradingview", "tradingview-nologin"
EXCHANGES = {"NS": "NSE", "BO": "BSE"}
SYMBOL_FIXES = str.maketrans({"-": "_", "&": "_"})
DEFAULT_WORKERS = 3      # parallel websockets; kept small so the account is not flagged
DEFAULT_ATTEMPTS = 3     # TradingView drops a connection now and then: ask again
RETRY_WAIT_SECONDS = 1.0

Client = Any   # a tvDatafeed.TvDatafeed, or a fake in tests
MakeClient = Callable[[str | None, str | None], Client]
Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]


def credentials() -> tuple[str, str] | None:
    """(username, password) from TV_USERNAME / TV_PASSWORD, or None when either is missing."""
    username, password = os.environ.get("TV_USERNAME", "").strip(), os.environ.get("TV_PASSWORD", "")
    return (username, password) if username and password else None


def tv_symbol(symbol: str) -> tuple[str, str]:
    """"BAJAJ-AUTO.NS" -> ("BAJAJ_AUTO", "NSE")."""
    base, _, suffix = symbol.upper().partition(".")
    return base.translate(SYMBOL_FIXES), EXCHANGES.get(suffix, "NSE")


def to_ist(times: pd.Index) -> pd.DatetimeIndex:
    """Naive local-zone times (as tvDatafeed builds them) -> naive IST times."""
    return pd.DatetimeIndex([datetime.fromisoformat(str(value)).astimezone(IST).replace(tzinfo=None)
                             for value in times])


def to_bars(raw: pd.DataFrame, source: str) -> pd.DataFrame:
    """tvDatafeed frame (datetime index, open..volume) -> Date / Open / High / Low / Close / Volume / Source."""
    bars = pd.DataFrame({
        "Date": to_ist(raw.index), "Open": raw["open"].to_numpy(dtype=float),
        "High": raw["high"].to_numpy(dtype=float), "Low": raw["low"].to_numpy(dtype=float),
        "Close": raw["close"].to_numpy(dtype=float), "Volume": raw["volume"].to_numpy(dtype=float),
    })
    return bars.dropna(subset=["High", "Low", "Close"]).sort_values("Date").reset_index(drop=True).assign(
        **{SOURCE_COLUMN: source})


def _default_client(username: str | None, password: str | None) -> Client:
    from tvDatafeed import TvDatafeed  # imported here: the app still runs where tvDatafeed is not installed

    return TvDatafeed(username=username, password=password)


def _two_hour_interval() -> Any:
    from tvDatafeed import Interval

    return Interval.in_2_hour


class TradingViewFeed:
    """symbols -> {symbol: latest `n_bars` 2-hour bars from TradingView}. Symbols that fail are left out."""

    def __init__(self, username: str | None, password: str | None, n_bars: int,
                 workers: int = DEFAULT_WORKERS, attempts: int = DEFAULT_ATTEMPTS,
                 make_client: MakeClient | None = None, interval: Any = None,
                 wait: Callable[[float], None] = time.sleep) -> None:
        self._username, self._password = username, password
        self._n_bars, self._workers, self._attempts = n_bars, workers, attempts
        self._make_client = make_client or _default_client
        self._interval = interval
        self._wait = wait
        self._lock = threading.Lock()
        self._token: str | None = None
        self._local = threading.local()

    def _sign_in(self) -> str:
        """Token of the one sign-in of this process (done on first use)."""
        with self._lock:
            if self._token is None:
                client = self._make_client(self._username, self._password)
                self._token = getattr(client, "token", NOLOGIN_TOKEN) or NOLOGIN_TOKEN
                if self._token == NOLOGIN_TOKEN:
                    logger.warning("TradingView sign-in failed: using TradingView data without login (may be delayed)")
                else:
                    logger.info("Signed in to TradingView")
            return self._token

    @property
    def source(self) -> str:
        return TRADINGVIEW if self._sign_in() != NOLOGIN_TOKEN else TRADINGVIEW_NOLOGIN

    def _client(self) -> Client:
        """One client per worker thread, carrying the shared token (no extra sign-in)."""
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._make_client(None, None)
            client.token = self._sign_in()
            self._local.client = client
        return client

    def _request(self, client: Client, name: str, exchange: str) -> pd.DataFrame | None:
        try:
            return client.get_hist(name, exchange, interval=self._interval or _two_hour_interval(),
                                   n_bars=self._n_bars)
        finally:
            ws = getattr(client, "ws", None)
            if ws is not None:
                try:
                    ws.close()
                except Exception:  # an already-dropped socket cannot be closed twice
                    pass

    def fetch_one(self, symbol: str) -> pd.DataFrame | None:
        """2-hour bars of one stock, or None after `attempts` tries."""
        name, exchange = tv_symbol(symbol)
        for attempt in range(1, self._attempts + 1):
            try:
                raw = self._request(self._client(), name, exchange)
                if raw is not None and not raw.empty:
                    return to_bars(raw, self.source).tail(self._n_bars).reset_index(drop=True)
            except Exception as exc:  # network, odd reply: try again, then leave it to the fallback
                logger.debug("TradingView %s attempt %d failed: %s", symbol, attempt, exc)
            if attempt < self._attempts:
                self._wait(RETRY_WAIT_SECONDS)
        logger.warning("No TradingView 2-hour bars for %s (%s:%s)", symbol, exchange, name)
        return None

    def __call__(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        self._sign_in()   # once, before the workers start
        with ThreadPoolExecutor(max_workers=max(1, self._workers)) as pool:
            frames = dict(zip(symbols, pool.map(self.fetch_one, symbols)))
        return {symbol: frame for symbol, frame in frames.items() if frame is not None}
