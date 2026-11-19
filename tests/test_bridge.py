"""Streamlit bridge tests: same services as the Flask API, driven by request dicts instead of HTTP."""
from __future__ import annotations

import shutil
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_entry.bridge import build_component_dir, handle_request, is_new_request, page_fingerprint
from stock_entry.config import PROJECT_ROOT, Settings
from stock_entry.fundamentals import Fundamentals
from stock_entry.market_data import MarketDataError
from stock_entry.services import Services, build_services
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.integration


def fake_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    if "DOWN.NS" in symbols:
        raise MarketDataError("offline")
    data = {"ENTRY.NS": make_prices(SANSERA_CLOSES[:30]), "WAIT.NS": make_prices(SANSERA_CLOSES[:29])}
    return {symbol: data[symbol] for symbol in symbols if symbol in data}


def fake_fundamentals(symbol: str) -> Fundamentals:
    known = {"ENTRY.NS": Fundamentals(150_000, 0.1), "WAIT.NS": Fundamentals(50_000, 0.25)}
    return known.get(symbol, Fundamentals())


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    tickers = tmp_path / "tickers.csv"
    tickers.write_text("Ticker,Name,Sector\nENTRY.NS,Entry Co,Auto\nWAIT.NS,Wait Co,IT\nNODATA.NS,,\n", encoding="utf-8")
    return replace(Settings(), universe_path=tmp_path / "stock_M_d_S_I.csv", tickers_path=tickers,
                   scan_path=tmp_path / "data" / "scan.json",
                   fundamentals_path=tmp_path / "data" / "fundamentals.json",
                   zone_trades_path=tmp_path / "data" / "zone_entry_trades.csv",
                   web_dir=PROJECT_ROOT / "web", batch_size=2, max_failed_share=0.5)


@pytest.fixture
def services(settings: Settings) -> Services:
    return build_services(settings, fetch=fake_fetch, fetch_fundamentals=fake_fundamentals)


def call(services: Services, op: str, **extra: object) -> dict:
    return handle_request(services, {"id": f"t-{op}", "op": op, **extra})


def wait_for_scan(services: Services) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        progress = call(services, "scan_status")["data"]["progress"]
        if not progress["running"]:
            return progress
        time.sleep(0.01)
    raise AssertionError("scan did not finish")


def test_scan_flow_end_to_end(services: Services) -> None:
    empty = call(services, "scan")
    assert empty["id"] == "t-scan" and empty["success"] is True and empty["error"] is None
    assert empty["data"]["scan"] is None

    assert call(services, "start_scan")["success"] is True
    assert wait_for_scan(services)["error"] is None

    scan = call(services, "scan")["data"]["scan"]
    assert {row["ticker"]: row["status"] for row in scan["rows"]} == {"ENTRY.NS": "entry", "WAIT.NS": "wait"}
    assert scan["failed"] == ["NODATA.NS"]


def test_second_scan_while_running_is_refused(settings: Settings) -> None:
    def slow_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
        time.sleep(0.2)
        return fake_fetch(symbols)

    services = build_services(settings, fetch=slow_fetch, fetch_fundamentals=fake_fundamentals)
    assert call(services, "start_scan")["success"] is True
    second = call(services, "start_scan")
    assert second["success"] is False and second["error"] == "A scan is already running."
    wait_for_scan(services)


def test_tickers(services: Services, settings: Settings) -> None:
    listed = call(services, "tickers")
    assert [row["ticker"] for row in listed["data"]] == ["ENTRY.NS", "WAIT.NS", "NODATA.NS"]
    settings.tickers_path.unlink()
    missing = call(services, "tickers")
    assert missing["success"] is False and missing["data"] is None and missing["error"]


def test_stock_detail(services: Services) -> None:
    body = call(services, "stock", symbol="entry")  # lower case, no suffix
    assert body["success"] is True
    assert body["data"]["summary"]["ticker"] == "ENTRY.NS"
    assert body["data"]["summary"]["name"] == "Entry Co"
    assert body["data"]["history"][-1]["entry_status"] == "entry"


@pytest.mark.parametrize("symbol", ["NODATA", "bad symbol!", "DOWN", "A" * 40, "", None, 42, {"a": 1}])
def test_stock_detail_errors(services: Services, symbol: object) -> None:
    body = call(services, "stock", symbol=symbol)
    assert body["success"] is False and body["data"] is None and body["error"]
    assert "Traceback" not in body["error"] and "offline" not in body["error"]


@pytest.mark.parametrize("request_value", [
    None, "scan", 7, [], {}, {"id": "x"}, {"id": "x", "op": "delete_everything"}, {"id": "x", "op": ["scan"]},
])
def test_malformed_requests_get_an_error_envelope(services: Services, request_value: object) -> None:
    body = handle_request(services, request_value)
    assert body["success"] is False and body["data"] is None and body["error"]


def test_unexpected_failure_does_not_leak_details(services: Services) -> None:
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    broken = replace(services, load_universe=boom)
    body = call(broken, "tickers")
    assert body["success"] is False and "secret" not in body["error"]


def test_build_component_dir_swaps_in_the_bridge_client(tmp_path: Path) -> None:
    web = PROJECT_ROOT / "web"
    target = build_component_dir(web, tmp_path / "component")
    assert (target / "api.js").read_text(encoding="utf-8") == (web / "api.streamlit.js").read_text(encoding="utf-8")
    index = (target / "index.html").read_text(encoding="utf-8")
    assert "/static/" not in index and 'src="app.js"' in index and 'href="styles.css"' in index
    for name in ("app.js", "chart.js", "detail.js", "format.js", "table.js", "styles.css"):
        assert (target / name).read_bytes() == (web / name).read_bytes()
    assert build_component_dir(web, tmp_path / "component") == target  # safe to run again


def test_versioned_component_dir_tags_every_asset_and_import(tmp_path: Path) -> None:
    web = PROJECT_ROOT / "web"
    target = build_component_dir(web, tmp_path / "component", version="abc123")
    index = (target / "index.html").read_text(encoding="utf-8")
    assert 'src="app.js?v=abc123"' in index and 'href="styles.css?v=abc123"' in index
    app = (target / "app.js").read_text(encoding="utf-8")
    assert 'from "./api.js?v=abc123"' in app and 'from "./cards.js?v=abc123"' in app
    assert 'from "./format.js"' not in app  # no import left without the version
    assert (target / "styles.css").read_bytes() == (web / "styles.css").read_bytes()


def test_page_fingerprint_changes_with_the_page_files(tmp_path: Path) -> None:
    web = tmp_path / "web"
    shutil.copytree(PROJECT_ROOT / "web", web)
    before = page_fingerprint(web)
    assert before == page_fingerprint(web) and len(before) == 12
    (web / "styles.css").write_text("body { color: red; }", encoding="utf-8")
    assert page_fingerprint(web) != before


def test_build_component_dir_needs_the_web_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_component_dir(tmp_path / "missing", tmp_path / "component")


def test_scan_cannot_be_restarted_right_after_it_finished(services: Services) -> None:
    assert call(services, "start_scan")["success"] is True
    wait_for_scan(services)
    again = call(services, "start_scan")
    assert again["success"] is False and "minute" in again["error"]

    no_cooldown = replace(services, settings=replace(services.settings, scan_cooldown_seconds=0,
                                                     failed_scan_cooldown_seconds=0))
    assert call(no_cooldown, "start_scan")["success"] is True
    wait_for_scan(no_cooldown)


def test_server_paths_are_not_shown_to_visitors(services: Services, settings: Settings) -> None:
    settings.tickers_path.unlink()
    listed = call(services, "tickers")
    assert listed["success"] is False and str(settings.tickers_path.parent) not in listed["error"]

    assert call(services, "start_scan")["success"] is True
    progress = wait_for_scan(services)
    assert progress["error"] and str(settings.tickers_path.parent) not in progress["error"]
    shown = call(services, "scan")["data"]["progress"]["error"]
    assert shown and str(settings.tickers_path.parent) not in shown


@pytest.mark.parametrize(("request_value", "handled", "expected"), [
    (None, None, False),
    ({"id": "a-1", "op": "scan"}, None, True),
    ({"id": "a-1", "op": "scan"}, {"id": "a-1", "op": "scan"}, False),   # same request seen again after a rerun
    ({"id": "a-2", "op": "scan"}, {"id": "a-1", "op": "scan"}, True),
    ({"op": "stock", "symbol": "TCS"}, None, False),                      # no id: the page never sends this
    ({"id": 5, "op": "scan"}, None, False),
    ("scan", None, False),
])
def test_is_new_request(request_value: object, handled: object, expected: bool) -> None:
    assert is_new_request(request_value, handled) is expected


def test_cooldown_uses_the_same_clock_as_the_scanner(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    import stock_entry.bridge as bridge_module

    assert call(services, "start_scan")["success"] is True
    wait_for_scan(services)
    later = datetime.fromisoformat(services.scanner.progress()["finished_at"]) + timedelta(hours=1)
    monkeypatch.setattr(bridge_module, "now_ist", lambda: later)
    assert call(services, "start_scan")["success"] is True
    wait_for_scan(services)


def test_code_fingerprint_changes_with_the_python_code(tmp_path) -> None:
    from stock_entry.bridge import code_fingerprint

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    first = code_fingerprint(tmp_path)
    assert first == code_fingerprint(tmp_path) and len(first) == 12
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert code_fingerprint(tmp_path) != first
    assert code_fingerprint() == code_fingerprint()   # the real package


def test_zone_results_op_reads_the_saved_trades(services: Services) -> None:
    body = call(services, "zone_results")
    assert body["error"] is None and body["data"]["summary"] == [] and body["data"]["max_sessions"] == 20
