"""Day-wise update of TBC_all_stocks_bars.csv: fetch only the latest 2-hour bars and merge them in.

    python -m stock_entry.update_two_hour_csv            # latest 12 bars (about 3 sessions) per stock
    python -m stock_entry.update_two_hour_csv --bars 4   # today's bars only

Instead of re-downloading 500 bars per stock, this asks TradingView for the last few bars of every stock
already in the CSV, replaces bars with the same time (a bar fetched during market hours is completed by the
later fetch), appends the new ones and keeps the latest 500 per stock. The notebook's signal columns
(status, status_clean, angles, Final, 3LL_HH, 4LL_HH, LH_breakout) are recomputed with the app's rules,
TBC_all_stocks_summary.csv is rebuilt from the result, and the saved scan is updated.

Uses TV_USERNAME / TV_PASSWORD when set, otherwise TradingView without login. Then commit and push the two
CSV files: the "Apply 2-hour CSV" workflow updates the live site.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stock_entry import apply_two_hour_csv
from stock_entry.config import Settings
from stock_entry.swings import NONE, swing_columns
from stock_entry.tradingview import TradingViewFeed, credentials, tv_symbol

logger = logging.getLogger(__name__)

DEFAULT_FETCH_BARS = 12        # about 3 sessions: also fills a day that was missed
MIN_FETCHED_SHARE = 0.5        # fewer stocks than this coming back: leave the CSV untouched
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]
BAR_COLUMNS = ["Ticker", "datetime", "symbol", *PRICE_COLUMNS, "status", "status_clean", "ang_LL_LL", "ang_LL_HL",
               "Final", "3LL_HH", "4LL_HH", "LH_breakout", "LH_breakout_level"]
INFO_COLUMNS = ["Stock Name", "Sector", "MarketCap_Cr"]   # summary columns kept from the previous summary
BEFORE_ENTRY = "Before Entry"

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]


@dataclass(frozen=True)
class UpdateResult:
    stocks: int
    fetched: int
    added: int
    replaced: int
    missing: tuple[str, ...]
    last_bar: str | None


def app_symbol(ticker: str) -> str:
    return f"{ticker}.NS"


def fetched_rows(ticker: str, bars: pd.DataFrame) -> pd.DataFrame:
    """TradingView bars (Date, Open..Volume) -> CSV price rows of one ticker."""
    name, exchange = tv_symbol(app_symbol(ticker))
    return pd.DataFrame({
        "Ticker": ticker, "datetime": pd.to_datetime(bars["Date"]).dt.strftime(TIME_FORMAT),
        "symbol": f"{exchange}:{name}", "open": bars["Open"].to_numpy(dtype=float),
        "high": bars["High"].to_numpy(dtype=float), "low": bars["Low"].to_numpy(dtype=float),
        "close": bars["Close"].to_numpy(dtype=float), "volume": bars["Volume"].to_numpy(dtype=float),
    })


def notebook_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """One ticker's price rows (oldest first) with the notebook's signal columns recomputed."""
    bars = prices.rename(columns={"high": "High", "low": "Low", "close": "Close"}).reset_index(drop=True)
    swings = swing_columns(bars)
    blank = lambda values: pd.Series(values, dtype=object).replace(NONE, np.nan).to_numpy()  # noqa: E731
    return prices.reset_index(drop=True).assign(
        status=blank(swings["swing_label"]), status_clean=blank(swings["swing_clean"]),
        ang_LL_LL=swings["ang_ll_ll"].to_numpy(), ang_LL_HL=swings["ang_ll_hl"].to_numpy(),
        Final=np.where(swings["before_entry"].to_numpy(dtype=bool), BEFORE_ENTRY, None),
        **{"3LL_HH": swings["ll3_hh"].to_numpy(dtype=bool), "4LL_HH": swings["ll4_hh"].to_numpy(dtype=bool),
           "LH_breakout": swings["lh_breakout"].to_numpy(dtype=bool)},
        LH_breakout_level=swings["lh_breakout_level"].to_numpy(dtype=float),
    )[BAR_COLUMNS]


def merge_ticker(old: pd.DataFrame, new: pd.DataFrame | None, keep: int) -> tuple[pd.DataFrame, int, int]:
    """Old rows + fetched rows of one ticker (fetched wins on the same time), latest `keep`, signals redone."""
    prices = old[["Ticker", "datetime", "symbol", *PRICE_COLUMNS]]
    added = replaced = 0
    if new is not None and not new.empty:
        known = set(prices["datetime"])
        replaced = int(new["datetime"].isin(known).sum())
        added = len(new) - replaced
        prices = pd.concat([prices[~prices["datetime"].isin(set(new["datetime"]))], new], ignore_index=True)
    prices = prices.sort_values("datetime").tail(keep)
    return notebook_signals(prices), added, replaced


def merge_bars(bars: pd.DataFrame, fetched: dict[str, pd.DataFrame], keep: int) -> tuple[pd.DataFrame, int, int]:
    """The whole CSV with the fetched bars merged in (a new frame; `bars` is unchanged)."""
    parts, added, replaced = [], 0, 0
    for ticker, old in bars.groupby("Ticker", sort=False):
        new = fetched.get(ticker)
        merged, ticker_added, ticker_replaced = merge_ticker(old, None if new is None else fetched_rows(ticker, new), keep)
        parts.append(merged)
        added, replaced = added + ticker_added, replaced + ticker_replaced
    return pd.concat(parts, ignore_index=True), added, replaced


def _last_true(frame: pd.DataFrame, column: str) -> tuple[Any, Any]:
    hits = np.flatnonzero(frame[column].to_numpy(dtype=bool))
    if not len(hits):
        return None, None
    return frame["datetime"].iloc[hits[-1]], len(frame) - 1 - int(hits[-1])


def summary_row(ticker: str, frame: pd.DataFrame) -> dict[str, Any]:
    """One summary line, same columns as the notebook's TBC_all_stocks_summary.csv."""
    labelled = frame[frame["status_clean"].notna()]
    before_at, before_ago = _last_true(frame.assign(_before=frame["Final"].eq(BEFORE_ENTRY)), "_before")
    ll3_at, ll3_ago = _last_true(frame, "3LL_HH")
    ll4_at, ll4_ago = _last_true(frame, "4LL_HH")
    lhb_at, lhb_ago = _last_true(frame, "LH_breakout")
    level = frame.loc[frame["LH_breakout"].to_numpy(dtype=bool), "LH_breakout_level"]
    return {
        "Ticker": ticker, "bars": len(frame), "last_bar": frame["datetime"].iloc[-1],
        "close": float(frame["close"].iloc[-1]),
        "last_label": labelled["status_clean"].iloc[-1] if len(labelled) else None,
        "last_label_at": labelled["datetime"].iloc[-1] if len(labelled) else None,
        "before_entry_at": before_at, "before_entry_bars_ago": before_ago,
        "3LL_HH_at": ll3_at, "3LL_HH_bars_ago": ll3_ago, "4LL_HH_at": ll4_at, "4LL_HH_bars_ago": ll4_ago,
        "LH_breakout_at": lhb_at, "LH_breakout_bars_ago": lhb_ago,
        "LH_breakout_level": float(level.iloc[-1]) if len(level) else None,
    }


def build_summary(bars: pd.DataFrame, previous: pd.DataFrame | None) -> pd.DataFrame:
    """Summary of the merged bars; name / sector / market cap carried over from the previous summary."""
    summary = pd.DataFrame([summary_row(ticker, frame) for ticker, frame in bars.groupby("Ticker", sort=False)])
    if previous is not None and set(INFO_COLUMNS) <= set(previous.columns):
        info = previous.drop_duplicates("Ticker").set_index("Ticker")[INFO_COLUMNS]
        summary = summary.join(info, on="Ticker")
    else:
        summary = summary.assign(**{column: None for column in INFO_COLUMNS})
    order = ["Ticker", *INFO_COLUMNS] + [c for c in summary.columns if c not in ("Ticker", *INFO_COLUMNS)]
    return summary[order]


REPLACE_ATTEMPTS = 20
REPLACE_WAIT_SECONDS = 0.5


def _write_atomic(frame: pd.DataFrame, path: Path, wait: Callable[[float], None] = time.sleep) -> None:
    """Write next to the file, then swap. On Windows the swap fails while another program has the file open
    (Excel, an editor, the app reading it), so retry for a few seconds, then explain."""
    temp = path.with_suffix(".tmp")
    frame.to_csv(temp, index=False)
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                temp.unlink(missing_ok=True)
                raise RuntimeError(f"{path.name} is open in another program (Excel, PyCharm, ...). "
                                   "Close it and run the update again; the file was not changed.") from None
            wait(REPLACE_WAIT_SECONDS)


def update(bars_path: Path, summary_path: Path, fetch: Fetch, fetch_bars: int, keep: int) -> UpdateResult:
    """Fetch, merge and save both CSVs. Raises RuntimeError (files untouched) when too few stocks came back."""
    bars = pd.read_csv(bars_path, dtype={"Ticker": str, "datetime": str})
    tickers = list(dict.fromkeys(bars["Ticker"]))
    logger.info("Fetching the latest %d 2-hour bars of %d stocks", fetch_bars, len(tickers))
    by_symbol = fetch([app_symbol(ticker) for ticker in tickers])
    fetched = {ticker: by_symbol[app_symbol(ticker)] for ticker in tickers if app_symbol(ticker) in by_symbol}
    if len(fetched) < MIN_FETCHED_SHARE * len(tickers):
        raise RuntimeError(f"Only {len(fetched)} of {len(tickers)} stocks came back from TradingView; "
                           "the CSV was not changed. Try again later.")
    merged, added, replaced = merge_bars(bars, fetched, keep)
    previous = pd.read_csv(summary_path) if summary_path.is_file() else None
    _write_atomic(merged, bars_path)
    _write_atomic(build_summary(merged, previous), summary_path)
    missing = tuple(ticker for ticker in tickers if ticker not in fetched)
    return UpdateResult(stocks=len(tickers), fetched=len(fetched), added=added, replaced=replaced,
                        missing=missing, last_bar=str(merged["datetime"].max()) if len(merged) else None)


def tradingview_fetch(fetch_bars: int) -> Fetch:
    login = credentials()
    username, password = login if login else (None, None)
    return TradingViewFeed(username, password, n_bars=fetch_bars)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append the latest 2-hour bars to TBC_all_stocks_bars.csv")
    parser.add_argument("--bars", type=int, default=DEFAULT_FETCH_BARS,
                        help=f"2-hour bars to fetch per stock (default {DEFAULT_FETCH_BARS}, about 3 sessions; 4 = one session)")
    parser.add_argument("--no-scan", action="store_true", help="do not update data/scan_latest.json")
    args = parser.parse_args(argv)
    if not 1 <= args.bars <= 500:
        parser.error("--bars must be between 1 and 500")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    bars_path = settings.two_hour_csv_path
    if not bars_path.is_file():
        logger.error("%s not found: run the notebook loop once first", bars_path)
        return 1
    try:
        result = update(bars_path, bars_path.with_name("TBC_all_stocks_summary.csv"),
                        tradingview_fetch(args.bars), args.bars, settings.swing_bars)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Updated %s: %d of %d stocks fetched, %d new bars, %d bars refreshed, latest bar %s",
                bars_path.name, result.fetched, result.stocks, result.added, result.replaced, result.last_bar)
    if result.missing:
        logger.warning("Not fetched (kept as they were): %s", ", ".join(result.missing))
    if not args.no_scan:
        apply_two_hour_csv.run(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
