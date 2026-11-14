"""Validate Telegram Mini App initData using the official HMAC flow.

See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InvalidInitData(ValueError):
    pass


def verify_init_data(
    raw: str, bot_token: str, expected_user_id: int,
    *, now: int | None = None, max_age: int = 3600,
) -> dict:
    if not raw or len(raw) > 8192:
        raise InvalidInitData("Missing or oversized Telegram initData")
    try:
        parts = parse_qsl(raw, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=32)
    except ValueError as exc:
        raise InvalidInitData("Malformed initData") from exc
    data = dict(parts)
    if len(parts) != len(data):
        raise InvalidInitData("Duplicate initData fields")
    supplied = data.pop("hash", None)
    if not supplied or len(supplied) != 64:
        raise InvalidInitData("Missing initData hash")
    # Telegram's Ed25519 third-party 'signature' is not part of bot HMAC check.
    data.pop("signature", None)
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"),
                      hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise InvalidInitData("Invalid Telegram signature")
    try:
        issued = int(data["auth_date"])
        user = json.loads(data["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidInitData("Invalid Telegram user or time") from exc
    current = int(time.time()) if now is None else now
    if issued > current + 30 or current - issued > max_age:
        raise InvalidInitData("Telegram session expired")
    if user_id != expected_user_id:
        raise InvalidInitData("Not the owner")
    return user
