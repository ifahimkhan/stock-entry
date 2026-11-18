"""Day-wise update of TBC_all_stocks_bars.csv: merge fetched bars, recompute signals, rebuild the summary."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_entry.update_two_hour_csv import (BAR_COLUMNS, build_summary, fetched_rows, main, merge_bars,
                                             notebook_signals, update)

pytestmark = pytest.mark.unit


def price_rows(ticker: str, times: pd.DatetimeIndex, seed: int = 1) -> pd.DataFrame:
    close = 100 + np.cumsum(np.random.default_rng(seed).normal(0, 2, len(times)))
    return pd.DataFrame({"Ticker": ticker, "datetime": times.strftime("%Y-%m-%d %H:%M:%S"), "symbol": f"NSE:{ticker}",
                         "open": close, "high": close + 1.5, "low": close - 1.5, "close": close, "volume": 1000.0})


def tv_bars(times: pd.DatetimeIndex, close: float) -> pd.DataFrame:
    """Bars as TradingViewFeed returns them."""
    return pd.DataFrame({"Date": times, "Open": close, "High": close + 1, "Low": close - 1, "Close": close,
                         "Volume": 5.0, "Source": "tradingview"})


def csv_file(path: Path, tickers: dict[str, int]) -> Path:
    frames = [notebook_signals(price_rows(t, pd.date_range("2026-08-03 09:15", periods=n, freq="2h"), i))
              for i, (t, n) in enumerate(tickers.items())]
    pd.concat(frames, ignore_index=True).to_csv(path, index=False)
    return path


def test_fetched_rows_use_the_notebook_columns() -> None:
    rows = fetched_rows("BAJAJ-AUTO", tv_bars(pd.date_range("2026-11-19 09:15", periods=2, freq="2h"), 10.0))
    assert list(rows.columns) == ["Ticker", "datetime", "symbol", "open", "high", "low", "close", "volume"]
    assert rows["symbol"].iloc[0] == "NSE:BAJAJ_AUTO" and rows["datetime"].iloc[0] == "2026-11-19 09:15:00"


def test_merge_appends_new_bars_replaces_same_time_and_keeps_the_latest() -> None:
    times = pd.date_range("2026-08-03 09:15", periods=60, freq="2h")
    bars = notebook_signals(price_rows("KRN", times))
    new_times = pd.DatetimeIndex([times[-1], times[-1] + pd.Timedelta(hours=2), times[-1] + pd.Timedelta(hours=4)])
    merged, added, replaced = merge_bars(bars, {"KRN": tv_bars(new_times, 555.0)}, keep=50)
    assert (added, replaced) == (2, 1)
    assert len(merged) == 50 and merged["datetime"].is_monotonic_increasing
    assert merged["close"].iloc[-3:].tolist() == [555.0] * 3   # the refetched bar replaced the old one
    assert list(merged.columns) == BAR_COLUMNS
    assert len(bars) == 60   # the input is not changed


def test_stocks_not_fetched_keep_their_bars() -> None:
    bars = notebook_signals(price_rows("KRN", pd.date_range("2026-08-03 09:15", periods=30, freq="2h")))
    merged, added, replaced = merge_bars(bars, {}, keep=500)
    assert (added, replaced) == (0, 0)
    pd.testing.assert_frame_equal(merged[["datetime", "close"]], bars[["datetime", "close"]])


def test_summary_carries_name_sector_and_market_cap() -> None:
    bars = notebook_signals(price_rows("KRN", pd.date_range("2026-08-03 09:15", periods=80, freq="2h")))
    previous = pd.DataFrame({"Ticker": ["KRN"], "Stock Name": ["KRN Ltd"], "Sector": ["Tech"], "MarketCap_Cr": [5464.0]})
    summary = build_summary(bars, previous)
    row = summary.iloc[0]
    assert row["Stock Name"] == "KRN Ltd" and row["bars"] == 80 and row["last_bar"] == bars["datetime"].iloc[-1]
    assert list(summary.columns[:4]) == ["Ticker", "Stock Name", "Sector", "MarketCap_Cr"]
    assert build_summary(bars, None)["Stock Name"].isna().all()


def test_update_writes_both_files(tmp_path: Path) -> None:
    path = csv_file(tmp_path / "bars.csv", {"KRN": 40, "TCS": 40})
    summary_path = tmp_path / "summary.csv"
    nxt = pd.date_range("2026-11-19 09:15", periods=2, freq="2h")
    result = update(path, summary_path, lambda symbols: {"KRN.NS": tv_bars(nxt, 1.0), "TCS.NS": tv_bars(nxt, 2.0)}, 2, 500)
    assert (result.fetched, result.added, result.missing) == (2, 4, ())
    assert result.last_bar == "2026-11-19 11:15:00"
    assert len(pd.read_csv(path)) == 84 and len(pd.read_csv(summary_path)) == 2


def test_update_leaves_the_files_alone_when_too_few_stocks_came_back(tmp_path: Path) -> None:
    path = csv_file(tmp_path / "bars.csv", {"KRN": 40, "TCS": 40, "INFY": 40})
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="not changed"):
        update(path, tmp_path / "summary.csv", lambda symbols: {}, 4, 500)
    assert path.read_bytes() == before and not (tmp_path / "summary.csv").exists()


def test_main_rejects_bad_bar_counts() -> None:
    with pytest.raises(SystemExit):
        main(["--bars", "0"])
