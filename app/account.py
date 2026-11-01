"""Sign-in on the website with e-mail and password, set up once after the first sign-in through Telegram.

Why: Telegram stays the way to create an account and to recover it, but people also want to open the site from any
browser without a bot round trip. The first Telegram sign-in asks for a name, an e-mail and a password (it can be postponed);
later the website accepts «e-mail + password».

Rules
- the password is never stored, only a salted PBKDF2 hash; it must be 8-72 characters with a letter and a digit;
- a wrong password is answered the same way as an unknown e-mail (no way to find out who is registered);
- 8 attempts per 15 minutes per address and per e-mail; 10 failures in a row lock the account for 15 minutes;
- changing the password needs the old one; it closes all other website sessions;
- forgot the password: sign in through Telegram and set a new one (no e-mail is sent, so none can be intercepted);
- the e-mail is not confirmed by a letter (the server sends no mail yet): it is only a login name of an account that was
  already proved by Telegram. `email_verified` is kept for the day mail is switched on;
- administrators open the admin panel only through Telegram, never by password."""
import datetime as dt
import hashlib
import hmac
import re
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import guard, inbox
from app.admin_auth import ADM, staff
from app.audit import log_action
from app.auth import current_user_id
from app.database import get_db
from app.models import User, WebSession
from app.payments import aware, now
from app.users import get_or_create_user, writer
from app.webauth import WEB_SESSION_TTL, _hash

router = APIRouter()
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
ITER = 200_000
LOCK_AFTER, LOCK_FOR = 10, dt.timedelta(minutes=15)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITER).hex()
    return f"pbkdf2_sha256${ITER}${salt}${digest}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        algo, iters, salt, digest = (stored or "").split("$")
        probe = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters)).hex()
        return algo == "pbkdf2_sha256" and hmac.compare_digest(probe, digest)
    except (ValueError, TypeError):
        hmac.compare_digest("x", "y")                         # keep the time about the same for a broken record
        return False


def norm_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not EMAIL.match(email) or len(email) > 160:
        raise HTTPException(400, "Проверьте e-mail: он должен выглядеть как name@example.com")
    return email


def check_new_password(password: str, email: str = "", name: str = "") -> None:
    if not 8 <= len(password) <= 72:
        raise HTTPException(400, "Пароль: от 8 до 72 символов")
    if not (re.search(r"[A-Za-zА-Яа-я]", password) and re.search(r"\d", password)):
        raise HTTPException(400, "В пароле нужны и буквы, и цифры")
    if password.lower() in {email, name.lower()} or (email and password.lower() == email.split("@")[0]):
        raise HTTPException(400, "Пароль не должен совпадать с e-mail или именем")


def view(user: User) -> dict:
    return {"name": user.display_name, "email": user.email or "", "has_password": bool(user.password_hash),
            "email_verified": bool(user.email_verified), "skipped": bool(user.signup_skipped),
            "needs_setup": user.consent_at is not None and not user.signup_skipped and not (user.email and user.password_hash)}


@router.get("/api/me/account")
def get_account(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return view(get_or_create_user(db, user_id))


class AccountIn(BaseModel):
    display_name: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=160)
    password: str = Field(default="", max_length=72)
    current_password: str = Field(default="", max_length=72)


@router.put("/api/me/account")
def save_account(payload: AccountIn, user_id: str = Depends(writer), db: Session = Depends(get_db),
                 x_web_session: str | None = Header(default=None)):
    user = get_or_create_user(db, user_id)
    guard.rate_limit(user_id, "account_save", 10, 3600)
    name = " ".join(payload.display_name.split())
    if name:
        if inbox.NAME_BAD.search(name):
            raise HTTPException(400, "В имени нельзя писать ссылки, @username и телефон")
        user.display_name = name
    if payload.email:
        email = norm_email(payload.email)
        taken = db.scalar(select(User.tg_id).where(User.email == email, User.tg_id != user_id))
        if taken is not None:
            raise HTTPException(409, "Этот e-mail уже занят")
        if user.password_hash and email != (user.email or "") and not check_password(payload.current_password, user.password_hash):
            raise HTTPException(403, "Чтобы сменить e-mail, введите текущий пароль")
        if email != user.email:
            user.email, user.email_verified = email, False
    if payload.password:
        if not user.email:
            raise HTTPException(400, "Сначала укажите e-mail")
        check_new_password(payload.password, user.email, user.display_name or "")
        if user.password_hash and not check_password(payload.current_password, user.password_hash):
            raise HTTPException(403, "Текущий пароль указан неверно")
        user.password_hash, user.password_set_at, user.pw_fails, user.pw_locked_until = hash_password(payload.password), now(), 0, None
        keep = _hash(x_web_session) if x_web_session else ""
        db.execute(delete(WebSession).where(WebSession.tg_id == user_id, WebSession.token_hash != keep))   # other browsers sign in again
    if user.email and user.password_hash:
        user.signup_skipped = False
    db.commit()
    return view(user)


@router.post("/api/me/account/skip")
def skip_setup(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    user.signup_skipped = True
    db.commit()
    return view(user)


class PasswordLogin(BaseModel):
    email: str = Field(max_length=160)
    password: str = Field(max_length=72)


@router.post("/api/auth/password-login")
def password_login(payload: PasswordLogin, request: Request, db: Session = Depends(get_db)):
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else "")
    email = (payload.email or "").strip().lower()
    guard.rate_limit(f"ip:{ip}", "pwlogin", 8, 900)
    guard.rate_limit(f"em:{email[:80]}", "pwlogin", 8, 900)
    wrong = HTTPException(401, "Неверный e-mail или пароль")
    user = db.scalar(select(User).where(User.email == email)) if email else None
    if user is None or not user.password_hash:
        check_password(payload.password, None)
        raise wrong
    if user.pw_locked_until is not None and aware(user.pw_locked_until) > now():
        raise HTTPException(429, "Слишком много неверных попыток. Попробуйте через 15 минут или войдите через Telegram")
    if not check_password(payload.password, user.password_hash):
        user.pw_fails = (user.pw_fails or 0) + 1
        if user.pw_fails >= LOCK_AFTER:
            user.pw_locked_until, user.pw_fails = now() + LOCK_FOR, 0
        db.commit()
        raise wrong
    if user.blocked or user.deleted_at is not None:
        raise HTTPException(403, "Аккаунт недоступен")
    user.pw_fails, user.pw_locked_until = 0, None
    session = secrets.token_urlsafe(32)
    db.add(WebSession(token_hash=_hash(session), tg_id=user.tg_id, expires_at=now() + WEB_SESSION_TTL))
    db.commit()
    return {"status": "ok", "session": session, "tg_id": user.tg_id}


# ---------- administrators ----------
class ResetIn(BaseModel):
    what: str = Field(pattern="^(password|email|both)$")
    reason: str = Field(min_length=3, max_length=200)


@router.post("/api/admin/users/{tg_id}/account-reset")
def admin_reset(tg_id: str, payload: ResetIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """The person lost access to the site login or the e-mail is someone else's: clear it. They can set it again after a Telegram sign-in."""
    user = db.get(User, tg_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    if payload.what in {"password", "both"}:
        user.password_hash, user.password_set_at, user.pw_fails, user.pw_locked_until = None, None, 0, None
    if payload.what in {"email", "both"}:
        user.email, user.email_verified = None, False
        user.password_hash = None                              # a password without an e-mail is useless
    user.signup_skipped = False
    db.execute(delete(WebSession).where(WebSession.tg_id == tg_id))
    log_action(db, actor, "user.account_reset", "user", tg_id, f"{payload.what}: {payload.reason}")
    db.commit()
    guard.notify_user(db, tg_id, "Данные для входа на сайт сброшены поддержкой. Войдите через Telegram и задайте их заново.", "moderation")
    return view(user)
