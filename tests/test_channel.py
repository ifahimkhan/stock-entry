"""Parallel channel: two parallel lines through swing points that enclose the price."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stock_entry.channel import (ASCENDING, BREAKDOWN, BREAKOUT_UP, DESCENDING, HORIZONTAL, INSIDE,
                                 apply_to_scan, empty_channel, parallel_channel, pivots)

WAVE = 16   # bars per swing: a low every 16 bars, a high in between


def zigzag(count: int, slope: float, start: float = 100.0, width: float = 10.0) -> pd.DataFrame:
    """Bars that bounce between two parallel lines `width` apart, rising `slope` per bar."""
    bar = np.arange(count)
    phase = (bar % WAVE) / WAVE
    swing = np.where(phase < 0.5, phase * 2, 2 - phase * 2)   # 0 at a low, 1 at a high
    middle = start + slope * bar + (swing - 0.5) * (width - 1.0)
    return pd.DataFrame({"Date": pd.date_range("2026-01-05", periods=count, freq="B"),
                         "High": middle + 0.5, "Low": middle - 0.5, "Close": middle})


def with_last_close(bars: pd.DataFrame, close: float) -> pd.DataFrame:
    last = bars.index[-1]
    return bars.assign(Close=bars["Close"].where(bars.index != last, close),
                       High=bars["High"].where(bars.index != last, close + 0.5),
                       Low=bars["Low"].where(bars.index != last, close - 0.5))


def test_pivots_are_the_extreme_of_five_bars_on_each_side() -> None:
    bars = zigzag(60, 0.0)
    assert list(pivots(bars["Low"].to_numpy(), high=False)) == [16, 32, 48]
    assert list(pivots(bars["High"].to_numpy(), high=True)) == [8, 24, 40]


def test_rising_falling_and_flat_channels_are_told_apart() -> None:
    rising = parallel_channel(zigzag(84, 1.0))
    assert rising["channel"] is True and rising["channel_type"] == ASCENDING and rising["channel_status"] == INSIDE
    assert rising["channel_slope_pct"] > 0.5 and rising["channel_upper"] > rising["channel_lower"]
    assert parallel_channel(zigzag(90, -1.0, start=200.0))["channel_type"] == DESCENDING
    flat = parallel_channel(zigzag(84, 0.0))
    assert flat["channel_type"] == HORIZONTAL and flat["channel_slope_pct"] == 0.0
    assert (flat["channel_lower"], flat["channel_upper"]) == (95.0, 105.0)   # lows at 95, highs at 105
    assert flat["channel_width_pct"] == 10.0 and 0 <= flat["channel_position_pct"] <= 100


def test_a_close_outside_the_lines_is_a_breakout_or_a_breakdown() -> None:
    bars = zigzag(84, 0.0)
    up = parallel_channel(with_last_close(bars, 108.0))
    assert up["channel_status"] == BREAKOUT_UP and up["channel_zone"] == BREAKOUT_UP
    assert up["channel_break_date"] == pd.Timestamp(bars["Date"].iloc[-1]).strftime("%Y-%m-%d")
    assert parallel_channel(with_last_close(bars, 92.0))["channel_status"] == BREAKDOWN


def test_no_channel_without_swings_or_price_columns() -> None:
    line = pd.DataFrame({"Date": pd.date_range("2026-01-05", periods=84, freq="B"),
                         "High": np.arange(84) + 101.0, "Low": np.arange(84) + 99.0, "Close": np.arange(84) + 100.0})
    assert parallel_channel(line) == empty_channel()                      # a straight rise has no swing points
    assert parallel_channel(zigzag(84, 0.0)[["Date", "Close"]]) == empty_channel()
    assert parallel_channel(zigzag(84, 0.0, width=2.0)) == empty_channel()   # narrower than the minimum width


def test_apply_to_scan_fills_every_row_and_leaves_the_input_alone() -> None:
    scan = {"rows": [{"ticker": "A.NS"}, {"ticker": "B.NS"}]}
    updated, counts = apply_to_scan(scan, {"A.NS": zigzag(84, 0.0)})
    assert counts == {HORIZONTAL: 1} and updated["rows"][0]["channel"] is True
    assert updated["rows"][1] == {"ticker": "B.NS", **empty_channel()} and scan["rows"][0] == {"ticker": "A.NS"}
