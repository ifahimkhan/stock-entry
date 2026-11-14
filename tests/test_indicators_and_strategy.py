"""Indicators must equal the formulas in 'Mid Cap-1d-Final Irfan.ipynb'; screens must apply its thresholds."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_entry.analysis import analyze, build_row
from stock_entry.indicators import INDICATOR_COLUMNS
from stock_entry.strategy import cap_category, evaluate_screens, volatility_category
from stock_entry.tickers import Ticker, load_tickers
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.unit


def random_bars(rows: int = 160, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 500 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, rows)))
    high = close * (1 + rng.uniform(0, 0.02, rows))
    low = close * (1 - rng.uniform(0, 0.02, rows))
    dates = pd.bdate_range("2026-01-01", periods=rows)
    return pd.DataFrame({"Date": dates, "Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1_000, 9_000, rows)})


def notebook_reference(data: pd.DataFrame) -> pd.DataFrame:
    """The notebook's own code, lightly reshaped: the oracle for the last bar."""
    out = pd.DataFrame(index=data.index)
    tr = pd.concat([data["High"] - data["Low"], abs(data["High"] - data["Close"].shift(1)),
                    abs(data["Low"] - data["Close"].shift(1))], axis=1).max(axis=1)
    out["atr_pct"] = tr.rolling(14).mean() / data["Close"] * 100
    source = (data["High"] + data["Low"] + data["Close"]) / 3
    ma1 = source.rolling(60).mean()
    mean_dev1 = (source - ma1).abs().rolling(60).mean()
    out["cci"] = (source - ma1) / (0.015 * mean_dev1)
    out["cci_ma"] = out["cci"].ewm(span=9, adjust=False).mean()
    delta = data["Close"].diff()
    gain = pd.Series(np.where(delta > 0, delta, 0), index=data.index)
    loss = pd.Series(np.where(delta < 0, -delta, 0), index=data.index)
    rs = gain.ewm(alpha=1 / 14, adjust=False).mean() / loss.ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi"] = 100 - (100 / (1 + rs))
    return out


def test_indicators_match_notebook_formulas() -> None:
    bars = random_bars()
    analyzed = analyze(bars)
    reference = notebook_reference(bars)
    for column in ("atr_pct", "cci", "cci_ma", "rsi"):
        pd.testing.assert_series_equal(analyzed[column], reference[column], check_names=False, rtol=1e-9)
    assert set(INDICATOR_COLUMNS) <= set(analyzed.columns)
    assert analyzed["cci"].iloc[:118].isna().all() and analyzed["cci"].iloc[118:].notna().all()


def test_last_bar_flags_match_notebook_last_bar_rules() -> None:
    bars = random_bars(seed=11)
    last = analyze(bars).iloc[-1]
    high, low, close = bars["High"], bars["Low"], bars["Close"]
    candle = high.iloc[-1] > high.iloc[-2] and low.iloc[-1] > low.iloc[-2] and close.iloc[-1] > high.iloc[-2]
    assert bool(last["candle_3rule"]) == candle
    assert bool(last["close_breakout"]) == (close.iloc[-1] > close.iloc[-6:-1].max())
    assert bool(last["ema7_pass"]) == (close.iloc[-1] > close.ewm(span=7, adjust=False).mean().iloc[-1])


def test_close_only_data_still_analyzes_without_cci() -> None:
    analyzed = analyze(make_prices(SANSERA_CLOSES))
    assert analyzed["cci"].isna().all() and not analyzed["candle_3rule"].any()
    assert analyzed["rsi"].notna().sum() > 0
    row = build_row(Ticker("SANSERA.NS"), analyzed)
    assert row["cci"] is None and row["cci_setup"] is False and row["breakout_setup"] is False
    assert row["last_entry_price"] == 4653.4            # entry logic unchanged
    json.dumps(row, allow_nan=False)


def frame_with_last(previous: dict, last: dict) -> pd.DataFrame:
    base = {"cci": -50.0, "cci_ma": -60.0, "cci_pass": True, "rsi": 50.0, "atr_pct": 2.0,
            "ema7_pass": True, "candle_3rule": True, "close_breakout": True}
    return pd.DataFrame([{**base, **previous}, {**base, **last}])


def test_cci_setup_thresholds() -> None:
    crossed = frame_with_last({"cci_pass": False}, {"cci": -10.0, "cci_ma": -14.0})
    result = evaluate_screens(crossed, market_cap_cr=5000, debt_to_equity=0.4)
    assert result["cci_cross"] and result["cci_setup"] and result["cci_gap"] == 4.0
    assert not evaluate_screens(crossed, debt_to_equity=0.41)["cci_setup"]                      # too much debt
    assert not evaluate_screens(frame_with_last({"cci_pass": True}, {}))["cci_setup"]              # no fresh cross
    assert not evaluate_screens(frame_with_last({"cci_pass": False}, {"cci": 5.0, "cci_ma": 0.0}))["cci_setup"]
    assert not evaluate_screens(frame_with_last({"cci_pass": False}, {"cci": -10.0, "cci_ma": -13.0}))["cci_setup"]
    assert not evaluate_screens(frame_with_last({"cci_pass": False}, {"ema7_pass": False}))["cci_setup"]


def test_breakout_setup_thresholds_and_top_pick() -> None:
    good = frame_with_last({}, {"cci": -5.0, "cci_ma": -8.0, "rsi": 46.0})
    result = evaluate_screens(good, market_cap_cr=2000, debt_to_equity=0.1)
    assert result["breakout_setup"] and result["top_pick"] and result["cap_category"] == "Small"
    assert result["screens"] == "Breakout" and result["screen_count"] == 1 and result["fundamentals_known"]
    assert not evaluate_screens(good, market_cap_cr=50_000, debt_to_equity=0.1)["top_pick"]      # mid cap
    assert not evaluate_screens(good, market_cap_cr=2000, debt_to_equity=0.25)["top_pick"]       # debt > 0.2
    assert evaluate_screens(good, market_cap_cr=2000, debt_to_equity=0.25)["breakout_setup"]     # still <= 0.3
    assert not evaluate_screens(good, debt_to_equity=0.31)["breakout_setup"]
    for change in ({"rsi": 39.9}, {"cci": 101.0, "cci_ma": 90.0}, {"cci": -5.0, "cci_ma": -6.5},
                   {"candle_3rule": False}, {"close_breakout": False}, {"ema7_pass": False}, {"cci_pass": False}):
        assert not evaluate_screens(frame_with_last({}, change))["breakout_setup"], change
    assert not evaluate_screens(frame_with_last({}, {"cci": 10.0, "cci_ma": 5.0, "rsi": 60.0}))["top_pick"]  # CCI > 0


def test_missing_fundamentals_skip_the_debt_filter_and_say_so() -> None:
    result = evaluate_screens(frame_with_last({}, {"cci": -5.0, "cci_ma": -8.0}))
    assert result["breakout_setup"] and result["fundamentals_known"] is False
    assert result["cap_category"] == "" and result["debt_to_equity"] is None


def test_missing_indicator_values_never_pass() -> None:
    result = evaluate_screens(frame_with_last({"cci_pass": False}, {"cci": float("nan"), "cci_ma": float("nan"),
                                                                    "rsi": float("nan"), "atr_pct": float("nan")}))
    assert result["cci"] is None and result["rsi"] is None and result["volatility"] == ""
    assert not result["cci_setup"] and not result["breakout_setup"] and not result["top_pick"]
    with pytest.raises(ValueError):
        evaluate_screens(frame_with_last({}, {}).iloc[0:0])


def test_categories() -> None:
    assert [cap_category(v) for v in (None, 33_500, 33_501, 106_300, 106_301)] == ["", "Small", "Mid", "Mid", "Large"]
    assert [volatility_category(v) for v in (None, 3, 3.1, 5, 5.1)] == ["", "Low", "High", "High", "Very high"]


def test_load_tickers_reads_optional_fundamentals(tmp_path: Path) -> None:
    path = tmp_path / "tickers.csv"
    path.write_text("Ticker,MarketCap_Cr,Debt_to_Equity\nAAA,5464,0.067\nBBB,,abc\nCCC,-5,inf\n", encoding="utf-8")
    tickers = load_tickers(path)
    assert (tickers[0].market_cap_cr, tickers[0].debt_to_equity) == (5464.0, 0.067)
    assert (tickers[1].market_cap_cr, tickers[1].debt_to_equity) == (None, None)
    assert (tickers[2].market_cap_cr, tickers[2].debt_to_equity) == (None, None)
