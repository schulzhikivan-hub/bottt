"""Chat conveniences: pin an important conversation to the top, and keep your own quick replies."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import company
from app.database import Base, get_db
from app.guard import check_text
from app.models import Conversation, Listing
from app.payments import now
from app.users import writer

router = APIRouter()
MAX_PINS, MAX_REPLIES = 5, 12


class ConvPin(Base):
    __tablename__ = "conv_pins"
    conv_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class QuickReply(Base):
    __tablename__ = "quick_replies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    text: Mapped[str] = mapped_column(String(200))


def pinned_ids(db: Session, user_id: str) -> set[int]:
    return set(db.scalars(select(ConvPin.conv_id).where(ConvPin.user_tg_id == user_id)).all())


def _mine(db: Session, conv_id: int, user_id: str) -> Conversation:
    c = db.get(Conversation, conv_id)
    ok = c is not None and (user_id in {c.buyer_tg_id, c.seller_tg_id} or (
        c.listing_id and (lst := db.get(Listing, c.listing_id)) is not None and lst.company_id and company.role_of(db, lst.company_id, user_id) in company.WRITE))
    if not ok:
        raise HTTPException(404, "Диалог не найден")
    return c


@router.post("/api/conversations/{conv_id}/pin")
def pin(conv_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    _mine(db, conv_id, user_id)
    if db.get(ConvPin, (conv_id, user_id)) is None:
        if (db.scalar(select(func.count()).select_from(ConvPin).where(ConvPin.user_tg_id == user_id)) or 0) >= MAX_PINS:
            raise HTTPException(409, f"Закрепить можно не больше {MAX_PINS} диалогов")
        db.add(ConvPin(conv_id=conv_id, user_tg_id=user_id))
        db.commit()
    return {"pinned": True}


@router.delete("/api/conversations/{conv_id}/pin")
def unpin(conv_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    row = db.get(ConvPin, (conv_id, user_id))
    if row is not None:
        db.delete(row)
        db.commit()
    return {"pinned": False}


class ReplyIn(BaseModel):
    text: str = Field(min_length=2, max_length=200)


@router.get("/api/me/quick-replies")
def my_replies(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    return [{"id": r.id, "text": r.text} for r in db.scalars(select(QuickReply).where(QuickReply.user_tg_id == user_id).order_by(QuickReply.id)).all()]


@router.post("/api/me/quick-replies", status_code=201)
def add_reply(p: ReplyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    text = p.text.strip()
    check_text(text, "")
    mine = db.scalars(select(QuickReply).where(QuickReply.user_tg_id == user_id)).all()
    if any(r.text == text for r in mine):
        raise HTTPException(409, "Такой ответ уже есть")
    if len(mine) >= MAX_REPLIES:
        raise HTTPException(409, f"Своих быстрых ответов не больше {MAX_REPLIES}")
    r = QuickReply(user_tg_id=user_id, text=text)
    db.add(r)
    db.commit()
    return {"id": r.id, "text": r.text}


@router.delete("/api/me/quick-replies/{reply_id}")
def delete_reply(reply_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    r = db.get(QuickReply, reply_id)
    if r is None or r.user_tg_id != user_id:
        raise HTTPException(404, "Ответ не найден")
    db.delete(r)
    db.commit()
    return {"ok": True}
