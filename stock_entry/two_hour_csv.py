"""2-hour bars from TBC_all_stocks_bars.csv, the file the TradingView.ipynb loop writes.

The app reads 2-hour bars from this file first, for the scan and the stock detail, so the web app shows
the same bars as the notebook. Stocks the file does not have (the notebook skips financial stocks) still
come from TradingView. Re-running the notebook and committing the file updates the live site.

The CSV is the notebook's output: Ticker, datetime, open, high, low, close, volume and the notebook's own
signal columns. Only the prices are used; the app computes the signals itself with the same rules.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

CSV_SOURCE = "tbc-csv"   # swing_source of rows whose 2-hour bars came from the CSV
PRICE_COLUMNS = {"datetime": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
REQUIRED_COLUMNS = ("Ticker", *PRICE_COLUMNS)
DEFAULT_SUFFIX = ".NS"

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]


def _symbol(ticker: str) -> str:
    """CSV ticker -> app symbol: "BAJAJ-AUTO" -> "BAJAJ-AUTO.NS" (a suffix already given is kept)."""
    name = str(ticker).strip().upper()
    return name if "." in name else name + DEFAULT_SUFFIX


def read_bars(path: Path) -> dict[str, pd.DataFrame]:
    """{symbol: Date / Open / High / Low / Close / Volume / Source bars, oldest first} from the CSV."""
    raw = pd.read_csv(path, usecols=lambda column: column in REQUIRED_COLUMNS)
    missing = [column for column in REQUIRED_COLUMNS if column not in raw.columns]
    if missing:
        raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
    bars = raw.rename(columns=PRICE_COLUMNS)
    bars["Date"] = pd.to_datetime(bars["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna(subset=["Ticker", "Date", "High", "Low", "Close"])
    frames: dict[str, pd.DataFrame] = {}
    for ticker, group in bars.groupby("Ticker", sort=False):
        frame = (group.drop(columns="Ticker").drop_duplicates("Date", keep="last")
                 .sort_values("Date").reset_index(drop=True).assign(Source=CSV_SOURCE))
        frames[_symbol(ticker)] = frame
    return frames


class CsvTwoHourBars:
    """symbols -> {symbol: 2-hour bars} from the CSV. Re-reads the file when it changes on disk."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._stamp: tuple[float, int] | None = None
        self._frames: dict[str, pd.DataFrame] = {}

    def _current(self) -> dict[str, pd.DataFrame]:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return {}
        stamp = (stat.st_mtime, stat.st_size)
        with self._lock:
            if stamp != self._stamp:
                try:
                    self._frames = read_bars(self._path)
                    logger.info("2-hour bars for %d stocks loaded from %s", len(self._frames), self._path.name)
                except (OSError, ValueError, pd.errors.ParserError) as exc:
                    logger.error("Could not read %s: %s", self._path.name, exc)
                    self._frames = {}
                self._stamp = stamp
            return self._frames

    def __call__(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        frames = self._current()
        return {symbol: frames[symbol].copy() for symbol in symbols if symbol in frames}


class CsvFirstTwoHourBars:
    """2-hour bars from the CSV; the stocks it does not have from `fallback` (TradingView), when given."""

    def __init__(self, csv: CsvTwoHourBars, fallback: Fetch | None) -> None:
        self._csv, self._fallback = csv, fallback

    def __call__(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        found = self._csv(symbols)
        rest = [symbol for symbol in symbols if symbol not in found]
        if rest and self._fallback is not None:
            found = {**found, **self._fallback(rest)}
        return found
