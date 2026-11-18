"""Flask app: serves the web page and a small JSON API.

Every API response uses the same envelope: {"success": bool, "data": ..., "error": str | None}.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from stock_entry.config import Settings
from stock_entry.fundamentals import FetchOne
from stock_entry.history import StockNotFoundError
from stock_entry.market_data import MarketDataError
from stock_entry.services import Fetch, build_services
from stock_entry.tickers import InvalidSymbolError, normalize_symbol
from stock_entry.tv_login import TradingViewLogin, TradingViewLoginError

logger = logging.getLogger(__name__)

CLIENT_HEADER = "X-Requested-With"   # browsers cannot set this on cross-site form posts
CLIENT_HEADER_VALUE = "stock-entry"


def _ok(data: Any, status: int = 200) -> tuple[Response, int]:
    return jsonify({"success": True, "data": data, "error": None}), status


def _fail(message: str, status: int) -> tuple[Response, int]:
    return jsonify({"success": False, "data": None, "error": message}), status


def create_app(settings: Settings, fetch: Fetch | None = None,
               fetch_fundamentals: FetchOne | None = None, tv_login: TradingViewLogin | None = None) -> Flask:
    """Build the app. `fetch` (symbols -> {symbol: bars}) and `fetch_fundamentals` can be replaced in tests."""
    services = build_services(settings, fetch, fetch_fundamentals, tv_login=tv_login)
    store, scanner, history, fundamentals = services.store, services.scanner, services.history, services.fundamentals
    load_universe, known_ticker = services.load_universe, services.known_ticker

    app = Flask(__name__, static_folder=None)
    allowed_hosts = {"127.0.0.1", "localhost", settings.host}

    @app.before_request
    def reject_foreign_hosts() -> tuple[Response, int] | None:
        """Blocks DNS-rebinding: a web page on another domain cannot reach this local server."""
        hostname = request.host.rsplit(":", 1)[0] if ":" in request.host else request.host
        if hostname not in allowed_hosts:
            return _fail("This server only answers requests made to 127.0.0.1 or localhost.", 403)
        return None

    @app.get("/")
    def index() -> Response:
        return send_from_directory(settings.web_dir, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename: str) -> Response:
        return send_from_directory(settings.web_dir, filename)

    @app.get("/api/scan")
    def get_scan() -> tuple[Response, int]:
        return _ok({"scan": store.load(), "progress": scanner.progress()})

    @app.post("/api/scan")
    def start_scan() -> tuple[Response, int]:
        if request.headers.get(CLIENT_HEADER) != CLIENT_HEADER_VALUE:
            return _fail("This request must come from the Stock Entry page.", 403)
        if not scanner.start():
            return _fail("A scan is already running.", 409)
        history.clear()
        return _ok({"progress": scanner.progress()}, 202)

    @app.get("/api/scan/status")
    def scan_status() -> tuple[Response, int]:
        return _ok({"progress": scanner.progress()})

    tv_login = services.tv_login

    def _from_page() -> bool:
        return request.headers.get(CLIENT_HEADER) == CLIENT_HEADER_VALUE

    @app.get("/api/tradingview")
    def tradingview_status() -> tuple[Response, int]:
        if tv_login is None:
            return _fail("TradingView login is not used by this server.", 404)
        return _ok(tv_login.status())

    @app.post("/api/tradingview/login")
    def tradingview_sign_in() -> tuple[Response, int]:
        """Body {"username", "password"}. Kept in memory only, so it is gone when the server restarts."""
        if not _from_page():
            return _fail("This request must come from the Stock Entry page.", 403)
        if tv_login is None:
            return _fail("TradingView login is not used by this server.", 404)
        if scanner.progress().get("running"):
            return _fail("Wait for the running scan to finish, then sign in.", 409)
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        try:
            status = tv_login.sign_in(body.get("username"), body.get("password"))
        except TradingViewLoginError as exc:
            return _fail(str(exc), 400)
        history.clear()   # stock details opened before now have no (or old) 2-hour bars
        return _ok(status)

    @app.post("/api/tradingview/logout")
    def tradingview_sign_out() -> tuple[Response, int]:
        if not _from_page():
            return _fail("This request must come from the Stock Entry page.", 403)
        if tv_login is None:
            return _fail("TradingView login is not used by this server.", 404)
        if scanner.progress().get("running"):
            return _fail("Wait for the running scan to finish, then sign out.", 409)
        history.clear()
        return _ok(tv_login.sign_out())

    @app.get("/api/tickers")
    def list_tickers() -> tuple[Response, int]:
        try:
            tickers = load_universe()
        except (FileNotFoundError, ValueError) as exc:
            return _fail(str(exc), 404)
        return _ok([{"ticker": t.symbol, "name": t.name, "sector": t.sector} for t in tickers])

    @app.get("/api/stocks/<symbol>")
    def get_stock(symbol: str) -> tuple[Response, int]:
        try:
            normalized = normalize_symbol(symbol, settings.default_suffix)
            return _ok(history.get(fundamentals.enrich_one(known_ticker(normalized))))
        except InvalidSymbolError as exc:
            return _fail(str(exc), 400)
        except StockNotFoundError as exc:
            return _fail(str(exc), 404)
        except MarketDataError as exc:
            logger.error("Price download failed for %s: %s", symbol, exc)
            return _fail("Could not reach Yahoo Finance. Check the internet connection and try again.", 502)

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException) -> tuple[Response, int]:
        return _fail(exc.name, exc.code or 500)

    @app.errorhandler(Exception)
    def unexpected(exc: Exception) -> tuple[Response, int]:
        logger.exception("Unhandled error on %s", request.path, exc_info=exc)
        return _fail("Something went wrong on the server. See the server log.", 500)

    return app
