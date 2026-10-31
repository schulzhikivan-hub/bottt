"""Staff sign-in for the admin panel: a one-time code is sent by the bot to the administrator's Telegram.

Roles (ТЗ § 3):
- moderator: moderation, reports, dating, employer applications, user blocks;
- admin: everything a moderator does, plus prices, payments, statistics, content and the journal;
- super_admin: everything, plus managing staff accounts.
"""
import datetime as dt
import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from app import guard
from app.audit import log_action
from app.config import settings
from app.database import get_db
from app.models import Admin, AdminCode, AdminSession
from app.payments import now, aware

router = APIRouter()
ROLES = ("moderator", "admin", "super_admin")
MOD = ("moderator", "admin", "super_admin")
ADM = ("admin", "super_admin")
SUP = ("super_admin",)
CODE_TTL = dt.timedelta(minutes=5)
SESSION_TTL = dt.timedelta(hours=12)
MAX_ATTEMPTS = 5


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bootstrap_admins(db: Session) -> None:
    """ADMIN_IDS from the environment are always super administrators."""
    for tg in [x.strip() for x in settings.admin_ids.split(",") if x.strip().isdigit()]:
        if db.get(Admin, tg) is None:
            db.add(Admin(tg_id=tg, role="super_admin", active=True, created_at=now()))
    db.commit()


def staff(*roles: str):
    """Dependency: a valid admin session whose role is allowed. Returns the administrator's Telegram ID."""
    def dependency(x_admin_session: str | None = Header(default=None),
                   db: Session = Depends(get_db)) -> str:
        if not x_admin_session:
            raise HTTPException(401, "Войдите в админ-панель")
        session = db.get(AdminSession, _hash(x_admin_session))
        if session is None or aware(session.expires_at) < now():
            raise HTTPException(401, "Сессия истекла. Войдите снова")
        admin = db.get(Admin, session.tg_id)
        if admin is None or not admin.active:
            raise HTTPException(401, "Доступ отключён")
        if admin.role not in roles:
            raise HTTPException(403, "Недостаточно прав для этого действия")
        return session.tg_id
    return dependency


class CodeRequest(BaseModel):
    tg_id: str = Field(min_length=3, max_length=20)


class CodeVerify(BaseModel):
    tg_id: str = Field(min_length=3, max_length=20)
    code: str = Field(min_length=6, max_length=6)


@router.post("/api/admin/auth/request")
def request_code(payload: CodeRequest, db: Session = Depends(get_db)):
    tg = payload.tg_id.strip()
    guard.rate_limit(tg, "admin_code", 5, 600)
    admin = db.get(Admin, tg)
    if admin is not None and admin.active:
        code = f"{secrets.randbelow(10 ** 6):06d}"
        db.execute(delete(AdminCode).where(AdminCode.tg_id == tg))
        db.add(AdminCode(tg_id=tg, code_hash=_hash(code), expires_at=now() + CODE_TTL, attempts=0))
        db.commit()
        guard.send_telegram(tg, f"Код входа в админ-панель KOREA HUB: {code}\n"
                                "Действует 5 минут. Никому не сообщайте.")
    # Одинаковый ответ для всех, чтобы не показывать, кто администратор
    return {"sent": True, "hint": "Если код не пришёл, откройте бота в Telegram и нажмите /start"}


@router.post("/api/admin/auth/verify")
def verify_code(payload: CodeVerify, db: Session = Depends(get_db)):
    tg = payload.tg_id.strip()
    row = db.scalar(select(AdminCode).where(AdminCode.tg_id == tg))
    invalid = HTTPException(401, "Код неверный или истёк. Запросите новый")
    if row is None or aware(row.expires_at) < now() or row.attempts >= MAX_ATTEMPTS:
        if row is not None:
            db.delete(row)
            db.commit()
        raise invalid
    if not hmac.compare_digest(row.code_hash, _hash(payload.code)):
        row.attempts += 1
        db.commit()
        raise invalid
    db.delete(row)
    admin = db.get(Admin, tg)
    if admin is None or not admin.active:
        db.commit()
        raise invalid
    token = secrets.token_urlsafe(32)
    db.add(AdminSession(token_hash=_hash(token), tg_id=tg, expires_at=now() + SESSION_TTL))
    log_action(db, tg, "auth.login", "admin", tg, admin.role)
    db.commit()
    return {"token": token, "role": admin.role, "tg_id": tg}


@router.post("/api/admin/auth/logout")
def logout(x_admin_session: str | None = Header(default=None), db: Session = Depends(get_db)):
    if x_admin_session:
        session = db.get(AdminSession, _hash(x_admin_session))
        if session is not None:
            db.delete(session)
            db.commit()
    return {"ok": True}


@router.get("/api/admin/me")
def me(actor: str = Depends(staff(*ROLES)), db: Session = Depends(get_db)):
    admin = db.get(Admin, actor)
    return {"tg_id": admin.tg_id, "role": admin.role}


class AdminRoleIn(BaseModel):
    role: str
    active: bool = True


@router.get("/api/admin/admins")
def list_admins(actor: str = Depends(staff(*SUP)), db: Session = Depends(get_db)):
    return [{"tg_id": a.tg_id, "role": a.role, "active": a.active}
            for a in db.scalars(select(Admin).order_by(Admin.created_at)).all()]


@router.put("/api/admin/admins/{tg_id}")
def set_admin(tg_id: str, payload: AdminRoleIn, actor: str = Depends(staff(*SUP)),
              db: Session = Depends(get_db)):
    if payload.role not in ROLES:
        raise HTTPException(400, "Роль: moderator, admin или super_admin")
    if tg_id == actor and (not payload.active or payload.role != "super_admin"):
        raise HTTPException(400, "Нельзя лишить доступа самого себя")
    admin = db.get(Admin, tg_id)
    if admin is None:
        if not tg_id.isdigit():
            raise HTTPException(400, "Укажите числовой Telegram ID")
        admin = Admin(tg_id=tg_id, role=payload.role, active=payload.active, created_at=now())
        db.add(admin)
    else:
        admin.role = payload.role
        admin.active = payload.active
    log_action(db, actor, "admin.set", "admin", tg_id, f"{payload.role} active={payload.active}")
    db.commit()
    return {"tg_id": tg_id, "role": admin.role, "active": admin.active}
