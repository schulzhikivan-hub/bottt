"""Automatic sign-in.

Inside Telegram nothing is needed: the Mini App proves who you are with signed initData.
On the website (a normal browser) the user presses "Sign in with Telegram":
1. the site asks for a sign-in link and shows it (a t.me link to the bot);
2. the bot shows where the request came from and asks "Is this you?";
3. after the user confirms, the site receives its session by itself.
Administrators who open the panel from the bot inside Telegram are signed in automatically.
"""
import datetime as dt
import hashlib
import json
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import guard, payments
from app.audit import log_action
from app.auth import validate_init_data
from app.config import settings
from app.database import get_db
from app.models import Admin, AdminSession, LoginRequest, WebSession
from app.payments import PaymentError, now, aware

router = APIRouter()
LOGIN_TTL = dt.timedelta(minutes=5)
WEB_SESSION_TTL = dt.timedelta(days=30)
ADMIN_SESSION_TTL = dt.timedelta(hours=12)
_username: dict[str, str] = {}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bot_username() -> str:
    if settings.bot_username:
        return settings.bot_username.lstrip("@")
    if "name" not in _username:
        _username["name"] = payments.telegram_call("getMe", {})["username"]
    return _username["name"]


# ---- helpers used by the bot ----

def login_request(db: Session, token: str) -> LoginRequest | None:
    """A pending, not expired request for this token, or None."""
    row = db.get(LoginRequest, _hash(token))
    if row is None or row.status != "pending" or aware(row.expires_at) < now():
        return None
    return row


def confirm_login(db: Session, token: str, tg_id: str) -> bool:
    row = login_request(db, token)
    if row is None:
        return False
    row.status = "confirmed"
    row.tg_id = tg_id
    db.commit()
    return True


def reject_login(db: Session, token: str) -> bool:
    row = login_request(db, token)
    if row is None:
        return False
    row.status = "rejected"
    db.commit()
    return True


# ---- website sign-in ----

@router.post("/api/auth/start")
def start_login(request: Request, db: Session = Depends(get_db)):
    guard.rate_limit("web", "login_start", 60, 60)
    db.execute(delete(LoginRequest).where(LoginRequest.expires_at < now() - dt.timedelta(hours=1)))
    token = secrets.token_urlsafe(24)
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else "")
    db.add(LoginRequest(token_hash=_hash(token), status="pending", ip=ip[:60],
                        agent=request.headers.get("user-agent", "")[:120],
                        expires_at=now() + LOGIN_TTL, created_at=now()))
    db.commit()
    try:
        name = bot_username()
    except (PaymentError, KeyError):
        raise HTTPException(503, "Бот сейчас недоступен. Попробуйте позже")
    return {"token": token, "url": f"https://t.me/{name}?start=login_{token}"}


@router.get("/api/auth/poll/{token}")
def poll_login(token: str, db: Session = Depends(get_db)):
    row = db.get(LoginRequest, _hash(token))
    if row is None or aware(row.expires_at) < now() or row.status == "used":
        return {"status": "expired"}
    if row.status == "pending":
        return {"status": "pending"}
    if row.status == "rejected":
        return {"status": "rejected"}
    # confirmed: hand over the session once
    row.status = "used"
    session = secrets.token_urlsafe(32)
    db.add(WebSession(token_hash=_hash(session), tg_id=row.tg_id, expires_at=now() + WEB_SESSION_TTL))
    result = {"status": "ok", "session": session, "tg_id": row.tg_id}
    admin = db.get(Admin, row.tg_id)
    if admin is not None and admin.active:
        admin_token = secrets.token_urlsafe(32)
        db.add(AdminSession(token_hash=_hash(admin_token), tg_id=row.tg_id,
                            expires_at=now() + ADMIN_SESSION_TTL))
        log_action(db, row.tg_id, "auth.login", "admin", row.tg_id, "через бота на сайте")
        result.update({"admin_session": admin_token, "role": admin.role})
    db.commit()
    return result


@router.post("/api/auth/logout")
def logout_web(x_web_session: str | None = Header(default=None), db: Session = Depends(get_db)):
    if x_web_session:
        row = db.get(WebSession, _hash(x_web_session))
        if row is not None:
            db.delete(row)
            db.commit()
    return {"ok": True}


# ---- administrators opening the panel inside Telegram ----

@router.post("/api/admin/auth/telegram")
def admin_auto_login(x_telegram_init_data: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    try:
        user = validate_init_data(x_telegram_init_data or "", settings.bot_token)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Откройте панель из Telegram")
    tg = str(user["id"])
    admin = db.get(Admin, tg)
    if admin is None or not admin.active:
        raise HTTPException(403, "У вас нет доступа к админ-панели")
    token = secrets.token_urlsafe(32)
    db.add(AdminSession(token_hash=_hash(token), tg_id=tg, expires_at=now() + ADMIN_SESSION_TTL))
    log_action(db, tg, "auth.login", "admin", tg, "из Telegram")
    db.commit()
    return {"token": token, "role": admin.role, "tg_id": tg}
