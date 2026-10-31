"""Administrator actions shared by the website API and the Telegram bot.

One place for the rules (reasons are mandatory, every action goes to the journal),
so the bot and the API can never disagree.
"""
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.audit import log_action
from app.models import Listing, Report, User, DatingProfile, Payment
from app.payments import now, aware


class OpError(Exception):
    """An action that cannot be done. status follows HTTP codes (400, 404, 409)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def moderate_listing(db: Session, actor: str, listing_id: int, decision: str, reason: str = "") -> Listing:
    """decision: approve | reject (reason required) | needs_fix (comment required)."""
    if decision not in {"approve", "reject", "needs_fix"}:
        raise OpError(400, "Решение: approve, reject или needs_fix")
    if decision in {"reject", "needs_fix"} and len(reason.strip()) < 3:
        raise OpError(400, "Укажите причину (не короче 3 символов)")
    item = db.get(Listing, listing_id)
    if item is None:
        raise OpError(404, "Объявление не найдено")
    before = item.status
    item.status = {"approve": "approved", "reject": "rejected", "needs_fix": "needs_fix"}[decision]
    item.note = "" if decision == "approve" else reason.strip()
    log_action(db, actor, f"listing.{decision}", "listing", listing_id, f"{before} -> {item.status}: {reason}")
    db.commit()
    return item


def decide_report(db: Session, actor: str, report_id: int, decision: str) -> Report:
    if decision not in {"resolve", "reject"}:
        raise OpError(400, "Решение: resolve или reject")
    report = db.get(Report, report_id)
    if report is None:
        raise OpError(404, "Жалоба не найдена")
    if report.status != "new":
        raise OpError(409, "Жалоба уже рассмотрена")
    if decision == "resolve":
        if report.target_type == "listing" and report.target_id.isdigit():
            listing = db.get(Listing, int(report.target_id))
            if listing:
                listing.status = "hidden"
        if report.target_type == "dating" and report.target_id.isdigit():
            profile = db.get(DatingProfile, int(report.target_id))
            if profile:
                profile.status = "rejected"
        if report.target_type == "user":
            user = db.get(User, report.target_id)
            if user:
                user.blocked = True
    report.status = "resolved" if decision == "resolve" else "rejected"
    log_action(db, actor, f"report.{decision}", "report", report_id, report.target_type + ":" + report.target_id)
    db.commit()
    return report


def block_user(db: Session, actor: str, tg_id: str, reason: str) -> User:
    if len(reason.strip()) < 3:
        raise OpError(400, "Укажите причину (не короче 3 символов)")
    user = db.get(User, tg_id)
    if user is None:
        raise OpError(404, "Пользователь не найден")
    user.blocked = True
    user.block_reason = reason.strip()[:300]
    log_action(db, actor, "user.block", "user", tg_id, reason)
    db.commit()
    return user


def unblock_user(db: Session, actor: str, tg_id: str, reason: str) -> User:
    if len(reason.strip()) < 3:
        raise OpError(400, "Укажите причину (не короче 3 символов)")
    user = db.get(User, tg_id)
    if user is None:
        raise OpError(404, "Пользователь не найден")
    user.blocked = False
    user.block_reason = ""
    log_action(db, actor, "user.unblock", "user", tg_id, reason)
    db.commit()
    return user


def decide_employer(db: Session, actor: str, tg_id: str, decision: str) -> User:
    if decision not in {"approve", "reject"}:
        raise OpError(400, "Решение: approve или reject")
    user = db.get(User, tg_id)
    if user is None or user.employer_status != "pending":
        raise OpError(404, "Заявка не найдена")
    user.employer_status = "approved" if decision == "approve" else "rejected"
    log_action(db, actor, f"employer.{decision}", "user", tg_id, user.company_name)
    db.commit()
    return user


def decide_dating(db: Session, actor: str, profile_id: int, decision: str) -> DatingProfile:
    if decision not in {"approve", "reject"}:
        raise OpError(400, "Решение: approve или reject")
    profile = db.get(DatingProfile, profile_id)
    if profile is None:
        raise OpError(404, "Анкета не найдена")
    if decision == "approve" and profile.photo_key is None:
        raise OpError(400, "Нельзя опубликовать анкету без фото")
    profile.status = "published" if decision == "approve" else "rejected"
    log_action(db, actor, f"dating.{decision}", "dating", profile_id, profile.status)
    db.commit()
    return profile


def compute_stats(db: Session) -> dict:
    current = now()
    paid = db.scalars(select(Payment).where(Payment.status == "paid")).all()

    def window(days: int):
        since = current - timedelta(days=days)
        rows = [p for p in paid if p.paid_at and aware(p.paid_at) >= since]
        return {"sales": len(rows), "stars": sum(p.stars for p in rows)}

    by_status = dict(db.execute(select(Listing.status, func.count()).group_by(Listing.status)).all())
    top: dict[str, int] = {}
    for p in paid:
        top[p.service_code] = top.get(p.service_code, 0) + 1
    return {
        "users": db.scalar(select(func.count()).select_from(User)),
        "listings": by_status,
        "reports_new": db.scalar(select(func.count()).select_from(Report).where(Report.status == "new")),
        "payers": len({p.user_tg_id for p in paid}),
        "revenue_stars_total": sum(p.stars for p in paid),
        "day": window(1), "week": window(7), "month": window(30),
        "top_services": sorted(top.items(), key=lambda kv: -kv[1]),
    }
