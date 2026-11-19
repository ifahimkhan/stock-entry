"""API integration tests: real Flask app, fake price download, temp files."""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from flask.testing import FlaskClient

from stock_entry.app import CLIENT_HEADER, CLIENT_HEADER_VALUE, create_app
from stock_entry.config import PROJECT_ROOT, Settings, load_settings
from stock_entry.fundamentals import Fundamentals
from stock_entry.market_data import MarketDataError
from tests.conftest import SANSERA_CLOSES, make_prices

pytestmark = pytest.mark.integration
HEADERS = {CLIENT_HEADER: CLIENT_HEADER_VALUE}


def fake_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    if "DOWN.NS" in symbols:
        raise MarketDataError("offline")
    data = {"ENTRY.NS": make_prices(SANSERA_CLOSES[:30]), "WAIT.NS": make_prices(SANSERA_CLOSES[:29])}
    return {symbol: data[symbol] for symbol in symbols if symbol in data}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    tickers = tmp_path / "tickers.csv"
    tickers.write_text("Ticker,Name,Sector\nENTRY.NS,Entry Co,Auto\nWAIT.NS,Wait Co,IT\nNODATA.NS,,\n", encoding="utf-8")
    return replace(Settings(), universe_path=tmp_path / "stock_M_d_S_I.csv", tickers_path=tickers,
                   scan_path=tmp_path / "data" / "scan.json",
                   fundamentals_path=tmp_path / "data" / "fundamentals.json",
                   zone_trades_path=tmp_path / "data" / "zone_entry_trades.csv",
                   web_dir=PROJECT_ROOT / "web", batch_size=2, max_failed_share=0.5)


def fake_fundamentals(symbol: str) -> Fundamentals:
    known = {"ENTRY.NS": Fundamentals(150_000, 0.1), "WAIT.NS": Fundamentals(50_000, 0.25)}
    return known.get(symbol, Fundamentals())


@pytest.fixture
def client(settings: Settings) -> FlaskClient:
    return create_app(settings, fetch=fake_fetch, fetch_fundamentals=fake_fundamentals).test_client()


def wait_for_scan(client: FlaskClient) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        progress = client.get("/api/scan/status").get_json()["data"]["progress"]
        if not progress["running"]:
            return progress
        time.sleep(0.01)
    raise AssertionError("scan did not finish")


def test_scan_flow_end_to_end(client: FlaskClient) -> None:
    empty = client.get("/api/scan").get_json()
    assert empty == {"success": True, "error": None,
                     "data": {"scan": None, "progress": empty["data"]["progress"]}}

    started = client.post("/api/scan", headers=HEADERS)
    assert started.status_code == 202
    assert wait_for_scan(client)["error"] is None

    scan = client.get("/api/scan").get_json()["data"]["scan"]
    assert {row["ticker"]: row["status"] for row in scan["rows"]} == {"ENTRY.NS": "entry", "WAIT.NS": "wait"}
    assert scan["failed"] == ["NODATA.NS"]
    assert scan["rows"][0]["name"] == "Entry Co"
    caps = {row["ticker"]: (row["cap_category"], row["market_cap_cr"], row["debt_to_equity"]) for row in scan["rows"]}
    assert caps == {"ENTRY.NS": ("Large", 150_000, 0.1), "WAIT.NS": ("Mid", 50_000, 0.25)}
    assert client.get("/api/stocks/WAIT").get_json()["data"]["summary"]["cap_category"] == "Mid"


def test_scan_post_requires_page_header(client: FlaskClient) -> None:
    response = client.post("/api/scan")
    assert response.status_code == 403
    assert response.get_json()["success"] is False


def test_stock_detail(client: FlaskClient) -> None:
    body = client.get("/api/stocks/entry").get_json()  # lower case, no suffix
    assert body["success"] is True
    assert body["data"]["summary"]["ticker"] == "ENTRY.NS"
    assert body["data"]["summary"]["name"] == "Entry Co"
    assert body["data"]["history"][-1]["entry_status"] == "entry"


@pytest.mark.parametrize(("symbol", "status"), [
    ("NODATA", 404), ("bad%20symbol!", 400), ("DOWN", 502), ("A" * 40, 400),
])
def test_stock_detail_errors(client: FlaskClient, symbol: str, status: int) -> None:
    response = client.get(f"/api/stocks/{symbol}")
    body = response.get_json()
    assert response.status_code == status
    assert body["success"] is False and body["data"] is None and body["error"]
    assert "Traceback" not in body["error"] and "offline" not in body["error"]


def test_tickers_endpoint(client: FlaskClient, settings: Settings) -> None:
    body = client.get("/api/tickers").get_json()
    assert [row["ticker"] for row in body["data"]] == ["ENTRY.NS", "WAIT.NS", "NODATA.NS"]
    settings.tickers_path.unlink()
    assert client.get("/api/tickers").status_code == 404
    assert client.get("/api/stocks/ENTRY").status_code == 200  # detail works without the list


def test_page_static_and_error_envelopes(client: FlaskClient) -> None:
    assert b"<html" in client.get("/").data.lower()
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/../run.py").status_code == 404
    missing = client.get("/api/nope")
    assert missing.status_code == 404 and missing.get_json()["success"] is False
    assert client.delete("/api/scan").status_code == 405


def test_notebook_file_is_the_stock_list_when_present(client: FlaskClient, settings: Settings) -> None:
    settings.universe_path.write_text(
        "Ticker,Market Cap,Debt to Equity,Sector,Industry\n"
        "ENTRY.NS,1.2e12,0.05,Technology,Software\n"       # 1.2e12 rupees = 120,000 crore -> Large
        "BANK.NS,5e11,0.9,Financial Services,Banks\n",
        encoding="utf-8",
    )
    listed = client.get("/api/tickers").get_json()["data"]
    assert listed[0] == {"ticker": "ENTRY.NS", "name": "Entry Co", "sector": "Technology"}  # name from tickers.csv
    assert [item["ticker"] for item in listed] == ["ENTRY.NS", "BANK.NS"]   # every sector is scanned
    summary = client.get("/api/stocks/ENTRY").get_json()["data"]["summary"]
    assert (summary["market_cap_cr"], summary["debt_to_equity"], summary["cap_category"]) == (120_000, 0.05, "Large")


def test_requests_for_another_host_are_rejected(client: FlaskClient) -> None:
    response = client.get("/api/scan", headers={"Host": "evil.example.com"})
    assert response.status_code == 403 and response.get_json()["success"] is False
    assert client.get("/api/scan", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_bad_tick_does_not_break_json(settings: Settings) -> None:
    bad_tick = make_prices([("2026-01-01", 5.0), ("2026-01-02", 0.0), ("2026-01-05", 5.0), ("2026-01-06", 6.0)])
    client = create_app(settings, fetch=lambda symbols: {"ZERO.NS": bad_tick},
                        fetch_fundamentals=fake_fundamentals).test_client()
    response = client.get("/api/stocks/ZERO")
    assert response.status_code == 200
    assert b"Infinity" not in response.data and b"NaN" not in response.data
    assert len(response.get_json()["data"]["history"]) == 3  # the zero-price bar is dropped


def test_load_settings_validates_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOCK_ENTRY_PORT", "8123")
    assert load_settings().port == 8123
    monkeypatch.setenv("STOCK_ENTRY_PORT", "99999")
    with pytest.raises(ValueError, match="STOCK_ENTRY_PORT"):
        load_settings()


def test_scan_job_saves_a_scan_and_reports_failure(settings: Settings, tmp_path: Path) -> None:
    from stock_entry.scan_job import run_once
    from stock_entry.services import build_services

    services = build_services(settings, fetch=fake_fetch, fetch_fundamentals=fake_fundamentals)
    assert run_once(services) == 0
    saved = services.store.load()
    assert {row["ticker"] for row in saved["rows"]} == {"ENTRY.NS", "WAIT.NS"}

    broken = build_services(replace(settings, tickers_path=tmp_path / "missing.csv"),
                            fetch=fake_fetch, fetch_fundamentals=fake_fundamentals)
    assert run_once(broken) == 1


def test_zone_results_endpoint_returns_summary_trades_and_rules(client: FlaskClient, settings: Settings) -> None:
    empty = client.get("/api/zone-results").get_json()["data"]
    assert empty["summary"] == [] and empty["trades"] == [] and "Win" in empty["rules"]
    settings.zone_trades_path.parent.mkdir(parents=True, exist_ok=True)
    settings.zone_trades_path.write_text(
        "timeframe,ticker,hh_at,signal_at,pattern,hh_close,change_pct,entry_at,entry,outcome,exit_at,exit,return_pct,sessions\n"
        "1d,A.NS,2026-01-01 00:00,2026-01-08 00:00,3LL-HH,10,4.2,2026-01-09 00:00,10,win,2026-01-12 00:00,10.5,5.0,2\n"
        "2h,B.NS,2026-01-01 09:15,2026-01-02 11:15,4LL-HH,20,,,,open,,,,0\n", encoding="utf-8")
    response = client.get("/api/zone-results")
    assert b"NaN" not in response.data   # strict JSON: the browser cannot read NaN
    data = response.get_json()["data"]
    every = {(row["timeframe"], row["pattern"]): row for row in data["summary"]}
    assert every[("1d", "all")]["win_rate"] == 100.0 and every[("2h", "all")]["open"] == 1
    assert [trade["ticker"] for trade in data["trades"]] == ["A.NS", "B.NS"]   # newest signal first
    assert data["trades"][1]["change_pct"] is None   # missing values come back as null, not NaN
