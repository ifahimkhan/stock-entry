"""Tight range: the close stays within +/-2% of the run's first close for 7 or more trading days."""
from __future__ import annotations

import pandas as pd

from stock_entry.tight_range import TIGHT_DAYS, apply_to_scan, empty_tight_range, tight_range


def bars(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Date": pd.date_range("2026-01-05", periods=len(closes), freq="B"), "Close": closes})


def test_seven_closes_within_two_percent_of_the_first_are_a_tight_range() -> None:
    closes = [90.0, 120.0, 100.0, 101.0, 99.0, 100.5, 98.2, 101.9, 100.0]   # the run starts at 100.0
    found = tight_range(bars(closes))
    assert found["tight_range"] is True and found["tight_days"] == TIGHT_DAYS
    assert found["tight_base_close"] == 100.0 and found["tight_base_date"] == "2026-01-07"
    assert (found["tight_low_pct"], found["tight_high_pct"], found["tight_change_pct"]) == (-1.8, 1.9, 0.0)


def test_one_close_outside_the_band_breaks_the_run() -> None:
    above = [100.0, 101.0, 99.0, 102.1, 100.0, 100.0, 100.0]   # +2.1% on day 4
    below = [100.0, 101.0, 99.0, 97.9, 100.0, 100.0, 100.0]    # -2.1% on day 4
    assert tight_range(bars(above)) == empty_tight_range()
    assert tight_range(bars(below)) == empty_tight_range()
    exact = [100.0, 102.0, 98.0, 100.0, 100.0, 100.0, 100.0]   # exactly +/-2% still counts
    assert tight_range(bars(exact))["tight_range"] is True


def test_six_days_are_not_enough_and_the_longest_run_is_reported() -> None:
    assert tight_range(bars([100.0] * 6)) == empty_tight_range()
    assert tight_range(bars([50.0] + [100.0] * 12))["tight_days"] == 12
    # the 7-day window (base 103) fails, yet a longer run from the base 101 holds: runs are not nested
    closes = [101.0, 101.0, 103.0, 100.9, 101.0, 101.0, 101.0, 101.0, 101.0]
    assert tight_range(bars(closes))["tight_days"] == 9


def test_apply_to_scan_fills_every_row_and_leaves_the_input_alone() -> None:
    scan = {"data_date": "2026-01-16", "rows": [{"ticker": "A.NS"}, {"ticker": "B.NS"}, {"ticker": "C.NS"}]}
    frames = {"A.NS": bars([100.0] * 8), "B.NS": bars([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0])}
    updated, found = apply_to_scan(scan, frames)
    assert found == 1 and [row["tight_range"] for row in updated["rows"]] == [True, False, False]
    assert updated["rows"][2]["tight_days"] is None           # no bars for C.NS: empty fields, not missing ones
    assert scan["rows"][0] == {"ticker": "A.NS"}              # not mutated


def test_a_fall_before_the_tight_range_is_measured_from_the_highest_close() -> None:
    from stock_entry.tight_range import FALL_DAYS

    flat = [100.0] * 7                                           # the run: base 100.0
    fell = tight_range(bars([80.0] * 5 + [112.0, 108.0, 104.0] + flat))
    assert fell["tight_after_fall"] is True and fell["tight_fall_pct"] == -10.71    # 100 against the high 112
    assert fell["tight_peak_close"] == 112.0 and fell["tight_peak_date"] == "2026-01-12"
    small = tight_range(bars([105.0, 104.0, 103.0] + flat))      # only -4.76% from 105
    assert small["tight_range"] is True and small["tight_after_fall"] is False and small["tight_fall_pct"] == -4.76
    rose = tight_range(bars([80.0, 85.0, 90.0] + flat))          # the base is the highest close: no fall
    assert rose["tight_after_fall"] is False and rose["tight_fall_pct"] == 0.0
    old = tight_range(bars([150.0] + [100.0, 105.0] * (FALL_DAYS // 2) + flat))   # 150 is older than the look-back
    assert old["tight_days"] == 7 and old["tight_after_fall"] is False and old["tight_peak_close"] == 105.0
    assert tight_range(bars(flat))["tight_after_fall"] is False   # no bars before the run


def volume_bars(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    return bars(closes).assign(Volume=volumes)


def test_entry_is_an_up_breakout_with_the_biggest_volume_of_the_range() -> None:
    from stock_entry.tight_range import empty_tight_entry, tight_entry

    before = [90.0 + i for i in range(20)]                    # 20 rising days, volume 100 each
    flat = [120.0, 121.0, 119.0, 120.5, 120.0, 121.5, 120.0]  # 7 days within +/-2% of 120
    closes, quiet = before + flat + [123.0], [100.0] * 20 + [150.0, 90.0, 80.0, 110.0, 95.0, 85.0, 90.0]
    found = tight_entry(volume_bars(closes, quiet + [200.0]))   # +2.5% on the biggest volume
    assert found["tight_entry"] is True and found["tight_entry_days_ago"] == 0
    assert (found["tight_entry_range_days"], found["tight_entry_base_close"], found["tight_entry_pct"]) == (7, 120.0, 2.5)
    assert found["tight_entry_volume_x"] == 2.0               # against the 20-day average of 100
    assert tight_entry(volume_bars(closes, quiet + [140.0])) == empty_tight_entry()        # a range day had 150
    assert tight_entry(volume_bars(before + flat + [122.4], quiet + [200.0])) == empty_tight_entry()   # exactly +2%: still inside
    assert tight_entry(volume_bars(before + flat + [117.0], quiet + [200.0])) == empty_tight_entry()   # a break down
    busy = [400.0] * 20 + quiet[20:]                          # above every range day, below the 20-day average
    assert tight_entry(volume_bars(closes, busy + [200.0])) == empty_tight_entry()


def test_entry_stays_listed_for_a_few_days_and_needs_volume_data() -> None:
    from stock_entry.tight_range import ENTRY_MAX_AGE, empty_tight_entry, tight_entry

    before = [90.0 + i for i in range(20)]
    flat = [120.0, 121.0, 119.0, 120.5, 120.0, 121.5, 120.0]
    volumes = [100.0] * 20 + [150.0, 90.0, 80.0, 110.0, 95.0, 85.0, 90.0, 200.0]
    later = tight_entry(volume_bars(before + flat + [123.0, 124.0, 125.0], volumes + [60.0, 60.0]))
    assert later["tight_entry"] is True and later["tight_entry_days_ago"] == 2 and later["tight_entry_close"] == 123.0
    assert later["tight_entry_now_pct"] == 4.17               # the latest close 125 against the base 120
    tail = [124.0 + i for i in range(ENTRY_MAX_AGE)]          # the breakout is now ENTRY_MAX_AGE days old
    assert tight_entry(volume_bars(before + flat + [123.0] + tail, volumes + [60.0] * ENTRY_MAX_AGE)) == empty_tight_entry()
    assert tight_entry(bars(before + flat + [123.0])) == empty_tight_entry()   # no Volume column
