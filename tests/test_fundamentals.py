from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_entry.fundamentals import Fundamentals, FundamentalsService, from_yahoo_info
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
    assert sorted(fetch.calls) == ["A.NS", "B.NS", "BOOM.NS", "NONE.NS"]                          # CSV.NS not fetched
    assert seen[0] == (1, 5) and seen[-1] == (5, 5)

    fetch.calls.clear()
    service.enrich(tickers)
    assert sorted(fetch.calls) == ["BOOM.NS", "NONE.NS"]      # successes cached, failures retried

    clock[0] += timedelta(days=8)
    fetch.calls.clear()
    assert service.enrich_one(Ticker("A.NS")).market_cap_cr == 200_000
    assert fetch.calls == ["A.NS"]                            # stale entry refreshed


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
