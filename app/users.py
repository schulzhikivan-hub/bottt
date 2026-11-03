"""User helpers shared by API modules: account state, consent and blocks."""
from fastapi import Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.database import get_db
from app.models import User, Block


def get_or_create_user(db: Session, tg_id: str) -> User:
    user = db.get(User, tg_id)
    if user is None:
        user = User(tg_id=tg_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def writer(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> str:
    """Dependency for actions that change data: account must be active and consented."""
    user = get_or_create_user(db, user_id)
    if user.deleted_at is not None or user.blocked:
        raise HTTPException(403, "Аккаунт недоступен")
    if user.consent_at is None:
        raise HTTPException(403, "Сначала примите пользовательское соглашение в приложении")
    return user_id


def is_blocked_pair(db: Session, a: str, b: str) -> bool:
    return db.scalar(select(Block.id).where(or_(
        (Block.blocker_tg_id == a) & (Block.blocked_tg_id == b),
        (Block.blocker_tg_id == b) & (Block.blocked_tg_id == a),
    )).limit(1)) is not None
