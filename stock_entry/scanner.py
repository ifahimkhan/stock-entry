"""Scan the whole ticker universe in batches, in a background thread, and save the result."""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from stock_entry.analysis import analyze, build_row
from stock_entry.market_data import MarketDataError
from stock_entry.store import ScanStore
from stock_entry.tickers import Ticker

logger = logging.getLogger(__name__)

Fetch = Callable[[list[str]], dict[str, pd.DataFrame]]
OnProgress = Callable[[int, int], None]
Enrich = Callable[[list[Ticker], OnProgress], list[Ticker]]
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
    return datetime.now().isoformat(timespec="seconds")


def _batches(items: list[Ticker], size: int) -> list[list[Ticker]]:
    return [items[start:start + size] for start in range(0, len(items), size)]


def _scan_batch(batch: list[Ticker], fetch: Fetch) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows for the stocks that produced signals, and the symbols that did not."""
    symbols = [ticker.symbol for ticker in batch]
    try:
        frames = fetch(symbols)
    except Exception as exc:  # one bad batch (network, odd Yahoo reply) must not abort the whole scan
        logger.warning("Batch starting at %s failed: %s", symbols[0], exc)
        return [], symbols

    rows, failed = [], []
    for ticker in batch:
        frame = frames.get(ticker.symbol)
        if frame is None or frame.empty:
            failed.append(ticker.symbol)
            continue
        try:
            rows.append(build_row(ticker, analyze(frame)))
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Signals failed for %s: %s", ticker.symbol, exc)
            failed.append(ticker.symbol)
    return rows, failed


def run_scan(tickers: list[Ticker], fetch: Fetch, batch_size: int, on_progress: OnProgress | None = None,
             max_failed_share: float = DEFAULT_MAX_FAILED_SHARE) -> dict[str, Any]:
    """Scan every ticker and return {scanned_at, data_date, total, rows, failed}.

    Raises MarketDataError when more than `max_failed_share` of the tickers return no data.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    rows: list[dict[str, Any]] = []
    failed: list[str] = []
    done = 0
    for batch in _batches(tickers, batch_size):
        batch_rows, batch_failed = _scan_batch(batch, fetch)
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
                 enrich: Enrich | None = None) -> None:
        self._enrich = enrich
        self._load_universe = load_universe
        self._fetch = fetch
        self._store = store
        self._batch_size = batch_size
        self._max_failed_share = max_failed_share
        self._lock = threading.Lock()
        self._progress = ScanProgress()

    def progress(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._progress)

    def start(self) -> bool:
        """Start a scan. Returns False when one is already running."""
        with self._lock:
            if self._progress.running:
                return False
            self._progress = ScanProgress(running=True, started_at=_now())
        threading.Thread(target=self._run, name="stock-scan", daemon=True).start()
        return True

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
                            max_failed_share=self._max_failed_share)
            self._store.save(scan)
            self._update(running=False, finished_at=_now())
            logger.info("Scan finished: %d stocks, %d without data", len(scan["rows"]), len(scan["failed"]))
        except (MarketDataError, FileNotFoundError, ValueError, OSError) as exc:
            logger.error("Scan failed: %s", exc)
            self._update(running=False, finished_at=_now(), error=str(exc))
        except Exception:  # keep the thread from dying silently with `running` stuck on True
            logger.exception("Scan crashed")
            self._update(running=False, finished_at=_now(), error="The scan stopped unexpectedly. See the server log.")
