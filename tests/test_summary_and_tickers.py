from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from stock_entry.signals import add_entry_signals
from stock_entry.summary import history_records, summarize_latest
from stock_entry.tickers import InvalidSymbolError, load_tickers, normalize_symbol
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.unit


def test_summary_on_entry_day() -> None:
    signals = add_entry_signals(make_prices(SANSERA_CLOSES[:30]))  # last bar = 09 Nov entry
    row = summarize_latest("SANSERA.NS", signals, "Sansera Engineering", "Auto")
    assert row["status"] == "entry"
    assert row["date"] == "2026-11-09"
    assert row["entry_price"] == 4653.4
    assert row["last_entry_date"] == "2026-11-09"
    assert row["sessions_since_entry"] == 0
    assert row["name"] == "Sansera Engineering"
    assert row["valid"] is True


def test_summary_on_wait_day() -> None:
    signals = add_entry_signals(make_prices(SANSERA_CLOSES[:24]))  # last bar = 30 Oct, second wait day
    row = summarize_latest("SANSERA.NS", signals)
    assert row["status"] == "wait"
    assert row["wait_days"] == 2
    assert row["breakout_date"] == "2026-10-28"
    assert row["breakout_close"] == 4310.0
    assert row["pullback_pct"] == round((4111.6 / 4310.0 - 1) * 100, 2)
    assert row["valid"] is None
    assert row["entry_price"] is None
    assert row["last_entry_date"] is None


def test_summary_after_entry_tracks_gain(sansera: pd.DataFrame) -> None:
    row = summarize_latest("SANSERA.NS", add_entry_signals(sansera))
    assert row["status"] == ""
    assert row["last_entry_price"] == 4653.4
    assert row["sessions_since_entry"] == 2
    assert row["since_entry_pct"] == round((4701.8 / 4653.4 - 1) * 100, 2)


def test_summary_and_history_are_json_safe(sansera: pd.DataFrame) -> None:
    signals = add_entry_signals(sansera)
    payload = {"summary": summarize_latest("X.NS", signals), "history": history_records(signals, 10)}
    text = json.dumps(payload, allow_nan=False)  # raises if any NaN / numpy type leaks through
    assert len(payload["history"]) == 10
    assert payload["history"][-1]["Date"] == "2026-11-12"
    assert '"valid": null' in text


def test_summary_rejects_empty_frame(sansera: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="no price bars"):
        summarize_latest("X.NS", add_entry_signals(sansera).iloc[0:0])


@pytest.mark.parametrize(("typed", "expected"), [
    ("tcs", "TCS.NS"), ("  Reliance ", "RELIANCE.NS"), ("M&M", "M&M.NS"),
    ("BAJAJ-AUTO.NS", "BAJAJ-AUTO.NS"), ("500325.BO", "500325.BO"),
])
def test_normalize_symbol(typed: str, expected: str) -> None:
    assert normalize_symbol(typed) == expected


@pytest.mark.parametrize("typed", ["", "   ", "../etc/passwd", "TCS;DROP", "<script>", "A" * 40, "TCS.NS.X"])
def test_normalize_symbol_rejects_bad_input(typed: str) -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(typed)


def test_load_tickers_filters_sector_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "tickers.csv"
    path.write_text(
        "Symbol,Company Name,Industry\n"
        "TCS,Tata Consultancy,Information Technology\n"
        "HDFCBANK,HDFC Bank,Financial Services\n"
        "TCS,Duplicate,Information Technology\n"
        "bad symbol!,Junk,Other\n"
        ",Missing,Other\n"
        "INFY.NS,,\n",
        encoding="utf-8",
    )
    tickers = load_tickers(path, excluded_sectors=("financial services",))
    assert [t.symbol for t in tickers] == ["TCS.NS", "INFY.NS"]
    assert tickers[0].name == "Tata Consultancy"
    assert tickers[1].name == "" and tickers[1].sector == ""


def test_load_tickers_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_tickers(tmp_path / "missing.csv")
    no_column = tmp_path / "a.csv"
    no_column.write_text("Foo\nTCS\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Ticker"):
        load_tickers(no_column)
    empty = tmp_path / "b.csv"
    empty.write_text("Ticker\n!!!\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no usable"):
        load_tickers(empty)
