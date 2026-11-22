"""Ticker universe: read tickers.csv and validate/normalize symbols."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}(\.[A-Z]{1,4})?$")
SYMBOL_COLUMNS = ("ticker", "symbol")
NAME_COLUMNS = ("name", "stock name", "company name")
SECTOR_COLUMNS = ("sector", "industry")
MARKET_CAP_CRORE_COLUMNS = ("marketcap_cr", "market_cap_cr")
MARKET_CAP_RUPEE_COLUMNS = ("market cap", "marketcap")  # raw rupees, as saved by the notebook from Yahoo
RUPEES_PER_CRORE = 1e7
DEBT_COLUMNS = ("debt_to_equity", "debt to equity")


class InvalidSymbolError(ValueError):
    """Raised when text is not a usable stock symbol."""


@dataclass(frozen=True)
class Ticker:
    symbol: str
    name: str = ""
    sector: str = ""
    market_cap_cr: float | None = None    # market cap in crore; optional column in tickers.csv
    debt_to_equity: float | None = None   # ratio, e.g. 0.25; optional column in tickers.csv
    book_value: float | None = None       # book value per share in rupees; from Yahoo, for the P/B ratio


def normalize_symbol(text: str, default_suffix: str = ".NS") -> str:
    """'tcs' -> 'TCS.NS'. Symbols that already carry an exchange suffix are kept as typed."""
    symbol = str(text).strip().upper()
    if not SYMBOL_PATTERN.match(symbol):
        raise InvalidSymbolError(
            f"'{str(text).strip()[:30]}' is not a valid stock symbol. Use letters, digits, & or -, e.g. TCS or TCS.NS"
        )
    return symbol if "." in symbol else symbol + default_suffix


def _find_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    by_lower = {str(column).strip().lower(): column for column in frame.columns}
    return next((by_lower[name] for name in candidates if name in by_lower), None)


def _text(row: pd.Series, column: str | None) -> str:
    if column is None or pd.isna(row[column]):
        return ""
    return str(row[column]).strip()


def _positive_number(row: pd.Series, column: str | None) -> float | None:
    """A finite number >= 0 from the CSV, or None when the cell is empty or not a number."""
    if column is None:
        return None
    value = pd.to_numeric(row[column], errors="coerce")
    if pd.isna(value) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def load_tickers(path: Path, default_suffix: str = ".NS", excluded_sectors: tuple[str, ...] = ()) -> list[Ticker]:
    """Read the ticker list. Needs a Ticker/Symbol column.

    Optional columns: Name, Sector, MarketCap_Cr (crore) and Debt_to_Equity (ratio) for the screens.
    """
    if not path.exists():
        raise FileNotFoundError(f"Ticker list not found: {path}. Create it with a 'Ticker' column.")
    frame = pd.read_csv(path)
    symbol_column = _find_column(frame, SYMBOL_COLUMNS)
    if symbol_column is None:
        raise ValueError(f"{path.name} needs a 'Ticker' or 'Symbol' column")
    name_column = _find_column(frame, NAME_COLUMNS)
    sector_column = _find_column(frame, SECTOR_COLUMNS)
    cap_column = _find_column(frame, MARKET_CAP_CRORE_COLUMNS)
    rupee_cap_column = None if cap_column else _find_column(frame, MARKET_CAP_RUPEE_COLUMNS)
    debt_column = _find_column(frame, DEBT_COLUMNS)
    excluded = {sector.lower() for sector in excluded_sectors}

    tickers: dict[str, Ticker] = {}
    for _, row in frame.dropna(subset=[symbol_column]).iterrows():
        try:
            symbol = normalize_symbol(row[symbol_column], default_suffix)
        except InvalidSymbolError:
            continue  # skip junk rows in the CSV; valid rows still load
        sector = _text(row, sector_column)
        if sector.lower() in excluded or symbol in tickers:
            continue
        rupee_cap = _positive_number(row, rupee_cap_column)
        market_cap_cr = _positive_number(row, cap_column) if cap_column else (
            None if rupee_cap is None else round(rupee_cap / RUPEES_PER_CRORE))
        tickers[symbol] = Ticker(
            symbol=symbol, name=_text(row, name_column), sector=sector,
            market_cap_cr=market_cap_cr, debt_to_equity=_positive_number(row, debt_column),
        )
    if not tickers:
        raise ValueError(f"{path.name} has no usable ticker symbols")
    return list(tickers.values())


def load_universe(paths: tuple[Path, ...], default_suffix: str = ".NS",
                  excluded_sectors: tuple[str, ...] = ()) -> list[Ticker]:
    """Stock list from the first file that exists; later files only supply missing company names."""
    existing = [path for path in paths if path.exists()]
    if not existing:
        raise FileNotFoundError(
            f"No stock list found. Expected one of: {', '.join(path.name for path in paths)} (needs a 'Ticker' column)."
        )
    primary = load_tickers(existing[0], default_suffix, excluded_sectors)
    names: dict[str, str] = {}
    for path in existing[1:]:
        try:
            names = {**{t.symbol: t.name for t in load_tickers(path, default_suffix) if t.name}, **names}
        except ValueError:
            continue  # an unusable secondary file only means fewer company names
    return [replace(ticker, name=ticker.name or names.get(ticker.symbol, "")) for ticker in primary]
