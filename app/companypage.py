"""The public face of a company: a tagline, contacts, address, what it offers; and reviews by people who really dealt with it
(they applied to a vacancy or wrote to the company)."""
import datetime as dt

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import company, photos
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.database import Base, get_db
from app.guard import check_text
from app.models import Application, Conversation, Listing
from app.payments import now
from app.users import writer

router = APIRouter()
BENEFITS = {"housing": "Жильё", "meals": "Питание", "insurance": "Страховка", "transport": "Развозка", "visa": "Помощь с визой",
            "training": "Обучение", "russian": "Говорят по-русски", "contract": "Официальный договор"}


class CompanyPage(Base):
    __tablename__ = "company_pages"
    company_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tagline: Mapped[str] = mapped_column(String(120), default="")
    telegram: Mapped[str] = mapped_column(String(80), default="")
    instagram: Mapped[str] = mapped_column(String(80), default="")
    kakao: Mapped[str] = mapped_column(String(80), default="")
    address: Mapped[str] = mapped_column(String(160), default="")
    hours: Mapped[str] = mapped_column(String(80), default="")
    benefits: Mapped[str] = mapped_column(String(200), default="")      # keys of BENEFITS, comma separated
    cover_key: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CompanyReview(Base):
    __tablename__ = "company_reviews"
    __table_args__ = (UniqueConstraint("company_id", "reviewer_tg_id", name="uq_company_review"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, index=True)
    reviewer_tg_id: Mapped[str] = mapped_column(String(40))
    rating: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(500), default="")
    reply: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(12), default="published")        # published | hidden
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


def _page_out(p: CompanyPage | None) -> dict:
    p = p or CompanyPage(company_id=0)
    return {"cover": photos.url(p.cover_key) if p.cover_key else None, "tagline": p.tagline or "", "telegram": p.telegram or "", "instagram": p.instagram or "", "kakao": p.kakao or "",
            "address": p.address or "", "hours": p.hours or "", "benefits": [b for b in (p.benefits or "").split(",") if b in BENEFITS]}


def summary(db: Session, company_id: int) -> dict:
    rows = db.scalars(select(CompanyReview).where(CompanyReview.company_id == company_id, CompanyReview.status == "published")
                      .order_by(CompanyReview.id.desc())).all()
    avg = round(sum(r.rating for r in rows) / len(rows), 1) if rows else 0
    return {"rating": avg, "count": len(rows), "items": [{"id": r.id, "rating": r.rating, "text": r.text, "reply": r.reply,
                                                         "at": r.created_at.date().isoformat()} for r in rows[:10]]}


def public_extra(db: Session, company_id: int) -> dict:
    return {"page": _page_out(db.get(CompanyPage, company_id)), "benefits_titles": BENEFITS, "reviews": summary(db, company_id)}


class PageIn(BaseModel):
    tagline: str = Field(default="", max_length=120)
    telegram: str = Field(default="", max_length=80)
    instagram: str = Field(default="", max_length=80)
    kakao: str = Field(default="", max_length=80)
    address: str = Field(default="", max_length=160)
    hours: str = Field(default="", max_length=80)
    benefits: list[str] = []


@router.get("/api/company/{company_id}/page")
def get_page(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id)
    return {**_page_out(db.get(CompanyPage, c.id)), "all_benefits": BENEFITS}


@router.put("/api/company/{company_id}/page")
def set_page(company_id: int, p: PageIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.TEAM)
    check_text(p.tagline, p.address)
    row = db.get(CompanyPage, c.id) or CompanyPage(company_id=c.id)
    row.tagline, row.address, row.hours = p.tagline.strip(), p.address.strip(), p.hours.strip()
    row.telegram, row.instagram, row.kakao = p.telegram.strip().lstrip("@"), p.instagram.strip().lstrip("@"), p.kakao.strip()
    row.benefits = ",".join(b for b in dict.fromkeys(p.benefits) if b in BENEFITS)
    db.add(row)
    log_action(db, user_id, "company.edit", "company", c.id, "публичная страница")
    db.commit()
    return _page_out(row)


def _dealt_with(db: Session, company_id: int, user_id: str) -> bool:
    ids = select(Listing.id).where(Listing.company_id == company_id)
    if db.scalar(select(func.count()).select_from(Application).where(Application.applicant_tg_id == user_id, Application.listing_id.in_(ids))):
        return True
    return bool(db.scalar(select(func.count()).select_from(Conversation).where(Conversation.buyer_tg_id == user_id, Conversation.listing_id.in_(ids))))


@router.get("/api/companies/{company_id}/review-state")
def review_state(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    mine = db.scalar(select(CompanyReview).where(CompanyReview.company_id == company_id, CompanyReview.reviewer_tg_id == user_id))
    can = mine is None and company.role_of(db, company_id, user_id) is None and _dealt_with(db, company_id, user_id)
    return {"can": can, "mine": {"rating": mine.rating, "text": mine.text} if mine else None}


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=500)


@router.post("/api/companies/{company_id}/reviews", status_code=201)
def add_review(company_id: int, p: ReviewIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c = db.get(company.Company, company_id)
    if c is None or c.status != "verified":
        raise HTTPException(404, "Компания не найдена")
    if company.role_of(db, c.id, user_id) is not None:
        raise HTTPException(403, "Отзыв о своей компании оставить нельзя")
    if not _dealt_with(db, c.id, user_id):
        raise HTTPException(403, "Отзыв может оставить тот, кто откликался на вакансию компании или писал ей")
    if db.scalar(select(CompanyReview.id).where(CompanyReview.company_id == c.id, CompanyReview.reviewer_tg_id == user_id)):
        raise HTTPException(409, "Вы уже оставили отзыв")
    check_text(p.text, "")
    db.add(CompanyReview(company_id=c.id, reviewer_tg_id=user_id, rating=p.rating, text=p.text.strip()))
    db.commit()
    return summary(db, c.id)


class ReplyIn(BaseModel):
    text: str = Field(min_length=2, max_length=500)


@router.post("/api/company/{company_id}/reviews/{review_id}/reply")
def reply(company_id: int, review_id: int, p: ReplyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.TEAM)
    r = db.get(CompanyReview, review_id)
    if r is None or r.company_id != c.id:
        raise HTTPException(404, "Отзыв не найден")
    if r.reply:
        raise HTTPException(409, "Ответ уже есть")
    check_text(p.text, "")
    r.reply = p.text.strip()
    db.commit()
    return {"reply": r.reply}


@router.get("/api/admin/company-reviews")
def admin_list(status: str = "published", actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    rows = db.scalars(select(CompanyReview).where(CompanyReview.status == status).order_by(CompanyReview.id.desc()).limit(100)).all()
    names = {c.id: c.name for c in db.scalars(select(company.Company).where(company.Company.id.in_([r.company_id for r in rows] or [0]))).all()}
    return [{"id": r.id, "company_id": r.company_id, "company": names.get(r.company_id, ""), "reviewer": r.reviewer_tg_id, "rating": r.rating,
             "text": r.text, "reply": r.reply, "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/api/admin/company-reviews/{review_id}/{decision}")
def admin_decide(review_id: int, decision: str, db: Session = Depends(get_db), actor: str = Depends(staff(*MOD))):
    if decision not in {"hide", "show"}:
        raise HTTPException(400, "Решение: hide или show")
    r = db.get(CompanyReview, review_id)
    if r is None:
        raise HTTPException(404, "Отзыв не найден")
    r.status = "hidden" if decision == "hide" else "published"
    log_action(db, actor, f"company_review.{decision}", "company", r.company_id, f"отзыв #{review_id}")
    db.commit()
    return {"status": r.status}


@router.post("/api/company/{company_id}/cover", status_code=201)
async def set_cover(company_id: int, file: UploadFile = File(...), user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """A wide picture at the top of the public page (cropped to 1200×500)."""
    import io
    import uuid
    from PIL import Image, ImageOps
    c, role = company._company(db, company_id, user_id, company.TEAM)
    data = await file.read(photos.MAX_UPLOAD_BYTES + 1)
    if len(data) > photos.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Фото больше 8 МБ")
    full, _thumb = photos.process_image(data)
    img = ImageOps.fit(Image.open(io.BytesIO(full)).convert("RGB"), (1200, 500))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    key = uuid.uuid4().hex
    (photos.photo_root() / f"{key}.jpg").write_bytes(out.getvalue())
    row = db.get(CompanyPage, c.id) or CompanyPage(company_id=c.id)
    if row.cover_key:
        photos.delete_files(row.cover_key)
    row.cover_key = key
    db.add(row)
    log_action(db, user_id, "company.edit", "company", c.id, "обложка")
    db.commit()
    return {"cover": photos.url(key)}


@router.delete("/api/company/{company_id}/cover")
def remove_cover(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.TEAM)
    row = db.get(CompanyPage, c.id)
    if row is not None and row.cover_key:
        photos.delete_files(row.cover_key)
        row.cover_key = None
        db.commit()
    return {"cover": None}


# ---------- ready-made replies of the company (for applicants and for chats) ----------
KINDS = {"invite": "Приглашение", "reject": "Отказ", "chat": "Для чата"}
MAX_REPLY_TEMPLATES = 20


class CompanyReply(Base):
    __tablename__ = "company_replies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="chat")
    text: Mapped[str] = mapped_column(String(500))


class CompanyReplyIn(BaseModel):
    kind: str = Field(default="chat", max_length=10)
    text: str = Field(min_length=3, max_length=500)


@router.get("/api/company/{company_id}/reply-templates")
def list_reply_templates(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.WRITE)
    return [{"id": r.id, "kind": r.kind, "text": r.text} for r in db.scalars(select(CompanyReply).where(CompanyReply.company_id == c.id).order_by(CompanyReply.id)).all()]


@router.post("/api/company/{company_id}/reply-templates", status_code=201)
def add_reply_template(company_id: int, p: CompanyReplyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.WRITE)
    if p.kind not in KINDS:
        raise HTTPException(400, "Вид шаблона: invite, reject или chat")
    check_text(p.text, "")
    count = db.scalar(select(func.count()).select_from(CompanyReply).where(CompanyReply.company_id == c.id)) or 0
    if count >= MAX_REPLY_TEMPLATES:
        raise HTTPException(409, f"Шаблонов уже {MAX_REPLY_TEMPLATES}: удалите ненужные")
    r = CompanyReply(company_id=c.id, kind=p.kind, text=p.text.strip())
    db.add(r)
    db.commit()
    return {"id": r.id, "kind": r.kind, "text": r.text}


@router.delete("/api/company/{company_id}/reply-templates/{reply_id}")
def delete_reply_template(company_id: int, reply_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.WRITE)
    r = db.get(CompanyReply, reply_id)
    if r is None or r.company_id != c.id:
        raise HTTPException(404, "Шаблон не найден")
    db.delete(r)
    db.commit()
    return {"ok": True}
