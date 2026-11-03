"""Invitations: every user has a personal link t.me/<bot>?start=ref_<code>. The inviter is told when a new person
arrives through it and sees the count. No reward is paid yet: the owner decides the bonus (see docs/CONTENT.md)."""
import secrets

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import guard
from app.database import get_db
from app.models import Referral, User
from app.users import get_or_create_user, writer

router = APIRouter()


def code_for(db: Session, user: User) -> str:
    if not user.ref_code:
        for _ in range(5):
            candidate = secrets.token_hex(4)
            if db.scalar(select(User.tg_id).where(User.ref_code == candidate)) is None:
                user.ref_code = candidate
                db.commit()
                break
    return user.ref_code or ""


def register(db: Session, invited_tg: str, code: str) -> bool:
    """Called by the bot on /start ref_<code>. Counts only a person who has no account yet and is not the inviter."""
    inviter = db.scalar(select(User).where(User.ref_code == code))
    if inviter is None or inviter.tg_id == invited_tg or inviter.blocked or inviter.deleted_at is not None:
        return False
    if db.get(User, invited_tg) is not None or db.get(Referral, invited_tg) is not None:
        return False
    db.add(Referral(invited_tg_id=invited_tg, inviter_tg_id=inviter.tg_id))
    db.commit()
    guard.notify_user(db, inviter.tg_id, "По вашей ссылке пришёл новый участник. Спасибо, что рассказываете о KOREA HUB!", "messages")
    return True


@router.get("/api/me/referral")
def my_referral(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    count = db.scalar(select(func.count()).select_from(Referral).where(Referral.inviter_tg_id == user_id)) or 0
    return {"code": code_for(db, user), "invited": int(count)}
