"""IST clock, Yahoo retry with backoff, and the scanner's smaller-batch retry."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from stock_entry.clock import IST, now_ist
from stock_entry.market_data import MarketDataError, download_history
from stock_entry.scanner import run_scan
from stock_entry.tickers import Ticker
from tests.conftest import SANSERA_CLOSES, make_prices
from tests.test_market_data_and_scanner import yahoo_frame

pytestmark = pytest.mark.unit
NO_SLEEP = lambda seconds: None  # noqa: E731


def test_now_ist_is_india_time_without_offset_suffix() -> None:
    utc = datetime(2026, 11, 14, 22, 40, tzinfo=timezone.utc)
    assert now_ist(lambda tz: utc.astimezone(tz)) == datetime(2026, 11, 15, 4, 10)
    assert now_ist().tzinfo is None  # stored as "2026-11-15T04:10:00", same format as before
    assert IST.utcoffset(None) == timedelta(hours=5, minutes=30)


def test_download_retries_errors_with_growing_waits() -> None:
    calls, waits = [], []

    def flaky(symbols: list[str], period: str, interval: str) -> pd.DataFrame:
        calls.append(symbols)
        if len(calls) < 3:
            raise ConnectionError("rate limited")
        return yahoo_frame({"A.NS": [1.0, 2.0]})

    result = download_history(["A.NS"], "6mo", downloader=flaky, sleep=waits.append, retry_wait=2.0)
    assert list(result) == ["A.NS"] and len(calls) == 3
    assert waits == [2.0, 4.0]


def test_download_gives_up_after_the_last_attempt() -> None:
    waits: list[float] = []

    def boom(symbols: list[str], period: str, interval: str) -> pd.DataFrame:
        raise ConnectionError("offline")

    with pytest.raises(MarketDataError, match="offline"):
        download_history(["A.NS"], "6mo", downloader=boom, sleep=waits.append, attempts=3)
    assert len(waits) == 2  # no pointless wait after the final attempt


def test_empty_batch_is_retried_but_a_single_unknown_symbol_is_not() -> None:
    calls: list[list[str]] = []

    def empty_then_data(symbols: list[str], period: str, interval: str) -> pd.DataFrame:
        calls.append(symbols)
        return pd.DataFrame() if len(calls) == 1 else yahoo_frame({"A.NS": [1.0], "B.NS": [2.0]})

    assert set(download_history(["A.NS", "B.NS"], "6mo", downloader=empty_then_data, sleep=NO_SLEEP)) == {"A.NS", "B.NS"}
    assert len(calls) == 2

    calls.clear()
    assert download_history(["NOPE.NS"], "6mo", downloader=empty_then_data, sleep=NO_SLEEP) == {}
    assert len(calls) == 1  # one stock with no data is a wrong symbol, not throttling


def test_scan_refetches_missing_stocks_in_smaller_batches() -> None:
    data = {f"S{i}.NS": make_prices(SANSERA_CLOSES) for i in range(4)}
    calls: list[list[str]] = []

    def throttled(symbols: list[str]) -> dict[str, pd.DataFrame]:
        calls.append(symbols)
        if len(symbols) > 2:   # Yahoo drops part of a big request
            return {symbols[0]: data[symbols[0]]}
        return {s: data[s] for s in symbols if s in data}

    tickers = [Ticker(symbol) for symbol in data] + [Ticker("GONE.NS")]
    scan = run_scan(tickers, throttled, batch_size=5, max_failed_share=0.5)
    assert sorted(row["ticker"] for row in scan["rows"]) == sorted(data)
    assert scan["failed"] == ["GONE.NS"]
    assert calls[0] == [t.symbol for t in tickers]
    assert all(len(call) <= 2 for call in calls[1:])  # retries use half-size batches


def test_scan_asks_again_with_refetch_and_never_splits_an_empty_batch() -> None:
    data = {"A.NS": make_prices(SANSERA_CLOSES), "B.NS": make_prices(SANSERA_CLOSES)}
    first, again = [], []

    def partial_reply(symbols: list[str]) -> dict[str, pd.DataFrame]:
        first.append(symbols)
        return {"A.NS": data["A.NS"]} if "A.NS" in symbols else {}

    def refetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
        again.append(symbols)
        return {s: data[s] for s in symbols if s in data}

    scan = run_scan([Ticker("A.NS"), Ticker("B.NS")], partial_reply, batch_size=2, refetch=refetch)
    assert sorted(row["ticker"] for row in scan["rows"]) == ["A.NS", "B.NS"]
    assert first == [["A.NS", "B.NS"]] and again == [["B.NS"]]

    again.clear()
    with pytest.raises(MarketDataError):  # Yahoo returned nothing at all: no pile of extra requests
        run_scan([Ticker("X.NS"), Ticker("Y.NS")], lambda symbols: {}, batch_size=2, refetch=refetch)
    assert again == []


def test_default_services_refetch_makes_a_single_attempt(tmp_path: Path) -> None:
    from stock_entry.config import Settings
    from stock_entry.services import build_services

    scanner = build_services(replace(Settings(), scan_path=tmp_path / "s.json")).scanner
    assert scanner._refetch.keywords["attempts"] == 1
    assert "attempts" not in scanner._fetch.keywords
