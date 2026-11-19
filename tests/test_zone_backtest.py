"""TBC Zone Entry win rate: trade scoring, merging saved trades and the summary."""
from __future__ import annotations

import pandas as pd
import pytest

from stock_entry.zone_backtest import (TRADE_COLUMNS, Signal, closing_high_days, merge_trades, score,
                                       summarize)

pytestmark = pytest.mark.unit


def daily(rows: list[tuple[float, float, float, float]], start: str = "2026-01-05") -> pd.DataFrame:
    """Daily bars from (open, high, low, close) tuples, one business day apart."""
    dates = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame([{"Date": d, "Open": o, "High": h, "Low": lo, "Close": c}
                         for d, (o, h, lo, c) in zip(dates, rows)], columns=["Date", "Open", "High", "Low", "Close"])


SIGNAL = Signal("1d", "A.NS", pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02"), "3LL-HH", 99.0, 4.2)


def test_entry_is_the_next_session_open_and_a_target_hit_is_a_win() -> None:
    trade = score(SIGNAL, daily([(100, 102, 99, 101), (101, 106, 100, 105)]))
    assert trade["entry"] == 100 and str(trade["entry_at"])[:10] == "2026-01-05"
    assert (trade["outcome"], trade["exit"], trade["return_pct"], trade["sessions"]) == ("win", 105.0, 5.0, 2)


def test_stop_hit_is_a_loss_and_both_in_one_candle_counts_as_a_loss() -> None:
    assert score(SIGNAL, daily([(100, 101, 96.5, 98)]))["outcome"] == "loss"
    both = score(SIGNAL, daily([(100, 106, 96, 101)]))
    assert (both["outcome"], both["return_pct"]) == ("loss", -3.0)


def test_a_gap_through_a_level_fills_at_the_open() -> None:
    gap_up = score(SIGNAL, daily([(100, 101, 99, 100), (110, 112, 109, 111)]))
    assert (gap_up["outcome"], gap_up["exit"], gap_up["return_pct"]) == ("win", 110.0, 10.0)
    gap_down = score(SIGNAL, daily([(100, 101, 99, 100), (90, 91, 88, 89)]))
    assert (gap_down["outcome"], gap_down["return_pct"]) == ("loss", -10.0)


def test_no_level_hit_is_a_timeout_after_max_sessions_else_open() -> None:
    flat = [(100, 101, 99, 100.5)] * 20
    timeout = score(SIGNAL, daily(flat))
    assert (timeout["outcome"], timeout["sessions"], timeout["return_pct"]) == ("timeout", 20, 0.5)
    assert score(SIGNAL, daily(flat[:7]))["outcome"] == "open"
    assert score(SIGNAL, daily([], start="2026-01-05"))["outcome"] == "open"   # no session after the signal yet


def trade(ticker: str, outcome: str, ret: float, hh: str = "2026-01-01", pattern: str = "3LL-HH") -> dict:
    return {"timeframe": "1d", "ticker": ticker, "hh_at": pd.Timestamp(hh), "signal_at": pd.Timestamp("2026-01-02"),
            "pattern": pattern, "hh_close": 1.0, "change_pct": 1.0, "entry_at": None, "entry": 1.0,
            "outcome": outcome, "exit_at": None, "exit": 1.0, "return_pct": ret, "sessions": 3}


def test_merge_adds_only_new_trades_and_keeps_every_saved_one() -> None:
    old = pd.DataFrame([trade("WIN.NS", "win", 5), trade("OPEN.NS", "open", 1), trade("OLD.NS", "loss", -3)],
                       columns=TRADE_COLUMNS)
    new = pd.DataFrame([trade("WIN.NS", "loss", -3), trade("OPEN.NS", "win", 5), trade("NEW.NS", "open", 0)],
                       columns=TRADE_COLUMNS)
    merged = merge_trades(old, new).set_index("ticker")["outcome"].to_dict()
    # a saved trade keeps its signal; an open one is scored again later by rescore, not replaced here
    assert merged == {"WIN.NS": "win", "OPEN.NS": "open", "OLD.NS": "loss", "NEW.NS": "open"}


def test_summary_win_rate_ignores_timeouts_and_open_trades() -> None:
    trades = pd.DataFrame([trade("A", "win", 5), trade("B", "win", 5, pattern="4LL-HH"), trade("C", "loss", -3),
                           trade("D", "timeout", 1), trade("E", "open", 2)], columns=TRADE_COLUMNS)
    rows = {(r["timeframe"], r["pattern"]): r for r in summarize(trades)}
    every = rows[("1d", "all")]
    assert (every["trades"], every["wins"], every["losses"], every["timeouts"], every["open"]) == (4, 2, 1, 1, 1)
    assert every["win_rate"] == pytest.approx(66.7) and every["avg_return"] == pytest.approx(2.0)
    assert rows[("1d", "4LL-HH")]["win_rate"] == 100.0 and rows[("2h", "all")]["win_rate"] is None


def test_closing_high_days_uses_greater_or_equal() -> None:
    bars = daily([(1, 1, 1, c) for c in (10.0, 12.0, 11.0, 12.0, 13.0)])
    assert sorted(d.strftime("%d") for d in closing_high_days(bars)) == ["06", "08", "09"]


def test_rescore_updates_open_trades_and_leaves_finished_ones() -> None:
    from stock_entry.zone_backtest import rescore

    bars = daily([(100, 101, 99, 100), (101, 106, 100, 105)])
    saved = pd.DataFrame([trade("A.NS", "open", 0), trade("B.NS", "loss", -3)], columns=TRADE_COLUMNS)
    saved["signal_at"] = pd.Timestamp("2026-01-02")
    out = rescore(saved, {"A.NS": bars, "B.NS": bars}).set_index("ticker")
    assert out.loc["A.NS", "outcome"] == "win" and out.loc["B.NS", "outcome"] == "loss"
