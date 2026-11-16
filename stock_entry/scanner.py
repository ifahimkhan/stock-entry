"""Scan the whole ticker universe in batches, in a background thread, and save the result."""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, TypeVar

import pandas as pd

from stock_entry.analysis import analyze, build_row
from stock_entry.clock import now_ist
from stock_entry.intraday import analyze_swings
from stock_entry.market_data import MarketDataError
from stock_entry.store import ScanStore
from stock_entry.tickers import Ticker

logger = logging.getLogger(__name__)

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]
OnProgress = Callable[[int, int], None]
Enrich = Callable[[list[Ticker], OnProgress], list[Ticker]]
T = TypeVar("T")
DEFAULT_MAX_FAILED_SHARE = 0.25  # above this a scan counts as failed and the previous one is kept


@dataclass(frozen=True)
class ScanProgress:
    running: bool = False
    done: int = 0
    total: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    stage: str = ""   # "fundamentals" (market cap / debt) then "prices"


def _now() -> str:
    return now_ist().isoformat(timespec="seconds")


def _batches(items: list[T], size: int) -> list[list[T]]:
    return [items[start:start + size] for start in range(0, len(items), size)]


def _fetch_safely(symbols: list[str], fetch: Fetch) -> dict[str, pd.DataFrame]:
    try:
        return fetch(symbols)
    except Exception as exc:  # one bad batch (network, odd Yahoo reply) must not abort the whole scan
        logger.warning("Batch starting at %s failed: %s", symbols[0], exc)
        return {}


def _fetch_batch(symbols: list[str], fetch: Fetch, refetch: Fetch) -> dict[str, pd.DataFrame]:
    """Fetch a batch. Stocks missing from a partial reply are asked for once more, with `refetch`, in
    half-size batches: a throttled Yahoo tends to drop part of a large request. A batch that came back
    entirely empty is not split: Yahoo is down or refusing, and more requests would only make it worse."""
    frames = _fetch_safely(symbols, fetch)
    missing = [symbol for symbol in symbols if frames.get(symbol) is None or frames[symbol].empty]
    if len(symbols) > 1 and missing and len(missing) < len(symbols):
        for chunk in _batches(missing, max(1, len(symbols) // 2)):
            frames = {**frames, **_fetch_safely(chunk, refetch)}
    return frames


def _swings_safely(symbol: str, frame: pd.DataFrame | None) -> pd.DataFrame | None:
    """Analysed 2-hour bars, or None. A swing failure never drops the stock from the scan."""
    if frame is None or frame.empty:
        return None
    try:
        return analyze_swings(frame)
    except Exception:  # any swing bug must leave the daily scan intact
        logger.exception("Swing signals failed for %s", symbol)
        return None


def _scan_batch(batch: list[Ticker], fetch: Fetch, refetch: Fetch,
                swing_fetch: Fetch | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows for the stocks that produced signals, and the symbols that did not."""
    symbols = [ticker.symbol for ticker in batch]
    frames = _fetch_batch(symbols, fetch, refetch)
    swing_frames = _fetch_safely(symbols, swing_fetch) if swing_fetch is not None else {}
    rows, failed = [], []
    for ticker in batch:
        frame = frames.get(ticker.symbol)
        if frame is None or frame.empty:
            failed.append(ticker.symbol)
            continue
        try:
            swings = _swings_safely(ticker.symbol, swing_frames.get(ticker.symbol))
            rows.append(build_row(ticker, analyze(frame), swings))
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Signals failed for %s: %s", ticker.symbol, exc)
            failed.append(ticker.symbol)
    return rows, failed


def run_scan(tickers: list[Ticker], fetch: Fetch, batch_size: int, on_progress: OnProgress | None = None,
             max_failed_share: float = DEFAULT_MAX_FAILED_SHARE, refetch: Fetch | None = None,
             swing_fetch: Fetch | None = None) -> dict[str, Any]:
    """Scan every ticker and return {scanned_at, data_date, total, rows, failed}.

    `refetch` asks again for stocks missing from a batch (default: `fetch`). Give it a fetch without
    retries, so a delisted stock costs one extra request instead of a full retry-with-wait cycle.
    `swing_fetch` (symbols -> {symbol: 2-hour bars}) adds the TradingView.ipynb swing signals; without it
    the rows carry empty swing fields.
    Raises MarketDataError when more than `max_failed_share` of the tickers return no data.
    """
    refetch = refetch or fetch
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    rows: list[dict[str, Any]] = []
    failed: list[str] = []
    done = 0
    for batch in _batches(tickers, batch_size):
        batch_rows, batch_failed = _scan_batch(batch, fetch, refetch, swing_fetch)
        rows = [*rows, *batch_rows]
        failed = [*failed, *batch_failed]
        done += len(batch)
        if on_progress is not None:
            on_progress(done, len(tickers))
    if tickers and not rows:
        raise MarketDataError("No price data came back for any stock. Check the internet connection and try again.")
    if len(failed) > max_failed_share * len(tickers):
        # Raising keeps the previous good scan on disk instead of replacing it with a partial one.
        raise MarketDataError(
            f"Only {len(rows)} of {len(tickers)} stocks returned data (Yahoo Finance may be limiting requests). "
            "The previous scan was kept. Try again in a few minutes."
        )
    data_date = max((row["date"] for row in rows), default=None)  # ISO dates sort correctly as text
    return {"scanned_at": _now(), "data_date": data_date, "total": len(tickers), "rows": rows, "failed": failed}


class Scanner:
    """Runs one scan at a time in a background thread and exposes its progress."""

    def __init__(self, load_universe: Callable[[], list[Ticker]], fetch: Fetch,
                 store: ScanStore, batch_size: int, max_failed_share: float = DEFAULT_MAX_FAILED_SHARE,
                 enrich: Enrich | None = None, refetch: Fetch | None = None,
                 swing_fetch: Fetch | None = None) -> None:
        self._enrich = enrich
        self._swing_fetch = swing_fetch
        self._load_universe = load_universe
        self._fetch = fetch
        self._refetch = refetch or fetch
        self._store = store
        self._batch_size = batch_size
        self._max_failed_share = max_failed_share
        self._lock = threading.Lock()
        self._progress = ScanProgress()

    def progress(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._progress)

    def _claim(self) -> bool:
        """Mark a scan as running. False when one already is."""
        with self._lock:
            if self._progress.running:
                return False
            self._progress = ScanProgress(running=True, started_at=_now())
            return True

    def start(self) -> bool:
        """Start a scan in a background thread. Returns False when one is already running."""
        if not self._claim():
            return False
        threading.Thread(target=self._run, name="stock-scan", daemon=True).start()
        return True

    def run_now(self) -> bool:
        """Scan in the calling thread (the daily job). True when the scan was saved."""
        if not self._claim():
            return False
        self._run()
        return self.progress()["error"] is None

    def _update(self, **changes: Any) -> None:
        with self._lock:
            self._progress = replace(self._progress, **changes)

    def _with_fundamentals(self, tickers: list[Ticker]) -> list[Ticker]:
        """Add market cap / debt. If that fails the scan still runs, just without those filters."""
        if self._enrich is None:
            return tickers
        self._update(stage="fundamentals", done=0, total=len(tickers))
        try:
            return self._enrich(tickers, lambda done, total: self._update(done=done, total=total))
        except Exception:
            logger.exception("Could not load market cap / debt; scanning without them")
            return tickers

    def _run(self) -> None:
        try:
            tickers = self._with_fundamentals(self._load_universe())
            self._update(stage="prices", done=0, total=len(tickers))
            scan = run_scan(tickers, self._fetch, self._batch_size,
                            on_progress=lambda done, total: self._update(done=done, total=total),
                            max_failed_share=self._max_failed_share, refetch=self._refetch,
                            swing_fetch=self._swing_fetch)
            self._store.save(scan)
            self._update(running=False, finished_at=_now())
            logger.info("Scan finished: %d stocks, %d without data", len(scan["rows"]), len(scan["failed"]))
        except (MarketDataError, FileNotFoundError, ValueError, OSError) as exc:
            logger.error("Scan failed: %s", exc)
            self._update(running=False, finished_at=_now(), error=str(exc))
        except Exception:  # keep the thread from dying silently with `running` stuck on True
            logger.exception("Scan crashed")
            self._update(running=False, finished_at=_now(), error="The scan stopped unexpectedly. See the server log.")
