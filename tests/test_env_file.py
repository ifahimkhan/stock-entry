"""The .env reader used for TV_USERNAME / TV_PASSWORD."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from stock_entry.env_file import load_env_file, parse_env
from stock_entry.tradingview import credentials

pytestmark = pytest.mark.unit


def test_parse_env_accepts_spaces_quotes_export_and_comments() -> None:
    text = '# comment\nTV_USERNAME = "alice"\nexport TV_PASSWORD=\'p=ss word\'\nPLAIN=x\nbroken line\n'
    assert parse_env(text) == {"TV_USERNAME": "alice", "TV_PASSWORD": "p=ss word", "PLAIN": "x"}


def test_load_sets_only_named_missing_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('TV_USERNAME = "file-user"\nTV_PASSWORD = "file-pass"\nOTHER=1\n', encoding="utf-8")
    monkeypatch.setenv("TV_USERNAME", "env-user")
    monkeypatch.delenv("TV_PASSWORD", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    assert load_env_file(("TV_USERNAME", "TV_PASSWORD"), env) == ["TV_PASSWORD"]
    assert os.environ["TV_USERNAME"] == "env-user" and os.environ["TV_PASSWORD"] == "file-pass"
    assert "OTHER" not in os.environ
    assert load_env_file(("TV_USERNAME",), tmp_path / "missing.env") == []


def test_credentials_fall_back_to_the_project_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_entry import env_file

    env = tmp_path / ".env"
    env.write_text('TV_USERNAME = "u"\nTV_PASSWORD = "p"\n', encoding="utf-8")
    monkeypatch.setattr(env_file, "PROJECT_ENV", env)
    monkeypatch.delenv("TV_USERNAME", raising=False)
    monkeypatch.delenv("TV_PASSWORD", raising=False)
    assert credentials() == ("u", "p")


def test_session_token_is_read_from_the_page() -> None:
    from stock_entry.tradingview import token_from_session

    class Page:
        text = 'window.user = {"username":"u","auth_token":"tok-123","x":1}'

    seen = {}

    def get(url, cookies, headers, timeout):
        seen.update(cookies)
        return Page()

    assert token_from_session({"sessionid": "abc"}, get) == "tok-123" and seen == {"sessionid": "abc"}

    class SignedOut:
        text = "<html>no user</html>"

    assert token_from_session({"sessionid": "abc"}, lambda *a, **k: SignedOut()) is None

    def offline(*args, **kwargs):
        raise OSError("down")

    assert token_from_session({"sessionid": "abc"}, offline) is None


def test_feed_uses_the_session_token_before_the_password() -> None:
    from stock_entry.tradingview import TRADINGVIEW, TradingViewFeed

    made = []
    feed = TradingViewFeed("u", "p", n_bars=5, make_client=lambda *a: made.append(a), session=lambda: "tok")
    assert feed.source == TRADINGVIEW and feed.signed_in is True and made == []   # no password sign-in


def test_session_cookies_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_entry import env_file
    from stock_entry.tradingview import session_cookies

    monkeypatch.delenv("TV_SESSIONID", raising=False)
    monkeypatch.delenv("TV_SESSIONID_SIGN", raising=False)
    assert session_cookies() is None
    env = tmp_path / ".env"
    env.write_text('TV_SESSIONID = "sid"\nTV_SESSIONID_SIGN = "sign"\n', encoding="utf-8")
    monkeypatch.setattr(env_file, "PROJECT_ENV", env)
    assert session_cookies() == {"sessionid": "sid", "sessionid_sign": "sign"}
