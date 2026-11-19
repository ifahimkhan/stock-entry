"""Daily bars kept in data/daily_bars.csv, so a scan downloads only the days it does not have yet.

The first run (or a stock seen for the first time) downloads the full history (2 years). After that each
run asks Yahoo only for the last few days, replaces the days it already had (today's bar fetched during
market hours is completed later), appends the new ones and drops the same number of oldest days, so every
stock keeps the latest `keep` bars.

A split or bonus issue changes Yahoo's past prices: when an overlapping past day no longer matches the
stored close, that stock's full history is downloaded again.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]
FetchPeriod = Callable[[list[str], str], dict[str, pd.DataFrame]]

COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]
# Yahoo periods, shortest first, with the calendar days each covers.
PERIODS = (("5d", 5), ("1mo", 30), ("3mo", 90), ("6mo", 180), ("1y", 365), ("2y", 730))
MARGIN_DAYS = 4                 # fetch a few days more than the gap: holidays, a late or revised bar
SPLIT_TOLERANCE = 0.005         # a past close moving more than 0.5 %: split / bonus, so refetch fully
REPLACE_ATTEMPTS = 20
REPLACE_WAIT_SECONDS = 0.5


def period_for(gap_days: int, full_period: str) -> str:
    """Shortest Yahoo period that covers `gap_days` (plus a margin); the full period when nothing does."""
    needed = gap_days + MARGIN_DAYS
    return next((name for name, days in PERIODS if days >= needed), full_period)


def _split_suspected(old: pd.DataFrame, new: pd.DataFrame) -> bool:
    """True when a past day both frames have (not the latest stored day, which may be partial) disagrees."""
    past = old[old["Date"] < old["Date"].max()][["Date", "Close"]]
    both = past.merge(new[["Date", "Close"]], on="Date", suffixes=("_old", "_new"))
    if both.empty:
        return False
    change = (both["Close_new"] / both["Close_old"] - 1).abs()
    return bool((change > SPLIT_TOLERANCE).any())


def merge_bars(old: pd.DataFrame, new: pd.DataFrame, keep: int) -> pd.DataFrame:
    """Stored bars + fetched bars (fetched wins on the same day), latest `keep`, oldest first."""
    kept = old[~old["Date"].isin(set(new["Date"]))]
    return pd.concat([kept, new[COLUMNS]], ignore_index=True).sort_values("Date").tail(keep).reset_index(drop=True)


def read_store(path: Path) -> dict[str, pd.DataFrame]:
    if not path.is_file():
        return {}
    raw = pd.read_csv(path, dtype={"Ticker": str})
    raw["Date"] = pd.to_datetime(raw["Date"])
    return {symbol: group.drop(columns="Ticker").sort_values("Date").reset_index(drop=True)
            for symbol, group in raw.groupby("Ticker", sort=False)}


class DailyBarStore:
    """symbols -> {symbol: daily bars}, downloading only what data/daily_bars.csv does not have yet.

    `fetch_period(symbols, period)` downloads from Yahoo. Call `save()` after a scan to write the file."""

    def __init__(self, path: Path, fetch_period: FetchPeriod, full_period: str, keep: int,
                 today: Callable[[], date] = date.today) -> None:
        self._path, self._fetch_period = path, fetch_period
        self._full_period, self._keep, self._today = full_period, keep, today
        self._lock = threading.Lock()
        self._frames: dict[str, pd.DataFrame] | None = None
        self._dirty = False

    def _loaded(self) -> dict[str, pd.DataFrame]:
        if self._frames is None:
            try:
                self._frames = read_store(self._path)
                if self._frames:
                    logger.info("Daily bars for %d stocks loaded from %s", len(self._frames), self._path.name)
            except (OSError, ValueError, KeyError, pd.errors.ParserError) as exc:
                logger.error("Could not read %s (%s): downloading full history", self._path.name, exc)
                self._frames = {}
        return self._frames

    def _full(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        if not symbols:
            return {}
        logger.info("Full daily history (%s) for %d stocks", self._full_period, len(symbols))
        frames = self._fetch_period(symbols, self._full_period)
        return {symbol: frame[COLUMNS].tail(self._keep).reset_index(drop=True) for symbol, frame in frames.items()}

    def _recent(self, stored: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], list[str]]:
        """Merged frames of the stored stocks, and the stocks that need a full download (split / bonus).
        Stocks are grouped by how many days they miss, so one stale stock does not make all of them long."""
        groups: dict[str, list[str]] = {}
        for symbol, frame in stored.items():
            gap = (self._today() - frame["Date"].max().date()).days
            groups.setdefault(period_for(gap, self._full_period), []).append(symbol)
        merged: dict[str, pd.DataFrame] = {}
        refetch: list[str] = []
        for period, symbols in groups.items():
            fetched = self._fetch_period(symbols, period)
            for symbol in symbols:
                old, new = stored[symbol], fetched.get(symbol)
                if new is None or new.empty:
                    merged[symbol] = old          # nothing came back: keep what we have
                elif _split_suspected(old, new):
                    refetch.append(symbol)
                else:
                    merged[symbol] = merge_bars(old, new, self._keep)
        if refetch:
            logger.info("Past prices changed (split / bonus?) for %s: downloading their full history", ", ".join(refetch))
        return merged, refetch

    def __call__(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        with self._lock:
            frames = self._loaded()
            stored = {symbol: frames[symbol] for symbol in symbols if symbol in frames}
            missing = [symbol for symbol in symbols if symbol not in frames]
        merged, refetch = self._recent(stored)
        merged = {**merged, **self._full(missing + refetch)}
        with self._lock:
            self._frames = {**self._loaded(), **merged}
            self._dirty = self._dirty or bool(merged)
        return {symbol: frame.copy() for symbol, frame in merged.items()}

    def save(self) -> None:
        """Write the stored bars (only when something changed). Atomic: a crash never leaves half a file."""
        with self._lock:
            if not self._dirty or not self._frames:
                return
            frames, self._dirty = dict(self._frames), False
        table = pd.concat([frame.assign(Ticker=symbol) for symbol, frame in frames.items()], ignore_index=True)
        table = table[["Ticker", *COLUMNS]]
        table["Date"] = pd.to_datetime(table["Date"]).dt.strftime("%Y-%m-%d")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        table.to_csv(temp, index=False, float_format="%.4f")
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temp, self._path)
                break
            except PermissionError:   # Windows: another program has the file open
                if attempt == REPLACE_ATTEMPTS - 1:
                    temp.unlink(missing_ok=True)
                    logger.error("%s is open in another program: daily bars not saved", self._path.name)
                    return
                time.sleep(REPLACE_WAIT_SECONDS)
        logger.info("Saved daily bars of %d stocks to %s", len(frames), self._path.name)
