"""Staff editing for companies, resumes and reviews: the same «open → change → save with a reason → journal» as for listings."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import guard, photos
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.company import Company
from app.database import get_db
from app.models import Resume, Review, User
from app.payments import now

router = APIRouter()


def _apply(obj, data: dict) -> list[str]:
    """Sets the given fields and returns «field: was → became» for the journal."""
    changed = []
    for k, v in data.items():
        if v is None:
            continue
        old = getattr(obj, k)
        if old != v:
            changed.append(f"{k}: {str(old)[:40]} → {str(v)[:40]}")
            setattr(obj, k, v)
    return changed


# ---------- companies ----------
class CompanyEdit(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    reg_no: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    website: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=300)
    reason: str = Field(default="", max_length=200)


@router.put("/api/admin/companies/{company_id}/edit")
def edit_company(company_id: int, p: CompanyEdit, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "Компания не найдена")
    changed = _apply(c, p.model_dump(exclude={"reason"}))
    if changed:
        log_action(db, actor, "company.edit", "company", c.id, "; ".join(changed) + (f" · {p.reason}" if p.reason else ""))
        db.commit()
        if p.reason:
            guard.notify_user(db, c.owner_tg_id, f"Модератор поправил данные компании «{c.name}»: {p.reason}", "moderation")
    return {"changed": [x.split(":")[0] for x in changed]}


@router.delete("/api/admin/companies/{company_id}/logo")
def remove_company_logo(company_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "Компания не найдена")
    if c.logo_key:
        photos.delete_files(c.logo_key)
        c.logo_key = None
        log_action(db, actor, "company.logo_remove", "company", c.id, "")
        db.commit()
    return {"ok": True}


# ---------- resumes ----------
@router.get("/api/admin/resumes-all")
def resumes_all(status: str = "all", q: str = Query(default="", max_length=80), actor: str = Depends(staff(*MOD)),
                db: Session = Depends(get_db)):
    stmt = select(Resume).order_by(Resume.updated_at.desc()).limit(100)
    if status != "all":
        stmt = stmt.where(Resume.status == status)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(Resume.search_text.like(like), Resume.owner_tg_id == q.strip(), Resume.city.ilike(like)))
    rows = db.scalars(stmt).all()
    names = {u.tg_id: u.display_name for u in db.scalars(select(User).where(User.tg_id.in_([r.owner_tg_id for r in rows] or ["-"]))).all()}
    return [{"owner": r.owner_tg_id, "name": names.get(r.owner_tg_id, ""), "position": r.position, "city": r.city, "visa": r.visa,
             "experience": r.experience, "about": r.about, "salary_from": r.salary_from, "status": r.status, "note": r.note,
             "updated": r.updated_at.isoformat() if r.updated_at else ""} for r in rows]


class ResumeEdit(BaseModel):
    position: str | None = Field(default=None, min_length=3, max_length=80)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    visa: str | None = Field(default=None, max_length=8)
    experience: str | None = Field(default=None, max_length=300)
    about: str | None = Field(default=None, max_length=700)
    salary_from: int | None = Field(default=None, ge=0, le=100_000_000)
    reason: str = Field(default="", max_length=200)


@router.put("/api/admin/resumes/{owner}/edit")
def edit_resume(owner: str, p: ResumeEdit, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    r = db.get(Resume, owner)
    if r is None:
        raise HTTPException(404, "Резюме не найдено")
    changed = _apply(r, p.model_dump(exclude={"reason"}))
    if changed:
        r.search_text = " ".join([r.position, r.experience, r.about]).lower()
        r.updated_at = now()
        log_action(db, actor, "resume.edit", "resume", owner, "; ".join(changed) + (f" · {p.reason}" if p.reason else ""))
        db.commit()
        if p.reason:
            guard.notify_user(db, owner, f"Модератор поправил ваше резюме: {p.reason}", "moderation")
    return {"changed": [x.split(":")[0] for x in changed]}


@router.delete("/api/admin/resumes/{owner}")
def delete_resume(owner: str, reason: str = Query(min_length=3, max_length=200), actor: str = Depends(staff(*MOD)),
                  db: Session = Depends(get_db)):
    r = db.get(Resume, owner)
    if r is None:
        raise HTTPException(404, "Резюме не найдено")
    db.delete(r)
    log_action(db, actor, "resume.delete", "resume", owner, reason)
    db.commit()
    guard.notify_user(db, owner, f"Ваше резюме удалено модератором: {reason}", "moderation")
    return {"ok": True}


# ---------- reviews ----------
class ReviewEdit(BaseModel):
    text: str | None = Field(default=None, max_length=500)
    reply: str | None = Field(default=None, max_length=500)


@router.put("/api/admin/reviews/{review_id}/edit")
def edit_review(review_id: int, p: ReviewEdit, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """Only the words can be changed (to remove abuse or a phone number); the rating is the buyer's own and stays."""
    row = db.get(Review, review_id)
    if row is None:
        raise HTTPException(404, "Отзыв не найден")
    changed = _apply(row, p.model_dump())
    if changed:
        log_action(db, actor, "review.edit", "review", review_id, "; ".join(changed))
        db.commit()
    return {"changed": [x.split(":")[0] for x in changed]}


@router.delete("/api/admin/reviews/{review_id}")
def delete_review(review_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    row = db.get(Review, review_id)
    if row is None:
        raise HTTPException(404, "Отзыв не найден")
    db.delete(row)
    log_action(db, actor, "review.delete", "review", review_id, f"listing:{row.listing_id}")
    db.commit()
    return {"ok": True}
