"""Swing structure on 2-hour bars (TradingView.ipynb): labels, clean-up, angles, rules, 2-hour bars, wiring."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_entry.analysis import DAILY_SWING_BARS, analyze, build_row
from stock_entry.config import Settings
from stock_entry.history import HistoryService
from stock_entry.intraday import analyze_swings
from stock_entry.market_data import MarketDataError, split_by_symbol
from stock_entry.scanner import run_scan
from stock_entry.services import build_services
from stock_entry.swings import (HH, HL, LH, LL, clean_labels, daily_points, daily_summary, empty_swing_summary, hh_pattern, lh_breakout,
                                ll_angles, ll_count_at_hh, ll_then_hh, percent_angle, swing_columns, swing_labels,
                                swing_points, swing_summary)
from stock_entry.tickers import Ticker
from tests.conftest import SANSERA_CLOSES, make_prices

# Daily closes whose latest close is below an earlier one: at a new closing high the scan skips swings.
BELOW_HIGH = SANSERA_CLOSES[:-1]

pytestmark = pytest.mark.unit

# Turning points of a zigzag: two falling legs, then a reversal into rising legs.
TURNS = [120, 100, 112, 95, 108, 90, 104, 86, 100, 92, 110, 97, 118, 104, 126]
GOLDEN = [(30, LL), (36, LH), (42, LL), (48, LH), (54, HL), (60, HH), (66, HL), (72, HH), (78, HL), (84, HH)]


def zigzag_bars() -> pd.DataFrame:
    """2-hour bars along TURNS, 6 bars per leg, then a short fall so the last pivot confirms."""
    path: list[float] = []
    for start, end in zip(TURNS, TURNS[1:]):
        path += list(np.linspace(start, end, 7)[:-1])
    path += [TURNS[-1] - step for step in range(7)]
    close = np.array(path)
    dates = pd.date_range("2026-10-01 09:15", periods=len(close), freq="2h")
    return pd.DataFrame({"Date": dates, "Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close})


def labels(*pairs: tuple[int, str], size: int = 25) -> np.ndarray:
    out = np.full(size, "", dtype=object)
    for index, label in pairs:
        out[index] = label
    return out


# ---------------------------------------------------------------- marker

def test_swing_labels_match_golden_zigzag() -> None:
    bars = zigzag_bars()
    found = swing_labels(bars["High"].to_numpy(), bars["Low"].to_numpy())
    assert [(i, label) for i, label in enumerate(found) if label] == GOLDEN


def test_latest_bars_never_carry_a_label_and_short_input_is_safe() -> None:
    bars = zigzag_bars()
    found = swing_labels(bars["High"].to_numpy(), bars["Low"].to_numpy())
    assert not any(found[-5:])  # a pivot is confirmed 5 bars later
    assert list(swing_labels(np.array([1.0, 2.0]), np.array([0.5, 1.5]))) == ["", ""]


# ---------------------------------------------------------------- clean-up

def test_hh_becomes_lh_when_a_later_close_breaks_the_last_ll() -> None:
    close = np.full(25, 100.0)
    close[2], close[8] = 90.0, 89.0   # LL closes at 90; a later close at 89 is below it
    raw = labels((2, LL), (5, HH))
    assert clean_labels(raw, np.full(25, 101.0), close)[5] == LH


def test_hh_closing_below_previous_lh_high_becomes_lh() -> None:
    high, close = np.full(25, 100.0), np.full(25, 100.0)
    high[3], close[6] = 120.0, 110.0   # LH high 120; the HH closes at 110
    assert clean_labels(labels((3, LH), (6, HH)), high, close)[6] == LH


def test_hl_followed_by_ll_or_lh_is_dropped() -> None:
    flat = np.full(25, 100.0)
    cleaned = clean_labels(labels((1, HL), (2, HL), (3, LL), (5, HL), (7, HH)), flat, flat)
    assert [(i, x) for i, x in enumerate(cleaned) if x] == [(3, LL), (5, HL), (7, HH)]


# ---------------------------------------------------------------- angles

def test_percent_angle() -> None:
    assert percent_angle(100.0, 101.0, 1) == pytest.approx(45.0)
    assert percent_angle(100.0, 90.0, 10) == pytest.approx(-45.0)


def test_ll_angles_need_an_hh_after_the_hl() -> None:
    low = np.array([100.0, 0, 0, 95.0, 0, 97.0, 0, 0, 0, 0] + [0] * 15)
    with_hh = ll_angles(labels((0, LL), (3, LL), (5, HL), (7, HH)), low)
    without_hh = ll_angles(labels((0, LL), (3, LL), (5, HL)), low)
    assert with_hh[0][0] == pytest.approx(math.degrees(math.atan2(-5.0, 3)))
    assert with_hh[1][3] == pytest.approx(math.degrees(math.atan2(2 / 95 * 100, 2)))
    assert math.isnan(without_hh[1][3]) and math.isnan(with_hh[0][3])  # last LL has no next LL


# ---------------------------------------------------------------- rules

RULE_SEQ = ["LL", "LH", "LL", "", "LH", "LL", "HL", "HH", "LL", "LL", "HH",
            "LL", "LL", "LL", "LL", "HH", "LL", "LH", "LL", "LL", "HH"]


def test_ll_then_hh_exact_and_at_least() -> None:
    seq = np.array(RULE_SEQ, dtype=object)
    assert list(np.flatnonzero(ll_then_hh(seq, 3))) == [7, 20]
    assert list(np.flatnonzero(ll_then_hh(seq, 4))) == [15]
    assert list(np.flatnonzero(ll_then_hh(seq, 3, exact=False))) == [7, 15, 20]


def test_ll_count_and_pattern_on_each_hh() -> None:
    counts = ll_count_at_hh(np.array(RULE_SEQ, dtype=object))
    assert {i: int(c) for i, c in enumerate(counts) if c >= 0} == {7: 3, 10: 2, 15: 4, 20: 3}
    assert [hh_pattern(n) for n in (2, 3, 4, 5)] == ["", "3LL-HH", "4LL-HH", ""]


def test_lh_breakout_flags_first_close_above_last_lh_high() -> None:
    rows = [("LL", 100, 99), ("LH", 110, 105), ("LL", 95, 96), ("LH", 108, 104), ("LL", 90, 91),
            ("", 106, 107), ("", 112, 109), ("", 115, 114), ("LL", 100, 99), ("LH", 105, 103), ("", 107, 106),
            ("HH", 120, 118), ("LH", 117, 115), ("LH", 116, 114), ("LH", 115, 112), ("", 116, 116)]
    seq = np.array([row[0] for row in rows], dtype=object)
    high, close = (np.array([row[k] for row in rows], dtype=float) for k in (1, 2))
    flags, levels = lh_breakout(seq, high, close)
    assert list(np.flatnonzero(flags)) == [6, 15]
    assert levels[6] == 108 and levels[15] == 115


# ---------------------------------------------------------------- frame + summary

def test_swing_columns_summary_and_points_on_zigzag() -> None:
    analysed = analyze_swings(zigzag_bars())
    summary = swing_summary(analysed)
    assert summary["swing_bars"] == len(analysed)
    assert summary["swing_last_label"] == HH and summary["swing_last_label_at"] == "2026-10-08 09:15"
    assert summary["ang_ll_ll"] == pytest.approx(round(percent_angle(89.5, 85.5, 12), 2))
    assert summary["ll3_hh_at"] is None and summary["ll4_hh_at"] is None
    points = swing_points(analysed, limit=3)
    assert [point["label"] for point in points] == [HH, HL, HH]
    assert [(p["before_entry"], p["ll_count"], p["pattern"]) for p in points] == [
        (True, 0, None), (False, None, None), (True, 0, None)]
    assert summary["before_entry_at"] == "2026-10-08 09:15" and summary["before_entry_pattern"] is None
    assert summary["before_entry_ll"] == 0
    assert set(swing_columns(zigzag_bars()).columns) >= {"swing_label", "swing_clean", "lh_breakout_level"}


def test_summary_reports_bars_since_signal() -> None:
    frame = pd.DataFrame({
        "Date": pd.date_range("2026-11-02 09:15", periods=4, freq="2h"),
        "Close": [10.0, 11.0, 12.345, 13.0],
        "swing_clean": ["LL", "", "HH", ""], "ll3_hh": [False, False, True, False],
        "before_entry": [False, False, True, False], "hh_ll_count": [-1, -1, 3, -1],
        "ll4_hh": [False] * 4, "lh_breakout": [False, False, False, True],
        "lh_breakout_level": [np.nan, np.nan, np.nan, 101.234], "ang_ll_ll": [np.nan] * 4, "ang_ll_hl": [np.nan] * 4,
    })
    summary = swing_summary(frame)
    assert (summary["ll3_hh_at"], summary["ll3_hh_bars"]) == ("2026-11-02 13:15", 1)
    assert (summary["before_entry_at"], summary["before_entry_bars"]) == ("2026-11-02 13:15", 1)
    assert (summary["before_entry_ll"], summary["before_entry_pattern"], summary["before_entry_close"]) == (3, "3LL-HH", 12.35)
    assert (summary["lh_breakout_bars"], summary["lh_breakout_level"]) == (0, 101.23)
    assert summary["ang_ll_ll"] is None
    assert swing_summary(frame.iloc[0:0]) == empty_swing_summary()


# ---------------------------------------------------------------- 2-hour bars

def test_split_by_symbol_keeps_intraday_time_when_asked() -> None:
    index = pd.DatetimeIndex(["2026-11-16 09:15", "2026-11-16 10:15"]).tz_localize("Asia/Kolkata")
    raw = pd.DataFrame({"Close": [1.0, 2.0]}, index=index)
    kept = split_by_symbol(raw, ["A.NS"], normalize=False)["A.NS"]["Date"]
    assert kept.dt.strftime("%H:%M").tolist() == ["09:15", "10:15"] and kept.dt.tz is None
    assert split_by_symbol(raw, ["A.NS"])["A.NS"]["Date"].dt.strftime("%H:%M").tolist() == ["00:00", "00:00"]


# ---------------------------------------------------------------- wiring

def daily_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {symbol: make_prices(BELOW_HIGH) for symbol in symbols}


def test_build_row_without_swings_has_empty_swing_fields() -> None:
    row = build_row(Ticker("A.NS"), analyze(make_prices(SANSERA_CLOSES)))
    assert row["swing_bars"] == 0 and row["ll3_hh_at"] is None


def test_run_scan_adds_swing_fields_and_survives_swing_failures() -> None:
    def swing_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
        return {symbol: zigzag_bars() for symbol in symbols if symbol != "NOSWING.NS"}

    scan = run_scan([Ticker("A.NS"), Ticker("NOSWING.NS")], daily_fetch, batch_size=5, swing_fetch=swing_fetch)
    by_symbol = {row["ticker"]: row for row in scan["rows"]}
    assert by_symbol["A.NS"]["swing_last_label"] == HH
    assert by_symbol["NOSWING.NS"]["swing_bars"] == 0

    def broken(symbols: list[str]) -> dict[str, pd.DataFrame]:
        raise MarketDataError("hourly prices down")

    rows = run_scan([Ticker("A.NS")], daily_fetch, batch_size=5, swing_fetch=broken)["rows"]
    assert len(rows) == 1 and rows[0]["swing_bars"] == 0   # the daily scan still works

    bad = run_scan([Ticker("A.NS")], daily_fetch, batch_size=5,
                   swing_fetch=lambda symbols: {"A.NS": pd.DataFrame({"Date": [pd.Timestamp("2026-11-16")]})})
    assert bad["rows"][0]["swing_bars"] == 0   # bars without prices are skipped, not fatal


def test_history_includes_swing_points_and_tolerates_failures() -> None:
    service = HistoryService(daily_fetch, cache_seconds=60, detail_rows=10,
                             swing_fetch=lambda symbols: {symbols[0]: zigzag_bars()}, swing_points=4)
    detail = service.get(Ticker("A.NS"))
    assert len(detail["swings"]) == 4 and detail["summary"]["swing_last_label"] == HH
    bars = detail["bars_2h"]   # every bar, not only the labelled ones
    assert len(bars) == min(120, len(zigzag_bars())) and any(bar["label"] is None for bar in bars)
    assert {"open", "high", "low", "close", "volume", "raw_label", "label", "before_entry", "ll3_hh"} <= set(bars[0])
    assert bars[-1]["time"] == detail["summary"]["swing_last_bar"]

    def broken(symbols: list[str]) -> dict[str, pd.DataFrame]:
        raise MarketDataError("down")

    failing = HistoryService(daily_fetch, cache_seconds=60, detail_rows=10, swing_fetch=broken)
    assert failing.get(Ticker("A.NS"))["swings"] == [] and failing.get(Ticker("A.NS"))["bars_2h"] == []
    assert HistoryService(daily_fetch, cache_seconds=60, detail_rows=10).get(Ticker("A.NS"))["swings"] == []


def test_unexpected_swing_errors_never_drop_a_stock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def explode(frame: pd.DataFrame) -> pd.DataFrame:
        raise IndexError("swing bug")

    monkeypatch.setattr("stock_entry.scanner.analyze_swings", explode)
    monkeypatch.setattr("stock_entry.history.analyze_swings", explode)
    rows = run_scan([Ticker("A.NS")], daily_fetch, batch_size=5,
                    swing_fetch=lambda symbols: {"A.NS": zigzag_bars()})["rows"]
    assert len(rows) == 1 and rows[0]["swing_bars"] == 0
    service = HistoryService(daily_fetch, cache_seconds=60, detail_rows=10,
                             swing_fetch=lambda symbols: {symbols[0]: zigzag_bars()})
    assert service.get(Ticker("A.NS"))["swings"] == []
    # summary failure on a malformed frame falls back to empty fields too
    assert build_row(Ticker("A.NS"), analyze(make_prices(SANSERA_CLOSES)), pd.DataFrame({"x": [1]}))["swing_bars"] == 0


def test_failed_swings_are_not_cached() -> None:
    calls = []

    def flaky(symbols: list[str]) -> dict[str, pd.DataFrame]:
        calls.append(1)
        if len(calls) == 1:
            raise MarketDataError("down")
        return {symbols[0]: zigzag_bars()}

    service = HistoryService(daily_fetch, cache_seconds=600, detail_rows=10, swing_fetch=flaky)
    assert service.get(Ticker("A.NS"))["swings"] == []
    assert service.get(Ticker("A.NS"))["swings"] != []   # retried, then cached
    assert service.get(Ticker("A.NS")) is service.get(Ticker("A.NS"))


def test_intraday_times_are_converted_to_exchange_time() -> None:
    index = pd.DatetimeIndex(["2026-11-16 03:45", "2026-11-16 04:45"]).tz_localize("UTC")
    raw = pd.DataFrame({"Close": [1.0, 2.0]}, index=index)
    kept = split_by_symbol(raw, ["A.NS"], normalize=False)["A.NS"]["Date"]
    assert kept.dt.strftime("%H:%M").tolist() == ["09:15", "10:15"]


def test_scan_rows_with_swings_save_as_strict_json(tmp_path: Path) -> None:
    from stock_entry.store import ScanStore

    scan = run_scan([Ticker("A.NS")], daily_fetch, batch_size=5,
                    swing_fetch=lambda symbols: {"A.NS": zigzag_bars()})
    store = ScanStore(tmp_path / "scan.json")
    store.save(scan)
    assert store.load()["rows"][0]["swing_last_label"] == HH


def test_services_never_reach_yahoo_for_swings_when_fetch_is_injected(tmp_path: Path) -> None:
    services = build_services(Settings(scan_path=tmp_path / "scan.json"), fetch=daily_fetch)
    assert services.history.get(Ticker("A.NS"))["swings"] == []
    with_swings = build_services(Settings(scan_path=tmp_path / "scan.json"), fetch=daily_fetch,
                                 swing_fetch=lambda symbols: {symbols[0]: zigzag_bars()})
    assert with_swings.history.get(Ticker("A.NS"))["summary"]["swing_bars"] == len(zigzag_bars())


# ---------------------------------------------------------------- daily bars (TBC Zone Entry 1D)

def daily_zigzag() -> pd.DataFrame:
    """The zigzag as daily bars (one per calendar day), with volume."""
    bars = zigzag_bars()
    dates = pd.date_range("2026-07-01", periods=len(bars), freq="D")
    return bars.assign(Date=dates, Volume=1000.0)


def test_daily_summary_prefixes_keys_and_keeps_dates_only() -> None:
    summary = swing_summary(analyze_swings(daily_zigzag()))
    daily = daily_summary(summary)
    assert set(daily) == {f"d1_{key}" for key in summary}
    assert daily["d1_swing_last_label"] == HH
    assert daily["d1_before_entry_at"] == summary["before_entry_at"][:10] and len(daily["d1_before_entry_at"]) == 10
    assert daily_points([{"time": "2026-11-16 00:00", "close": 1.0}]) == [{"time": "2026-11-16", "close": 1.0}]


def test_build_row_has_daily_swings_next_to_two_hour_swings() -> None:
    row = build_row(Ticker("A.NS"), analyze(daily_zigzag()), analyze_swings(zigzag_bars()))
    assert row["swing_last_label"] == HH and row["swing_last_label_at"].endswith(":15")   # 2h untouched
    assert row["d1_swing_last_label"] == HH and len(row["d1_swing_last_label_at"]) == 10
    assert row["d1_swing_bars"] == len(daily_zigzag())


def test_daily_swings_use_only_the_latest_bars() -> None:
    long = pd.concat([daily_zigzag()] * 8, ignore_index=True)
    long = long.assign(Date=pd.date_range("2024-01-01", periods=len(long), freq="D"))
    assert build_row(Ticker("A.NS"), analyze(long))["d1_swing_bars"] == DAILY_SWING_BARS


def test_daily_swing_failure_never_drops_a_stock(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(frame: pd.DataFrame) -> pd.DataFrame:
        raise IndexError("swing bug")

    monkeypatch.setattr("stock_entry.analysis.analyze_swings", explode)
    rows = run_scan([Ticker("A.NS")], lambda symbols: {"A.NS": daily_zigzag()}, batch_size=5)["rows"]
    assert len(rows) == 1 and rows[0]["d1_swing_bars"] == 0 and rows[0]["d1_before_entry_at"] is None
    detail = HistoryService(lambda symbols: {symbols[0]: daily_zigzag()}, cache_seconds=60, detail_rows=10).get(Ticker("A.NS"))
    assert detail["swings_1d"] == [] and detail["summary"]["d1_swing_bars"] == 0


def test_close_only_prices_give_empty_daily_swings() -> None:
    assert build_row(Ticker("A.NS"), analyze(make_prices(SANSERA_CLOSES)))["d1_swing_bars"] == 0


def test_history_includes_daily_swing_points() -> None:
    service = HistoryService(lambda symbols: {symbols[0]: daily_zigzag()}, cache_seconds=60, detail_rows=10,
                             swing_points=4)
    points = service.get(Ticker("A.NS"))["swings_1d"]
    assert len(points) == 4 and all(len(point["time"]) == 10 for point in points)
    assert points[-1]["label"] == HH
    days = service.get(Ticker("A.NS"))["bars_1d"]   # every day, not only the labelled ones
    assert len(days) == min(500, len(daily_zigzag())) and any(day["label"] is None for day in days)
    assert all(len(day["time"]) == 10 for day in days) and {"open", "volume", "raw_label"} <= set(days[0])


def test_hh_change_from_lh_is_set_on_hh_bars_only() -> None:
    from stock_entry.swings import LH, hh_change_from_lh

    frame = pd.DataFrame({"swing_clean": ["", HH, LH, "", HH, LH, LH, HH],
                          "Close": [90.0, 100.0, 120.0, 110.0, 132.0, 150.0, 140.0, 147.0]})
    change = hh_change_from_lh(frame)
    assert pd.isna(change[0]) and pd.isna(change[1])          # first HH: no LH before it
    assert change[4] == pytest.approx(10.0)                     # 132 vs LH 120
    assert change[7] == pytest.approx(5.0)                      # 147 vs the latest LH (140)
    assert change[[0, 2, 3, 5, 6]].isna().all()


def test_detail_bars_carry_hh_change() -> None:
    service = HistoryService(lambda symbols: {symbols[0]: daily_zigzag()}, cache_seconds=60, detail_rows=10)
    days = service.get(Ticker("A.NS"))["bars_1d"]
    with_value = [day for day in days if day["hh_change_pct"] is not None]
    assert with_value and all(day["label"] == HH for day in with_value)


def test_zone_entry_summary_carries_the_swings_1d_change() -> None:
    from stock_entry.swings import hh_change_from_lh, swing_columns, swing_summary

    bars = daily_zigzag()
    frame = pd.concat([bars, swing_columns(bars)], axis=1)
    summary = swing_summary(frame)
    if summary["before_entry_at"] is None:
        assert summary["before_entry_change_pct"] is None
        return
    pos = len(frame) - 1 - summary["before_entry_bars"]
    expected = hh_change_from_lh(frame).iloc[pos]
    assert summary["before_entry_change_pct"] == (None if pd.isna(expected) else pytest.approx(expected, abs=0.01))



def test_at_closing_high_uses_greater_or_equal_over_the_last_500_closes() -> None:
    from stock_entry.analysis import at_closing_high

    def frame(closes: list[float]) -> pd.DataFrame:
        return pd.DataFrame({"Close": closes})

    assert at_closing_high(frame([10.0, 12.0, 11.0, 12.5])) is True
    assert at_closing_high(frame([10.0, 12.0, 11.0, 12.0])) is True    # equal to the best close counts
    assert at_closing_high(frame([10.0, 12.0, 11.0])) is False
    assert at_closing_high(frame([10.0])) is False
    assert at_closing_high(frame([99.0] + [10.0] * 500 + [11.0])) is True   # the 99 is older than 500 candles


def test_a_closing_high_gets_no_swing_fields_and_no_two_hour_download() -> None:
    from stock_entry.scanner import _scan_batch

    asked: list[list[str]] = []

    def swing_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
        asked.append(sorted(symbols))
        return {symbol: zigzag_bars() for symbol in symbols}

    daily = {"UP.NS": make_prices(SANSERA_CLOSES), "DIP.NS": make_prices(BELOW_HIGH)}
    rows, failed = _scan_batch([Ticker("UP.NS"), Ticker("DIP.NS")], lambda symbols: daily, lambda symbols: {}, swing_fetch)
    by = {row["ticker"]: row for row in rows}
    assert not failed and asked == [["DIP.NS"]]          # no 2-hour download for the closing high
    assert by["UP.NS"]["at_closing_high"] is True
    assert by["UP.NS"]["swing_bars"] == 0 and by["UP.NS"]["d1_swing_bars"] == 0
    assert by["DIP.NS"]["at_closing_high"] is False and by["DIP.NS"]["swing_bars"] > 0


def entry_frame(before: list[bool], ohlc: list[tuple[float, float, float]]) -> pd.DataFrame:
    """Bars with (high, low, close) and the before_entry flag of a swing frame."""
    return pd.DataFrame({"High": [h for h, _, _ in ohlc], "Low": [lo for _, lo, _ in ohlc],
                         "Close": [c for _, _, c in ohlc], "before_entry": before})


def test_entry_is_the_first_3_rule_candle_once_the_zone_hh_is_confirmed() -> None:
    from stock_entry.swings import PIVOT_RIGHT, entry_after_hh

    flat = (10.0, 9.0, 9.5)
    up = (11.0, 9.5, 10.5)            # high > prev high, low > prev low, close > prev high
    bars = [flat, up] + [flat] * (PIVOT_RIGHT - 1) + [flat, up, flat, up]
    before = [False, True] + [False] * (len(bars) - 2)   # the Zone Entry HH is bar 1
    entry = entry_after_hh(entry_frame(before, bars)).tolist()
    confirmed = 1 + PIVOT_RIGHT
    # bar 1 itself passes the 3 rules but the HH is not confirmed yet; first pass on or after bar 6 is bar 7
    assert [i for i, flag in enumerate(entry) if flag] == [confirmed + 1]


def test_entry_needs_a_zone_hh_and_one_entry_per_hh() -> None:
    from stock_entry.swings import entry_after_hh

    up_bars = [(10.0 + i, 9.0 + i, 10.5 + i) for i in range(12)]   # every bar passes the 3 rules
    assert not entry_after_hh(entry_frame([False] * 12, up_bars)).any()
    before = [True] + [False] * 11
    assert entry_after_hh(entry_frame(before, up_bars)).sum() == 1


def test_entry_2_is_the_first_3_rule_candle_after_lh_hh_hl_hh() -> None:
    from stock_entry.swings import PIVOT_RIGHT, entry_2_after_hh2

    flat, up = (10.0, 9.0, 9.5), (11.0, 9.5, 10.5)   # `up` after `flat` passes the 3 rules
    labels = ["LH", "", "HH", "", "HL", "", "HH"] + [""] * 10 + ["LL"] + [""] * 8
    bars = [flat] * len(labels)
    hh2 = 6
    bars[hh2] = (12.5, 9.0, 12.0)   # the last HH closes at 12.0, above the 10.5 close of every `up` candle
    for i in (hh2 + 2, hh2 + PIVOT_RIGHT + 1, hh2 + PIVOT_RIGHT + 3, len(labels) - 1):
        bars[i] = up
    frame = entry_frame([False] * len(labels), bars).assign(swing_clean=labels)
    entry = entry_2_after_hh2(frame).tolist()
    # bar 8 passes but the last HH is not confirmed yet; one entry per sequence; nothing once the LL is confirmed
    assert [i for i, flag in enumerate(entry) if flag] == [hh2 + PIVOT_RIGHT + 1]


def test_entry_2_needs_the_whole_lh_hh_hl_hh_sequence() -> None:
    from stock_entry.swings import entry_2_after_hh2

    up_bars = [(10.0 + i, 9.0 + i, 10.5 + i) for i in range(24)]   # every bar passes the 3 rules
    up_bars[6] = (40.0, 15.0, 39.0)   # the last HH of the right sequence closes above every later close
    wrong = (["HH", "", "HL", "", "HH"],                 # no LH before the first HH
             ["LL", "", "HH", "", "HL", "", "HH"],       # LL, not LH
             ["LH", "", "HH", "", "LH", "", "HH"],       # no HL
             ["LH", "", "HH", "", "HL", "", ""])         # second HH missing
    for labels in wrong:
        frame = entry_frame([False] * 24, up_bars).assign(swing_clean=labels + [""] * (24 - len(labels)))
        assert not entry_2_after_hh2(frame).any(), labels
    right = ["LH", "", "HH", "", "HL", "", "HH"]
    frame = entry_frame([False] * 24, up_bars).assign(swing_clean=right + [""] * 17)
    assert entry_2_after_hh2(frame).sum() == 1


def test_entry_2_candle_must_close_below_the_close_of_the_last_hh() -> None:
    from stock_entry.swings import PIVOT_RIGHT, entry_2_after_hh2

    flat, up = (10.0, 9.0, 9.5), (11.0, 9.5, 10.5)   # `up` after `flat` passes the 3 rules, close 10.5
    labels = ["LH", "", "HH", "", "HL", "", "HH"] + [""] * 14
    hh2, first, second = 6, 6 + PIVOT_RIGHT + 1, 6 + PIVOT_RIGHT + 4

    def entries(hh_close: float) -> list[int]:
        bars = [flat] * len(labels)
        bars[hh2] = (hh_close + 0.5, 9.0, hh_close)
        bars[first] = (12.0, 9.5, 11.5)   # passes the 3 rules, close 11.5
        bars[second] = up                  # passes the 3 rules, close 10.5
        frame = entry_frame([False] * len(labels), bars).assign(swing_clean=labels)
        return [i for i, flag in enumerate(entry_2_after_hh2(frame).tolist()) if flag]

    assert entries(12.0) == [first]    # both close below the HH close 12.0: the first one is the entry
    assert entries(11.0) == [second]   # 11.5 is not below 11.0, so it is skipped; 10.5 is
    assert entries(10.5) == []         # neither closes below 10.5 (equal does not count)


def breakout_frame(labels: list[str], breakout: list[int]) -> pd.DataFrame:
    flags = [i in breakout for i in range(len(labels))]
    return pd.DataFrame({"swing_clean": labels, "lh_breakout": flags})


def test_before_entry_1_is_the_hh_right_after_an_lh_breakout() -> None:
    from stock_entry.swings import before_entry_after_breakout

    def marked(labels: list[str], breakout: list[int]) -> list[int]:
        flags = before_entry_after_breakout(breakout_frame(labels, breakout)).tolist()
        return [i for i, flag in enumerate(flags) if flag]

    assert marked(["LH", "", "", "HH", "", "HH"], [2]) == [3]        # LH, breakout, HH; the next HH has no breakout
    assert marked(["LH", "", "HH"], [2]) == [2]                      # the breakout candle is the HH itself
    assert marked(["LH", "", "HH"], []) == []                        # no breakout
    assert marked(["LH", "", "LL", "HH"], [1]) == []                 # another label between the breakout and the HH
    assert marked(["LH", "", "HL", "HH"], [1]) == []
    assert marked(["LH", "", "LH", "", "HH"], [1, 3]) == [4]         # a later breakout starts the sequence again


def test_entry_1_follows_only_the_hhs_passed_to_it() -> None:
    from stock_entry.swings import PIVOT_RIGHT, entry_after_hh

    up_bars = [(10.0 + i, 9.0 + i, 10.5 + i) for i in range(20)]     # every bar passes the 3 rules
    frame = entry_frame([True] + [False] * 19, up_bars)              # the old flag says bar 0
    chosen = pd.Series([False] * 3 + [True] + [False] * 16)          # the new Before Entry is bar 3
    entry = entry_after_hh(frame, chosen).tolist()
    assert [i for i, flag in enumerate(entry) if flag] == [3 + PIVOT_RIGHT]
