"""Rising parallel channel on 2-hour bars and its entries at the lower line."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stock_entry.channel_2h import (ENTRY_1, ENTRY_2, PREFIX, Touch, channel_fields, empty_channel, entry_marks,
                                    find, touches)

PERIOD = 24   # bars from one low of the zigzag to the next
WIDTH = 8.0


def zigzag(bars: int = 120, slope: float = 0.1, start: float = 100.0) -> pd.DataFrame:
    """A price that walks between two rising parallel lines: lows on the lower line every PERIOD bars."""
    index = np.arange(bars)
    phase = index % PERIOD
    share = np.where(phase <= PERIOD // 2, phase, PERIOD - phase) / (PERIOD // 2)   # 0 at a low, 1 at a high
    middle = start + slope * index + share * WIDTH
    dates = pd.date_range("2026-01-05 09:15", periods=bars, freq="2h")
    return pd.DataFrame({"Date": dates, "Open": middle, "High": middle + 0.2, "Low": middle - 0.2,
                         "Close": middle, "Volume": 1000.0})


def test_touches_need_a_cross_of_the_middle_between_them() -> None:
    #            touch, still low, middle crossed, touch, touch again without a cross, cross, touch
    low = np.array([0.0, 0.5, 3.0, 0.2, 0.1, 4.0, 1.0])
    high = np.array([1.0, 1.5, 6.0, 1.2, 1.1, 7.0, 2.0])
    found = touches(slope=0.0, lower=0.0, width=10.0, first=0, stop=len(low), high=high, low=low)
    assert found == (Touch(0, 1), Touch(3, 2), Touch(6, 3))


def test_a_bar_that_stops_before_the_end_is_not_counted() -> None:
    low = np.array([0.0, 6.0, 0.0])
    high = np.array([1.0, 7.0, 1.0])
    assert touches(0.0, 0.0, 10.0, 0, 2, high, low) == (Touch(0, 1),)


def test_rising_two_hour_channel_is_found_with_its_entries() -> None:
    bars = zigzag()
    fit = find(bars)
    assert fit is not None and fit.first == 72 and fit.break_bar is None
    assert [touch.number for touch in fit.touches] == [1, 2, 3]
    fields = channel_fields(bars)
    assert fields[f"{PREFIX}channel"] is True
    assert fields[f"{PREFIX}bars"] == 48 and fields[f"{PREFIX}touches"] == 3
    assert fields[f"{PREFIX}slope_pct"] > 0.3                      # per day: steeper than a flat channel
    # the lower line rises 0.1 a bar over the 47 bars from the first anchor to the latest bar
    assert round(fields[f"{PREFIX}lower"] - fields[f"{PREFIX}start_lower"], 2) == 4.7
    assert round(fields[f"{PREFIX}upper"] - fields[f"{PREFIX}lower"], 2) == 8.4
    assert fields[f"{PREFIX}final"] == ENTRY_2 and fields[f"{PREFIX}zone"] == "Near lower line"
    assert fields[f"{PREFIX}entry1_at"] < fields[f"{PREFIX}entry2_at"] == fields[f"{PREFIX}final_at"]
    marks = entry_marks(bars)
    assert sorted(marks.values()) == [ENTRY_1, ENTRY_2]
    assert marks[fields[f"{PREFIX}entry1_at"]] == ENTRY_1 and marks[fields[f"{PREFIX}entry2_at"]] == ENTRY_2


def test_only_the_second_touch_so_far_is_entry_1() -> None:
    bars = zigzag(bars=114)   # the price has not come back to the lower line after the second touch
    fields = channel_fields(bars)
    assert fields[f"{PREFIX}channel"] is True and fields[f"{PREFIX}touches"] == 2
    assert fields[f"{PREFIX}final"] == ENTRY_1 and fields[f"{PREFIX}entry2_at"] is None


def test_a_channel_shorter_than_40_bars_is_not_listed() -> None:
    assert channel_fields(zigzag(bars=108)) == empty_channel()    # 37 bars from the first anchor


def test_flat_and_falling_channels_are_not_listed() -> None:
    assert channel_fields(zigzag(slope=0.0)) == empty_channel()
    assert channel_fields(zigzag(slope=-0.1, start=200.0)) == empty_channel()


def test_no_bars_give_empty_fields_and_no_marks() -> None:
    assert channel_fields(None) == empty_channel() and entry_marks(None) == {}
    assert channel_fields(pd.DataFrame()) == empty_channel()
    assert channel_fields(pd.DataFrame({"Date": [], "Close": []})) == empty_channel()


def test_a_close_below_the_lower_line_is_a_breakdown_and_ends_the_touch_count() -> None:
    bars = zigzag()
    low_line = 100.0 + 0.1 * 119
    broken = bars.copy()
    broken.loc[119, ["Open", "High", "Low", "Close"]] = [low_line - 1, low_line - 0.5, low_line - 3, low_line - 2]
    fields = channel_fields(broken)
    assert fields[f"{PREFIX}zone"] == "Breakdown" and fields[f"{PREFIX}break_at"] == "2026-01-15 07:15"


def test_csv_apply_and_stock_detail_carry_the_channel() -> None:
    from stock_entry.apply_two_hour_csv import apply_bars
    from stock_entry.history import _with_p_final

    scan = {"rows": [{"ticker": "UP.NS", "at_closing_high": True}, {"ticker": "GONE.NS"}]}
    updated, _ = apply_bars(scan, {"UP.NS": zigzag()})
    in_csv, missing = updated["rows"]
    assert in_csv[f"{PREFIX}final"] == ENTRY_2            # looked for at a closing high too
    assert missing[f"{PREFIX}channel"] is False           # not in the CSV: empty fields
    assert scan["rows"][0] == {"ticker": "UP.NS", "at_closing_high": True}   # the input is unchanged
    points = _with_p_final([{"time": "2026-01-05 09:15"}, {"time": "2026-01-05 11:15"}], {"2026-01-05 11:15": ENTRY_1})
    assert [point["p_final"] for point in points] == [None, ENTRY_1]
