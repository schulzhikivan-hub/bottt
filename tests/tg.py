"""Helpers to build Telegram-signed initData for tests (mirrors Telegram's algorithm)."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

TEST_BOT_TOKEN = "123456:TEST-TOKEN"


def make_init_data(user_id: int, bot_token: str = TEST_BOT_TOKEN, auth_date: float | None = None) -> str:
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAHtest",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":"), ensure_ascii=False),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def as_user(user_id: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}
