"""Parallel channel: two parallel trendlines that enclose the price, as the TradingView drawing tool.

How it is built, on daily bars:
  * swing points are pivots with PIVOT_BARS bars on each side (a pivot is known PIVOT_BARS days later);
  * the base line joins the last two swing lows (rising or flat channel) or the last two swing highs
    (falling or flat channel);
  * the parallel line has the same slope and passes through the swing point on the other side that lies
    farthest from the base line, from the first base point on. That is the third anchor;
  * a second swing point on that side must come within TOUCH_SHARE of the channel width of the parallel
    line, so each line is touched at least twice;
  * the channel holds when no bar between the first and the last anchor reaches outside the lines by more
    than TOLERANCE of the channel width.

Type, from the slope of the lines in percent of the price per day: Ascending above FLAT_SLOPE_PCT,
Descending below -FLAT_SLOPE_PCT, Horizontal in between.

After the last anchor the first close outside the lines is a break: "Breakout up" above the upper line,
"Breakdown" below the lower line. A break older than BREAK_MAX_AGE trading days drops the channel.

A Descending channel has its own "Breakout up": the latest close is above the highest High of the
LL_HIGH_BARS candles just before the latest LL (the cleaned daily swing label, as in the Swings 1D table).
It shows for as long as the close stays above that level; its date is the first day of that run of closes.
A close above the upper line of a falling channel is no breakout any more ("Above upper line"), though it
still ends the channel after BREAK_MAX_AGE trading days.

    python -m stock_entry.channel

recomputes these fields of the saved scan from data/daily_bars.csv, in seconds and without downloads.
"""
from __future__ import annotations

import logging
import sys
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from stock_entry.config import Settings
from stock_entry.daily_store import read_store
from stock_entry.store import ScanStore
from stock_entry.swings import LL, swing_columns

logger = logging.getLogger(__name__)
PIVOT_BARS = 5            # bars on each side of a swing point (same as the swing labels)
LOOKBACK = 250            # the channel is looked for in this many latest daily bars
MIN_DAYS = 30             # shortest channel: trading days from the first anchor to the latest day
MIN_WIDTH_PCT = 3.0       # narrowest channel, in percent of its middle line
MAX_WIDTH_PCT = 20.0      # widest channel: beyond this the lines say little
TOUCH_SHARE = 0.25        # a second swing point within this share of the width of the parallel line: a touch
TOLERANCE = 0.10          # a bar may reach outside the lines by this share of the channel width
FLAT_SLOPE_PCT = 0.30     # slope within +/- this percent of the price per day: horizontal
BREAK_MAX_AGE = 5         # a break is listed while it is fewer than this many trading days old
NEAR_LINE_PCT = 25.0      # position within this percent of a line: "near" that line
LL_HIGH_BARS = 4          # Descending breakout: close above the highest High of this many candles before the latest LL
SWING_BARS = 500          # the swing labels are worked out on this many latest daily bars, as the Swings 1D table
PRICE_DECIMALS = 2
SLOPE_DECIMALS = 3
ASCENDING, DESCENDING, HORIZONTAL = "Ascending", "Descending", "Horizontal"
INSIDE, BREAKOUT_UP, BREAKDOWN = "Inside", "Breakout up", "Breakdown"
ABOVE_UPPER = "Above upper line"   # a falling channel whose close is over its upper line, without the LL breakout
REQUIRED = ("Date", "High", "Low", "Close")


def empty_channel() -> dict[str, Any]:
    return {"channel": False, "channel_type": None, "channel_status": None, "channel_zone": None,
            "channel_since": None, "channel_days": None, "channel_slope_pct": None, "channel_width_pct": None,
            "channel_lower": None, "channel_upper": None, "channel_position_pct": None,
            "channel_break_date": None, "channel_start_lower": None, "channel_start_upper": None,
            "channel_breakout_level": None}


def pivots(values: np.ndarray, bars: int = PIVOT_BARS, high: bool = True) -> np.ndarray:
    """Indexes of the swing highs (or lows) of `values`: the extreme of the `bars` bars on each side."""
    found = []
    for i in range(bars, len(values) - bars):
        window = values[i - bars:i + bars + 1]
        if values[i] == (window.max() if high else window.min()) and (window == values[i]).sum() == 1:
            found.append(i)
    return np.array(found, dtype=int)


def _candidate(base: np.ndarray, other: np.ndarray, base_values: np.ndarray, other_values: np.ndarray,
               base_is_low: bool) -> dict[str, Any] | None:
    """Channel from the last two `base` pivots and the farthest `other` pivot, or None when there is none.

    Returns the slope per bar, the first anchor, the last anchor and the lower-line value at bar 0."""
    if len(base) < 2:
        return None
    first, second = int(base[-2]), int(base[-1])
    slope = (base_values[second] - base_values[first]) / (second - first)
    intercept = base_values[first] - slope * first
    across = other[other >= first]
    if not len(across):
        return None
    distance = other_values[across] - (slope * across + intercept)
    third = int(across[np.argmax(distance if base_is_low else -distance)])
    offset = float(other_values[third] - (slope * third + intercept))
    if (offset <= 0) if base_is_low else (offset >= 0):
        return None
    gaps = np.abs(np.delete(distance, int(np.argmax(distance if base_is_low else -distance))) - offset)
    if not len(gaps) or gaps.min() > TOUCH_SHARE * abs(offset):
        return None   # the parallel line is touched once only
    lower = intercept if base_is_low else intercept + offset
    return {"slope": float(slope), "lower": float(lower), "width": abs(offset),
            "first": first, "last": max(second, third)}


def _holds(found: dict[str, Any], high: np.ndarray, low: np.ndarray) -> bool:
    """No bar between the first and the last anchor reaches outside the lines beyond the tolerance."""
    index = np.arange(found["first"], found["last"] + 1)
    lower = found["slope"] * index + found["lower"]
    slack = TOLERANCE * found["width"]
    return bool(np.all(low[index] >= lower - slack) and np.all(high[index] <= lower + found["width"] + slack))


def _kind(slope_pct: float) -> str:
    if slope_pct > FLAT_SLOPE_PCT:
        return ASCENDING
    return DESCENDING if slope_pct < -FLAT_SLOPE_PCT else HORIZONTAL


def ll_breakout(high: np.ndarray, close: np.ndarray, labels: np.ndarray) -> tuple[float | None, int | None]:
    """The Descending breakout: (level, bar). Level = the highest High of the LL_HIGH_BARS candles just
    before the latest LL of `labels`. Bar = None unless the latest close is above the level; then the first
    bar of the run of closes above it that reaches the latest bar. (None, None) without an LL or with fewer
    candles before it."""
    marked = np.flatnonzero(labels == LL)
    if not len(marked) or marked[-1] < LL_HIGH_BARS:
        return None, None
    at = int(marked[-1])
    level = float(high[at - LL_HIGH_BARS:at].max())
    above = close[at + 1:] > level
    if not len(above) or not above[-1]:
        return level, None
    below = np.flatnonzero(~above)   # the run starts right after the last close at or under the level
    return level, at + 1 + (int(below[-1]) + 1 if len(below) else 0)


def _describe(found: dict[str, Any], dates: pd.Series, close: np.ndarray,
              ll_level: float | None = None, ll_bar: int | None = None) -> dict[str, Any] | None:
    """Scan fields of one candidate, or None when it is too short, too narrow / wide or long broken.
    `ll_level` / `ll_bar` are the Descending breakout (see ll_breakout); other channels ignore them."""
    last = len(close) - 1
    line = lambda bar: found["slope"] * bar + found["lower"]   # noqa: E731  the lower line
    lower, width = line(last), found["width"]
    middle = lower + width / 2
    days = last - found["first"] + 1
    if days < MIN_DAYS or lower <= 0 or not MIN_WIDTH_PCT <= width / middle * 100 <= MAX_WIDTH_PCT:
        return None
    slope_pct = found["slope"] / middle * 100
    kind = _kind(slope_pct)
    left_bar, left_up = None, False
    for bar in range(found["last"] + 1, last + 1):   # the first close outside the lines
        if close[bar] > line(bar) + width or close[bar] < line(bar):
            left_bar, left_up = bar, bool(close[bar] > line(bar) + width)
            break
    if left_bar is not None and last - left_bar >= BREAK_MAX_AGE:
        return None
    status, break_bar = INSIDE, None
    if left_bar is not None and not left_up:
        status, break_bar = BREAKDOWN, left_bar
    elif left_bar is not None and kind != DESCENDING:
        status, break_bar = BREAKOUT_UP, left_bar
    if kind == DESCENDING:   # its breakout is the close above the candles before the latest LL, not the line
        if ll_bar is not None:   # the latest close is above the level: that is the state now
            status, break_bar = BREAKOUT_UP, ll_bar
    else:
        ll_level = None
    position = (close[last] - lower) / width * 100
    zone = ("Near lower line" if position <= NEAR_LINE_PCT else ABOVE_UPPER if position > 100
            else "Near upper line" if position >= 100 - NEAR_LINE_PCT else "Middle")
    day = lambda bar: pd.Timestamp(dates.iloc[bar]).strftime("%Y-%m-%d")   # noqa: E731
    price = lambda value: round(float(value), PRICE_DECIMALS)             # noqa: E731
    return {
        "channel": True, "channel_type": kind, "channel_status": status,
        "channel_zone": zone if status == INSIDE else status,
        "channel_since": day(found["first"]), "channel_days": days,
        "channel_slope_pct": round(float(slope_pct), SLOPE_DECIMALS),
        "channel_width_pct": price(width / middle * 100),
        "channel_lower": price(lower), "channel_upper": price(lower + width),
        "channel_position_pct": round(float(position), 1),
        "channel_break_date": None if break_bar is None else day(break_bar),
        "channel_start_lower": price(line(found["first"])), "channel_start_upper": price(line(found["first"]) + width),
        "channel_breakout_level": None if ll_level is None else price(ll_level),
    }


def _clean_labels(bars: pd.DataFrame) -> np.ndarray:
    """The cleaned swing label (HH / HL / LH / LL / "") of each bar, from the latest SWING_BARS bars so
    they match the Swings 1D table; all empty when the swing analysis fails."""
    labels = np.full(len(bars), "", dtype=object)
    try:
        recent = bars.tail(SWING_BARS)
        labels[len(bars) - len(recent):] = swing_columns(recent)["swing_clean"].to_numpy()
    except Exception:   # a swing problem only leaves the Descending breakout empty
        logger.exception("Swing labels for the channel breakout failed")
    return labels


def parallel_channel(bars: pd.DataFrame, labels: np.ndarray | None = None) -> dict[str, Any]:
    """Channel fields of the latest bar of `bars` (Date, High, Low, Close; oldest first).
    `labels`: the cleaned swing label of each bar that has High, Low and Close (worked out when not given)."""
    if not set(REQUIRED) <= set(bars.columns):
        return empty_channel()
    priced = bars.dropna(subset=list(REQUIRED[1:]))
    labels = _clean_labels(priced) if labels is None else np.asarray(labels, dtype=object)
    labels = labels[-LOOKBACK:]
    clean = priced.tail(LOOKBACK).reset_index(drop=True)
    high, low = clean["High"].to_numpy(dtype=float), clean["Low"].to_numpy(dtype=float)
    close = clean["Close"].to_numpy(dtype=float)
    ll_level, ll_bar = ll_breakout(high, close, labels)
    highs, lows = pivots(high, high=True), pivots(low, high=False)
    options = []
    for found, wanted in ((_candidate(lows, highs, low, high, True), (ASCENDING, HORIZONTAL)),
                          (_candidate(highs, lows, high, low, False), (DESCENDING, HORIZONTAL))):
        if found is None or not _holds(found, high, low):
            continue
        fields = _describe(found, clean["Date"], close, ll_level, ll_bar)
        if fields is not None and fields["channel_type"] in wanted:   # rising: from lows; falling: from highs
            options.append(fields)
    if not options:
        return empty_channel()
    return min(options, key=lambda fields: fields["channel_width_pct"])   # two flat ones: the narrower


def apply_to_scan(scan: dict[str, Any], frames: dict[str, pd.DataFrame]) -> tuple[dict[str, Any], Counter]:
    """A new scan whose rows carry the channel fields from `frames`; stocks without bars get empty fields."""
    rows = []
    for row in scan["rows"]:
        frame = frames.get(row["ticker"])
        rows.append({**row, **(empty_channel() if frame is None else parallel_channel(frame.sort_values("Date")))})
    return {**scan, "rows": rows}, Counter(row["channel_type"] for row in rows if row["channel"])


def run(settings: Settings) -> int:
    store = ScanStore(settings.scan_path)
    scan = store.load()
    if scan is None:
        logger.error("No saved scan at %s: run a scan first", settings.scan_path)
        return 1
    if not settings.daily_bars_path.is_file():
        logger.error("%s not found", settings.daily_bars_path)
        return 1
    updated, counts = apply_to_scan(scan, read_store(settings.daily_bars_path))
    store.save(updated)
    logger.info("Parallel channels: %s of %d stocks", dict(counts), len(updated["rows"]))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(Settings()))
