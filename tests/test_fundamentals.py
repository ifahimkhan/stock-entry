from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_entry.fundamentals import (Fundamentals, FundamentalsService, book_value_per_share, from_yahoo_info,
                                      latest_balance, price_to_book)
from stock_entry.scanner import Scanner
from stock_entry.store import ScanStore
from stock_entry.tickers import Ticker
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.unit


def test_yahoo_units_are_converted_to_crore_and_ratio() -> None:
    # KRN.NS as returned by Yahoo; the notebook's CSV has it as ~6800 Cr and 0.16.
    result = from_yahoo_info({"marketCap": 67_060_000_000, "debtToEquity": 15.563})
    assert result == Fundamentals(market_cap_cr=6706, debt_to_equity=0.1556)


@pytest.mark.parametrize("info", [{}, {"marketCap": None, "debtToEquity": "n/a"},
                                  {"marketCap": float("nan"), "debtToEquity": float("inf")},
                                  {"marketCap": -5, "debtToEquity": True}])
def test_bad_yahoo_values_become_none(info: dict) -> None:
    assert from_yahoo_info(info).is_empty


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, symbol: str) -> Fundamentals:
        self.calls.append(symbol)
        if symbol == "BOOM.NS":
            raise ConnectionError("offline")
        return {"A.NS": Fundamentals(200_000, 0.1), "B.NS": Fundamentals(40_000, None)}.get(symbol, Fundamentals())


def make_service(tmp_path: Path, fetch: Recorder, clock: list[datetime]) -> FundamentalsService:
    return FundamentalsService(tmp_path / "data" / "fundamentals.json", fetch, max_age_days=7, workers=2,
                               now=lambda: clock[0])


def test_enrich_fetches_caches_and_keeps_csv_values(tmp_path: Path) -> None:
    fetch, clock = Recorder(), [datetime(2026, 11, 14, 9, 0)]
    service = make_service(tmp_path, fetch, clock)
    tickers = [Ticker("A.NS"), Ticker("B.NS", debt_to_equity=0.5), Ticker("CSV.NS", market_cap_cr=10, debt_to_equity=0.2),
               Ticker("NONE.NS"), Ticker("BOOM.NS")]
    seen: list[tuple[int, int]] = []
    enriched = {t.symbol: t for t in service.enrich(tickers, lambda done, total: seen.append((done, total)))}

    assert (enriched["A.NS"].market_cap_cr, enriched["A.NS"].debt_to_equity) == (200_000, 0.1)
    assert (enriched["B.NS"].market_cap_cr, enriched["B.NS"].debt_to_equity) == (40_000, 0.5)   # CSV debt kept
    assert (enriched["CSV.NS"].market_cap_cr, enriched["CSV.NS"].debt_to_equity) == (10, 0.2)
    assert enriched["NONE.NS"].market_cap_cr is None and enriched["BOOM.NS"].market_cap_cr is None
    assert sorted(fetch.calls) == ["A.NS", "B.NS", "BOOM.NS", "CSV.NS", "NONE.NS"]   # CSV.NS too: for its book value
    assert seen[0] == (0, 5) and seen[-1] == (5, 5)

    fetch.calls.clear()
    service.enrich(tickers)
    assert sorted(fetch.calls) == ["BOOM.NS", "CSV.NS", "NONE.NS"]   # successes cached, empty answers and failures retried

    clock[0] += timedelta(days=8)
    fetch.calls.clear()
    assert service.enrich_one(Ticker("A.NS")).market_cap_cr == 200_000
    assert fetch.calls == ["A.NS"]                            # stale entry refreshed


def test_book_value_is_read_from_yahoo_negative_included() -> None:
    assert from_yahoo_info({"bookValue": 252.834}).book_value == 252.83
    assert not from_yahoo_info({"bookValue": 252.834}).is_empty
    assert from_yahoo_info({"bookValue": -59.496}).book_value == -59.5     # HMT.NS: liabilities above assets
    for bad in (0, None, "n/a", float("nan"), True):
        assert from_yahoo_info({"bookValue": bad}).book_value is None


def test_price_to_book_is_price_over_book_value_per_share() -> None:
    assert price_to_book(2061.70, 252.83) == 8.15     # 2061.70 / 252.83 = 8.1545
    assert price_to_book(50.0, -59.5) == -0.84        # negative book value: negative P/B
    assert price_to_book(2061.70, None) is None and price_to_book(None, 252.83) is None
    assert price_to_book(2061.70, 0) is None and price_to_book(float("nan"), 252.83) is None


def test_book_value_per_share_from_the_balance_sheet() -> None:
    # GRSE, in crore: (10636.10 - 8009.98) / 11.4552 = 229.25
    assert book_value_per_share(10636.10, 8009.98, 11.4552) == 229.25
    assert price_to_book(2061.70, book_value_per_share(10636.10, 8009.98, 11.4552)) == 8.99
    assert book_value_per_share(5037.1, 26158.1, 35.560164) == -593.95      # liabilities above assets
    for assets, liabilities, shares in ((None, 1, 1), (1, None, 1), (1, 1, None), (1, 1, 0), (1, 1, -5),
                                        (float("nan"), 1, 1), (5, 5, 1)):
        assert book_value_per_share(assets, liabilities, shares) is None


def test_balance_sheet_is_used_only_when_yahoo_has_no_book_value() -> None:
    balance = {"total_assets": 10636.10, "total_liabilities": 8009.98, "shares": 11.4552}
    assert from_yahoo_info({}, balance).book_value == 229.25
    assert from_yahoo_info({"bookValue": 252.83}, balance).book_value == 252.83
    no_shares = {"total_assets": 10636.10, "total_liabilities": 8009.98, "shares": None}
    assert from_yahoo_info({"sharesOutstanding": 11.4552}, no_shares).book_value == 229.25   # share count from info
    assert from_yahoo_info({}, no_shares).book_value is None


def test_latest_balance_reads_the_newest_period() -> None:
    sheet = pd.DataFrame(
        {pd.Timestamp("2025-03-31"): [900.0, 500.0, 10.0], pd.Timestamp("2026-03-31"): [1000.0, 600.0, 10.0]},
        index=["Total Assets", "Total Liabilities Net Minority Interest", "Ordinary Shares Number"])
    assert latest_balance(sheet) == {"total_assets": 1000.0, "total_liabilities": 600.0, "shares": 10.0}
    assert latest_balance(sheet.drop(index="Ordinary Shares Number"))["shares"] is None
    assert latest_balance(sheet.drop(index="Total Assets")) is None
    assert latest_balance(pd.DataFrame()) is None and latest_balance(None) is None


class BookRecorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, symbol: str) -> Fundamentals:
        self.calls.append(symbol)
        return {"A.NS": Fundamentals(200_000, 0.1, 252.83), "NOBOOK.NS": Fundamentals(5_000, 0.3, None)}.get(symbol, Fundamentals())


def test_book_value_is_fetched_even_when_the_stock_list_has_cap_and_debt(tmp_path: Path) -> None:
    fetch, clock = BookRecorder(), [datetime(2026, 11, 14, 9, 0)]
    service = make_service(tmp_path, fetch, clock)
    complete = Ticker("A.NS", market_cap_cr=10, debt_to_equity=0.2)   # cap and debt already in the stock list

    one = service.enrich([complete])[0]
    assert (one.market_cap_cr, one.debt_to_equity, one.book_value) == (10, 0.2, 252.83)   # list values kept
    assert service.enrich_one(complete).book_value == 252.83 and fetch.calls == ["A.NS"]   # cached

    assert service.enrich_one(Ticker("NOBOOK.NS")).book_value is None
    service.enrich_one(Ticker("NOBOOK.NS"))
    assert fetch.calls.count("NOBOOK.NS") == 1          # Yahoo has no book value: not asked again while fresh


def test_cache_entry_from_before_book_value_is_fetched_once_more(tmp_path: Path) -> None:
    path = tmp_path / "data" / "fundamentals.json"
    path.parent.mkdir()
    path.write_text('{"A.NS": {"market_cap_cr": 200000, "debt_to_equity": 0.1, "fetched_at": "2026-11-14T08:00:00"}}',
                    encoding="utf-8")
    fetch = BookRecorder()
    service = make_service(tmp_path, fetch, [datetime(2026, 11, 14, 9, 0)])
    assert service.enrich([Ticker("A.NS")])[0].book_value == 252.83
    assert service.enrich_one(Ticker("A.NS")).book_value == 252.83 and fetch.calls == ["A.NS"]


def test_corrupt_cache_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "data" / "fundamentals.json"
    path.parent.mkdir()
    path.write_text("[not a dict", encoding="utf-8")
    service = make_service(tmp_path, Recorder(), [datetime(2026, 11, 14)])
    assert service.enrich_one(Ticker("A.NS")).market_cap_cr == 200_000
    path.write_text('{"A.NS": "junk", "B.NS": {"fetched_at": "not a date"}}', encoding="utf-8")
    assert service.enrich_one(Ticker("B.NS")).market_cap_cr == 40_000


def fake_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {symbol: make_prices(SANSERA_CLOSES) for symbol in symbols}


def run_to_end(scanner: Scanner) -> dict:
    import time

    scanner.start()
    deadline = time.monotonic() + 5
    while scanner.progress()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    return scanner.progress()


def test_scanner_uses_enrichment_and_survives_its_failure(tmp_path: Path) -> None:
    store = ScanStore(tmp_path / "scan.json")
    universe = [Ticker("A.NS"), Ticker("B.NS")]

    def enrich(tickers, on_progress):
        on_progress(len(tickers), len(tickers))
        return [Ticker(t.symbol, market_cap_cr=200_000, debt_to_equity=0.1) for t in tickers]

    progress = run_to_end(Scanner(lambda: universe, fake_fetch, store, batch_size=5, enrich=enrich))
    assert progress["error"] is None and progress["stage"] == "prices"
    assert {row["cap_category"] for row in store.load()["rows"]} == {"Large"}

    def broken(tickers, on_progress):
        raise RuntimeError("yahoo down")

    progress = run_to_end(Scanner(lambda: universe, fake_fetch, store, batch_size=5, enrich=broken))
    assert progress["error"] is None
    assert {row["cap_category"] for row in store.load()["rows"]} == {""}   # scanned without fundamentals
