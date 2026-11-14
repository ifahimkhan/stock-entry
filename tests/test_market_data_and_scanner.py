from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from stock_entry.history import HistoryService, StockNotFoundError
from stock_entry.market_data import MarketDataError, download_history, split_by_symbol
from stock_entry.scanner import Scanner, run_scan
from stock_entry.store import ScanStore
from stock_entry.tickers import Ticker
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.unit


def yahoo_frame(closes_by_symbol: dict[str, list[float]], ticker_level_first: bool = True) -> pd.DataFrame:
    """Mimic yfinance's two-level column layout for a multi-ticker download."""
    length = max(len(closes) for closes in closes_by_symbol.values())
    index = pd.date_range("2026-01-01", periods=length, freq="D", name="Date")
    columns = {}
    for symbol, closes in closes_by_symbol.items():
        padded = closes + [float("nan")] * (length - len(closes))
        for field in ("Open", "High", "Low", "Close", "Volume"):
            key = (symbol, field) if ticker_level_first else (field, symbol)
            columns[key] = padded
    return pd.DataFrame(columns, index=index)


@pytest.mark.parametrize("ticker_level_first", [True, False])
def test_split_by_symbol_handles_both_column_orders(ticker_level_first: bool) -> None:
    raw = yahoo_frame({"A.NS": [1.0, 2.0, 3.0], "B.NS": [5.0, 6.0]}, ticker_level_first)
    frames = split_by_symbol(raw, ["A.NS", "B.NS", "MISSING.NS"])
    assert set(frames) == {"A.NS", "B.NS"}
    assert frames["A.NS"]["Close"].tolist() == [1.0, 2.0, 3.0]
    assert frames["B.NS"]["Close"].tolist() == [5.0, 6.0]  # trailing NaN close dropped
    assert list(frames["A.NS"].columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]


def test_split_by_symbol_flat_columns_and_empty() -> None:
    flat = yahoo_frame({"A.NS": [1.0, 2.0]}).xs("A.NS", axis=1, level=0)
    assert split_by_symbol(flat, ["A.NS"])["A.NS"]["Close"].tolist() == [1.0, 2.0]
    assert split_by_symbol(flat, ["A.NS", "B.NS"]) == {}
    assert split_by_symbol(pd.DataFrame(), ["A.NS"]) == {}
    all_nan = yahoo_frame({"A.NS": [float("nan")]})
    assert split_by_symbol(all_nan, ["A.NS"]) == {}


def test_split_by_symbol_strips_timezone() -> None:
    raw = yahoo_frame({"A.NS": [1.0, 2.0]})
    raw.index = raw.index.tz_localize("Asia/Kolkata")
    assert split_by_symbol(raw, ["A.NS"])["A.NS"]["Date"].dt.tz is None


def test_download_history_wraps_errors_and_skips_empty_request() -> None:
    def boom(symbols: list[str], period: str, interval: str) -> pd.DataFrame:
        raise ConnectionError("offline")

    with pytest.raises(MarketDataError, match="offline"):
        download_history(["A.NS"], "6mo", downloader=boom, sleep=lambda seconds: None)
    assert download_history([], "6mo", downloader=boom) == {}
    ok = download_history(["A.NS"], "6mo", downloader=lambda s, period, interval: yahoo_frame({"A.NS": [1.0]}))
    assert list(ok) == ["A.NS"]


def fake_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    data = {"ENTRY.NS": make_prices(SANSERA_CLOSES[:30]), "WAIT.NS": make_prices(SANSERA_CLOSES[:29])}
    return {symbol: data[symbol] for symbol in symbols if symbol in data}


UNIVERSE = [Ticker("ENTRY.NS", "Entry Co", "Auto"), Ticker("WAIT.NS"), Ticker("NODATA.NS")]


def test_run_scan_collects_rows_failures_and_progress() -> None:
    seen: list[tuple[int, int]] = []
    scan = run_scan(UNIVERSE, fake_fetch, batch_size=2, max_failed_share=0.5,
                    on_progress=lambda done, total: seen.append((done, total)))
    assert {row["ticker"]: row["status"] for row in scan["rows"]} == {"ENTRY.NS": "entry", "WAIT.NS": "wait"}
    assert scan["failed"] == ["NODATA.NS"]
    assert scan["data_date"] == "2026-11-09"
    assert scan["total"] == 3
    assert seen == [(2, 3), (3, 3)]


def test_run_scan_survives_a_failed_batch_but_not_total_failure() -> None:
    def flaky(symbols: list[str]) -> dict[str, pd.DataFrame]:
        if "WAIT.NS" in symbols:
            raise MarketDataError("timeout")
        return fake_fetch(symbols)

    scan = run_scan(UNIVERSE, flaky, batch_size=1, max_failed_share=1.0)
    assert [row["ticker"] for row in scan["rows"]] == ["ENTRY.NS"]
    assert scan["failed"] == ["WAIT.NS", "NODATA.NS"]

    def crash_on_wait(symbols: list[str]) -> dict[str, pd.DataFrame]:
        if "WAIT.NS" in symbols:
            raise KeyError("unexpected yfinance layout")  # not a MarketDataError
        return fake_fetch(symbols)

    assert run_scan(UNIVERSE, crash_on_wait, batch_size=1, max_failed_share=1.0)["failed"] == ["WAIT.NS", "NODATA.NS"]
    with pytest.raises(MarketDataError, match="No price data"):
        run_scan([Ticker("NODATA.NS")], fake_fetch, batch_size=5)
    with pytest.raises(ValueError):
        run_scan(UNIVERSE, fake_fetch, batch_size=0)


def test_run_scan_marks_stocks_with_bad_data_as_failed() -> None:
    bad = lambda symbols: {"ENTRY.NS": make_prices(SANSERA_CLOSES), "BAD.NS": pd.DataFrame({"Close": [1.0]})}
    scan = run_scan([Ticker("ENTRY.NS"), Ticker("BAD.NS")], bad, batch_size=5, max_failed_share=0.5)
    assert scan["failed"] == ["BAD.NS"]


def test_mostly_failed_scan_keeps_previous_scan(tmp_path: Path) -> None:
    store = ScanStore(tmp_path / "scan.json")
    good = {"rows": [{"ticker": "OLD.NS"}], "scanned_at": "earlier"}
    store.save(good)
    scanner = Scanner(lambda: UNIVERSE, fake_fetch_one, store, batch_size=5, max_failed_share=0.25)
    scanner.start()
    progress = wait_until_idle(scanner)
    assert "previous scan was kept" in progress["error"]
    assert store.load() == good


def fake_fetch_one(symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {"ENTRY.NS": make_prices(SANSERA_CLOSES[:30])} if "ENTRY.NS" in symbols else {}


def test_store_retries_replace_when_file_is_briefly_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stock_entry.store as store_module

    real_replace = store_module.os.replace
    attempts: list[int] = []

    def locked_twice(source: Path, target: Path) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("file in use")
        real_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", locked_twice)
    monkeypatch.setattr(store_module, "REPLACE_RETRY_SECONDS", 0)
    store = ScanStore(tmp_path / "scan.json")
    store.save({"rows": []})
    assert len(attempts) == 3 and store.load() == {"rows": []}

    attempts.clear()
    monkeypatch.setattr(store_module.os, "replace", lambda s, t: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        store.save({"rows": []})


def wait_until_idle(scanner: Scanner) -> dict:
    deadline = time.monotonic() + 5
    while scanner.progress()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    return scanner.progress()


def test_scanner_saves_scan_and_reports_progress(tmp_path: Path) -> None:
    store = ScanStore(tmp_path / "data" / "scan.json")
    scanner = Scanner(lambda: UNIVERSE, fake_fetch, store, batch_size=2, max_failed_share=0.5)
    assert store.load() is None
    assert scanner.start() is True
    progress = wait_until_idle(scanner)
    assert progress["running"] is False and progress["error"] is None
    assert (progress["done"], progress["total"]) == (3, 3)
    assert len(store.load()["rows"]) == 2


def test_scanner_refuses_second_scan_while_running(tmp_path: Path) -> None:
    def slow(symbols: list[str]) -> dict[str, pd.DataFrame]:
        time.sleep(0.2)
        return fake_fetch(symbols)

    scanner = Scanner(lambda: UNIVERSE, slow, ScanStore(tmp_path / "scan.json"), batch_size=5)
    assert scanner.start() is True
    assert scanner.start() is False
    assert wait_until_idle(scanner)["running"] is False


def test_scanner_reports_errors(tmp_path: Path) -> None:
    def no_list() -> list[Ticker]:
        raise FileNotFoundError("Ticker list not found")

    scanner = Scanner(no_list, fake_fetch, ScanStore(tmp_path / "scan.json"), batch_size=5)
    scanner.start()
    assert "Ticker list not found" in wait_until_idle(scanner)["error"]

    def crash() -> list[Ticker]:
        raise RuntimeError("unexpected")

    crashing = Scanner(crash, fake_fetch, ScanStore(tmp_path / "scan.json"), batch_size=5)
    crashing.start()
    assert "unexpectedly" in wait_until_idle(crashing)["error"]


def test_store_ignores_corrupt_or_wrong_shaped_files(tmp_path: Path) -> None:
    path = tmp_path / "scan.json"
    path.write_text("{not json", encoding="utf-8")
    assert ScanStore(path).load() is None
    path.write_text('{"rows": "nope"}', encoding="utf-8")
    assert ScanStore(path).load() is None
    ScanStore(path).save({"rows": [], "scanned_at": "now"})
    assert ScanStore(path).load() == {"rows": [], "scanned_at": "now"}


def test_history_service_caches_and_expires() -> None:
    calls: list[list[str]] = []
    clock = [0.0]

    def counting(symbols: list[str]) -> dict[str, pd.DataFrame]:
        calls.append(symbols)
        return fake_fetch(symbols)

    service = HistoryService(counting, cache_seconds=60, detail_rows=5, clock=lambda: clock[0])
    first = service.get(Ticker("ENTRY.NS", "Entry Co"))
    assert first["summary"]["status"] == "entry" and len(first["history"]) == 5
    service.get(Ticker("ENTRY.NS"))
    assert len(calls) == 1          # served from cache
    clock[0] = 61.0
    service.get(Ticker("ENTRY.NS"))
    assert len(calls) == 2          # cache expired
    service.clear()
    service.get(Ticker("ENTRY.NS"))
    assert len(calls) == 3
    with pytest.raises(StockNotFoundError):
        service.get(Ticker("NODATA.NS"))
