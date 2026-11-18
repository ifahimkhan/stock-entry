"""2-hour bars from TBC_all_stocks_bars.csv: reading, CSV-first feed, and applying it to a saved scan."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_entry.apply_two_hour_csv import apply_bars, run, swing_fields
from stock_entry.config import Settings
from stock_entry.services import build_services
from stock_entry.store import ScanStore
from stock_entry.two_hour_csv import CSV_SOURCE, CsvFirstTwoHourBars, CsvTwoHourBars, read_bars

pytestmark = pytest.mark.unit


def notebook_csv(path: Path, tickers: dict[str, int]) -> Path:
    """A CSV shaped like the notebook's output: {ticker: number of 2-hour bars}, plus its signal columns."""
    frames = []
    for ticker, count in tickers.items():
        close = 100 + np.cumsum(np.random.default_rng(len(ticker)).normal(0, 2, count))
        frames.append(pd.DataFrame({
            "Ticker": ticker, "datetime": pd.date_range("2026-08-03 09:15", periods=count, freq="2h"),
            "symbol": f"NSE:{ticker}", "open": close, "high": close + 1.5, "low": close - 1.5, "close": close,
            "volume": 1000.0, "status": "", "status_clean": "", "Final": "", "3LL_HH": False,
        }))
    data = pd.concat(frames, ignore_index=True)
    data.iloc[::-1].to_csv(path, index=False)   # newest first: the reader must sort
    return path


def test_read_bars_maps_tickers_and_sorts_oldest_first(tmp_path: Path) -> None:
    frames = read_bars(notebook_csv(tmp_path / "bars.csv", {"KRN": 40, "BAJAJ-AUTO": 30}))
    assert set(frames) == {"KRN.NS", "BAJAJ-AUTO.NS"}
    krn = frames["KRN.NS"]
    assert list(krn.columns) == ["Date", "Open", "High", "Low", "Close", "Volume", "Source"]
    assert len(krn) == 40 and krn["Date"].is_monotonic_increasing
    assert set(krn["Source"]) == {CSV_SOURCE}


def test_read_bars_rejects_a_file_without_prices(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    pd.DataFrame({"Ticker": ["A"], "datetime": ["2026-11-18 09:15"]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        read_bars(path)


def test_csv_feed_returns_copies_and_rereads_a_changed_file(tmp_path: Path) -> None:
    path = notebook_csv(tmp_path / "bars.csv", {"KRN": 20})
    feed = CsvTwoHourBars(path)
    first = feed(["KRN.NS", "NOPE.NS"])
    assert list(first) == ["KRN.NS"]
    first["KRN.NS"].loc[0, "Close"] = -1.0
    assert feed(["KRN.NS"])["KRN.NS"].loc[0, "Close"] != -1.0   # callers cannot change the cached bars

    notebook_csv(path, {"KRN": 25, "TCS": 20})
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert len(feed(["KRN.NS"])["KRN.NS"]) == 25 and "TCS.NS" in feed(["TCS.NS"])


def test_missing_or_broken_csv_gives_no_bars(tmp_path: Path) -> None:
    assert CsvTwoHourBars(tmp_path / "none.csv")(["KRN.NS"]) == {}
    broken = tmp_path / "broken.csv"
    broken.write_text("Ticker,datetime\nKRN,2026-11-18\n", encoding="utf-8")
    assert CsvTwoHourBars(broken)(["KRN.NS"]) == {}


def test_csv_first_asks_the_fallback_only_for_missing_stocks(tmp_path: Path) -> None:
    csv = CsvTwoHourBars(notebook_csv(tmp_path / "bars.csv", {"KRN": 20}))
    asked: list[list[str]] = []

    def tradingview(symbols: list[str]) -> dict[str, pd.DataFrame]:
        asked.append(symbols)
        return {symbol: pd.DataFrame({"Source": ["tradingview"]}) for symbol in symbols}

    found = CsvFirstTwoHourBars(csv, tradingview)(["KRN.NS", "HDFCBANK.NS"])
    assert asked == [["HDFCBANK.NS"]]
    assert found["KRN.NS"]["Source"].iloc[-1] == CSV_SOURCE and found["HDFCBANK.NS"]["Source"].iloc[-1] == "tradingview"
    assert CsvFirstTwoHourBars(csv, None)(["HDFCBANK.NS"]) == {}
    asked.clear()
    CsvFirstTwoHourBars(csv, tradingview)(["KRN.NS"])
    assert asked == []   # nothing missing: TradingView is not called at all


def test_apply_bars_updates_listed_stocks_only_and_keeps_the_input(tmp_path: Path) -> None:
    frames = read_bars(notebook_csv(tmp_path / "bars.csv", {"KRN": 120}))
    scan = {"data_date": "2026-11-18", "rows": [
        {"ticker": "KRN.NS", "close": 1.0, "swing_source": "tradingview", "swing_bars": 3},
        {"ticker": "HDFCBANK.NS", "close": 2.0, "swing_source": "tradingview", "swing_bars": 500},
    ]}
    updated, counts = apply_bars(scan, frames)
    assert counts == {"updated": 1, "not in csv": 1}
    krn, bank = updated["rows"]
    assert krn["swing_source"] == CSV_SOURCE and krn["swing_bars"] == 120 and krn["close"] == 1.0
    assert krn["swing_last_bar"] == swing_fields(frames["KRN.NS"])["swing_last_bar"]
    assert bank == scan["rows"][1]
    assert scan["rows"][0]["swing_source"] == "tradingview"   # the input scan is not changed


def test_run_saves_the_scan_and_reports_missing_files(tmp_path: Path) -> None:
    settings = replace(Settings(), scan_path=tmp_path / "scan.json", two_hour_csv_path=tmp_path / "bars.csv")
    assert run(settings) == 1   # no scan yet
    ScanStore(settings.scan_path).save({"data_date": "2026-11-18", "rows": [{"ticker": "KRN.NS"}]})
    assert run(settings) == 1   # no CSV yet
    notebook_csv(settings.two_hour_csv_path, {"KRN": 80})
    assert run(settings) == 0
    assert ScanStore(settings.scan_path).load()["rows"][0]["swing_source"] == CSV_SOURCE


def test_services_read_the_csv_first_when_it_exists(tmp_path: Path) -> None:
    csv_path = notebook_csv(tmp_path / "bars.csv", {"KRN": 30})
    settings = replace(Settings(), scan_path=tmp_path / "scan.json", two_hour_csv_path=csv_path)
    services = build_services(settings)
    bars = services.history._swing_fetch(["KRN.NS"])
    assert bars["KRN.NS"]["Source"].iloc[-1] == CSV_SOURCE
    assert services.tv_login is not None   # the TradingView login page still works

    without = build_services(replace(settings, two_hour_csv_path=tmp_path / "none.csv"))
    assert not isinstance(without.history._swing_fetch, CsvFirstTwoHourBars)
