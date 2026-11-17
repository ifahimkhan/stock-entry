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
from stock_entry.intraday import analyze_swings, fetch_two_hour, to_two_hour
from stock_entry.market_data import MarketDataError, split_by_symbol
from stock_entry.scanner import run_scan
from stock_entry.services import build_services
from stock_entry.swings import (HH, HL, LH, LL, clean_labels, daily_points, daily_summary, empty_swing_summary, hh_pattern, lh_breakout,
                                ll_angles, ll_count_at_hh, ll_then_hh, percent_angle, swing_columns, swing_labels,
                                swing_points, swing_summary)
from stock_entry.tickers import Ticker
from tests.conftest import SANSERA_CLOSES, make_prices

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

def hourly(day: str, values: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([(pd.Timestamp(f"{day} {t}"), o, h, l, c, v) for t, o, h, l, c, v in values],
                        columns=["Date", "Open", "High", "Low", "Close", "Volume"])


SESSION = [("09:15", 10, 12, 9, 11, 100), ("10:15", 11, 13, 10, 12, 50), ("11:15", 12, 12.5, 11, 11.5, 10),
           ("12:15", 11.5, 14, 11, 13, 20), ("13:15", 13, 13, 12, 12.5, 5), ("14:15", 12.5, 15, 12, 14, 5),
           ("15:15", 14, 14.2, 13.8, 14.1, 1)]


def test_to_two_hour_pairs_hours_from_the_open() -> None:
    bars = to_two_hour(hourly("2026-11-16", SESSION))
    assert bars["Date"].dt.strftime("%H:%M").tolist() == ["09:15", "11:15", "13:15", "15:15"]
    first = bars.iloc[0]
    assert (first["Open"], first["High"], first["Low"], first["Close"], first["Volume"]) == (10, 13, 9, 12, 150)
    assert bars.iloc[3]["Close"] == 14.1  # the short 15:15 candle stays on its own
    assert to_two_hour(hourly("2026-11-16", [])).empty


def test_fetch_two_hour_keeps_latest_bars_and_drops_empty() -> None:
    two_days = pd.concat([hourly("2026-11-13", SESSION), hourly("2026-11-16", SESSION)], ignore_index=True)
    fetched = fetch_two_hour(["A.NS", "B.NS"], lambda symbols: {"A.NS": two_days, "B.NS": two_days.iloc[0:0]},
                             max_bars=5)
    assert list(fetched) == ["A.NS"] and len(fetched["A.NS"]) == 5
    assert fetched["A.NS"]["Date"].iloc[-1] == pd.Timestamp("2026-11-16 15:15")


def test_split_by_symbol_keeps_intraday_time_when_asked() -> None:
    index = pd.DatetimeIndex(["2026-11-16 09:15", "2026-11-16 10:15"]).tz_localize("Asia/Kolkata")
    raw = pd.DataFrame({"Close": [1.0, 2.0]}, index=index)
    kept = split_by_symbol(raw, ["A.NS"], normalize=False)["A.NS"]["Date"]
    assert kept.dt.strftime("%H:%M").tolist() == ["09:15", "10:15"] and kept.dt.tz is None
    assert split_by_symbol(raw, ["A.NS"])["A.NS"]["Date"].dt.strftime("%H:%M").tolist() == ["00:00", "00:00"]


# ---------------------------------------------------------------- wiring

def daily_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {symbol: make_prices(SANSERA_CLOSES) for symbol in symbols}


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

    def broken(symbols: list[str]) -> dict[str, pd.DataFrame]:
        raise MarketDataError("down")

    failing = HistoryService(daily_fetch, cache_seconds=60, detail_rows=10, swing_fetch=broken)
    assert failing.get(Ticker("A.NS"))["swings"] == []
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
