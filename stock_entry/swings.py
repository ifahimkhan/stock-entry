"""Swing structure on 2-hour bars. Same rules as TradingView.ipynb.

swing_label    HH / HL / LH / LL from the "HH LL HL LH Marker" Pine indicator (pivots 5 bars each side),
               placed on the pivot bar, as TradingView draws it. A pivot is only known `PIVOT_RIGHT` bars
               later, so the latest 5 bars never carry a label.
swing_clean    swing_label after the notebook's clean-up (ll_angles):
                 1. an HH becomes LH when a later close (up to the next HH) is below the close of the
                    last LL before it  -> uses later bars, so a past HH can turn into LH on a new scan
                 2. an HH whose close is below the high of the previous LH becomes LH
                 3. an HL followed by LL or LH is dropped
ang_ll_ll      angle LL low -> next LL low, written on the first LL     (method "percent":
ang_ll_hl      angle LL low -> first HL low, only when an HH follows     degrees(atan(% change / bars)))
before_entry   the notebook's Final = "Before Entry": every HH that survives the clean-up
hh_ll_count    on each HH: LLs since the previous HH -> pattern "3LL-HH" / "4LL-HH" (other counts: none)
ll3_hh/ll4_hh  exactly 3 / 4 LLs since the previous HH, then an HH (flag on the HH bar)
lh_breakout    at least 3 LLs or 3 LHs since the previous HH, then the first later close above the
               high of the last LH (flag on that candle; level in lh_breakout_level)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

PIVOT_LEFT = 5
PIVOT_RIGHT = 5
MAX_LOOKBACK = 1000
HH, HL, LH, LL, NONE = "HH", "HL", "LH", "LL", ""
LABEL_PRIORITY = (HH, LL, HL, LH)   # the notebook's idxmax order when one bar has two labels
SETUP_COUNT = 3                     # LLs / LHs needed before an LH breakout
SWING_COLUMNS = ("swing_label", "swing_clean", "before_entry", "hh_ll_count", "ang_ll_ll", "ang_ll_hl",
                 "ll3_hh", "ll4_hh", "lh_breakout", "lh_breakout_level")
NAN = float("nan")


# ---------------------------------------------------------------- marker (Pine port)

def _pivot(high: np.ndarray, low: np.ndarray, i: int, lb: int, rb: int) -> tuple[float, float]:
    """ta.pivothigh / ta.pivotlow confirmed on bar i (the pivot itself is bar i - rb)."""
    if i < lb + rb:
        return NAN, NAN
    c = i - rb
    ph = high[c] if np.all(high[c - lb:c] <= high[c]) and np.all(high[c + 1:i + 1] < high[c]) else NAN
    pl = low[c] if np.all(low[c - lb:c] >= low[c]) and np.all(low[c + 1:i + 1] > low[c]) else NAN
    return ph, pl


def _prev(history: list[float], current: float) -> float:
    """valuewhen(not na(x), x, 1): the previous committed value."""
    if current == current:
        return history[-1] if history else NAN
    return history[-2] if len(history) >= 2 else NAN


def _zigzag_point(ph: float, pl: float, hl_hist: list[float], zz_hist: list[float]) -> tuple[float, float]:
    """Direction (1 high / -1 low) and zigzag price of this bar, with the indicator's filters."""
    if ph == ph:
        hl, zz = 1.0, ph
    elif pl == pl:
        hl, zz = -1.0, pl
    else:
        hl, zz = NAN, NAN
    prev_hl = _prev(hl_hist, hl)
    if pl == pl and hl == -1 and prev_hl == -1 and ph > _prev(zz_hist, zz):
        zz = NAN
    if ph == ph and hl == 1 and prev_hl == 1 and pl < _prev(zz_hist, zz):
        zz = NAN
    if hl == 1 and prev_hl == -1 and zz < _prev(zz_hist, zz):
        hl = NAN
    if hl == -1 and _prev(hl_hist, hl) == 1 and zz > _prev(zz_hist, zz):
        hl = NAN
    return hl, (zz if hl == hl else NAN)


def _previous_points(hl_arr: np.ndarray, zz_arr: np.ndarray, i: int, hl: float, max_lookback: int) -> list[float]:
    """findprevious(): the 4 zigzag points before bar i, alternating direction (0.0 when missing)."""
    other = -1.0 if hl == 1 else 1.0
    points, start = [], 1
    for target in (other, hl, other, hl):
        value = 0.0
        for back in range(start, max_lookback + 1):
            j = i - back
            if j < 0:
                break
            if hl_arr[j] == target and zz_arr[j] == zz_arr[j]:
                value, start = zz_arr[j], back + 1
                break
        points.append(value)
    return points


def _classify(a: float, b: float, c: float, d: float, e: float) -> tuple[bool, bool, bool, bool]:
    """(HH, HL, LH, LL) conditions of the indicator for zigzag point a and the 4 before it."""
    hh = a > b and a > c and c > b and c > d
    ll = a < b and a < c and c < b and c < d
    hl = (a >= c and (b > c and b > d and d > c and d > e)) or (a < b and a > c and b < d)
    lh = (a <= c and (b < c and b < d and d < c and d < e)) or (a > b and a < c and b > d)
    return hh, hl, lh, ll


def swing_labels(high: np.ndarray, low: np.ndarray, lb: int = PIVOT_LEFT, rb: int = PIVOT_RIGHT,
                 max_lookback: int = MAX_LOOKBACK) -> np.ndarray:
    """HH / HL / LH / LL per bar on the pivot bar ("" elsewhere)."""
    n = len(high)
    flags = {label: np.zeros(n, dtype=bool) for label in (HH, HL, LH, LL)}
    hl_arr, zz_arr = np.full(n, NAN), np.full(n, NAN)
    hl_hist: list[float] = []
    zz_hist: list[float] = []
    for i in range(n):
        hl, zz = _zigzag_point(*_pivot(high, low, i, lb, rb), hl_hist, zz_hist)
        hl_arr[i], zz_arr[i] = hl, zz
        if hl == hl:
            hl_hist.append(hl)
        if zz == zz:
            zz_hist.append(zz)
        if zz != zz or hl != hl:
            continue
        found = _classify(zz, *_previous_points(hl_arr, zz_arr, i, hl, max_lookback))
        for label, hit in zip((HH, HL, LH, LL), found):
            flags[label][i] = hit
    labels = np.full(n, NONE, dtype=object)
    for label in reversed(LABEL_PRIORITY):   # higher priority written last wins
        confirmed = flags[label]
        on_pivot = np.r_[confirmed[rb:], np.zeros(min(rb, n), dtype=bool)]  # Pine offset = -rb
        labels[on_pivot] = label
    return labels


# ---------------------------------------------------------------- clean-up (ll_angles rules 1-3)

def _hh_broken_to_lh(labels: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Rule 1: a later close (up to the next HH) below the close of the last LL turns the HH into LH."""
    out = labels.copy()
    hh_pos = np.flatnonzero(labels == HH)
    last_ll_close = NAN
    for i, label in enumerate(labels):
        if label == LL:
            last_ll_close = close[i]
        elif label == HH and last_ll_close == last_ll_close:
            later = hh_pos[hh_pos > i]
            end = later[0] if len(later) else len(labels)
            if np.any(close[i + 1:end] < last_ll_close):
                out[i] = LH
    return out


def _hh_below_lh(labels: np.ndarray, high: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Rule 2: an HH closing below the high of the previous LH becomes LH (and the new reference LH)."""
    out = labels.copy()
    last_lh_high = NAN
    for i, label in enumerate(out):
        if label == LH:
            last_lh_high = high[i]
        elif label == HH and last_lh_high == last_lh_high and close[i] < last_lh_high:
            out[i] = LH
            last_lh_high = high[i]
    return out


def _drop_failed_hl(labels: np.ndarray) -> np.ndarray:
    """Rule 3: an HL whose next label is LL or LH is dropped (checked from the end backwards)."""
    out = labels.copy()
    next_kept = NONE
    for i in range(len(out) - 1, -1, -1):
        label = out[i]
        if label == NONE:
            continue
        if label == HL and next_kept in (LL, LH):
            out[i] = NONE
            continue
        next_kept = label
    return out


def clean_labels(labels: np.ndarray, high: np.ndarray, close: np.ndarray) -> np.ndarray:
    return _drop_failed_hl(_hh_below_lh(_hh_broken_to_lh(labels, close), high, close))


# ---------------------------------------------------------------- angles (method "percent")

def percent_angle(low_from: float, low_to: float, bars: int) -> float:
    """degrees(atan2(% change of the low, bars between the two points))."""
    return float(np.degrees(np.arctan2((low_to - low_from) / low_from * 100.0, bars)))


def ll_angles(clean: np.ndarray, low: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(LL -> next LL, LL -> HL) angles on the LL bars; LL -> HL only when an HH follows that HL
    before the next LL."""
    n = len(clean)
    ang_ll, ang_hl = np.full(n, NAN), np.full(n, NAN)
    ll_idx, hl_idx, hh_idx = (np.flatnonzero(clean == label) for label in (LL, HL, HH))
    for k, i0 in enumerate(ll_idx):
        has_next = k + 1 < len(ll_idx)
        next_ll = ll_idx[k + 1] if has_next else n
        if has_next:
            ang_ll[i0] = percent_angle(low[i0], low[next_ll], next_ll - i0)
        later_hl = hl_idx[(hl_idx > i0) & (hl_idx < next_ll)]
        if len(later_hl) and np.any((hh_idx > later_hl[0]) & (hh_idx < next_ll)):
            ang_hl[i0] = percent_angle(low[i0], low[later_hl[0]], later_hl[0] - i0)
    return ang_ll, ang_hl


# ---------------------------------------------------------------- rules

def ll_count_at_hh(clean: np.ndarray) -> np.ndarray:
    """On each HH bar: how many LLs came since the previous HH (-1 on other bars)."""
    counts = np.full(len(clean), -1, dtype=int)
    count = 0
    for i, label in enumerate(clean):
        if label == LL:
            count += 1
        elif label == HH:
            counts[i] = count
            count = 0
    return counts


def ll_then_hh(clean: np.ndarray, n_ll: int, exact: bool = True) -> np.ndarray:
    """True on an HH that came after n_ll LLs (exactly, or at least) since the previous HH."""
    counts = ll_count_at_hh(clean)
    return (counts == n_ll) if exact else (counts >= n_ll)


def hh_pattern(ll_count: int) -> str:
    """Name of the setup an HH completes: "3LL-HH", "4LL-HH", or "" for any other LL count."""
    return f"{ll_count}LL-HH" if ll_count in (3, 4) else ""


def lh_breakout(clean: np.ndarray, high: np.ndarray, close: np.ndarray,
                n: int = SETUP_COUNT) -> tuple[np.ndarray, np.ndarray]:
    """(flag, level): first close above the last LH high after >= n LLs or >= n LHs since the last HH."""
    flags, levels = np.zeros(len(clean), dtype=bool), np.full(len(clean), NAN)
    ll_count, lh_count, level = 0, 0, NAN
    for i, label in enumerate(clean):
        if label == LL:
            ll_count += 1
        elif label == LH:
            lh_count, level = lh_count + 1, high[i]
            continue  # the breakout must come on a later candle
        if (ll_count >= n or lh_count >= n) and level == level and close[i] > level:
            flags[i], levels[i] = True, level
            ll_count, lh_count, level = 0, 0, NAN
            continue
        if label == HH:
            ll_count, lh_count, level = 0, 0, NAN
    return flags, levels


# ---------------------------------------------------------------- frame + summary

def swing_columns(bars: pd.DataFrame) -> pd.DataFrame:
    """The swing columns for `bars` (needs High, Low, Close; oldest bar first), same index."""
    high, low, close = (bars[column].to_numpy(dtype=float) for column in ("High", "Low", "Close"))
    labels = swing_labels(high, low)
    clean = clean_labels(labels, high, close)
    ang_ll, ang_hl = ll_angles(clean, low)
    breakout, level = lh_breakout(clean, high, close)
    return pd.DataFrame({
        "swing_label": labels, "swing_clean": clean,
        "before_entry": clean == HH, "hh_ll_count": ll_count_at_hh(clean),
        "ang_ll_ll": np.round(ang_ll, 2), "ang_ll_hl": np.round(ang_hl, 2),
        "ll3_hh": ll_then_hh(clean, 3), "ll4_hh": ll_then_hh(clean, 4),
        "lh_breakout": breakout, "lh_breakout_level": level,
    }, index=bars.index)


def bar_time(value: Any) -> str | None:
    """Bar start as "2026-11-16 09:15" (2-hour bars need the time, not only the date)."""
    return None if value is None or pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def _number(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), 2)


def _last_flag(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    """Time of the latest bar with `column` True and how many bars ago it was."""
    hits = np.flatnonzero(frame[column].to_numpy(dtype=bool))
    if not len(hits):
        return {f"{column}_at": None, f"{column}_bars": None}
    pos = int(hits[-1])
    return {f"{column}_at": bar_time(frame["Date"].iloc[pos]), f"{column}_bars": len(frame) - 1 - pos}


def _last_value(frame: pd.DataFrame, column: str) -> tuple[float | None, str | None]:
    values = frame[column].dropna()
    if values.empty:
        return None, None
    return _number(values.iloc[-1]), bar_time(frame["Date"].loc[values.index[-1]])


def _before_entry(frame: pd.DataFrame) -> dict[str, Any]:
    """Latest "Before Entry" (clean HH): when, how long ago, LLs before it and the 3LL / 4LL pattern."""
    found = _last_flag(frame, "before_entry")
    if found["before_entry_at"] is None:
        return {**found, "before_entry_ll": None, "before_entry_pattern": None, "before_entry_close": None,
                "before_entry_change_pct": None}
    pos = len(frame) - 1 - found["before_entry_bars"]
    row = frame.iloc[pos]
    count = int(row["hh_ll_count"])
    return {**found, "before_entry_ll": count, "before_entry_pattern": hh_pattern(count) or None,
            "before_entry_close": _number(row["Close"]),
            # the Change % of Swings 1D: this HH's close vs the close of the last LH before it
            "before_entry_change_pct": _number(hh_change_from_lh(frame).iloc[pos])}


def empty_swing_summary() -> dict[str, Any]:
    """Row fields when no 2-hour bars came back for a stock."""
    return {"swing_bars": 0, "swing_source": None, "swing_last_bar": None, "swing_last_label": None, "swing_last_label_at": None,
            "before_entry_at": None, "before_entry_bars": None, "before_entry_ll": None,
            "before_entry_pattern": None, "before_entry_close": None, "before_entry_change_pct": None,
            "ll3_hh_at": None, "ll3_hh_bars": None, "ll4_hh_at": None, "ll4_hh_bars": None,
            "lh_breakout_at": None, "lh_breakout_bars": None, "lh_breakout_level": None,
            "ang_ll_ll": None, "ang_ll_ll_at": None, "ang_ll_hl": None, "ang_ll_hl_at": None}


def swing_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Latest swing facts of one stock's analysed 2-hour bars (Date + swing columns)."""
    if frame.empty:
        return empty_swing_summary()
    labelled = frame[frame["swing_clean"] != NONE]
    breakout = _last_flag(frame, "lh_breakout")
    hits = frame.loc[frame["lh_breakout"].to_numpy(dtype=bool), "lh_breakout_level"]
    ang_ll, ang_ll_at = _last_value(frame, "ang_ll_ll")
    ang_hl, ang_hl_at = _last_value(frame, "ang_ll_hl")
    return {
        "swing_bars": len(frame),
        "swing_source": frame["Source"].iloc[-1] if "Source" in frame.columns else None,   # tradingview / tradingview-nologin
        "swing_last_bar": bar_time(frame["Date"].iloc[-1]),
        "swing_last_label": labelled["swing_clean"].iloc[-1] if len(labelled) else None,
        "swing_last_label_at": bar_time(labelled["Date"].iloc[-1]) if len(labelled) else None,
        **_before_entry(frame),
        **_last_flag(frame, "ll3_hh"), **_last_flag(frame, "ll4_hh"), **breakout,
        "lh_breakout_level": _number(hits.iloc[-1]) if len(hits) else None,
        "ang_ll_ll": ang_ll, "ang_ll_ll_at": ang_ll_at, "ang_ll_hl": ang_hl, "ang_ll_hl_at": ang_hl_at,
    }


def hh_change_from_lh(frame: pd.DataFrame) -> pd.Series:
    """On each clean HH bar: % from the close of the latest LH before it to the HH's close (else NaN)."""
    values, last_lh = [], None
    for label, close in zip(frame["swing_clean"], frame["Close"].astype(float)):
        values.append((close / last_lh - 1) * 100 if label == HH and last_lh else np.nan)
        if label == LH:
            last_lh = close
    return pd.Series(values, index=frame.index)


def three_rule_candle(frame: pd.DataFrame) -> pd.Series:
    """The 3-rule candle: a higher high, a higher low and a close above the previous bar's high."""
    high, low, close = (frame[column].astype(float) for column in ("High", "Low", "Close"))
    return (high > high.shift(1)) & (low > low.shift(1)) & (close > high.shift(1))


def entry_after_hh(frame: pd.DataFrame) -> pd.Series:
    """Entry: the first 3-rule candle once a TBC Zone Entry HH ("Before Entry") is confirmed.

    An HH is known PIVOT_RIGHT bars after the pivot, so the entry is searched from that bar on, until the
    next Zone Entry HH (one entry per HH)."""
    candle = three_rule_candle(frame).to_numpy()
    zone_hh = np.flatnonzero(frame["before_entry"].to_numpy(dtype=bool))
    entry = np.zeros(len(frame), dtype=bool)
    for k, pivot in enumerate(zone_hh):
        end = zone_hh[k + 1] + PIVOT_RIGHT if k + 1 < len(zone_hh) else len(frame)
        hits = np.flatnonzero(candle[pivot + PIVOT_RIGHT:end])
        if hits.size:
            entry[pivot + PIVOT_RIGHT + hits[0]] = True
    return pd.Series(entry, index=frame.index)


def swing_points(frame: pd.DataFrame, limit: int, every_bar: bool = False) -> list[dict[str, Any]]:
    """The latest `limit` labelled or breakout bars (oldest first) for the single-stock view; with
    `every_bar`, the latest `limit` bars whether labelled or not (the rows of TBC_all_stocks_bars.csv)."""
    marked = frame if every_bar else frame[(frame["swing_clean"] != NONE) | frame["lh_breakout"].astype(bool)]
    change = hh_change_from_lh(frame)
    entry = entry_after_hh(frame)
    marked = marked.tail(limit)
    return [{
        "hh_change_pct": _number(change.loc[index]), "entry": bool(entry.loc[index]),
        "time": bar_time(row["Date"]), "open": _number(row.get("Open")), "high": _number(row["High"]),
        "low": _number(row["Low"]), "volume": _number(row.get("Volume")),
        "close": _number(row["Close"]), "label": row["swing_clean"] or None,
        "raw_label": row["swing_label"] or None,
        "ang_ll_ll": _number(row["ang_ll_ll"]), "ang_ll_hl": _number(row["ang_ll_hl"]),
        "before_entry": bool(row["before_entry"]),
        "ll_count": int(row["hh_ll_count"]) if row["before_entry"] else None,
        "pattern": hh_pattern(int(row["hh_ll_count"])) or None if row["before_entry"] else None,
        "ll3_hh": bool(row["ll3_hh"]), "ll4_hh": bool(row["ll4_hh"]),
        "lh_breakout": bool(row["lh_breakout"]), "lh_breakout_level": _number(row["lh_breakout_level"]),
    } for index, row in marked.iterrows()]


# ---------------------------------------------------------------- daily bars

DAILY_PREFIX = "d1_"   # daily swing fields sit next to the 2-hour ones in a scan row: d1_before_entry_at, ...
DAILY_TIME_KEYS = ("_at", "swing_last_bar", "time")


def _as_day(key: str, value: Any) -> Any:
    """Daily bars have no time of day: "2026-11-16 00:00" -> "2026-11-16"."""
    is_time = isinstance(value, str) and key.endswith(DAILY_TIME_KEYS)
    return value[:10] if is_time else value


def daily_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """A swing summary of daily bars as scan-row fields: keys prefixed with d1_, times as dates."""
    return {f"{DAILY_PREFIX}{key}": _as_day(key, value) for key, value in summary.items()}


def daily_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Swing points of daily bars with the time as a date."""
    return [{key: _as_day(key, value) for key, value in point.items()} for point in points]
