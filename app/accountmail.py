"""E-mail confirmation and password recovery by a letter (works only when the owner has set up SMTP, see app/mailer.py).
A link in the letter holds a one-time token; only its hash is stored. Recovery works only for a CONFIRMED e-mail, and the answer
to «send me a link» is the same for every address (nobody can find out who is registered)."""
import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, String, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import guard, mailer
from app.account import check_new_password, hash_password, norm_email
from app.audit import log_action
from app.config import settings
from app.database import Base, get_db
from app.models import User, WebSession
from app.payments import aware, now
from app.users import get_or_create_user, writer
from app.webauth import _hash

router = APIRouter()
TTL = {"confirm": dt.timedelta(hours=24), "reset": dt.timedelta(hours=1)}


class EmailToken(Base):
    __tablename__ = "email_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(40), index=True)
    purpose: Mapped[str] = mapped_column(String(10))
    email: Mapped[str] = mapped_column(String(160))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


def _issue(db: Session, user: User, purpose: str) -> str:
    db.execute(delete(EmailToken).where(EmailToken.tg_id == user.tg_id, EmailToken.purpose == purpose))     # the newest link only
    token = secrets.token_urlsafe(32)
    db.add(EmailToken(token_hash=_hash(token), tg_id=user.tg_id, purpose=purpose, email=user.email or "", expires_at=now() + TTL[purpose]))
    db.commit()
    return token


def _take(db: Session, token: str, purpose: str) -> tuple[EmailToken, User]:
    row = db.get(EmailToken, _hash(token or ""))
    if row is None or row.purpose != purpose or aware(row.expires_at) < now():
        raise HTTPException(400, "Ссылка устарела или уже использована. Запросите новую")
    user = db.get(User, row.tg_id)
    if user is None or (user.email or "") != row.email:
        raise HTTPException(400, "Ссылка устарела: e-mail был изменён")
    return row, user


def _link(param: str, token: str) -> str:
    return f"{settings.webapp_url.rstrip('/')}/?{param}={token}"


@router.get("/api/mail-state")
def mail_state():
    return {"enabled": mailer.enabled()}


@router.post("/api/me/account/email-confirm/send")
def send_confirmation(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    if not mailer.enabled():
        raise HTTPException(503, "Почта на сайте пока не подключена")
    user = get_or_create_user(db, user_id)
    if not user.email:
        raise HTTPException(400, "Сначала укажите e-mail в «Вход на сайт»")
    if user.email_verified:
        raise HTTPException(409, "E-mail уже подтверждён")
    guard.rate_limit(user_id, "mail_confirm", 3, 3600)
    token = _issue(db, user, "confirm")
    ok = mailer.send(user.email, "KOREA HUB: подтвердите e-mail",
                     f"Здравствуйте!\n\nЧтобы подтвердить e-mail, откройте ссылку (действует 24 часа):\n{_link('confirm', token)}\n\n"
                     "Если вы не регистрировались на KOREA HUB, просто проигнорируйте письмо.")
    if not ok:
        raise HTTPException(502, "Не удалось отправить письмо. Попробуйте позже")
    return {"sent": True}


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=100)


@router.post("/api/auth/email-confirm")
def confirm(p: TokenIn, db: Session = Depends(get_db)):
    row, user = _take(db, p.token, "confirm")
    user.email_verified = True
    db.delete(row)
    log_action(db, user.tg_id, "account.email_confirmed", "user", user.tg_id, "")
    db.commit()
    return {"ok": True}


class ResetRequest(BaseModel):
    email: str = Field(max_length=160)


@router.post("/api/auth/password-reset/request")
def reset_request(p: ResetRequest, request: Request, db: Session = Depends(get_db)):
    if not mailer.enabled():
        raise HTTPException(503, "Почта на сайте пока не подключена: войдите через Telegram и задайте новый пароль в «Настройки → Вход на сайт»")
    email = norm_email(p.email)
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    guard.rate_limit(f"ip:{forwarded or (request.client.host if request.client else '')}", "pwreset", 5, 3600)
    guard.rate_limit(f"em:{email}", "pwreset", 3, 3600)
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and user.email_verified and not user.blocked and user.deleted_at is None:
        token = _issue(db, user, "reset")
        mailer.send(email, "KOREA HUB: восстановление пароля",
                    f"Здравствуйте!\n\nЧтобы задать новый пароль, откройте ссылку (действует 1 час):\n{_link('reset', token)}\n\n"
                    "Если вы не просили восстановить пароль, ничего не делайте: пароль останется прежним.")
    return {"ok": True}                                           # the same answer for everyone


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=100)
    password: str = Field(max_length=72)


@router.post("/api/auth/password-reset")
def reset_apply(p: ResetIn, db: Session = Depends(get_db)):
    row, user = _take(db, p.token, "reset")
    check_new_password(p.password, user.email or "", user.display_name or "")
    user.password_hash, user.password_set_at, user.pw_fails, user.pw_locked_until = hash_password(p.password), now(), 0, None
    db.execute(delete(WebSession).where(WebSession.tg_id == user.tg_id))            # every browser signs in again with the new password
    db.delete(row)
    log_action(db, user.tg_id, "account.password_reset", "user", user.tg_id, "по письму")
    db.commit()
    return {"ok": True}
