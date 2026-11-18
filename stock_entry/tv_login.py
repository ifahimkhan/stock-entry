"""The TradingView login the 2-hour feed uses, replaceable from the page while the local app runs.

The scan and the stock detail both call this object for 2-hour bars. It starts with TV_USERNAME /
TV_PASSWORD from the environment (when set) and can be swapped for a login typed into the page.
The password is kept in memory only: never written to disk, logged or sent back to the page.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import pandas as pd

from stock_entry.tradingview import TRADINGVIEW, TradingViewFeed, credentials

logger = logging.getLogger(__name__)

MAX_FIELD_LENGTH = 128
ENV, PAGE = "env", "page"

MakeFeed = Callable[..., TradingViewFeed]


class TradingViewLoginError(Exception):
    """A login that was not accepted; the message is safe to show on the page (it never holds the password)."""


def _clean(username: Any, password: Any) -> tuple[str, str]:
    if not isinstance(username, str) or not isinstance(password, str):
        raise TradingViewLoginError("Type your TradingView username and password.")
    username = username.strip()
    if not username or not password:
        raise TradingViewLoginError("Type your TradingView username and password.")
    if len(username) > MAX_FIELD_LENGTH or len(password) > MAX_FIELD_LENGTH:
        raise TradingViewLoginError("That username or password is too long.")
    return username, password


class TradingViewLogin:
    """symbols -> {symbol: 2-hour bars} through the current TradingView login ({} when there is none)."""

    def __init__(self, n_bars: int, make_feed: MakeFeed = TradingViewFeed,
                 env: Callable[[], tuple[str, str] | None] = credentials) -> None:
        self._n_bars, self._make_feed = n_bars, make_feed
        self._lock = threading.Lock()
        login = env()
        self._feed: TradingViewFeed | None = make_feed(*login, n_bars=n_bars) if login else None
        self._username: str | None = login[0] if login else None
        self._origin: str | None = ENV if login else None
        if login is None:
            logger.warning("TV_USERNAME / TV_PASSWORD not set: no 2-hour swing data until you sign in on the page")

    def __call__(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        with self._lock:
            feed = self._feed
        return feed(symbols) if feed is not None else {}

    def sign_in(self, username: Any, password: Any) -> dict[str, Any]:
        """Sign in now; on success every later scan and stock detail uses this login."""
        username, password = _clean(username, password)
        feed = self._make_feed(username, password, n_bars=self._n_bars)
        try:
            accepted = feed.source == TRADINGVIEW   # signs in
        except Exception as exc:  # noqa: BLE001 - any failure; its text is not shown or logged (it could echo input)
            logger.warning("TradingView sign-in could not reach TradingView (%s)", type(exc).__name__)
            raise TradingViewLoginError("Could not reach TradingView. Check the internet connection and try again.") from None
        if not accepted:
            raise TradingViewLoginError("TradingView did not accept this username and password, or could not be "
                                        "reached, or asked for a captcha / 2-factor code (which this sign-in cannot "
                                        "answer). Check the login and try again.")
        with self._lock:
            self._feed, self._username, self._origin = feed, username, PAGE
        logger.info("TradingView login replaced from the page")
        return self.status()

    def sign_out(self) -> dict[str, Any]:
        with self._lock:
            self._feed, self._username, self._origin = None, None, None
        return self.status()

    def status(self) -> dict[str, Any]:
        """{configured, signed_in (None until the first sign-in is tried), username, origin}. Never the password."""
        with self._lock:
            feed, username, origin = self._feed, self._username, self._origin
        return {"configured": feed is not None, "signed_in": feed.signed_in if feed is not None else False,
                "username": username, "origin": origin}
