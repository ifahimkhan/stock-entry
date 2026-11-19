"""Win rate of the TBC Zone Entry lists (1D and 2h): replay past signals without hindsight, score each trade.

A signal is the day a stock first appears in a Zone Entry list: a clean HH that completes a 3LL-HH or
4LL-HH pattern, seen with only the bars known on that day. A raw swing pivot is fixed once its PIVOT_RIGHT
bars have passed, so `confirm_signals` only replays the bars on which each HH pivot can be listed;
`brute_force_signals` replays every bar and exists to check that shortcut.

Trade rules (written on the page too):
- Entry: open of the first daily session after the signal day (the list is read after the close).
- Win: a daily high reaches entry x (1 + TARGET); loss: a daily low reaches entry x (1 - STOP); if a session
  opens beyond either level the trade fills at that open. Both levels inside one candle count as a loss.
- Timeout: neither level within MAX_SESSIONS sessions (entry day included); return at that close.
- Open: fewer than MAX_SESSIONS sessions so far and no level hit; not counted in the win rate.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_entry.analysis import DAILY_SWING_BARS, at_closing_high
from stock_entry.intraday import analyze_swings
from stock_entry.swings import HH, PIVOT_RIGHT, hh_change_from_lh, hh_pattern

logger = logging.getLogger(__name__)

TARGET = 0.05
STOP = 0.03
MAX_SESSIONS = 20
WARMUP_BARS = 100              # fewer bars than this: LL counts and the closing-high test are unreliable
WINDOW_BARS = 500              # bars a live scan sees (daily store keeps 500; the 2-hour CSV keeps 500)
MAX_BARS_AGO = {"1d": 15, "2h": 12}   # the app's list windows (BEFORE_ENTRY_MAX_SESSIONS / _BARS)
PATTERNS = ("3LL-HH", "4LL-HH")

TRADE_COLUMNS = ["timeframe", "ticker", "hh_at", "signal_at", "pattern", "hh_close", "change_pct",
                 "entry_at", "entry", "outcome", "exit_at", "exit", "return_pct", "sessions"]
FINISHED = ("win", "loss", "timeout")


@dataclass(frozen=True)
class Signal:
    timeframe: str
    ticker: str
    hh_at: pd.Timestamp       # the HH pivot bar
    signal_at: pd.Timestamp   # the bar that confirmed it: the stock enters the list here
    pattern: str
    hh_close: float
    change_pct: float | None


def _zone_entry(asof: pd.DataFrame, max_bars_ago: int) -> tuple[int, str] | None:
    """(position, pattern) of the latest Zone Entry HH in `asof` (analysed bars), when it is listed."""
    hits = np.flatnonzero(asof["before_entry"].to_numpy(dtype=bool))
    if not len(hits):
        return None
    pos = int(hits[-1])
    pattern = hh_pattern(int(asof["hh_ll_count"].iloc[pos])) or ""
    if pattern not in PATTERNS or len(asof) - 1 - pos > max_bars_ago:
        return None
    return pos, pattern


def _signal(timeframe: str, ticker: str, asof: pd.DataFrame, pos: int, pattern: str) -> Signal:
    change = hh_change_from_lh(asof).iloc[pos]
    return Signal(timeframe, ticker, asof["Date"].iloc[pos], asof["Date"].iloc[-1], pattern,
                  float(asof["Close"].iloc[pos]), None if pd.isna(change) else round(float(change), 2))


def confirm_signals(timeframe: str, ticker: str, bars: pd.DataFrame,
                    skip: Callable[[pd.Timestamp], bool] = lambda when: False,
                    since: pd.Timestamp | None = None) -> list[Signal]:
    """Zone Entry signals of one stock. Each raw HH pivot is checked on the bars from the one that confirms
    it to the last one on which it can still be listed (the clean-up can make an HH qualify a few bars late).
    `skip(signal_at)` drops a signal (a closing high that day: the app does no swing analysis then);
    `since` only checks bars after that time (the daily update)."""
    bars = bars.dropna(subset=["High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)
    labels = analyze_swings(bars)["swing_label"].to_numpy()
    dates = bars["Date"]
    limit = MAX_BARS_AGO[timeframe]
    signals = []
    for pivot in np.flatnonzero(labels == HH):
        pivot = int(pivot)
        for end in range(max(pivot + PIVOT_RIGHT, WARMUP_BARS), min(pivot + limit, len(bars) - 1) + 1):
            if since is not None and dates.iloc[end] <= since:
                continue
            asof = analyze_swings(bars.iloc[max(0, end + 1 - WINDOW_BARS):end + 1])
            found = _zone_entry(asof, limit)
            if found is None or asof["Date"].iloc[found[0]] != dates.iloc[pivot]:
                continue
            signal = _signal(timeframe, ticker, asof, *found)
            if not skip(signal.signal_at):
                signals.append(signal)
            break   # one trade per HH: the first bar it shows in the list
    return signals


def brute_force_signals(timeframe: str, ticker: str, bars: pd.DataFrame,
                        skip: Callable[[pd.Timestamp], bool] = lambda when: False) -> list[Signal]:
    """Same as confirm_signals, replaying every bar (slow): the first bar each HH shows in the list."""
    bars = bars.dropna(subset=["High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)
    seen, signals = set(), []
    for end in range(WARMUP_BARS, len(bars)):
        asof = analyze_swings(bars.iloc[max(0, end + 1 - WINDOW_BARS):end + 1])
        found = _zone_entry(asof, MAX_BARS_AGO[timeframe])
        if found is None:
            continue
        hh_at = asof["Date"].iloc[found[0]]
        if hh_at in seen:
            continue
        seen.add(hh_at)
        signal = _signal(timeframe, ticker, asof, *found)
        if not skip(signal.signal_at):
            signals.append(signal)
    return signals


def closing_high_days(daily: pd.DataFrame) -> set[pd.Timestamp]:
    """Days whose close was >= every close of the previous DAILY_SWING_BARS - 1 days (as of that day)."""
    closes = daily.sort_values("Date")["Close"].astype(float).reset_index(drop=True)
    best = closes.shift(1).rolling(DAILY_SWING_BARS - 1, min_periods=1).max()
    dates = pd.to_datetime(daily.sort_values("Date")["Date"]).dt.normalize().reset_index(drop=True)
    return set(dates[(closes >= best) & best.notna()])


def score(signal: Signal, daily: pd.DataFrame, target: float = TARGET, stop: float = STOP,
          max_sessions: int = MAX_SESSIONS) -> dict:
    """The trade of one signal on daily bars (Date, Open, High, Low, Close; oldest first)."""
    day = pd.Timestamp(signal.signal_at).normalize()
    after = daily[pd.to_datetime(daily["Date"]).dt.normalize() > day].head(max_sessions)
    base = {"timeframe": signal.timeframe, "ticker": signal.ticker, "hh_at": signal.hh_at,
            "signal_at": signal.signal_at, "pattern": signal.pattern, "hh_close": signal.hh_close,
            "change_pct": signal.change_pct}
    if after.empty:
        return {**base, "entry_at": None, "entry": None, "outcome": "open", "exit_at": None, "exit": None,
                "return_pct": None, "sessions": 0}
    entry = float(after["Open"].iloc[0])
    up, down = entry * (1 + target), entry * (1 - stop)
    outcome, exit_at, exit_price = None, None, None
    for i, bar in enumerate(after.itertuples(index=False)):
        opening = entry if i == 0 else float(bar.Open)
        if opening <= down:
            outcome, exit_price = "loss", opening
        elif opening >= up:
            outcome, exit_price = "win", opening
        elif float(bar.Low) <= down:          # both inside one candle: counted as a loss
            outcome, exit_price = "loss", down
        elif float(bar.High) >= up:
            outcome, exit_price = "win", up
        if outcome:
            exit_at, sessions = bar.Date, i + 1
            break
    else:
        sessions = len(after)
        exit_at, exit_price = after["Date"].iloc[-1], float(after["Close"].iloc[-1])
        outcome = "timeout" if sessions >= max_sessions else "open"
    return {**base, "entry_at": after["Date"].iloc[0], "entry": round(entry, 2), "outcome": outcome,
            "exit_at": exit_at, "exit": round(exit_price, 2),
            "return_pct": round((exit_price / entry - 1) * 100, 2), "sessions": sessions}


def merge_trades(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Saved trades + the replayed trades not saved yet. A saved trade keeps its signal (the first bar it
    was listed); open ones are scored again by `rescore`. Trades older than the replay window are kept."""
    key = ["timeframe", "ticker", "hh_at"]
    if old.empty:
        return new[TRADE_COLUMNS].sort_values(["signal_at", "ticker"]).reset_index(drop=True)
    saved = {tuple(row) for row in old[key].astype(str).to_numpy()}
    fresh = new[[tuple(row) not in saved for row in new[key].astype(str).to_numpy()]]
    merged = pd.concat([old, fresh], ignore_index=True) if len(fresh) else old
    return merged[TRADE_COLUMNS].sort_values(["signal_at", "ticker"]).reset_index(drop=True)


def summarize(trades: pd.DataFrame) -> list[dict]:
    """Win rate per timeframe and pattern (plus "all"): N, wins, losses, timeouts, open, win %, avg return."""
    out = []
    for timeframe in ("1d", "2h"):
        frame = trades[trades["timeframe"] == timeframe]
        for pattern in ("all", *PATTERNS):
            part = frame if pattern == "all" else frame[frame["pattern"] == pattern]
            counts = part["outcome"].value_counts()
            wins, losses = int(counts.get("win", 0)), int(counts.get("loss", 0))
            done = part[part["outcome"].isin(FINISHED)]
            out.append({"timeframe": timeframe, "pattern": pattern, "trades": int(len(done)), "wins": wins,
                        "losses": losses, "timeouts": int(counts.get("timeout", 0)), "open": int(counts.get("open", 0)),
                        "win_rate": round(wins / (wins + losses) * 100, 1) if wins + losses else None,
                        "avg_return": round(float(done["return_pct"].mean()), 2) if len(done) else None})
    return out


def replay(daily: dict[str, pd.DataFrame], two_hour: dict[str, pd.DataFrame],
           tickers: Iterable[str] | None = None) -> pd.DataFrame:
    """Scored trades of every stock (1D from `daily`, 2h from `two_hour`, both scored on daily bars)."""
    rows = []
    for ticker in sorted(tickers if tickers is not None else daily):
        bars = daily.get(ticker)
        if bars is None or len(bars) < WARMUP_BARS:
            continue
        highs = closing_high_days(bars)
        skip = lambda when: pd.Timestamp(when).normalize() in highs  # noqa: E731
        try:
            signals = confirm_signals("1d", ticker, bars, skip)
            if ticker in two_hour:
                signals += confirm_signals("2h", ticker, two_hour[ticker], skip)
        except Exception:  # one bad stock must not stop the replay
            logger.exception("Zone Entry replay failed for %s", ticker)
            continue
        rows += [score(signal, bars) for signal in signals]
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


# ---------- Saved trades, the page payload and the command line ----------

def read_trades(path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=TRADE_COLUMNS)
    trades = pd.read_csv(path)
    for column in ("hh_at", "signal_at", "entry_at", "exit_at"):
        trades[column] = pd.to_datetime(trades[column], errors="coerce")
    return trades


def write_trades(trades: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    trades[TRADE_COLUMNS].to_csv(temp, index=False, date_format="%Y-%m-%d %H:%M")
    temp.replace(path)


def rescore(trades: pd.DataFrame, daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Open trades scored again on the latest daily bars (finished ones are left as they are)."""
    rows = []
    for row in trades.to_dict("records"):
        bars = daily.get(row["ticker"])
        if row["outcome"] in FINISHED or bars is None:
            rows.append(row)
            continue
        signal = Signal(row["timeframe"], row["ticker"], row["hh_at"], row["signal_at"], row["pattern"],
                        row["hh_close"], None if pd.isna(row["change_pct"]) else row["change_pct"])
        rows.append(score(signal, bars))
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def _replay_one(args: tuple) -> list[dict]:
    ticker, daily_bars, two_hour_bars, since = args
    highs = closing_high_days(daily_bars)
    skip = lambda when: pd.Timestamp(when).normalize() in highs  # noqa: E731
    try:
        signals = confirm_signals("1d", ticker, daily_bars, skip, since.get("1d"))
        if two_hour_bars is not None:
            signals += confirm_signals("2h", ticker, two_hour_bars, skip, since.get("2h"))
    except Exception:  # one bad stock must not stop the replay
        logger.exception("Zone Entry replay failed for %s", ticker)
        return []
    return [score(signal, daily_bars) for signal in signals]


def update(daily: dict[str, pd.DataFrame], two_hour: dict[str, pd.DataFrame], saved: pd.DataFrame,
           full: bool, workers: int = 1) -> pd.DataFrame:
    """Saved trades + the signals of the bars not replayed yet (all bars when `full`), open ones rescored."""
    since = {} if full or saved.empty else {
        timeframe: part["signal_at"].max() for timeframe, part in saved.groupby("timeframe")}
    jobs = [(ticker, bars, two_hour.get(ticker), since) for ticker, bars in sorted(daily.items())
            if len(bars) >= WARMUP_BARS]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers) as pool:
            results = list(pool.map(_replay_one, jobs, chunksize=8))
    else:
        results = [_replay_one(job) for job in jobs]
    fresh = pd.DataFrame([row for rows in results for row in rows], columns=TRADE_COLUMNS)
    return rescore(merge_trades(saved, fresh), daily)


RULES = (f"Entry at the open of the session after the stock enters the list. Win = +{TARGET:.0%} before "
         f"-{STOP:.0%} (daily highs / lows; a gap through a level fills at the open; both in one candle counts as "
         f"a loss). Timeout = neither within {MAX_SESSIONS} sessions. Win rate = wins / (wins + losses). "
         "Replayed without hindsight on the current stock list, so stocks that were dropped are missing; "
         "the 2-hour replay covers only the bars in TBC_all_stocks_bars.csv (about 4 months).")


def results(path) -> dict:
    """Page payload: per-timeframe / pattern win rates, every trade (newest first) and the rules."""
    trades = read_trades(path)
    times = ("hh_at", "signal_at", "entry_at", "exit_at")

    def clean(key: str, value):   # strict JSON: no NaN / NaT, times as "YYYY-MM-DD HH:MM"
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return None
        if key in times:
            return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")
        return value.item() if hasattr(value, "item") else value

    records = trades.sort_values("signal_at", ascending=False).to_dict("records")
    table = [{key: clean(key, value) for key, value in row.items()} for row in records]
    return {"summary": summarize(trades) if len(trades) else [], "trades": table,
            "rules": RULES, "target": TARGET, "stop": STOP, "max_sessions": MAX_SESSIONS}


def main(argv: list[str] | None = None) -> int:
    """python -m stock_entry.zone_backtest [--full] [--workers N]"""
    import argparse
    import os
    from stock_entry.config import Settings
    from stock_entry.daily_store import read_store
    from stock_entry.two_hour_csv import read_bars

    parser = argparse.ArgumentParser(description="Replay and score TBC Zone Entry signals (win rate).")
    parser.add_argument("--full", action="store_true", help="replay every bar (first run), not only new ones")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    daily = read_store(settings.daily_bars_path)
    if not daily:
        logger.error("No daily bars in %s: run the scan first", settings.daily_bars_path)
        return 1
    two_hour = read_bars(settings.two_hour_csv_path) if settings.two_hour_csv_path.is_file() else {}
    saved = read_trades(settings.zone_trades_path)
    trades = update(daily, two_hour, saved, full=args.full or saved.empty, workers=args.workers)
    write_trades(trades, settings.zone_trades_path)
    for row in summarize(trades):
        if row["pattern"] == "all":
            logger.info("%s: %d trades, %d wins, %d losses, %d timeouts, %d open, win rate %s%%",
                        row["timeframe"], row["trades"], row["wins"], row["losses"], row["timeouts"],
                        row["open"], row["win_rate"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
