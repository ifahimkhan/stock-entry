"""Signal rules must reproduce the Fahim.ipynb output row for row."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_entry.signals import ENTRY, WAIT, add_entry_signals, build_entry_status
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.unit


def test_valid_matches_notebook(sansera: pd.DataFrame) -> None:
    signals = add_entry_signals(sansera)
    true_rows = signals.index[signals["valid"].eq(True)].tolist()
    false_rows = signals.index[signals["valid"].eq(False)].tolist()
    assert true_rows == [20, 21, 26, 27, 29, 30, 31]
    assert false_rows == [5, 7, 10, 11, 14, 15, 17, 18, 19, 24, 25]
    assert signals["valid"].isna().sum() == 32 - 18


def test_prev4_max_close_only_on_up_days(sansera: pd.DataFrame) -> None:
    signals = add_entry_signals(sansera)
    assert signals.loc[20, "prev4_max_close"] == 4147.7
    assert signals.loc[24, "prev4_max_close"] == 4310.0
    assert pd.isna(signals.loc[22, "prev4_max_close"])      # down day
    assert signals["prev4_max_close"].iloc[:4].isna().all()  # fewer than 4 earlier bars


def test_entry_status_and_price_match_notebook(sansera: pd.DataFrame) -> None:
    signals = add_entry_signals(sansera)
    assert signals.index[signals["entry_status"] == WAIT].tolist() == [22, 23, 28]
    assert signals.index[signals["entry_status"] == ENTRY].tolist() == [24, 29]
    assert signals.loc[24, "entry_price"] == 4157.4
    assert signals.loc[29, "entry_price"] == 4653.4
    assert signals["entry_price"].notna().sum() == 2


def test_angle_and_pct_change_rounded_like_notebook(sansera: pd.DataFrame) -> None:
    signals = add_entry_signals(sansera)
    assert signals.loc[20, "pct_change"] == 3.45
    assert signals.loc[20, "angle"] == 73.81
    assert pd.isna(signals.loc[0, "angle"])


def test_input_frame_is_not_modified(sansera: pd.DataFrame) -> None:
    before = sansera.copy()
    add_entry_signals(sansera)
    pd.testing.assert_frame_equal(sansera, before)


def test_unsorted_input_and_missing_closes_are_handled() -> None:
    rows = [*SANSERA_CLOSES, ("2026-11-13", float("nan"))]
    shuffled = make_prices(rows).sample(frac=1, random_state=7)
    signals = add_entry_signals(shuffled)
    assert len(signals) == 32
    assert signals["Date"].is_monotonic_increasing
    assert signals.index[signals["entry_status"] == ENTRY].tolist() == [24, 29]


def test_equal_to_previous_high_is_not_valid() -> None:
    rows = [("2026-01-01", 100), ("2026-01-02", 101), ("2026-01-05", 99), ("2026-01-06", 98),
            ("2026-01-07", 101)]  # up day closing exactly at the 4-day high
    assert add_entry_signals(make_prices(rows)).loc[4, "valid"] is False


def test_flat_day_after_breakout_counts_as_wait() -> None:
    rows = [("2026-01-01", 10), ("2026-01-02", 9), ("2026-01-05", 9), ("2026-01-06", 9),
            ("2026-01-07", 12), ("2026-01-08", 12), ("2026-01-09", 13)]
    statuses = add_entry_signals(make_prices(rows))["entry_status"].tolist()
    assert statuses[4:] == ["", WAIT, ENTRY]


def test_no_wait_without_a_breakout() -> None:
    rows = [(f"2026-01-{day:02d}", 100 - day) for day in range(1, 11)]  # falls every day
    assert set(add_entry_signals(make_prices(rows))["entry_status"]) == {""}


def test_build_entry_status_rearms_when_reversal_is_also_breakout() -> None:
    valid = pd.Series([True, None, True, None, False], dtype=object)
    angle = pd.Series([10.0, -5.0, 20.0, -3.0, 4.0])
    assert build_entry_status(valid, angle).tolist() == ["", WAIT, ENTRY, WAIT, ENTRY]


def test_zero_price_bars_are_dropped_so_no_infinite_change() -> None:
    rows = [("2026-01-01", 5.0), ("2026-01-02", 0.0), ("2026-01-05", 5.0), ("2026-01-06", -1.0), ("2026-01-07", 6.0)]
    signals = add_entry_signals(make_prices(rows))
    assert signals["Close"].tolist() == [5.0, 5.0, 6.0]
    assert np.isfinite(signals["pct_change"].dropna()).all()


def test_missing_columns_raise_clear_error() -> None:
    with pytest.raises(ValueError, match="missing column"):
        add_entry_signals(pd.DataFrame({"Date": pd.to_datetime(["2026-01-01"])}))


def test_invalid_window_rejected(sansera: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="window"):
        add_entry_signals(sansera, window=0)
