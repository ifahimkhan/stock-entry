"""Daily bars on disk: full history once, then only the missing days, oldest dropped to keep the window."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_entry.daily_store import DailyBarStore, merge_bars, period_for, read_store

pytestmark = pytest.mark.unit


def bars(start: str, days: int, close: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=days)
    closes = [close + step * i for i in range(days)]
    return pd.DataFrame({"Date": dates, "Open": closes, "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Close": closes, "Volume": 1000.0})


class FakeYahoo:
    """fetch_period stand-in: serves slices of a fixed history and records each request."""

    def __init__(self, history: dict[str, pd.DataFrame]) -> None:
        self.history, self.calls = history, []

    def __call__(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
        self.calls.append((sorted(symbols), period))
        days = {"5d": 5, "1mo": 22, "3mo": 66}.get(period)
        return {s: (self.history[s].tail(days) if days else self.history[s]).reset_index(drop=True)
                for s in symbols if s in self.history}


def test_period_for_picks_the_shortest_covering_period() -> None:
    assert period_for(0, "2y") == "5d" and period_for(1, "2y") == "5d"
    assert period_for(10, "2y") == "1mo" and period_for(60, "2y") == "3mo" and period_for(5000, "2y") == "2y"


def test_merge_replaces_same_days_appends_new_and_drops_the_oldest() -> None:
    old = bars("2026-01-01", 10)
    new = bars("2026-01-14", 3, close=500.0)       # last stored day + 2 new days
    merged = merge_bars(old, new, keep=10)
    assert len(merged) == 10 and merged["Date"].is_monotonic_increasing
    assert merged["Date"].iloc[0] == old["Date"].iloc[2]       # 2 new days in, 2 oldest out
    assert merged["Close"].iloc[-3:].tolist() == [500.0, 501.0, 502.0]   # the refetched day was replaced


def test_first_run_downloads_full_history_then_only_recent_days(tmp_path: Path) -> None:
    full = {"A.NS": bars("2025-01-01", 300), "B.NS": bars("2025-01-01", 300, close=50.0)}
    yahoo = FakeYahoo({s: f.head(295) for s, f in full.items()})
    path = tmp_path / "daily.csv"
    store = DailyBarStore(path, yahoo, "2y", keep=250, today=lambda: date(2026, 2, 20))
    first = store(["A.NS", "B.NS"])
    assert yahoo.calls == [(["A.NS", "B.NS"], "2y")] and len(first["A.NS"]) == 250
    store.save()
    assert path.is_file() and set(read_store(path)) == {"A.NS", "B.NS"}

    # Next run: a new store reads the file; Yahoo now has 5 more days.
    yahoo.history = full
    last = read_store(path)["A.NS"]["Date"].max().date()
    store2 = DailyBarStore(path, yahoo, "2y", keep=250, today=lambda: last)
    yahoo.calls.clear()
    second = store2(["A.NS", "B.NS"])
    assert yahoo.calls == [(["A.NS", "B.NS"], "5d")]   # no 2-year download
    assert len(second["A.NS"]) == 250
    assert second["A.NS"]["Date"].max() == full["A.NS"]["Date"].max()
    assert second["A.NS"]["Date"].min() > first["A.NS"]["Date"].min()   # oldest days dropped


def test_split_triggers_a_full_refetch_and_new_stocks_are_downloaded_fully(tmp_path: Path) -> None:
    old = bars("2025-01-01", 100)
    split = old.assign(Close=old["Close"] / 2)          # Yahoo's past prices halved by a split
    yahoo = FakeYahoo({"A.NS": split, "NEW.NS": bars("2025-01-01", 100)})
    store = DailyBarStore(tmp_path / "d.csv", yahoo, "2y", keep=500, today=lambda: date(2025, 5, 21))
    store._frames = {"A.NS": old}
    got = store(["A.NS", "NEW.NS"])
    assert (["A.NS"], "5d") in yahoo.calls
    assert sorted(s for call in yahoo.calls if call[1] == "2y" for s in call[0]) == ["A.NS", "NEW.NS"]
    assert got["A.NS"]["Close"].iloc[0] == split["Close"].iloc[0]


def test_a_stock_missing_from_the_reply_keeps_its_bars_and_save_skips_when_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "d.csv"
    store = DailyBarStore(path, lambda symbols, period: {}, "2y", keep=500, today=lambda: date(2025, 2, 1))
    store._frames = {"A.NS": bars("2025-01-01", 20)}
    assert len(store(["A.NS"])["A.NS"]) == 20
    store._dirty = False
    store.save()
    assert not path.exists()
