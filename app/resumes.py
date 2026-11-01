"""Job seekers: one resume per person, checked by a moderator, searchable by employers.

Privacy: the public card has no name, no Telegram ID and no contacts. An employer who has an approved
vacancy of their own invites the person into a chat about that vacancy; the person decides whether to answer."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import guard
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.database import get_db
from app.fields import VISAS
from app.models import Conversation, Listing, Message, Resume
from app.payments import now
from app.users import is_blocked_pair, writer

router = APIRouter()


class ResumeIn(BaseModel):
    position: str = Field(min_length=3, max_length=80)
    city: str = Field(min_length=2, max_length=100)
    region_code: str | None = Field(default=None, max_length=20)
    visa: str = ""
    experience: str = Field(default="", max_length=300)
    about: str = Field(default="", max_length=700)
    salary_from: int | None = Field(default=None, ge=0, le=100_000_000)


def public(r: Resume) -> dict:
    return {"id": _rid(r), "position": r.position, "city": r.city, "visa": r.visa,
            "experience": r.experience, "about": r.about, "salary_from": r.salary_from,
            "updated": r.updated_at.date().isoformat() if r.updated_at else None}


def _rid(r: Resume) -> str:
    """Opaque id for the public card: hides the Telegram ID, still stable and reversible only on the server."""
    import hashlib
    import hmac
    from app.config import settings
    return hmac.new((settings.bot_token or "dev").encode(), r.owner_tg_id.encode(), hashlib.sha256).hexdigest()[:16]


def _by_public_id(db: Session, rid: str) -> Resume | None:
    for r in db.scalars(select(Resume).where(Resume.status == "approved")).all():
        if _rid(r) == rid:
            return r
    return None


def public_export(db: Session, tg_id: str) -> dict | None:
    r = db.get(Resume, tg_id)
    return None if r is None else {**public(r), "status": r.status}


@router.get("/api/me/resume")
def my_resume(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    r = db.get(Resume, user_id)
    return None if r is None else {**public(r), "status": r.status, "note": r.note, "region_code": r.region_code}


@router.put("/api/me/resume")
def save_resume(payload: ResumeIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    guard.rate_limit(user_id, "resume", 10, 86400)
    if payload.visa and payload.visa not in VISAS:
        raise HTTPException(400, "Неизвестный тип визы")
    guard.check_text(payload.position, payload.about + " " + payload.experience)
    r = db.get(Resume, user_id) or Resume(owner_tg_id=user_id)
    for k, v in payload.model_dump().items():
        setattr(r, k, v.strip() if isinstance(v, str) else v)
    r.search_text = " ".join([r.position, r.experience, r.about]).lower()
    r.status, r.note, r.updated_at = "pending", "", now()    # every edit is checked again
    db.add(r)
    db.commit()
    return {"status": r.status}


@router.post("/api/me/resume/hide")
def hide_resume(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    r = db.get(Resume, user_id)
    if r is None:
        raise HTTPException(404, "Резюме нет")
    r.status = "hidden"
    db.commit()
    return {"status": r.status}


@router.delete("/api/me/resume")
def delete_resume(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    r = db.get(Resume, user_id)
    if r is not None:
        db.delete(r)
        db.commit()
    return {"deleted": True}


@router.get("/api/resumes")
def search_resumes(q: str = Query(default="", max_length=60), city: str = Query(default="", max_length=100),
                   visa: str = Query(default="", max_length=8), limit: int = Query(default=30, ge=1, le=60),
                   user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Employers only: someone with at least one approved vacancy."""
    if not db.scalar(select(func.count()).select_from(Listing).where(
            Listing.author_telegram_id == user_id, Listing.category == "job", Listing.status == "approved")):
        raise HTTPException(403, "Резюме видят работодатели с опубликованной вакансией")
    cond = [Resume.status == "approved", Resume.owner_tg_id != user_id]
    if q.strip():
        cond.append(Resume.search_text.like(f"%{q.strip().lower()}%"))
    if city.strip():
        cond.append(Resume.city == city.strip())
    if visa:
        cond.append(Resume.visa == visa)
    rows = db.scalars(select(Resume).where(*cond).order_by(Resume.updated_at.desc()).limit(limit)).all()
    return [public(r) for r in rows]


class InviteIn(BaseModel):
    listing_id: int
    message: str = Field(default="", max_length=300)


@router.post("/api/resumes/{rid}/invite")
def invite(rid: str, payload: InviteIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    guard.rate_limit(user_id, "invite", 20, 86400)
    listing = db.get(Listing, payload.listing_id)
    if listing is None or listing.author_telegram_id != user_id or listing.category != "job" or listing.status != "approved":
        raise HTTPException(400, "Выберите свою опубликованную вакансию")
    r = _by_public_id(db, rid)
    if r is None or r.owner_tg_id == user_id:
        raise HTTPException(404, "Резюме не найдено")
    if is_blocked_pair(db, user_id, r.owner_tg_id):
        raise HTTPException(403, "Приглашение недоступно")
    text = " ".join(payload.message.split()) or f"Здравствуйте! Приглашаем вас рассмотреть вакансию «{listing.title}»."
    guard.check_text("", text)
    conv = db.scalar(select(Conversation).where(Conversation.listing_id == listing.id, Conversation.buyer_tg_id == r.owner_tg_id))
    if conv is None:
        conv = Conversation(listing_id=listing.id, buyer_tg_id=r.owner_tg_id, seller_tg_id=user_id)
        db.add(conv)
        db.commit()
        db.refresh(conv)
    db.add(Message(conversation_id=conv.id, sender_tg_id=user_id, text=text))
    db.commit()
    guard.notify_user(db, r.owner_tg_id, f"Работодатель приглашает вас на вакансию «{listing.title}». Ответьте в чате.", "messages")
    return {"conversation_id": conv.id}


# ---- staff ----
@router.get("/api/admin/resumes")
def admin_resumes(status: str = Query(default="pending"), actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Resume).where(Resume.status == status).order_by(Resume.updated_at).limit(100)).all()
    return [{"owner": r.owner_tg_id, **public(r), "status": r.status} for r in rows]


@router.post("/api/admin/resumes/{owner}/{decision}")
def admin_resume_decision(owner: str, decision: str, reason: str = Query(default="", max_length=300),
                          actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    mapping = {"approve": "approved", "reject": "rejected", "hide": "hidden"}
    if decision not in mapping:
        raise HTTPException(400, "Решение: approve, reject или hide")
    r = db.get(Resume, owner)
    if r is None:
        raise HTTPException(404, "Резюме не найдено")
    if decision == "reject" and not reason.strip():
        raise HTTPException(400, "Укажите причину отказа")
    r.status, r.note = mapping[decision], (reason.strip() if decision != "approve" else "")
    log_action(db, actor, f"resume.{decision}", "resume", owner, reason)
    db.commit()
    text = {"approve": "Ваше резюме опубликовано: работодатели могут его найти.",
            "reject": f"Резюме не прошло проверку: {reason.strip()}", "hide": "Ваше резюме скрыто модератором."}[decision]
    guard.notify_user(db, owner, text, "moderation")
    return {"status": r.status}
