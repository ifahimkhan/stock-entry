"""Ascending parallel channel on 2-hour bars, with its entries at the lower line.

The channel is built as the daily one (see stock_entry/channel.py): the base line joins the last two swing
lows, the parallel line passes through the farthest swing high and a second swing high must touch it. Here:
  * the bars are 2-hour bars (BARS_PER_DAY a session), looked for in the latest LOOKBACK of them;
  * the channel is MIN_BARS or more bars long, from its first anchor to the latest bar;
  * only a rising channel counts: slope above FLAT_SLOPE_PCT of the price per bar (0.30% a day);
  * after the last anchor the first close outside the lines is a break; BREAK_MAX_AGE bars later the
    channel is dropped.

Touches of the lower line, counted from the first anchor to the break (or the latest bar):
  * a bar touches when its Low is within TOUCH_SHARE of the channel width of the lower line;
  * after a touch the price must cross the middle (a later bar's High at or above the middle line)
    before the next touch counts;
  * touch 2 is "Entry 1", touch 3 is "Entry 2" (the P-Final column of the Swings 2h table).

Scan-row fields carry the prefix PREFIX.
"""
from __future__ import annotations

import logging
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from stock_entry import channel as daily

logger = logging.getLogger(__name__)
PREFIX = "ch2_"
BARS_PER_DAY = 4                                   # 2-hour bars of one NSE session
LOOKBACK = 250                                     # the channel is looked for in this many latest bars
MIN_BARS = 40                                      # shortest channel: bars from the first anchor to the latest bar
FLAT_SLOPE_PCT = daily.FLAT_SLOPE_PCT / BARS_PER_DAY   # per bar: the daily 0.30% a day
BREAK_MAX_AGE = daily.BREAK_MAX_AGE * BARS_PER_DAY     # a break is listed while it is fewer than this many bars old
TOUCH_SHARE = 0.25                                 # Low within this share of the width of the lower line: a touch
MIDDLE_SHARE = 0.5                                 # High at or above this share of the width: the middle is crossed
ENTRY_1, ENTRY_2 = "Entry 1", "Entry 2"
ENTRY_OF_TOUCH = {2: ENTRY_1, 3: ENTRY_2}
REQUIRED = ("Date", "High", "Low", "Close")
TIME_FORMAT = "%Y-%m-%d %H:%M"
EMPTY: dict[str, Any] = {
    "channel": False, "zone": None, "position_pct": None, "lower": None, "upper": None, "start_lower": None,
    "width_pct": None,
    "slope_pct": None, "since": None, "bars": None, "break_at": None, "touches": None,
    "final": None, "final_at": None, "final_bars": None, "entry1_at": None, "entry2_at": None,
}


class Touch(NamedTuple):
    bar: int        # position in the looked-at bars
    number: int     # 1 = the first touch of the lower line


class Fit(NamedTuple):
    """A rising 2-hour channel over `dates` / `close` (the latest LOOKBACK bars)."""
    slope: float            # of the lines, per bar
    lower: float            # lower line at bar 0
    width: float
    first: int              # first anchor
    break_bar: int | None   # first close outside the lines after the last anchor
    break_up: bool
    dates: pd.Series
    close: np.ndarray
    touches: tuple[Touch, ...]

    def line(self, bar: int) -> float:
        return self.slope * bar + self.lower


def empty_channel() -> dict[str, Any]:
    return {f"{PREFIX}{key}": value for key, value in EMPTY.items()}


def touches(slope: float, lower: float, width: float, first: int, stop: int,
            high: np.ndarray, low: np.ndarray) -> tuple[Touch, ...]:
    """Touches of the lower line by the bars first .. stop - 1; between two touches a bar's High must
    reach the middle line."""
    found: list[Touch] = []
    armed = True
    for bar in range(first, stop):
        line = slope * bar + lower
        if armed and low[bar] <= line + TOUCH_SHARE * width:
            found.append(Touch(bar, len(found) + 1))
            armed = False
        elif not armed and high[bar] >= line + MIDDLE_SHARE * width:
            armed = True
    return tuple(found)


def find(bars: pd.DataFrame) -> Fit | None:
    """The rising channel of the latest bars of `bars` (Date, High, Low, Close; oldest first), or None."""
    if not set(REQUIRED) <= set(bars.columns):
        return None
    clean = bars.dropna(subset=list(REQUIRED[1:])).tail(LOOKBACK).reset_index(drop=True)
    high, low = clean["High"].to_numpy(dtype=float), clean["Low"].to_numpy(dtype=float)
    close = clean["Close"].to_numpy(dtype=float)
    found = daily._candidate(daily.pivots(low, high=False), daily.pivots(high, high=True), low, high, True)
    if found is None or not daily._holds(found, high, low):
        return None
    last = len(close) - 1
    slope, base, width, first = found["slope"], found["lower"], found["width"], found["first"]
    lower = slope * last + base
    middle = lower + width / 2
    if last - first + 1 < MIN_BARS or lower <= 0:
        return None
    if not daily.MIN_WIDTH_PCT <= width / middle * 100 <= daily.MAX_WIDTH_PCT or slope / middle * 100 <= FLAT_SLOPE_PCT:
        return None
    break_bar, break_up = None, False
    for bar in range(found["last"] + 1, last + 1):   # the first close outside the lines
        line = slope * bar + base
        if close[bar] > line + width or close[bar] < line:
            break_bar, break_up = bar, bool(close[bar] > line + width)
            break
    if break_bar is not None and last - break_bar >= BREAK_MAX_AGE:
        return None
    stop = last + 1 if break_bar is None else break_bar
    return Fit(float(slope), float(base), float(width), int(first), break_bar, break_up, clean["Date"], close,
               touches(slope, base, width, first, stop, high, low))


def _time(fit: Fit, bar: int | None) -> str | None:
    return None if bar is None else pd.Timestamp(fit.dates.iloc[bar]).strftime(TIME_FORMAT)


def _fields(fit: Fit) -> dict[str, Any]:
    last = len(fit.close) - 1
    lower, width = fit.line(last), fit.width
    middle = lower + width / 2
    position = (fit.close[last] - lower) / width * 100
    zone = ("Near lower line" if position <= daily.NEAR_LINE_PCT
            else "Near upper line" if position >= 100 - daily.NEAR_LINE_PCT else "Middle")
    if fit.break_bar is not None:
        zone = daily.BREAKOUT_UP if fit.break_up else daily.BREAKDOWN
    entry_bar = {ENTRY_OF_TOUCH[touch.number]: touch.bar for touch in fit.touches if touch.number in ENTRY_OF_TOUCH}
    final = ENTRY_2 if ENTRY_2 in entry_bar else ENTRY_1 if ENTRY_1 in entry_bar else None
    price = lambda value: round(float(value), daily.PRICE_DECIMALS)   # noqa: E731
    return {
        "channel": True, "zone": zone, "position_pct": round(float(position), 1),
        "lower": price(lower), "upper": price(lower + width),
        "start_lower": price(fit.line(fit.first)),   # lower line at the first anchor: the chart draws from there
        "width_pct": price(width / middle * 100),
        "slope_pct": round(float(fit.slope / middle * 100 * BARS_PER_DAY), daily.SLOPE_DECIMALS),   # per day
        "since": _time(fit, fit.first), "bars": last - fit.first + 1, "break_at": _time(fit, fit.break_bar),
        "touches": len(fit.touches),
        "final": final, "final_at": _time(fit, entry_bar.get(final)),
        "final_bars": None if final is None else last - entry_bar[final],
        "entry1_at": _time(fit, entry_bar.get(ENTRY_1)), "entry2_at": _time(fit, entry_bar.get(ENTRY_2)),
    }


def channel_fields(bars: pd.DataFrame | None) -> dict[str, Any]:
    """Scan-row fields (prefixed) of the rising 2-hour channel of `bars`; empty ones without a channel.
    A problem with one stock's bars never drops it from the scan."""
    if bars is None or bars.empty:
        return empty_channel()
    try:
        fit = find(bars)
    except Exception:
        logger.exception("2-hour channel failed")
        return empty_channel()
    if fit is None:
        return empty_channel()
    return {f"{PREFIX}{key}": value for key, value in _fields(fit).items()}


def entry_marks(bars: pd.DataFrame | None) -> dict[str, str]:
    """{bar time: "Entry 1" / "Entry 2"} of the rising 2-hour channel of `bars`: the P-Final column."""
    if bars is None or bars.empty:
        return {}
    try:
        fit = find(bars)
    except Exception:
        logger.exception("2-hour channel entries failed")
        return {}
    if fit is None:
        return {}
    return {_time(fit, touch.bar): ENTRY_OF_TOUCH[touch.number] for touch in fit.touches if touch.number in ENTRY_OF_TOUCH}
