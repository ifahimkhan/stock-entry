"""TradingView login typed into the page: swaps the 2-hour feed, never leaks the password, local app only."""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from stock_entry.app import CLIENT_HEADER, CLIENT_HEADER_VALUE, create_app
from stock_entry.bridge import handle_request
from stock_entry.config import PROJECT_ROOT, Settings
from stock_entry.services import build_services
from stock_entry.tradingview import TRADINGVIEW, TradingViewFeed
from stock_entry.tv_login import TradingViewLogin, TradingViewLoginError
from tests.test_api import fake_fetch, fake_fundamentals
from tests.test_tradingview import FakeClient, raw_frame

pytestmark = pytest.mark.unit

SECRET = "pw"          # the password FakeClient accepts (with username "user")
WRONG = "s3cret-wrong"
HEADERS = {CLIENT_HEADER: CLIENT_HEADER_VALUE}


def make_login(env: tuple[str, str] | None = None, offline: bool = False) -> TradingViewLogin:
    replies = {"A": [raw_frame()]}

    def make_client(username: str | None, password: str | None) -> FakeClient:
        if offline:
            raise ConnectionError(f"could not post {password}")   # the text must never reach the page or log
        return FakeClient(username, password, replies, [])

    def make_feed(username: str, password: str, n_bars: int) -> TradingViewFeed:
        return TradingViewFeed(username, password, n_bars=n_bars, workers=1, attempts=1, interval="2H",
                               make_client=make_client, wait=lambda s: None)

    return TradingViewLogin(n_bars=500, make_feed=make_feed, env=lambda: env)


def test_without_a_login_there_are_no_two_hour_bars() -> None:
    login = make_login()
    assert login(["A.NS"]) == {}
    assert login.status() == {"configured": False, "signed_in": False, "username": None, "origin": None}


def test_environment_login_is_the_default_and_signs_in_on_first_use() -> None:
    login = make_login(env=("user", SECRET))
    assert login.status() == {"configured": True, "signed_in": None, "username": "user", "origin": "env"}
    assert login(["A.NS"])["A.NS"]["Source"].iloc[-1] == TRADINGVIEW
    assert login.status()["signed_in"] is True


def test_good_login_from_the_page_replaces_the_feed() -> None:
    login = make_login()
    status = login.sign_in(" user ", SECRET)
    assert status == {"configured": True, "signed_in": True, "username": "user", "origin": "page"}
    assert login(["A.NS"])["A.NS"]["Source"].iloc[-1] == TRADINGVIEW
    assert login.sign_out()["configured"] is False and login(["A.NS"]) == {}


def test_rejected_login_keeps_the_old_one(caplog: pytest.LogCaptureFixture) -> None:
    login = make_login(env=("user", SECRET))
    with caplog.at_level(logging.DEBUG), pytest.raises(TradingViewLoginError) as error:
        login.sign_in("user", WRONG)
    assert "did not accept" in str(error.value) and WRONG not in str(error.value)
    assert WRONG not in caplog.text
    assert login.status()["origin"] == "env"


def test_network_failure_message_never_echoes_the_password(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(TradingViewLoginError) as error:
        make_login(offline=True).sign_in("user", WRONG)
    assert "Could not reach TradingView" in str(error.value)
    assert WRONG not in str(error.value) and WRONG not in caplog.text
    assert error.value.__cause__ is None and error.value.__suppress_context__


@pytest.mark.parametrize("username, password", [("", SECRET), ("user", ""), (None, SECRET), ("user", 5),
                                                ("u" * 200, SECRET)])
def test_bad_input_is_refused(username: object, password: object) -> None:
    with pytest.raises(TradingViewLoginError):
        make_login().sign_in(username, password)


# ---------- Flask endpoints ----------

@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    tickers = tmp_path / "tickers.csv"
    tickers.write_text("Ticker,Name,Sector\nENTRY.NS,Entry Co,Auto\n", encoding="utf-8")
    return replace(Settings(), universe_path=tmp_path / "none.csv", tickers_path=tickers,
                   scan_path=tmp_path / "data" / "scan.json", fundamentals_path=tmp_path / "data" / "f.json",
                   web_dir=PROJECT_ROOT / "web", batch_size=2, max_failed_share=0.5)


@pytest.fixture
def client(settings: Settings) -> FlaskClient:
    return create_app(settings, fetch=fake_fetch, fetch_fundamentals=fake_fundamentals,
                      tv_login=make_login()).test_client()


def test_login_endpoint_signs_in_and_never_returns_the_password(client: FlaskClient) -> None:
    assert client.get("/api/tradingview").get_json()["data"]["configured"] is False
    response = client.post("/api/tradingview/login", json={"username": "user", "password": SECRET}, headers=HEADERS)
    assert response.status_code == 200
    assert response.get_json()["data"] == {"configured": True, "signed_in": True, "username": "user", "origin": "page"}
    assert SECRET not in client.get("/api/tradingview").get_data(as_text=True).replace('"user"', "")
    assert client.post("/api/tradingview/logout", headers=HEADERS).get_json()["data"]["configured"] is False


def test_rejected_login_is_a_400_without_the_password(client: FlaskClient) -> None:
    response = client.post("/api/tradingview/login", json={"username": "user", "password": WRONG}, headers=HEADERS)
    assert response.status_code == 400 and WRONG not in response.get_data(as_text=True)


def test_login_needs_the_page_header(client: FlaskClient) -> None:
    response = client.post("/api/tradingview/login", json={"username": "user", "password": SECRET})
    assert response.status_code == 403
    assert client.post("/api/tradingview/logout").status_code == 403
    assert client.post("/api/tradingview/login", data="nope", headers=HEADERS).status_code == 400


def test_login_waits_for_a_running_scan(settings: Settings) -> None:
    release = threading.Event()

    def slow_fetch(symbols: list[str]) -> dict:
        release.wait(5)
        return fake_fetch(symbols)

    client = create_app(settings, fetch=slow_fetch, fetch_fundamentals=fake_fundamentals,
                        tv_login=make_login()).test_client()
    assert client.post("/api/scan", headers=HEADERS).status_code == 202
    try:
        response = client.post("/api/tradingview/login", json={"username": "user", "password": SECRET},
                               headers=HEADERS)
        assert response.status_code == 409
    finally:
        release.set()


def test_test_apps_without_a_login_say_so(settings: Settings) -> None:
    client = create_app(settings, fetch=fake_fetch, fetch_fundamentals=fake_fundamentals).test_client()
    assert client.get("/api/tradingview").status_code == 404


# ---------- Streamlit (live site) ----------

@pytest.mark.parametrize("op", ["tradingview", "tradingview_login", "tradingview_logout"])
def test_live_site_refuses_page_logins(settings: Settings, op: str) -> None:
    services = build_services(settings, fetch=fake_fetch, fetch_fundamentals=fake_fundamentals, tv_login=make_login())
    reply = handle_request(services, {"id": "t", "op": op, "username": "user", "password": SECRET})
    assert reply["success"] is False and "Streamlit secrets" in reply["error"]
    assert services.tv_login.status()["configured"] is False
