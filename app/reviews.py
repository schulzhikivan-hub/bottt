"""Reviews and ratings of listing authors.

Who may write: someone who has a chat about that listing in which both sides wrote at least once, or an
applicant the employer invited. One review per person per listing, never about yourself.
Texts pass the same fraud/contact filter as listings. Reviews can be reported and hidden by moderators."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import guard
from app.auth import optional_user_id
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.database import get_db
from app.models import Application, Conversation, Listing, Message, Review, User
from app.users import writer

router = APIRouter()


class ReviewIn(BaseModel):
    listing_id: int
    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=4)


TAGS = {"polite": "Вежливый", "punctual": "Пунктуальный", "as_described": "Всё как в описании",
        "fair_price": "Честная цена", "fast": "Быстро отвечает", "unreliable": "Не пришёл/не ответил", "rude": "Грубый"}


def summary(db: Session, tg_id: str) -> dict:
    avg, count = db.execute(select(func.avg(Review.rating), func.count()).where(
        Review.target_tg_id == tg_id, Review.status == "published")).one()
    return {"rating": round(float(avg), 1) if count else None, "reviews": int(count or 0)}


def may_review(db: Session, reviewer: str, listing: Listing) -> bool:
    """Both people wrote in the chat about this listing, or the employer invited this applicant."""
    conv = db.scalar(select(Conversation).where(Conversation.listing_id == listing.id, Conversation.buyer_tg_id == reviewer))
    if conv is not None:
        senders = set(db.scalars(select(Message.sender_tg_id).where(Message.conversation_id == conv.id).distinct()).all())
        if reviewer in senders and listing.author_telegram_id in senders:
            return True
    return db.scalar(select(func.count()).select_from(Application).where(
        Application.listing_id == listing.id, Application.applicant_tg_id == reviewer,
        Application.status == "invited")) > 0


@router.post("/api/reviews", status_code=201)
def create_review(payload: ReviewIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    guard.rate_limit(user_id, "review", 5, 86400)
    listing = db.get(Listing, payload.listing_id)
    if listing is None:
        raise HTTPException(404, "Объявление не найдено")
    if listing.author_telegram_id == user_id:
        raise HTTPException(400, "Нельзя оставить отзыв о себе")
    if listing.company_id:
        raise HTTPException(403, "Отзывы о компаниях пока не принимаются: отзыв был бы о конкретном сотруднике")
    if not may_review(db, user_id, listing):
        raise HTTPException(403, "Отзыв можно оставить после переписки с автором по этому объявлению")
    if db.scalar(select(Review).where(Review.reviewer_tg_id == user_id, Review.listing_id == listing.id)):
        raise HTTPException(409, "Вы уже оставили отзыв по этому объявлению")
    text = " ".join(payload.text.split())
    if text:
        guard.check_text("", text)
    tags = [t for t in dict.fromkeys(payload.tags) if t in TAGS]
    review = Review(listing_id=listing.id, target_tg_id=listing.author_telegram_id, reviewer_tg_id=user_id,
                    rating=payload.rating, text=text, tags=",".join(tags))
    db.add(review)
    db.commit()
    guard.notify_user(db, listing.author_telegram_id, f"Новый отзыв ({payload.rating}★) по объявлению «{listing.title}»", "messages")
    return {"id": review.id, **summary(db, listing.author_telegram_id)}


class ReplyIn(BaseModel):
    text: str = Field(min_length=2, max_length=500)


@router.post("/api/reviews/{review_id}/reply")
def reply_to_review(review_id: int, payload: ReplyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """The reviewed person answers once, in public. The reviewer is told."""
    row = db.get(Review, review_id)
    if row is None or row.target_tg_id != user_id or row.status != "published":
        raise HTTPException(404, "Отзыв не найден")
    if row.reply:
        raise HTTPException(409, "Вы уже ответили на этот отзыв")
    text = " ".join(payload.text.split())
    guard.check_text("", text)
    row.reply = text
    db.commit()
    guard.notify_user(db, row.reviewer_tg_id, "Автор ответил на ваш отзыв.", "messages")
    return {"id": row.id, "reply": row.reply}


@router.delete("/api/reviews/{review_id}")
def delete_own_review(review_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    row = db.get(Review, review_id)
    if row is None or row.reviewer_tg_id != user_id:
        raise HTTPException(404, "Отзыв не найден")
    db.delete(row)
    db.commit()
    return {"deleted": True}


@router.get("/api/authors/{listing_id}/reviews")
def author_reviews(listing_id: int, viewer: str | None = Depends(optional_user_id), db: Session = Depends(get_db)):
    """Public: rating and the latest texts. Names are display names only, no Telegram IDs."""
    listing = db.get(Listing, listing_id)
    if listing is None or listing.status != "approved":
        raise HTTPException(404, "Объявление не найдено или снято с публикации")
    rows = db.scalars(select(Review).where(Review.target_tg_id == listing.author_telegram_id, Review.status == "published")
                      .order_by(Review.id.desc()).limit(20)).all()
    out = []
    for r in rows:
        u = db.get(User, r.reviewer_tg_id)
        out.append({"id": r.id, "rating": r.rating, "text": r.text, "tags": [TAGS[t] for t in (r.tags or "").split(",") if t in TAGS], "reply": r.reply, "can_reply": bool(viewer and viewer == r.target_tg_id and not r.reply), "name": (u.display_name if u and u.display_name else "Пользователь"),
                    "at": r.created_at.date().isoformat()})
    return {**summary(db, listing.author_telegram_id), "items": out}


@router.get("/api/listings/{listing_id}/review-state")
def review_state(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Tells the app whether to show «Оставить отзыв» and which review is already mine."""
    listing = db.get(Listing, listing_id)
    if listing is None or listing.author_telegram_id == user_id or listing.company_id:
        return {"can": False, "mine": None}
    mine = db.scalar(select(Review).where(Review.reviewer_tg_id == user_id, Review.listing_id == listing_id))
    return {"can": mine is None and may_review(db, user_id, listing),
            "mine": {"id": mine.id, "rating": mine.rating, "text": mine.text} if mine else None}


# ---- staff ----
@router.get("/api/admin/reviews")
def admin_reviews(status: str = Query(default="published"), actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Review).where(Review.status == status).order_by(Review.id.desc()).limit(100)).all()
    from app import adminview
    who = adminview.names(db, [r.target_tg_id for r in rows] + [r.reviewer_tg_id for r in rows])
    titles = {x.id: x.title for x in db.scalars(select(Listing).where(Listing.id.in_([r.listing_id for r in rows] or [0]))).all()}
    return [{"id": r.id, "listing_id": r.listing_id, "listing_title": titles.get(r.listing_id, ""), "target": r.target_tg_id,
             "target_name": who.get(r.target_tg_id, ""), "reviewer": r.reviewer_tg_id, "reviewer_name": who.get(r.reviewer_tg_id, ""),
             "rating": r.rating, "tags": r.tags, "reply": r.reply, "text": r.text, "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/api/admin/reviews/{review_id}/{decision}")
def admin_review_decision(review_id: int, decision: str, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    if decision not in {"hide", "show"}:
        raise HTTPException(400, "Решение: hide или show")
    row = db.get(Review, review_id)
    if row is None:
        raise HTTPException(404, "Отзыв не найден")
    row.status = "hidden" if decision == "hide" else "published"
    log_action(db, actor, f"review.{decision}", "review", review_id, f"listing:{row.listing_id}")
    db.commit()
    return {"id": row.id, "status": row.status}
