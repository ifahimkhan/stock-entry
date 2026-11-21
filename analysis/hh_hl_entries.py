"""HH / HL uptrend entries (long only): how often Entry 1 and Entry 2 work, on daily, weekly and monthly bars.

Run:  .venv\\Scripts\\python.exe analysis\\hh_hl_entries.py

Swings and the 3-rule candle come from the app (stock_entry/swings.py). Every bar is replayed "as of" its
own close, so a signal only uses what was known that day. See analysis/README.md for every assumption.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stock_entry.daily_store import read_store  # noqa: E402
from stock_entry.two_hour_csv import read_bars as read_two_hour  # noqa: E402
from stock_entry.swings import HH, HL, LH, NONE, PIVOT_RIGHT, clean_labels, swing_labels, three_rule_candle  # noqa: E402

# ------------------------------------------------------------------ CONFIG (every parameter lives here)
CONFIG = {
    "data_path": ROOT / "data" / "daily_bars.csv",
    "two_hour_path": ROOT / "TBC_all_stocks_bars.csv",   # the app's 2-hour bars (about 500 per stock, 4 per session)
    "output_dir": ROOT / "analysis" / "output",
    "signals_file": "signals.csv",
    "workbook_file": "hh_hl_entries.xlsx",
    "app_file": ROOT / "data" / "hh_hl_entries.json",   # what the web app's "TBC Zone Entry Sequence" section shows
    # timeframe -> pandas period ("" = bars as given) and the forward horizons in bars of that timeframe;
    # "two_hour": the bars come from two_hour_path instead of the daily file
    "timeframes": {
        "2h": {"period": "", "horizons": (4, 8, 20), "two_hour": True},   # 1, 2 and 5 sessions
        "Daily": {"period": "", "horizons": (5, 10, 20)},
        "Weekly": {"period": "W-FRI", "horizons": (2, 4, 8)},
        "Monthly": {"period": "M", "horizons": (1, 2, 3)},
    },
    "round_trip_cost_pct": 0.2,       # 0.1% per side, taken off every forward return
    "confirm_bars": PIVOT_RIGHT,      # a swing is known this many bars after its pivot (the app's rule)
    "min_sample": 30,                 # fewer measured signals than this: win rate flagged as unreliable
    "significance_p": 0.05,           # p-value below this: difference called statistically meaningful
    "sanity_samples": 3,              # Entry 2 signals printed with their surrounding candles
    "sanity_bars_before": 8,
    "sanity_bars_after": 3,
    "random_seed": 7,
    "daily_timeframe": "Daily",       # timeframe of the per-stock status and the weekly-trend split
    "two_hour_timeframe": "2h",       # timeframe of the per-stock 2-hour status
    "trend_timeframe": "Weekly",
    "limit_stocks": None,             # e.g. 20 for a quick trial run; None = every stock
}

ENTRY_1, ENTRY_2 = 1, 2
STAGE_NONE, STAGE_HH1, STAGE_HL1, STAGE_HH2 = "none", "HH1", "HL1", "HH2"
GROUP_E1, GROUP_E2A, GROUP_E2B = "Entry 1", "Entry 2 (a) after Entry 1", "Entry 2 (b) any"
TREND_UP, TREND_NOT_UP, TREND_UNKNOWN = "weekly up", "weekly not up", "weekly unknown"
PCT = 100.0


# ------------------------------------------------------------------ bars

def resample(bars: pd.DataFrame, period: str) -> pd.DataFrame:
    """Daily bars as weekly / monthly bars; a last period that is still running is dropped."""
    if not period:
        return bars.reset_index(drop=True)
    periods = bars["Date"].dt.to_period(period)
    out = bars.groupby(periods).agg(Date=("Date", "last"), Open=("Open", "first"), High=("High", "max"),
                                    Low=("Low", "min"), Close=("Close", "last"), Volume=("Volume", "sum"))
    if len(out) and out.index[-1].end_time.normalize() > bars["Date"].max():
        out = out.iloc[:-1]
    return out.reset_index(drop=True)


# ------------------------------------------------------------------ as-of replay

def replay(bars: pd.DataFrame, confirm: int) -> tuple[list[dict], list[dict]]:
    """Walk the bars one close at a time. Returns (signals, state per bar).

    At bar t only pivots up to t - confirm carry a label, and the app's clean-up runs on closes up to t.
    Stage: the last clean labels are LH-HH (HH1), LH-HH-HL (HL1) or LH-HH-HL-HH (HH2).
    Entry 1 = first 3-rule candle while the stage is HL1; Entry 2 = first one while it is HH2 that also
    closes below HH2's close (as in the app's Final column)."""
    high, low, close = (bars[c].to_numpy(dtype=float) for c in ("High", "Low", "Close"))
    raw = swing_labels(high, low)
    candle = three_rule_candle(bars).to_numpy(dtype=bool)
    fired1: dict[tuple, int] = {}
    fired2: dict[tuple, int] = {}
    signals, states = [], []
    for t in range(len(bars)):
        known = raw[:t + 1].copy()
        known[max(0, t + 1 - confirm):] = NONE
        clean = clean_labels(known, high[:t + 1], close[:t + 1])
        pos = np.flatnonzero(clean != NONE)
        last = [clean[p] for p in pos[-4:]]
        stage, key = STAGE_NONE, ()   # key: the pivots of HH1, HL1, HH2 (the LH before HH1 is implied)
        if last[-4:] == [LH, HH, HL, HH]:
            stage, key = STAGE_HH2, tuple(int(p) for p in pos[-3:])
        elif last[-3:] == [LH, HH, HL]:
            stage, key = STAGE_HL1, tuple(int(p) for p in pos[-2:])
        elif last[-2:] == [LH, HH]:
            stage, key = STAGE_HH1, (int(pos[-1]),)
        fired = None
        if candle[t] and stage == STAGE_HL1 and key not in fired1:
            fired1[key] = t
            fired = {"entry": ENTRY_1, "after_entry1": False, "pivots": key}
        elif candle[t] and stage == STAGE_HH2 and key not in fired2 and close[t] < close[key[2]]:
            fired2[key] = t
            fired = {"entry": ENTRY_2, "after_entry1": key[:2] in fired1, "pivots": key,
                     "entry1_idx": fired1.get(key[:2])}
        if fired:
            signals.append({**fired, "signal_idx": t})
        entry_done = (stage == STAGE_HL1 and key in fired1) or (stage == STAGE_HH2 and key in fired2)
        states.append({"stage": stage, "entry_fired": entry_done, "labels": len(pos),
                       "uptrend": len(last) >= 2 and all(label in (HH, HL) for label in last[-2:])})
    final = clean_labels(np.where(np.arange(len(raw)) <= len(raw) - 1 - confirm, raw, NONE), high, close) if len(raw) else raw
    for signal in signals:   # does the sequence still stand with today's knowledge (hindsight clean-up)?
        want = [HH, HL, HH][:len(signal["pivots"])]
        signal["sequence_stands_today"] = [final[p] for p in signal["pivots"]] == want
    return signals, states


# ------------------------------------------------------------------ outcomes

def bar_day(stamp: pd.Timestamp) -> object:
    """A bar's day; for an intraday bar its start as text, "2026-11-16 09:15" (the app's 2-hour format)."""
    stamp = pd.Timestamp(stamp)
    return stamp.date() if stamp == stamp.normalize() else stamp.strftime("%Y-%m-%d %H:%M")


def measure(bars: pd.DataFrame, signal_idx: int, horizons: tuple[int, ...], cost: float) -> dict:
    """Entry at the next bar's open; forward return, MFE and MAE over each horizon (entry bar = bar 1)."""
    entry_idx = signal_idx + 1
    out: dict = {"entry_date": None, "entry_price": None}
    if entry_idx >= len(bars):
        return {**out, **{f"h{k}_bars": n for k, n in enumerate(horizons, 1)}}
    entry = float(bars["Open"].iloc[entry_idx])
    out = {"entry_date": bar_day(bars["Date"].iloc[entry_idx]), "entry_price": round(entry, 2)}
    for k, n in enumerate(horizons, 1):
        out[f"h{k}_bars"] = n
        end = entry_idx + n   # bars entry_idx .. end-1 are the n bars held
        if end > len(bars) or not entry > 0:
            continue
        window = bars.iloc[entry_idx:end]
        gross = (float(window["Close"].iloc[-1]) / entry - 1) * PCT
        out[f"h{k}_ret_gross_pct"] = round(gross, 3)
        out[f"h{k}_ret_net_pct"] = round(gross - cost, 3)
        out[f"h{k}_mfe_pct"] = round((float(window["High"].max()) / entry - 1) * PCT, 3)
        out[f"h{k}_mae_pct"] = round((float(window["Low"].min()) / entry - 1) * PCT, 3)
        out[f"h{k}_win_gross"] = gross > 0
        out[f"h{k}_win_net"] = gross - cost > 0
    return out


def signal_rows(stock: str, timeframe: str, bars: pd.DataFrame, signals: list[dict], horizons, cfg) -> list[dict]:
    day = lambda i: bar_day(bars["Date"].iloc[i])  # noqa: E731
    confirmed = lambda i: day(min(i + cfg["confirm_bars"], len(bars) - 1))  # noqa: E731
    rows = []
    for s in signals:
        p = s["pivots"]
        hh2 = p[2] if len(p) > 2 else None
        rows.append({
            "stock": stock, "timeframe": timeframe, "entry_type": s["entry"],
            "after_entry1": s["after_entry1"] if s["entry"] == ENTRY_2 else None,
            "hh1_date": day(p[0]), "hh1_confirmed": confirmed(p[0]),
            "hl1_date": day(p[1]), "hl1_confirmed": confirmed(p[1]),
            "hh2_date": day(hh2) if hh2 is not None else None,
            "hh2_confirmed": confirmed(hh2) if hh2 is not None else None,
            "signal_date": day(s["signal_idx"]),
            **measure(bars, s["signal_idx"], horizons, cfg["round_trip_cost_pct"]),
            "sequence_stands_today": s["sequence_stands_today"], "signal_idx": s["signal_idx"],
        })
    return rows


# ------------------------------------------------------------------ summaries

def long_form(signals: pd.DataFrame, slots: int) -> pd.DataFrame:
    """One row per signal x horizon, with the entry groups (Entry 2 (b) holds every Entry 2)."""
    parts = []
    for k in range(1, slots + 1):
        cols = {f"h{k}_bars": "bars", f"h{k}_ret_gross_pct": "ret_gross", f"h{k}_ret_net_pct": "ret_net",
                f"h{k}_mfe_pct": "mfe", f"h{k}_mae_pct": "mae"}
        base = signals[["stock", "timeframe", "entry_type", "after_entry1", "weekly_trend"]].copy()
        for source, target in cols.items():
            base[target] = signals[source] if source in signals else np.nan
        parts.append(base)
    rows = pd.concat(parts, ignore_index=True)
    e1 = rows[rows.entry_type == ENTRY_1].assign(group=GROUP_E1)
    e2 = rows[rows.entry_type == ENTRY_2]
    return pd.concat([e1, e2[e2.after_entry1 == True].assign(group=GROUP_E2A), e2.assign(group=GROUP_E2B)],  # noqa: E712
                     ignore_index=True)


def stats(part: pd.DataFrame, min_sample: int) -> dict:
    done = part.dropna(subset=["ret_gross"])
    net, gross = done["ret_net"], done["ret_gross"]
    wins, losses = net[net > 0], net[net <= 0]
    mean = lambda s: round(float(s.mean()), 2) if len(s) else None  # noqa: E731
    return {
        "signals": len(part), "measured": len(done), "skipped_no_data": len(part) - len(done),
        "win_rate_gross_pct": round(float((gross > 0).mean()) * PCT, 1) if len(done) else None,
        "win_rate_net_pct": round(float((net > 0).mean()) * PCT, 1) if len(done) else None,
        "avg_ret_gross_pct": mean(gross), "avg_ret_net_pct": mean(net),
        "median_ret_net_pct": round(float(net.median()), 2) if len(done) else None,
        "avg_win_net_pct": mean(wins), "avg_loss_net_pct": mean(losses),
        "avg_mfe_pct": mean(done["mfe"]), "avg_mae_pct": mean(done["mae"]),
        "best_net_pct": round(float(net.max()), 2) if len(done) else None,
        "worst_net_pct": round(float(net.min()), 2) if len(done) else None,
        "reliable": "yes" if len(done) >= min_sample else f"NO (<{min_sample} signals)",
    }


def summarize(rows: pd.DataFrame, by: list[str], min_sample: int) -> pd.DataFrame:
    keys = [*by, "bars"]
    out = [{**dict(zip(keys, key)), **stats(part, min_sample)} for key, part in rows.groupby(keys, sort=False)]
    return pd.DataFrame(out)


def _p_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def compare(rows: pd.DataFrame, cfg) -> pd.DataFrame:
    """Entry 1 against Entry 2 (a) and (b): win-rate (two-proportion z) and mean-return (Welch z) tests."""
    out = []
    for (timeframe, bars), part in rows.dropna(subset=["ret_gross"]).groupby(["timeframe", "bars"], sort=False):
        base = part[part.group == GROUP_E1]["ret_net"]
        for other_name in (GROUP_E2A, GROUP_E2B):
            other = part[part.group == other_name]["ret_net"]
            row = {"timeframe": timeframe, "bars": bars, "compared": f"{other_name} vs {GROUP_E1}",
                   "n_entry1": len(base), "n_entry2": len(other)}
            if len(base) < 2 or len(other) < 2:
                out.append({**row, "verdict": "too few signals to compare"})
                continue
            w1, w2 = float((base > 0).mean()), float((other > 0).mean())
            pooled = (w1 * len(base) + w2 * len(other)) / (len(base) + len(other))
            se_w = math.sqrt(pooled * (1 - pooled) * (1 / len(base) + 1 / len(other)))
            se_m = math.sqrt(base.var() / len(base) + other.var() / len(other))
            p_win = _p_two_sided((w2 - w1) / se_w) if se_w else 1.0
            p_ret = _p_two_sided((other.mean() - base.mean()) / se_m) if se_m else 1.0
            small = min(len(base), len(other)) < cfg["min_sample"]
            better = "Entry 2" if other.mean() > base.mean() else "Entry 1"
            meaningful = (not small) and p_ret < cfg["significance_p"]
            out.append({**row,
                        "win_rate_entry1_pct": round(w1 * PCT, 1), "win_rate_entry2_pct": round(w2 * PCT, 1),
                        "win_rate_diff_pts": round((w2 - w1) * PCT, 1), "p_win_rate": round(p_win, 4),
                        "avg_ret_entry1_pct": round(float(base.mean()), 2), "avg_ret_entry2_pct": round(float(other.mean()), 2),
                        "avg_ret_diff_pct": round(float(other.mean() - base.mean()), 2), "p_avg_ret": round(p_ret, 4),
                        "verdict": f"{better} better on average return; "
                                   + ("difference is statistically meaningful" if meaningful else
                                      "sample too small to say" if small else "difference is NOT statistically meaningful")})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ run

def weekly_trend_at(dates: np.ndarray, states: list[dict], when: pd.Timestamp) -> str:
    """Weekly trend known at `when`: the last finished weekly bar on or before it."""
    i = int(np.searchsorted(dates, np.datetime64(when), side="right")) - 1
    if i < 0 or states[i]["labels"] < 2:
        return TREND_UNKNOWN
    return TREND_UP if states[i]["uptrend"] else TREND_NOT_UP


def status_text(state: dict, entry: int | None) -> str:
    if state["entry_fired"]:
        return f"Entry {ENTRY_1 if state['stage'] == STAGE_HL1 else ENTRY_2} fired"
    return state["stage"]


def run(cfg: dict) -> dict[str, pd.DataFrame]:
    store = read_store(cfg["data_path"])
    two_hour = read_two_hour(cfg["two_hour_path"]) if cfg["two_hour_path"].exists() else {}
    print(f"2-hour bars: {len(two_hour)} stocks from {cfg['two_hour_path'].name}")
    stocks = list(store)[:cfg["limit_stocks"]] if cfg["limit_stocks"] else list(store)
    print(f"Input: {cfg['data_path']}  |  {len(stocks)} stocks")
    first = store[stocks[0]]
    print("Columns:", ["Ticker", *first.columns]); print(first.head(3).to_string(index=False))
    all_rows, status, sanity_pool = [], [], []
    for n, stock in enumerate(stocks, 1):
        daily = store[stock].dropna(subset=["Open", "High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)
        per_tf = {}
        for timeframe, spec in cfg["timeframes"].items():
            if spec.get("two_hour"):
                bars = two_hour.get(stock, daily.iloc[:0]).dropna(subset=["Open", "High", "Low", "Close"]).reset_index(drop=True)
            else:
                bars = resample(daily, spec["period"])
            signals, states = replay(bars, cfg["confirm_bars"]) if len(bars) else ([], [])
            per_tf[timeframe] = (bars, states)
            all_rows += [{**row, "bars_in_stock": len(bars)}
                         for row in signal_rows(stock, timeframe, bars, signals, spec["horizons"], cfg)]
        d_bars, d_states = per_tf[cfg["daily_timeframe"]]
        if d_states:
            h_states = per_tf.get(cfg["two_hour_timeframe"], (None, []))[1]
            status.append({"stock": stock, "last_bar": d_bars["Date"].iloc[-1].date(), "bars": len(d_bars),
                           "status": status_text(d_states[-1], None),
                           "status_2h": status_text(h_states[-1], None) if h_states else STAGE_NONE})
        sanity_pool.append((stock, d_bars))
        per_tf_trend = per_tf[cfg["trend_timeframe"]]
        store[stock] = (per_tf_trend[0]["Date"].to_numpy(dtype="datetime64[ns]"), per_tf_trend[1])   # keep only what the trend split needs
        if n % 100 == 0 or n == len(stocks):
            print(f"  replayed {n} / {len(stocks)} stocks", flush=True)
    signals = pd.DataFrame(all_rows)
    daily_tf = cfg["daily_timeframe"]
    signals["weekly_trend"] = [
        weekly_trend_at(*store[s], pd.Timestamp(d)) if tf == daily_tf else None
        for s, tf, d in zip(signals.stock, signals.timeframe, signals.signal_date)]
    slots = max(len(spec["horizons"]) for spec in cfg["timeframes"].values())
    rows = long_form(signals, slots).dropna(subset=["bars"])
    rows["bars"] = rows["bars"].astype(int)
    tables = {
        "Summary": summarize(rows, ["timeframe", "group"], cfg["min_sample"]),
        "Comparison": compare(rows, cfg),
        "Weekly trend split": summarize(rows[rows.timeframe == daily_tf], ["weekly_trend", "group"], cfg["min_sample"]),
        "By stock": summarize(rows, ["stock", "timeframe", "group"], cfg["min_sample"]),
        "Current status": pd.DataFrame(status),
        "Signals": signals.drop(columns=["signal_idx"]),
    }
    save_app_file(tables, cfg)
    if not cfg.get("app_only"):
        report(signals, tables, cfg, dict(sanity_pool))
    return tables


RULES = ("Long only, on the app's swing labels. The sequence starts with an LH. Stage from the latest confirmed "
         "labels: LH, HH = HH1; LH, HH, HL = HL1; LH, HH, HL, HH = HH2 (a swing is confirmed {confirm} bars after its pivot). "
         "Entry 1 (assumed rule) = first 3-rule candle while the stage is HL1. Entry 2 = first 3-rule candle that closes below HH2's close while "
         "the stage is HH2; (a) only when an Entry 1 came first, (b) every Entry 2. Entry at the next bar's open. "
         "No exits: the return is measured N bars later, after a {cost}% round-trip cost. Win = return above 0. "
         "Replayed without hindsight on today's stock list. Groups under {min_sample} signals are not reliable. "
         "Past results do not predict future performance.")
APP_SIGNAL_COLUMNS = ["stock", "timeframe", "entry_type", "after_entry1", "hh2_date",
                      "signal_date", "entry_date", "entry_price", "weekly_trend"]   # kept small: the page loads it


def save_app_file(tables: dict[str, pd.DataFrame], cfg: dict) -> None:
    """data/hh_hl_entries.json for the web app: strict JSON (no NaN), dates as text."""
    def records(frame: pd.DataFrame) -> list[dict]:
        return json.loads(frame.to_json(orient="records"))

    signals = tables["Signals"]
    slots = max(len(spec["horizons"]) for spec in cfg["timeframes"].values())
    slim = signals[APP_SIGNAL_COLUMNS].copy()
    for column in [c for c in slim.columns if c.endswith("_date")]:
        slim[column] = slim[column].map(lambda value: str(value) if value is not None and value == value else None)
    for k in range(1, slots + 1):
        slim[f"ret{k}"] = signals.get(f"h{k}_ret_net_pct")
    status = tables["Current status"]
    payload = {
        "data_date": str(status["last_bar"].max()) if len(status) else None,
        "rules": RULES.format(confirm=cfg["confirm_bars"], cost=cfg["round_trip_cost_pct"], min_sample=cfg["min_sample"]),
        "min_sample": cfg["min_sample"],
        "horizons": {name: list(spec["horizons"]) for name, spec in cfg["timeframes"].items()},
        "status": {row.stock: row.status for row in status.itertuples() if row.status != STAGE_NONE},
        "status_2h": {row.stock: row.status_2h for row in status.itertuples() if row.status_2h != STAGE_NONE},
        "summary": records(tables["Summary"]), "comparison": records(tables["Comparison"]),
        "trend_split": records(tables["Weekly trend split"]),
        "signals": records(slim.sort_values("signal_date", ascending=False)),
    }
    cfg["app_file"].write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"Saved: {cfg['app_file']}")


def report(signals: pd.DataFrame, tables: dict[str, pd.DataFrame], cfg: dict, daily_bars: dict) -> None:
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    e2 = signals[signals.entry_type == ENTRY_2]
    print("\n=== ORDER CHECK (Entry 1 before Entry 2 in the same sequence) ===")
    for timeframe, part in e2.groupby("timeframe", sort=False):
        after = int((part.after_entry1 == True).sum())  # noqa: E712
        print(f"{timeframe}: {len(part)} Entry 2 signals, {after} after an Entry 1 of the same HH1-HL1, {len(part) - after} with no Entry 1 before")
    print("Entry 1 only fires before HH2 is confirmed, so it can never come after Entry 2 of the same sequence.")
    print("\n=== TIMING ===")
    print(f"Every swing is known {cfg['confirm_bars']} bars after its pivot: all {len(signals)} signals use the confirmation bar, not the swing bar.")
    fell = int((~signals.sequence_stands_today.astype(bool)).sum())
    print(f"{fell} of {len(signals)} signals sit on a sequence that today's (hindsight) labels no longer show; they are kept, as they were real at the time.")
    slots = max(len(spec["horizons"]) for spec in cfg["timeframes"].values())
    longest = signals[f"h{slots}_bars"]
    order = signals.sort_values(["stock", "timeframe", "signal_idx"])
    gap = order.groupby(["stock", "timeframe"])["signal_idx"].diff()
    print(f"Overlap: {int((gap <= longest.loc[order.index]).sum())} of {len(signals)} signals start inside the longest window of the previous signal of the same stock.")
    monthly = signals[signals.timeframe == "Monthly"]
    print(f"WARNING: monthly bars per stock: about {int(monthly.bars_in_stock.median()) if len(monthly) else 'n/a'} - far too few for swings; treat monthly results as not usable.")
    for name in ("Summary", "Comparison", "Weekly trend split"):
        print(f"\n=== {name.upper()} ===")
        print(tables[name].to_string(index=False))
    print("\n=== CURRENT STATUS (stocks per stage, daily) ===")
    print(tables["Current status"]["status"].value_counts().to_string())
    print("\n=== CURRENT STATUS (stocks per stage, 2-hour) ===")
    print(tables["Current status"]["status_2h"].value_counts().to_string())
    sanity(signals, daily_bars, cfg)
    save(tables, cfg)


def sanity(signals: pd.DataFrame, daily_bars: dict, cfg: dict) -> None:
    pool = signals[(signals.entry_type == ENTRY_2) & (signals.timeframe == cfg["daily_timeframe"])]
    picks = pool.sample(min(cfg["sanity_samples"], len(pool)), random_state=cfg["random_seed"])
    print("\n=== SANITY CHECK: random Entry 2 signals with their candles ===")
    for _, s in picks.iterrows():
        bars = daily_bars[s.stock]
        raw = swing_labels(bars["High"].to_numpy(dtype=float), bars["Low"].to_numpy(dtype=float))
        view = bars.assign(pivot=raw, rule3=three_rule_candle(bars)).iloc[
            max(0, s.signal_idx - cfg["sanity_bars_before"]):s.signal_idx + cfg["sanity_bars_after"] + 1].copy()
        view["note"] = ["<- signal (3-rule candle)" if i == s.signal_idx else "<- entry at open" if i == s.signal_idx + 1 else "" for i in view.index]
        view["Date"] = view["Date"].dt.date
        print(f"\n{s.stock}: HH1 {s.hh1_date}, HL1 {s.hl1_date}, HH2 {s.hh2_date} (confirmed {s.hh2_confirmed}), "
              f"signal {s.signal_date}, entry {s.entry_date} @ {s.entry_price}, after Entry 1: {s.after_entry1}")
        print(view[["Date", "Open", "High", "Low", "Close", "pivot", "rule3", "note"]].round(2).to_string(index=False))


def save(tables: dict[str, pd.DataFrame], cfg: dict) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    out = cfg["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    tables["Signals"].to_csv(out / cfg["signals_file"], index=False)
    params = pd.DataFrame([{"parameter": key, "value": str(value)} for key, value in cfg.items()])
    notes = pd.DataFrame({"note": [
        "Past results do not predict future performance.",
        f"Any row with fewer than {cfg['min_sample']} measured signals is marked NO in 'reliable': its win rate is not statistically reliable.",
        "Read the win rate together with the average return, average win and average loss.",
        "Entry 1 is an ASSUMED rule (first 3-rule candle after HL1 is confirmed): the data has no Entry 1 column.",
        "Signals of the same stock overlap and stocks move together, so the p-values are optimistic.",
        "Monthly: about 24 bars per stock, too few for swing labels. Do not rely on it.",
    ]})
    with pd.ExcelWriter(out / cfg["workbook_file"], engine="openpyxl") as writer:
        for name, frame in {**tables, "Parameters": params, "Notes": notes}.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            if len(frame.columns):
                sheet.auto_filter.ref = sheet.dimensions
            for i, column in enumerate(frame.columns, 1):
                cell = sheet.cell(row=1, column=i)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F3864")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                longest = max([len(str(column)), *(len(str(v)) for v in frame[column].head(200))])
                sheet.column_dimensions[get_column_letter(i)].width = min(60, max(10, longest + 2))
                if str(column).endswith("_pct") or str(column).endswith("_pts"):
                    for row in sheet.iter_rows(min_row=2, min_col=i, max_col=i):
                        row[0].number_format = "0.00"
            sheet.row_dimensions[1].height = 32
    print(f"\nSaved: {out / cfg['signals_file']}\nSaved: {out / cfg['workbook_file']}")


if __name__ == "__main__":
    # --app-only: just refresh data/hh_hl_entries.json (no report, CSV or workbook; used by the daily workflow)
    run({**CONFIG, "app_only": "--app-only" in sys.argv[1:]})
