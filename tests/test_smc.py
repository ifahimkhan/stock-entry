"""SMC / MRI columns: structure breaks, fair value gaps, EMA signals and the SPV / PV marks."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from stock_entry.analysis import analyze, build_row
from stock_entry.smc import (ABOVE, BEARISH, BOS_DOWN, BOS_UP, BULLISH, BUY, CHOCH_DOWN, CHOCH_UP, INSIDE, PV_BULL,
                             SELL, SMC_COLUMNS, SPV_BEAR, SPV_BULL, fair_value_gaps, smc_columns, smc_summary,
                             structure, wilder_atr)
from stock_entry.tickers import Ticker
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.unit

# Pivot low (bar 0), pivot high 106 (bar 2), then a fall into the order block.
LEAD = [(100, 101, 99, 100), (100, 103, 99.5, 102), (102, 106, 101, 105), (105, 105.5, 102, 103), (103, 104, 100, 101)]
ORDER_BLOCK = (101, 101.5, 97, 98)
SPV_BARS = LEAD + [ORDER_BLOCK, (98, 108, 97.5, 107.5), (107.5, 110, 103, 109)]
PV_BARS = LEAD + [ORDER_BLOCK, (98, 99.5, 97.5, 99), (99, 108, 98.5, 107.5), (107.5, 110, 103, 109)]


def bars_from(candles: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Bars from (open, high, low, close) tuples."""
    frame = pd.DataFrame(candles, columns=["Open", "High", "Low", "Close"]).astype(float)
    frame.insert(0, "Date", pd.bdate_range("2026-01-01", periods=len(frame)))
    return frame


def small(candles: list) -> pd.DataFrame:
    """Bars plus SMC columns computed with short lengths so a handful of candles is enough."""
    bars = bars_from(candles)
    return pd.concat([bars, smc_columns(bars, internal_length=2, swing_length=6)], axis=1)


def mirrored(candles: list) -> list:
    """Same candles flipped around 200: highs become lows, up moves become down moves."""
    return [(200 - o, 200 - l, 200 - h, 200 - c) for o, h, l, c in candles]


def arrays(candles: list) -> tuple[np.ndarray, ...]:
    frame = bars_from(candles)
    return tuple(frame[column].to_numpy() for column in ("Open", "High", "Low", "Close"))


def test_structure_marks_bos_then_choch() -> None:
    falling = [(109, 109.5, 104, 105), (105, 106, 100, 101), (101, 102, 95, 96), (96, 97, 90, 91)]
    _, high, low, close = arrays(SPV_BARS + falling)
    trends, events, breaks = structure(close, high, low, 2)
    assert events[6] == BOS_UP and trends[6] == BULLISH
    assert CHOCH_DOWN in events[7:] and trends[-1] == BEARISH
    assert (breaks[0].bar, breaks[0].bias, breaks[0].pivot_bar) == (6, BULLISH, 2)
    assert events.count(BOS_UP) == 1  # a broken level cannot break again


def test_mirrored_bars_give_bearish_structure() -> None:
    _, high, low, close = arrays(mirrored(SPV_BARS))
    _, events, _ = structure(close, high, low, 2)
    assert BOS_DOWN in events and BOS_UP not in events and CHOCH_UP not in events


def test_internal_break_is_ignored_when_level_equals_swing_level() -> None:
    _, high, low, close = arrays(SPV_BARS)
    same = (np.full(len(close), 106.0), np.full(len(close), np.nan))
    _, events, breaks = structure(close, high, low, 2, same)
    assert BOS_UP not in events and not breaks


def test_fair_value_gap_needs_middle_candle_to_close_through() -> None:
    open_, high, low, close = arrays([(100, 101, 99, 100.5), (100.5, 106, 100, 105.5), (105.5, 108, 103, 107)])
    assert fair_value_gaps(open_, high, low, close) == ["", "", BULLISH]
    open_, high, low, close = arrays([(100, 101, 99, 100.5), (100.5, 106, 100, 100.8), (105.5, 108, 103, 107)])
    assert fair_value_gaps(open_, high, low, close) == ["", "", ""]   # middle closed below 101
    open_, high, low, close = arrays(mirrored([(100, 101, 99, 100.5), (100.5, 106, 100, 105.5), (105.5, 108, 103, 107)]))
    assert fair_value_gaps(open_, high, low, close)[-1] == BEARISH


def test_spv_is_an_order_block_that_starts_a_gap() -> None:
    result = small(SPV_BARS)
    assert result["mri_mark"].tolist() == ["", "", "", "", "", SPV_BULL, "", ""]
    last = result.iloc[-1]
    assert last["mri_signal"] == SPV_BULL and last["mri_zone_bar"] == 5
    assert (last["mri_zone_high"], last["mri_zone_low"]) == (101.5, 97)


def test_pv_is_the_first_gap_candle_after_the_order_block() -> None:
    result = small(PV_BARS)
    assert result["mri_mark"].iloc[6] == PV_BULL and (result["mri_mark"].drop(index=6) == "").all()
    assert result["mri_signal"].iloc[-1] == PV_BULL
    assert (result["mri_zone_high"].iloc[-1], result["mri_zone_low"].iloc[-1]) == (99.5, 97.5)


def test_bearish_spv_is_the_mirror_image() -> None:
    result = small(mirrored(SPV_BARS))
    assert result["mri_mark"].iloc[5] == SPV_BEAR and result["mri_signal"].iloc[-1] == SPV_BEAR


def test_no_mark_without_a_gap() -> None:
    no_gap = LEAD + [ORDER_BLOCK, (98, 108, 97.5, 107.5), (107.5, 110, 101, 109)]  # low 101 does not clear 101.5
    assert (small(no_gap)["mri_mark"] == "").all()


def test_pv_gap_may_start_one_bar_after_the_break_but_not_later() -> None:
    late = LEAD + [ORDER_BLOCK, (98, 108, 97.5, 107.5), (107.5, 109, 100, 108), (108, 118, 107, 117), (117, 120, 112, 119)]
    result = small(late)   # break on bar 6, gap candles 7-8-9
    assert result["mri_mark"].iloc[7] == PV_BULL and result["mri_signal"].iloc[9] == PV_BULL
    later = LEAD + [ORDER_BLOCK, (98, 108, 97.5, 107.5), (107.5, 109, 100, 108), (108, 110, 104, 109),
                    (109, 119, 108, 118), (118, 121, 113, 120)]
    assert (small(later)["mri_mark"] == "").all()   # gap candles 8-9-10 start two bars after the break


def test_yahoo_filler_bar_is_dropped() -> None:
    from stock_entry.signals import clean_bars
    bars = bars_from([(100, 102, 99, 101), (101, 101, 101, 101), (105, 106, 104, 105), (105, 105, 105, 105)])
    cleaned = clean_bars(bars.assign(Open=[100, 101, 105, 104.9]))
    assert cleaned["Close"].tolist() == [101, 105, 105]   # flat copy of the previous close removed, real flat-ish bar kept


def test_ema_cross_gives_buy_and_sell() -> None:
    closes = [100.0] * 30 + [100 + 3 * i for i in range(1, 15)] + [142 - 6 * i for i in range(1, 15)]
    signals = smc_columns(bars_from([(c, c + 1, c - 1, c) for c in closes]))["ma_signal"]
    assert signals[signals != ""].tolist() == [BUY, SELL]


def test_wilder_atr_is_blank_until_enough_bars() -> None:
    high, low, close = np.array([2.0, 3, 4, 5]), np.array([1.0, 2, 3, 4]), np.array([1.5, 2.5, 3.5, 4.5])
    atr = wilder_atr(high, low, close, 3)
    assert np.isnan(atr[:2]).all() and atr[2] == pytest.approx(4 / 3) and atr[3] == pytest.approx((4 / 3 * 2 + 1.5) / 3)
    assert np.isnan(wilder_atr(high, low, close, 10)).all()


def test_summary_reports_active_spv_and_price_position() -> None:
    summary = smc_summary(small(SPV_BARS))
    assert (summary["spv_high"], summary["spv_low"], summary["spv_sessions"]) == (101.5, 97, 0)
    assert summary["spv_date"] == "2026-01-08" and summary["spv_position"] == ABOVE
    assert summary["spv_distance_pct"] == pytest.approx(6.88)
    assert summary["smc_trend"] == BULLISH and summary["mri_signal"] == SPV_BULL


def test_summary_drops_spv_once_price_trades_below_it() -> None:
    inside = smc_summary(small(SPV_BARS + [(109, 109, 99, 100)]))
    assert inside["spv_position"] == INSIDE and inside["spv_sessions"] == 1
    broken = smc_summary(small(SPV_BARS + [(109, 109, 96, 100)]))
    assert broken["spv_date"] is None and broken["spv_position"] is None


def test_summary_reports_latest_pv() -> None:
    summary = smc_summary(small(PV_BARS))
    assert (summary["pv_kind"], summary["pv_date"], summary["pv_sessions"]) == (PV_BULL, "2026-01-09", 0)
    assert summary["spv_date"] is None


def test_close_only_bars_give_blank_columns() -> None:
    result = smc_columns(make_prices(SANSERA_CLOSES))
    assert list(result.columns) == list(SMC_COLUMNS)
    assert (result["mri_mark"] == "").all() and result["ema9"].isna().all()


def test_row_with_smc_fields_is_json_safe() -> None:
    row = build_row(Ticker("X.NS"), analyze(bars_from(SPV_BARS)))
    assert row["spv_date"] is None and row["pv_date"] is None  # default lengths need more bars
    json.dumps(row, allow_nan=False)
    close_only = build_row(Ticker("SANSERA.NS"), analyze(make_prices(SANSERA_CLOSES)))
    assert close_only["spv_date"] is None
    json.dumps(close_only, allow_nan=False)
