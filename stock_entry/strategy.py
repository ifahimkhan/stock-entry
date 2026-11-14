"""The two screens from 'Mid Cap-1d-Final Irfan.ipynb', evaluated on a stock's latest bar.

CCI up-cross : CCI crossed above its EMA today, CCI <= 0, close above EMA 7, gap >= 4, debt/equity <= 0.4
Breakout     : close above the previous 5 closes, CCI above its EMA by >= 2, CCI <= 100, RSI >= 40,
               3-rule candle, close above EMA 7, debt/equity <= 0.3
Top pick     : Breakout + small cap + RSI >= 45 + CCI <= 0 + debt/equity <= 0.2

Debt/equity and market cap come from tickers.csv. When a value is missing that filter is skipped
(the stock is not rejected) and `fundamentals_known` is False so the page can say so.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from stock_entry.summary import clean_value

CCI_SETUP_MAX_CCI = 0
CCI_SETUP_MIN_GAP = 4
CCI_SETUP_MAX_DEBT = 0.4
BREAKOUT_MIN_GAP = 2
BREAKOUT_MAX_CCI = 100
BREAKOUT_MIN_RSI = 40
BREAKOUT_MAX_DEBT = 0.3
PICK_MIN_RSI = 45
PICK_MAX_CCI = 0
PICK_MAX_DEBT = 0.2
SMALL_CAP_MAX_CR = 33_500
MID_CAP_MAX_CR = 106_300
ATR_HIGH_PCT = 3
ATR_VERY_HIGH_PCT = 5


def cap_category(market_cap_cr: float | None) -> str:
    if market_cap_cr is None:
        return ""
    if market_cap_cr <= SMALL_CAP_MAX_CR:
        return "Small"
    return "Mid" if market_cap_cr <= MID_CAP_MAX_CR else "Large"


def volatility_category(atr_pct: float | None) -> str:
    if atr_pct is None:
        return ""
    if atr_pct <= ATR_HIGH_PCT:
        return "Low"
    return "High" if atr_pct <= ATR_VERY_HIGH_PCT else "Very high"


def _debt_ok(debt_to_equity: float | None, limit: float) -> bool:
    return debt_to_equity is None or debt_to_equity <= limit


def _number(value: Any) -> float | None:
    cleaned = clean_value(value)
    return None if cleaned is None else round(float(cleaned), 2)


def evaluate_screens(frame: pd.DataFrame, market_cap_cr: float | None = None,
                     debt_to_equity: float | None = None) -> dict[str, Any]:
    """Indicator values and screen results for the latest bar of an analyzed frame."""
    if frame.empty:
        raise ValueError("no price bars to screen")
    last = frame.iloc[-1]
    cci, cci_ma, rsi = _number(last["cci"]), _number(last["cci_ma"]), _number(last["rsi"])
    gap = None if cci is None or cci_ma is None else round(cci - cci_ma, 2)
    cci_pass = bool(last["cci_pass"])
    ema7_pass = bool(last["ema7_pass"])
    candle = bool(last["candle_3rule"])
    close_breakout = bool(last["close_breakout"])
    crossed_today = cci_pass and len(frame) >= 2 and not bool(frame.iloc[-2]["cci_pass"])
    category = cap_category(market_cap_cr)

    cci_setup = (crossed_today and ema7_pass and cci is not None and cci <= CCI_SETUP_MAX_CCI
                 and gap is not None and gap >= CCI_SETUP_MIN_GAP and _debt_ok(debt_to_equity, CCI_SETUP_MAX_DEBT))
    breakout_setup = (close_breakout and cci_pass and ema7_pass and candle
                      and gap is not None and gap >= BREAKOUT_MIN_GAP
                      and cci is not None and cci <= BREAKOUT_MAX_CCI
                      and rsi is not None and rsi >= BREAKOUT_MIN_RSI and _debt_ok(debt_to_equity, BREAKOUT_MAX_DEBT))
    top_pick = (breakout_setup and rsi >= PICK_MIN_RSI and cci <= PICK_MAX_CCI
                and category in ("Small", "") and _debt_ok(debt_to_equity, PICK_MAX_DEBT))
    screens = [name for name, passed in (("CCI", cci_setup), ("Breakout", breakout_setup)) if passed]
    atr_pct = _number(last["atr_pct"])
    return {
        "cci": cci, "cci_ma": cci_ma, "cci_gap": gap, "rsi": rsi, "atr_pct": atr_pct,
        "volatility": volatility_category(atr_pct),
        "cci_pass": cci_pass, "cci_cross": crossed_today, "ema7_pass": ema7_pass,
        "candle_3rule": candle, "close_breakout": close_breakout,
        "cci_setup": cci_setup, "breakout_setup": breakout_setup, "top_pick": top_pick,
        "screens": " + ".join(screens), "screen_count": len(screens),
        "market_cap_cr": market_cap_cr, "debt_to_equity": debt_to_equity, "cap_category": category,
        "fundamentals_known": market_cap_cr is not None and debt_to_equity is not None,
    }
