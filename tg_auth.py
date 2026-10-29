"""Verify Telegram Mini App ``initData`` so the server can trust *who* is calling.

Why this exists
---------------
The web games (Snake, Othello) run inside Telegram as Mini Apps. Anything the
browser sends in a JSON body (``user_id``, ``user_name`` ...) can be forged by
anyone. Telegram, however, signs ``Telegram.WebApp.initData`` with an HMAC made
from the bot token, so the server can check that the user id inside it is real.

Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

# initData older than this is rejected (a Mini App session rarely lasts longer).
DEFAULT_MAX_AGE = 24 * 60 * 60


def verify_init_data(init_data: str, bot_token: str, max_age: int = DEFAULT_MAX_AGE) -> dict | None:
    """Return the Telegram user dict if ``init_data`` is genuine, otherwise None.

    The returned dict contains at least ``id`` (int) and usually ``first_name``,
    ``last_name`` and ``username``.
    """
    if not init_data or not bot_token:
        return None
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = fields.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age and time.time() - auth_date > max_age:
        return None

    try:
        user = json.loads(fields.get("user", ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return user


def display_name(user: dict) -> str:
    """Best human-readable name for a Telegram user dict."""
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    return (full or user.get("username") or "Player")[:40]
