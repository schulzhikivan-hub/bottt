"""Telegram Mini App authentication.

The Mini App sends `initData` (a signed query string). The server verifies the
HMAC signature with the bot token and takes the user ID from the verified data.
Client-supplied user IDs are never trusted.
See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from app.config import settings

MAX_AGE_SECONDS = 24 * 60 * 60


def validate_init_data(init_data: str, bot_token: str, now: float | None = None) -> dict:
    """Return the verified Telegram user dict, or raise ValueError."""
    if not init_data or not bot_token:
        raise ValueError("missing initData or bot token")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("hash is missing")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("bad signature")

    current = time.time() if now is None else now
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise ValueError("bad auth_date") from exc
    if current - auth_date > MAX_AGE_SECONDS:
        raise ValueError("initData expired")

    user = json.loads(pairs.get("user", "{}"))
    if "id" not in user:
        raise ValueError("user is missing")
    return user


def current_user_id(x_telegram_init_data: str | None = Header(default=None)) -> str:
    """FastAPI dependency: the verified Telegram user ID as a string."""
    try:
        user = validate_init_data(x_telegram_init_data or "", settings.bot_token)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Откройте приложение из Telegram")
    return str(user["id"])
