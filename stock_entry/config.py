"""Application settings. Override host/port with the STOCK_ENTRY_HOST / STOCK_ENTRY_PORT env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8765  # not 5000: Windows reserves port 5000 for a system service on this machine


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"          # local only: do not expose this server to the network
    port: int = DEFAULT_PORT
    # Stock list (ticker, name, market cap in crore, debt/equity, sector), used when present.
    universe_path: Path = PROJECT_ROOT / "Final_data_greater_1000_debt_0.02.csv"
    tickers_path: Path = PROJECT_ROOT / "tickers.csv"   # fallback list; also supplies company names
    scan_path: Path = PROJECT_ROOT / "data" / "scan_latest.json"
    web_dir: Path = PROJECT_ROOT / "web"
    fundamentals_path: Path = PROJECT_ROOT / "data" / "fundamentals.json"
    fundamentals_max_age_days: int = 7   # market cap / debt are re-fetched from Yahoo after this
    fundamentals_workers: int = 6        # parallel Yahoo requests while fetching them
    history_period: str = "2y"       # price history per stock; swing structure (50) and EMA 200 need long history
    interval: str = "1d"
    # Swing scan (TradingView.ipynb): TradingView 2-hour bars (TV_USERNAME / TV_PASSWORD), latest 500.
    swing_bars: int = 500            # same as the notebook's n_bars
    swing_detail_points: int = 30    # swing points listed in the single-stock view
    batch_size: int = 40             # tickers per yfinance request
    max_failed_share: float = 0.25   # if more stocks than this return no data, keep the previous scan
    default_suffix: str = ".NS"      # appended to symbols typed without an exchange suffix
    excluded_sectors: tuple[str, ...] = ()  # every stock in the list is scanned, Financial Services included
    history_cache_seconds: int = 600
    detail_rows: int = 130           # bars returned for the single-stock view
    # Streamlit (public) only: how soon a visitor may start another scan. Prices are daily, so a
    # fresh scan rarely changes, and back-to-back scans get the server rate-limited by Yahoo.
    scan_cooldown_seconds: int = 600
    failed_scan_cooldown_seconds: int = 60


def load_settings() -> Settings:
    port_text = os.environ.get("STOCK_ENTRY_PORT", str(DEFAULT_PORT))
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError(f"STOCK_ENTRY_PORT must be a port number between 1 and 65535, got {port_text!r}")
    return Settings(host=os.environ.get("STOCK_ENTRY_HOST", "127.0.0.1"), port=int(port_text))
