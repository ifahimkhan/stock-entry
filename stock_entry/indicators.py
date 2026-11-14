"""Technical indicators from 'Mid Cap-1d-Final Irfan.ipynb', computed for every bar.

The notebook formulas are kept as written, including its CCI variant (mean deviation taken as the
rolling mean of |price - rolling mean|), so the app screens the same stocks the notebook does.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CCI_LENGTH = 60
CCI_MA_LENGTH = 9        # EMA of CCI
CCI_CONSTANT = 0.015
RSI_LENGTH = 14
ATR_LENGTH = 14
EMA_LENGTH = 7
CLOSE_BREAKOUT_LOOKBACK = 5  # close above the highest of the previous 5 closes
INDICATOR_COLUMNS = ("cci", "cci_ma", "cci_pass", "rsi", "atr_pct", "ema7_pass", "candle_3rule", "close_breakout")


def wilder_rsi(close: pd.Series, length: int = RSI_LENGTH) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).fillna(0)
    loss = (-delta).clip(lower=0).fillna(0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    return 100 - (100 / (1 + avg_gain / avg_loss))


def cci(high: pd.Series, low: pd.Series, close: pd.Series, length: int = CCI_LENGTH) -> pd.Series:
    typical = (high + low + close) / 3
    mean = typical.rolling(length).mean()
    mean_deviation = (typical - mean).abs().rolling(length).mean()
    return ((typical - mean) / (CCI_CONSTANT * mean_deviation)).replace([np.inf, -np.inf], np.nan)


def atr_percent(high: pd.Series, low: pd.Series, close: pd.Series, length: int = ATR_LENGTH) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    return true_range.rolling(length).mean() / close * 100


def indicator_columns(bars: pd.DataFrame) -> pd.DataFrame:
    """Indicator columns for cleaned bars (same index). Needs High and Low for CCI, ATR and the candle rule."""
    close = bars["Close"].astype(float)
    ema7 = close.ewm(span=EMA_LENGTH, adjust=False).mean()
    columns = {
        "rsi": wilder_rsi(close),
        "ema7_pass": close > ema7,
        "close_breakout": close > close.shift(1).rolling(CLOSE_BREAKOUT_LOOKBACK).max(),
    }
    if {"High", "Low"} <= set(bars.columns):
        high = bars["High"].astype(float)
        low = bars["Low"].astype(float)
        cci_values = cci(high, low, close)
        cci_ma = cci_values.ewm(span=CCI_MA_LENGTH, adjust=False).mean()
        columns = {
            **columns,
            "cci": cci_values,
            "cci_ma": cci_ma,
            "cci_pass": cci_values > cci_ma,
            "atr_pct": atr_percent(high, low, close),
            # Higher high, higher low, and a close above the previous bar's high.
            "candle_3rule": (high > high.shift(1)) & (low > low.shift(1)) & (close > high.shift(1)),
        }
    else:
        empty = pd.Series(np.nan, index=bars.index)
        no = pd.Series(False, index=bars.index)
        columns = {**columns, "cci": empty, "cci_ma": empty, "cci_pass": no, "atr_pct": empty, "candle_3rule": no}
    return pd.DataFrame(columns, index=bars.index)[list(INDICATOR_COLUMNS)]
