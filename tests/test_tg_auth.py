"""Tests for tg_auth.verify_init_data (Telegram Mini App signature check)."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import tg_auth

TOKEN = "123456:TEST_TOKEN"


def make_init_data(user: dict, token: str = TOKEN, auth_date: int | None = None, tamper: bool = False) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAH123",
        "user": json.dumps(user, separators=(",", ":")),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["user"] = json.dumps({**user, "id": 999}, separators=(",", ":"))
    return urlencode(fields)


def test_valid_init_data_returns_user():
    user = {"id": 42, "first_name": "Ali", "username": "ali"}
    assert tg_auth.verify_init_data(make_init_data(user), TOKEN) == user


def test_wrong_token_rejected():
    assert tg_auth.verify_init_data(make_init_data({"id": 42}), "999:OTHER") is None


def test_tampered_user_rejected():
    assert tg_auth.verify_init_data(make_init_data({"id": 42}, tamper=True), TOKEN) is None


def test_expired_rejected():
    old = int(time.time()) - 3 * 24 * 3600
    assert tg_auth.verify_init_data(make_init_data({"id": 42}, auth_date=old), TOKEN) is None


def test_missing_or_garbage_rejected():
    assert tg_auth.verify_init_data("", TOKEN) is None
    assert tg_auth.verify_init_data("hello=world", TOKEN) is None
    assert tg_auth.verify_init_data("hash=abc&user=%7B%7D", TOKEN) is None


def test_display_name():
    assert tg_auth.display_name({"id": 1, "first_name": "Ali", "last_name": "R"}) == "Ali R"
    assert tg_auth.display_name({"id": 1, "username": "bob"}) == "bob"
    assert tg_auth.display_name({"id": 1}) == "Player"
