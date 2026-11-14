"""Smart Money Concepts columns matching the "MRI Scan" TradingView indicator as closely as possible.

MRI Scan is closed source. Its settings and drawn output show it is the open LuxAlgo "Smart Money
Concepts" logic (internal structure 5, swing structure 50, ATR order-block filter, high/low
mitigation, fair value gaps) plus an EMA 9 / 20 crossover and two custom marks:

* SPV  - an internal order block candle that is also the first candle of a fair value gap.
* PV   - the first fair-value-gap candle after the order block, inside the same structure break.

The structure, order block and gap rules follow the published LuxAlgo algorithm (CC BY-NC-SA 4.0,
personal non-commercial use). The SPV / PV rules were inferred from the indicator's output on a
chart, so they are a close match, not a guaranteed identical copy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from stock_entry.signals import PRICE_DECIMALS
from stock_entry.summary import clean_value

INTERNAL_LENGTH = 5
SWING_LENGTH = 50
ATR_LENGTH = 200             # order-block volatility filter
HIGH_VOLATILITY_FACTOR = 2   # bar range >= 2 x ATR swaps its high / low for order-block search
FAST_MA, SLOW_MA, LONG_MA = 9, 20, 200
GAP_CONFIRM_BARS = 3         # the gap may start up to one bar after the break bar, so it completes by break + 3

BULLISH, BEARISH, NONE = "Bullish", "Bearish", ""
BOS_UP, BOS_DOWN, CHOCH_UP, CHOCH_DOWN = "BOS up", "BOS down", "CHoCH up", "CHoCH down"
BUY, SELL = "Buy", "Sell"
SPV_BULL, SPV_BEAR, PV_BULL, PV_BEAR = "SPV bull", "SPV bear", "PV bull", "PV bear"
ABOVE, INSIDE, BELOW = "Above", "Inside", "Below"
TEXT_COLUMNS = ("smc_trend", "smc_event", "smc_swing_trend", "smc_swing_event", "smc_fvg", "ma_signal",
                "mri_mark", "mri_signal")
NUMBER_COLUMNS = ("ema9", "ema20", "ema200", "mri_zone_high", "mri_zone_low", "mri_zone_bar")
SMC_COLUMNS = TEXT_COLUMNS + NUMBER_COLUMNS
_OHLC = ("Open", "High", "Low", "Close")


@dataclass(frozen=True)
class Break:
    """A structure break: bar where it happened, direction, and the pivot bar that was broken."""
    bar: int
    bias: str
    pivot_bar: int


def _legs(high: np.ndarray, low: np.ndarray, size: int) -> np.ndarray:
    """LuxAlgo leg: 0 while falling from a confirmed high, 1 while rising from a confirmed low."""
    legs = np.zeros(len(high), dtype=int)
    leg = 0
    for i in range(size, len(high)):
        if high[i - size] > high[i - size + 1:i + 1].max():
            leg = 0
        elif low[i - size] < low[i - size + 1:i + 1].min():
            leg = 1
        legs[i] = leg
    return legs


def _pivots(high: np.ndarray, low: np.ndarray, size: int) -> list[tuple[int, int, float, bool]]:
    """(confirm bar, pivot bar, level, is_high) each time a new leg starts."""
    legs = _legs(high, low, size)
    found = []
    for i in range(size + 1, len(high)):
        change = legs[i] - legs[i - 1]
        if change == 1:
            found.append((i, i - size, float(low[i - size]), False))
        elif change == -1:
            found.append((i, i - size, float(high[i - size]), True))
    return found


def _level_series(pivots: list[tuple[int, int, float, bool]], size: int, want_high: bool) -> tuple[np.ndarray, np.ndarray]:
    """Current pivot level and its bar for every bar (NaN / -1 before the first pivot)."""
    level = np.full(size, np.nan)
    bar = np.full(size, -1, dtype=int)
    for confirm, pivot_bar, value, is_high in pivots:
        if is_high == want_high:
            level[confirm:] = value
            bar[confirm:] = pivot_bar
    return level, bar


def structure(close: np.ndarray, high: np.ndarray, low: np.ndarray, length: int,
              swing_levels: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[list[str], list[str], list[Break]]:
    """Trend, BOS / CHoCH events and the breaks for one structure size.

    `swing_levels` (swing high, swing low) switches on the internal-structure rule that ignores
    an internal level equal to the swing level.
    """
    size = len(close)
    pivots = _pivots(high, low, length)
    high_level, high_bar = _level_series(pivots, size, True)
    low_level, low_bar = _level_series(pivots, size, False)
    trend = NONE
    crossed_high_bar = crossed_low_bar = -1   # pivot bar whose level was already broken
    trends, events, breaks = [], [], []
    for i in range(size):
        event = NONE
        if i > 0:
            differs_high = swing_levels is None or high_level[i] != swing_levels[0][i]
            differs_low = swing_levels is None or low_level[i] != swing_levels[1][i]
            crossed_up = close[i] > high_level[i] and close[i - 1] <= high_level[i - 1]
            crossed_down = close[i] < low_level[i] and close[i - 1] >= low_level[i - 1]
            if crossed_up and high_bar[i] != crossed_high_bar and differs_high:
                event = CHOCH_UP if trend == BEARISH else BOS_UP
                trend, crossed_high_bar = BULLISH, high_bar[i]
                breaks.append(Break(i, BULLISH, int(high_bar[i])))
            # Checked independently, as the indicator does; one bar cannot break both levels.
            if crossed_down and low_bar[i] != crossed_low_bar and differs_low:
                event = CHOCH_DOWN if trend == BULLISH else BOS_DOWN
                trend, crossed_low_bar = BEARISH, low_bar[i]
                breaks.append(Break(i, BEARISH, int(low_bar[i])))
        trends.append(trend)
        events.append(event)
    return trends, events, breaks


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    """ATR as TradingView computes it: Wilder average seeded with a simple mean, NaN before `length` bars."""
    previous = np.concatenate(([np.nan], close[:-1]))
    true_range = np.fmax(high - low, np.fmax(np.abs(high - previous), np.abs(low - previous)))
    atr = np.full(len(close), np.nan)
    if len(close) >= length:
        atr[length - 1] = true_range[:length].mean()
        for i in range(length, len(close)):
            atr[i] = (atr[i - 1] * (length - 1) + true_range[i]) / length
    return atr


def fair_value_gaps(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list[str]:
    """Gap flagged on its third candle: the middle candle must close through the first one's edge."""
    flags = [NONE] * len(close)
    for i in range(2, len(close)):
        middle_up, middle_down = close[i - 1] > open_[i - 1], close[i - 1] < open_[i - 1]
        if low[i] > high[i - 2] and close[i - 1] > high[i - 2] and middle_up:
            flags[i] = BULLISH
        elif high[i] < low[i - 2] and close[i - 1] < low[i - 2] and middle_down:
            flags[i] = BEARISH
    return flags


def _order_block_bar(break_: Break, parsed_high: np.ndarray, parsed_low: np.ndarray) -> int | None:
    """Extreme candle between the broken pivot and the break bar (break bar excluded)."""
    if break_.pivot_bar < 0 or break_.pivot_bar >= break_.bar:
        return None
    window = slice(break_.pivot_bar, break_.bar)
    offset = np.argmin(parsed_low[window]) if break_.bias == BULLISH else np.argmax(parsed_high[window])
    return break_.pivot_bar + int(offset)


def _marks(breaks: list[Break], gaps: list[str], parsed_high: np.ndarray, parsed_low: np.ndarray) -> dict[str, list]:
    """SPV / PV per internal break: the first same-direction gap that starts on or after the order
    block and no later than one bar after the break. SPV when it starts on the order block itself,
    PV otherwise."""
    size = len(gaps)
    mark, signal = [NONE] * size, [NONE] * size
    zone_high, zone_low, zone_bar = ([np.nan] * size for _ in range(3))
    for break_ in breaks:
        block = _order_block_bar(break_, parsed_high, parsed_low)
        if block is None:
            continue
        last_third = min(break_.bar + GAP_CONFIRM_BARS, size - 1)
        first = next((third - 2 for third in range(block + 2, last_third + 1) if gaps[third] == break_.bias), None)
        if first is None:
            continue
        confirm = max(break_.bar, first + 2)
        is_block = first == block
        bullish = break_.bias == BULLISH
        label = (SPV_BULL if bullish else SPV_BEAR) if is_block else (PV_BULL if bullish else PV_BEAR)
        mark[first], signal[confirm] = label, label
        # Zone = the marked candle's own range (for SPV this is the order block).
        zone_high[confirm] = max(parsed_high[first], parsed_low[first])
        zone_low[confirm] = min(parsed_high[first], parsed_low[first])
        zone_bar[confirm] = first
    return {"mri_mark": mark, "mri_signal": signal, "mri_zone_high": zone_high, "mri_zone_low": zone_low,
            "mri_zone_bar": zone_bar}


def _ma_signals(close: pd.Series) -> dict[str, Any]:
    fast = close.ewm(span=FAST_MA, adjust=False).mean()
    slow = close.ewm(span=SLOW_MA, adjust=False).mean()
    above, was_above = fast > slow, (fast > slow).shift(1)
    signal = pd.Series(NONE, index=close.index)
    signal = signal.mask(above & was_above.eq(False), BUY).mask(~above & was_above.eq(True), SELL)
    return {"ema9": fast, "ema20": slow, "ema200": close.ewm(span=LONG_MA, adjust=False).mean(), "ma_signal": signal}


def _blank(index: pd.Index) -> pd.DataFrame:
    columns = {name: pd.Series(NONE if name in TEXT_COLUMNS else np.nan, index=index) for name in SMC_COLUMNS}
    return pd.DataFrame(columns, index=index)


def smc_columns(bars: pd.DataFrame, internal_length: int = INTERNAL_LENGTH, swing_length: int = SWING_LENGTH,
                atr_length: int = ATR_LENGTH) -> pd.DataFrame:
    """SMC / MRI columns for cleaned bars (same index). Blank when open / high / low are missing."""
    if not set(_OHLC) <= set(bars.columns) or bars[list(_OHLC)].isna().any().any() or len(bars) < 3:
        return _blank(bars.index)
    open_, high, low, close = (bars[column].to_numpy(dtype=float) for column in _OHLC)
    size = len(close)
    swing_pivots = _pivots(high, low, swing_length)
    swing_levels = (_level_series(swing_pivots, size, True)[0], _level_series(swing_pivots, size, False)[0])
    swing_trend, swing_event, _ = structure(close, high, low, swing_length)
    trend, event, breaks = structure(close, high, low, internal_length, swing_levels)

    volatile = (high - low) >= HIGH_VOLATILITY_FACTOR * wilder_atr(high, low, close, atr_length)  # False while ATR is NaN
    parsed_high, parsed_low = np.where(volatile, low, high), np.where(volatile, high, low)
    gaps = fair_value_gaps(open_, high, low, close)
    columns = {
        "smc_trend": trend, "smc_event": event, "smc_swing_trend": swing_trend, "smc_swing_event": swing_event,
        "smc_fvg": gaps, **_marks(breaks, gaps, parsed_high, parsed_low),
    }
    frame = pd.DataFrame(columns, index=bars.index)
    moving = pd.DataFrame(_ma_signals(bars["Close"].astype(float)), index=bars.index)
    return pd.concat([frame, moving], axis=1)[list(SMC_COLUMNS)]


def _zone_state(frame: pd.DataFrame, confirm: int, bullish: bool) -> bool:
    """True while no later bar traded through the far side of the zone (high / low mitigation)."""
    row = frame.iloc[confirm]
    later = frame.iloc[confirm + 1:]
    return bool((later["Low"] >= row["mri_zone_low"]).all() if bullish else (later["High"] <= row["mri_zone_high"]).all())


def _position(close: float, high: float, low: float) -> tuple[str, float]:
    """Where the last price sits against a zone, with the distance as a percent of the price."""
    if close > high:
        return ABOVE, round((close - high) / close * 100, PRICE_DECIMALS)
    if close < low:
        return BELOW, round((low - close) / close * 100, PRICE_DECIMALS)
    return INSIDE, 0.0


def _latest(frame: pd.DataFrame, labels: tuple[str, ...]) -> int | None:
    positions = np.flatnonzero(frame["mri_signal"].isin(labels).to_numpy())
    return int(positions[-1]) if len(positions) else None


def _latest_active_spv(frame: pd.DataFrame) -> int | None:
    """Most recent bullish SPV whose order block has not been traded through."""
    positions = np.flatnonzero((frame["mri_signal"] == SPV_BULL).to_numpy())
    return next((int(p) for p in positions[::-1] if _zone_state(frame, int(p), True)), None)


def _price(value: Any) -> float | None:
    cleaned = clean_value(value)
    return None if cleaned is None else round(cleaned, PRICE_DECIMALS)


def smc_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Latest-bar structure state, last EMA signal, the active bullish SPV zone and the latest PV."""
    last = frame.iloc[-1]
    final = len(frame) - 1
    close = float(last["Close"])
    signals = np.flatnonzero((frame["ma_signal"] != NONE).to_numpy())
    last_signal = frame.iloc[int(signals[-1])] if len(signals) else None
    spv = _latest_active_spv(frame) if {"High", "Low"} <= set(frame.columns) else None
    pv = _latest(frame, (PV_BULL, PV_BEAR))
    spv_row = None if spv is None else frame.iloc[spv]
    pv_row = None if pv is None else frame.iloc[pv]
    position = (None, None) if spv_row is None else _position(close, spv_row["mri_zone_high"], spv_row["mri_zone_low"])
    return {
        "smc_trend": last["smc_trend"],
        "smc_event": last["smc_event"],
        "smc_swing_trend": last["smc_swing_trend"],
        "smc_swing_event": last["smc_swing_event"],
        "smc_fvg": last["smc_fvg"],
        "mri_signal": last["mri_signal"],
        "ma_signal": last["ma_signal"],
        "ma_signal_last": None if last_signal is None else last_signal["ma_signal"],
        "ma_signal_date": None if last_signal is None else clean_value(last_signal["Date"]),
        "ema9": _price(last["ema9"]),
        "ema20": _price(last["ema20"]),
        "ema200": _price(last["ema200"]),
        "spv_date": None if spv_row is None else clean_value(frame.iloc[int(spv_row["mri_zone_bar"])]["Date"]),
        "spv_sessions": None if spv is None else final - spv,
        "spv_high": None if spv_row is None else _price(spv_row["mri_zone_high"]),
        "spv_low": None if spv_row is None else _price(spv_row["mri_zone_low"]),
        "spv_position": position[0],
        "spv_distance_pct": position[1],
        "pv_kind": None if pv_row is None else pv_row["mri_signal"],
        "pv_date": None if pv_row is None else clean_value(frame.iloc[int(pv_row["mri_zone_bar"])]["Date"]),
        "pv_sessions": None if pv is None else final - pv,
        "pv_high": None if pv_row is None else _price(pv_row["mri_zone_high"]),
        "pv_low": None if pv_row is None else _price(pv_row["mri_zone_low"]),
    }
