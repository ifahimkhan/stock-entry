"""Streamlit front door: the page's API calls arrive as request dicts instead of HTTP requests.

The web page is the same one Flask serves. Under Streamlit its api.js is swapped for api.streamlit.js,
which sends {"id", "op", ...} through the Streamlit component channel; `handle_request` answers with
{"id", "success", "data", "error"} - the Flask envelope plus the request id.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import shutil
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from stock_entry.clock import now_ist
from stock_entry.history import StockNotFoundError
from stock_entry.market_data import MarketDataError
from stock_entry.services import Services
from stock_entry.tickers import InvalidSymbolError, normalize_symbol

logger = logging.getLogger(__name__)

BRIDGE_CLIENT = "api.streamlit.js"
MAX_ID_LENGTH = 64
PAGE_FILES = ("index.html", "app.js", "cards.js", "chart.js", "detail.js", "format.js", "market.js", "table.js", "styles.css")


class BridgeError(Exception):
    """A request that cannot be served; the message is safe to show on the page."""


def _without_paths(services: Services, text: str | None) -> str | None:
    """Replace the server's file paths in a message with bare file names before visitors see it."""
    if not text:
        return text
    settings = services.settings
    paths = [getattr(settings, field.name) for field in fields(settings)]
    for path in sorted((p for p in paths if isinstance(p, Path)), key=lambda p: len(str(p)), reverse=True):
        text = text.replace(str(path), path.name)
    return text


def _progress(services: Services) -> dict[str, Any]:
    progress = services.scanner.progress()
    return {**progress, "error": _without_paths(services, progress.get("error"))}


def _cooldown_left(services: Services, now: datetime) -> float:
    """Seconds until another scan may start; 0 when one may start now."""
    progress = services.scanner.progress()
    try:
        finished = datetime.fromisoformat(progress["finished_at"])
    except (KeyError, TypeError, ValueError):
        return 0.0  # no scan has finished since the server started
    settings = services.settings
    cooldown = settings.failed_scan_cooldown_seconds if progress.get("error") else settings.scan_cooldown_seconds
    return max(0.0, cooldown - (now - finished).total_seconds())


def _scan(services: Services, request: dict[str, Any]) -> Any:
    return {"scan": services.store.load(), "progress": _progress(services)}


def _scan_status(services: Services, request: dict[str, Any]) -> Any:
    return {"progress": _progress(services)}


def _start_scan(services: Services, request: dict[str, Any]) -> Any:
    wait = _cooldown_left(services, now_ist())
    if wait > 0:
        minutes = math.ceil(wait / 60)
        raise BridgeError(f"A scan just finished. The next one can start in about {minutes} minute"
                          f"{'' if minutes == 1 else 's'}.")
    if not services.scanner.start():
        raise BridgeError("A scan is already running.")
    services.history.clear()
    return {"progress": _progress(services)}


def _tickers(services: Services, request: dict[str, Any]) -> Any:
    try:
        tickers = services.load_universe()
    except (FileNotFoundError, ValueError) as exc:
        raise BridgeError(_without_paths(services, str(exc)) or "The stock list could not be loaded.") from exc
    return [{"ticker": t.symbol, "name": t.name, "sector": t.sector} for t in tickers]


def _stock(services: Services, request: dict[str, Any]) -> Any:
    symbol = request.get("symbol")
    if not isinstance(symbol, str):
        raise BridgeError("Type a stock symbol, e.g. TCS or TCS.NS")
    try:
        normalized = normalize_symbol(symbol, services.settings.default_suffix)
        return services.history.get(services.fundamentals.enrich_one(services.known_ticker(normalized)))
    except (InvalidSymbolError, StockNotFoundError) as exc:
        raise BridgeError(str(exc)) from exc
    except MarketDataError as exc:
        logger.error("Price download failed for %s: %s", symbol[:30], exc)
        raise BridgeError("Could not reach Yahoo Finance. Try again in a minute.") from exc


def _tradingview(services: Services, request: dict[str, Any]) -> Any:
    """The public site is shared by every visitor, so a login typed there would be used for everyone."""
    raise BridgeError("Signing in to TradingView from the page only works in the local app. "
                      "On the live site set TV_USERNAME / TV_PASSWORD in the Streamlit secrets.")


Handler = Callable[[Services, dict[str, Any]], Any]
HANDLERS: dict[str, Handler] = {
    "scan": _scan, "scan_status": _scan_status, "start_scan": _start_scan, "tickers": _tickers, "stock": _stock,
    "tradingview": _tradingview, "tradingview_login": _tradingview, "tradingview_logout": _tradingview,
}


def request_id(request: Any) -> str | None:
    """The request's id when it has a usable one. The page uses it to match the reply to the call."""
    value = request.get("id") if isinstance(request, dict) else None
    return value if isinstance(value, str) and 0 < len(value) <= MAX_ID_LENGTH else None


def is_new_request(request: Any, handled: Any) -> bool:
    """True for a request to serve now. Streamlit shows the page's last request again on every rerun,
    so one that was already served - or has no usable id to match a reply to - must be skipped."""
    return request_id(request) is not None and request != handled


def _reply(request: Any, data: Any = None, error: str | None = None) -> dict[str, Any]:
    return {"id": request_id(request), "success": error is None, "data": data, "error": error}


def handle_request(services: Services, request: Any) -> dict[str, Any]:
    """Serve one request from the page. Never raises: every failure becomes an error envelope."""
    op = request.get("op") if isinstance(request, dict) else None
    handler = HANDLERS.get(op) if isinstance(op, str) else None
    if handler is None:
        return _reply(request, error="Unknown request from the page. Reload the page and try again.")
    try:
        return _reply(request, data=handler(services, request))
    except BridgeError as exc:
        return _reply(request, error=str(exc))
    except Exception:
        logger.exception("Unhandled error serving %s", op)
        return _reply(request, error="Something went wrong on the server. See the server log.")


# ES module imports between page files, e.g. from "./format.js"
_RELATIVE_IMPORT = re.compile(r'(from\s+"\./[\w.-]+\.js)(")')
# <script src="app.js"> / <link href="styles.css"> once "/static/" is removed
_ASSET_LINK = re.compile(r'((?:src|href)="[\w.-]+\.(?:js|css))(")')


def page_fingerprint(web_dir: Path) -> str:
    """Short hash of the page files: it changes whenever a deploy changes the page."""
    digest = hashlib.sha256()
    for name in (*PAGE_FILES, BRIDGE_CLIENT):
        path = web_dir / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"")
    return digest.hexdigest()[:12]


def code_fingerprint(package_dir: Path | None = None) -> str:
    """Short hash of the app's Python code: it changes whenever a deploy changes stock_entry/*.py."""
    package_dir = package_dir or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_dir.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def build_component_dir(web_dir: Path, target: Path, version: str | None = None) -> Path:
    """Copy the page into `target` as a Streamlit component: relative asset links and the bridge api.js.

    With `version`, asset links and module imports get "?v=<version>", so a browser never mixes files it
    cached from an older deploy with new ones (Streamlit serves component files as cacheable).
    """
    missing = [name for name in (*PAGE_FILES, BRIDGE_CLIENT) if not (web_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Web page files missing from {web_dir}: {', '.join(missing)}")
    target.mkdir(parents=True, exist_ok=True)
    for name in PAGE_FILES:
        shutil.copyfile(web_dir / name, target / name)
    shutil.copyfile(web_dir / BRIDGE_CLIENT, target / "api.js")
    index = (target / "index.html").read_text(encoding="utf-8").replace("/static/", "")
    if version:
        tag = rf"\1?v={version}\2"
        index = _ASSET_LINK.sub(tag, index)
        for script in [target / name for name in (*PAGE_FILES, "api.js") if name.endswith(".js")]:
            script.write_text(_RELATIVE_IMPORT.sub(tag, script.read_text(encoding="utf-8")), encoding="utf-8")
    (target / "index.html").write_text(index, encoding="utf-8")
    return target
