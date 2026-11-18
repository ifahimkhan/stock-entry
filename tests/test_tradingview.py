"""TradingView 2-hour feed: symbols, IST times, one sign-in, sockets closed, retries, TradingView only."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from stock_entry.analysis import analyze, build_row
from stock_entry.config import Settings
from stock_entry.intraday import analyze_swings
from stock_entry.services import _two_hour_fetch
from stock_entry.tickers import Ticker
from stock_entry.tradingview import (NOLOGIN_TOKEN, SOURCE_COLUMN, TRADINGVIEW, TRADINGVIEW_NOLOGIN,
                                     TradingViewFeed, credentials, to_bars, tv_symbol)
from stock_entry.tv_login import TradingViewLogin
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.unit

# 2026-11-17 09:15 and 11:15 IST as epoch seconds, turned into naive local times like tvDatafeed does.
EPOCHS = [1794887100, 1794894300]


def raw_frame(rows: int = 2) -> pd.DataFrame:
    times = [datetime.fromtimestamp(EPOCHS[i % 2] + 86400 * (i // 2)) for i in range(rows)]
    close = [100.0 + i for i in range(rows)]
    return pd.DataFrame({"symbol": "NSE:X", "open": close, "high": [c + 1 for c in close],
                         "low": [c - 1 for c in close], "close": close, "volume": 10.0},
                        index=pd.DatetimeIndex(times, name="datetime"))


class FakeSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeClient:
    """Stands in for tvDatafeed.TvDatafeed. `replies` maps a TradingView name to a list of answers."""
    logins: list[tuple[str | None, str | None]] = []

    def __init__(self, username: str | None, password: str | None, replies: dict, sockets: list) -> None:
        self.token = "good-token" if username == "user" and password == "pw" else NOLOGIN_TOKEN
        self.replies, self.sockets, self.ws = replies, sockets, None
        self.calls: list[tuple[str, str]] = []
        FakeClient.logins.append((username, password))

    def get_hist(self, symbol: str, exchange: str, interval: object, n_bars: int) -> pd.DataFrame | None:
        self.ws = FakeSocket()
        self.sockets.append(self.ws)
        self.calls.append((symbol, exchange))
        answers = self.replies.get(symbol, [None])
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def make_feed(replies: dict, username: str | None = "user", password: str | None = "pw",
              n_bars: int = 500) -> tuple[TradingViewFeed, list]:
    FakeClient.logins = []
    sockets: list = []
    feed = TradingViewFeed(username, password, n_bars=n_bars, workers=2, attempts=3, interval="2H",
                           make_client=lambda u, p: FakeClient(u, p, replies, sockets), wait=lambda s: None)
    return feed, sockets


def test_tv_symbol_maps_nse_names() -> None:
    assert tv_symbol("BAJAJ-AUTO.NS") == ("BAJAJ_AUTO", "NSE")
    assert tv_symbol("M&M.NS") == ("M_M", "NSE")
    assert tv_symbol("tcs.bo") == ("TCS", "BSE")
    assert tv_symbol("ENRIN") == ("ENRIN", "NSE")


def test_bar_times_become_ist_whatever_the_machine_zone() -> None:
    bars = to_bars(raw_frame(), TRADINGVIEW)
    assert bars["Date"].dt.strftime("%Y-%m-%d %H:%M").tolist() == ["2026-11-17 09:15", "2026-11-17 11:15"]
    assert list(bars.columns) == ["Date", "Open", "High", "Low", "Close", "Volume", SOURCE_COLUMN]
    assert bars["Date"].dt.tz is None   # naive IST, like the Yahoo path


def test_fetch_signs_in_once_and_closes_every_socket() -> None:
    feed, sockets = make_feed({"A": [raw_frame()], "B_C": [raw_frame()]})
    frames = feed(["A.NS", "B-C.NS"])
    assert set(frames) == {"A.NS", "B-C.NS"}
    assert frames["A.NS"][SOURCE_COLUMN].iloc[-1] == TRADINGVIEW
    assert FakeClient.logins.count(("user", "pw")) == 1           # one real sign-in
    assert all(login == (None, None) for login in FakeClient.logins[1:])   # workers reuse the token
    assert sockets and all(socket.closed for socket in sockets)
    feed(["A.NS"])
    assert FakeClient.logins.count(("user", "pw")) == 1           # still one, on the next batch too


def test_retries_then_gives_up_and_keeps_latest_bars() -> None:
    feed, sockets = make_feed({"A": [ConnectionError("lost"), None, raw_frame(6)], "DEAD": [None]}, n_bars=4)
    frames = feed(["A.NS", "DEAD.NS"])
    assert list(frames) == ["A.NS"] and len(frames["A.NS"]) == 4   # third try worked; tail(n_bars)
    assert all(socket.closed for socket in sockets)


def test_failed_sign_in_still_uses_tradingview_and_says_so() -> None:
    feed, _ = make_feed({"A": [raw_frame()]}, password="wrong")
    assert feed(["A.NS"])["A.NS"][SOURCE_COLUMN].iloc[-1] == TRADINGVIEW_NOLOGIN


def test_with_credentials_two_hour_bars_come_from_tradingview_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TV_USERNAME", "user")
    monkeypatch.setenv("TV_PASSWORD", "pw")
    monkeypatch.setattr("stock_entry.services.download_history",
                        lambda *args, **kwargs: pytest.fail("Yahoo must not be asked for 2-hour bars"))
    login = _two_hour_fetch(Settings())
    assert isinstance(login, TradingViewLogin) and login.status()["origin"] == "env"


def test_credentials_need_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TV_USERNAME", raising=False)
    monkeypatch.setenv("TV_PASSWORD", "pw")
    assert credentials() is None
    monkeypatch.setenv("TV_USERNAME", " user ")
    assert credentials() == ("user", "pw")


def test_without_credentials_there_are_no_two_hour_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TV_USERNAME", raising=False)
    monkeypatch.delenv("TV_PASSWORD", raising=False)
    monkeypatch.setattr("stock_entry.services.download_history",
                        lambda *args, **kwargs: pytest.fail("Yahoo must not be asked for 2-hour bars"))
    assert _two_hour_fetch(Settings())(["A.NS"]) == {}


def test_scan_row_reports_the_two_hour_source() -> None:
    swings = analyze_swings(to_bars(raw_frame(), TRADINGVIEW))
    row = build_row(Ticker("A.NS"), analyze(make_prices(SANSERA_CLOSES)), swings)
    assert row["swing_source"] == TRADINGVIEW and row["d1_swing_source"] is None
    assert build_row(Ticker("A.NS"), analyze(make_prices(SANSERA_CLOSES)))["swing_source"] is None


def test_epochs_are_the_ist_times_they_claim() -> None:
    utc = [datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%M") for epoch in EPOCHS]
    assert utc == ["03:45", "05:45"]   # 09:15 and 11:15 IST
