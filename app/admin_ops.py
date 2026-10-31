"""Administrator actions shared by the website API and the Telegram bot.

One place for the rules (reasons are mandatory, every action goes to the journal),
so the bot and the API can never disagree.
"""
from datetime import timedelta

from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session

from app import applications, guard, lifecycle
from app.audit import log_action
from app.models import Listing, Report, User, DatingProfile, Payment

BLOCK_NOTE = "Аккаунт автора заблокирован"
from app.payments import now, aware


class OpError(Exception):
    """An action that cannot be done. status follows HTTP codes (400, 404, 409)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def queue_condition():
    """What moderators look at: new listings and waiting edits of published ones."""
    return or_(Listing.status == "pending", and_(Listing.status == "approved", Listing.has_edit.is_(True)))


def moderate_listing(db: Session, actor: str, listing_id: int, decision: str, reason: str = "") -> Listing:
    """decision: approve | reject (reason required) | needs_fix (comment required).

    For a published listing with a waiting edit, approve publishes the edit and reject/needs_fix drops it
    (the published version stays); for a new listing they publish or return it as before."""
    if decision not in {"approve", "reject", "needs_fix", "hide", "show"}:
        raise OpError(400, "Решение: approve, reject, needs_fix, hide или show")
    if decision in {"reject", "needs_fix", "hide"} and len(reason.strip()) < 3:
        raise OpError(400, "Укажите причину (не короче 3 символов)")
    item = db.get(Listing, listing_id)
    if item is None:
        raise OpError(404, "Объявление не найдено")
    before = item.status
    if decision == "hide":
        if item.status != "approved":
            raise OpError(409, "Скрыть можно только опубликованное объявление")
        item.status, item.note = "hidden", reason.strip()
        log_action(db, actor, "listing.hide", "listing", listing_id, reason)
        db.commit()
        applications.notify_ended(db, item, "скрыта модератором")
        return item
    if decision == "show":
        if item.status != "hidden":
            raise OpError(409, "Это объявление не скрыто")
        item.status, item.note = "approved", ""
        log_action(db, actor, "listing.show", "listing", listing_id, "")
        db.commit()
        return item
    if item.status == "approved" and item.has_edit:
        if decision == "approve":
            lifecycle.apply_edit(db, item)
        else:
            lifecycle.reject_edit(db, item, reason.strip())
        log_action(db, actor, f"listing.edit_{decision}", "listing", listing_id, reason)
        db.commit()
        return item
    if decision == "approve":
        if lifecycle.needs_photo(db, item):
            raise OpError(400, "Для этого раздела нужно хотя бы одно фото")
        lifecycle.publish(db, item)
    else:
        item.status = {"reject": "rejected", "needs_fix": "needs_fix"}[decision]
        item.note = reason.strip()
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
    hidden_listing = None
    if decision == "resolve":
        if report.target_type == "listing" and report.target_id.isdigit():
            listing = db.get(Listing, int(report.target_id))
            if listing:
                if listing.status == "approved":
                    hidden_listing = listing
                listing.status = "hidden"
                listing.note = "Скрыто по жалобам пользователей"
        if report.target_type == "dating" and report.target_id.isdigit():
            profile = db.get(DatingProfile, int(report.target_id))
            if profile:
                profile.status = "rejected"
        if report.target_type == "user":
            user = db.get(User, report.target_id)
            if user:
                user.blocked = True
                user.block_reason = "По жалобам пользователей"
    report.status = "resolved" if decision == "resolve" else "rejected"
    log_action(db, actor, f"report.{decision}", "report", report_id, report.target_type + ":" + report.target_id)
    db.commit()
    # the person who complained learns the result; the author learns why the listing is gone
    guard.notify_user(db, report.reporter_tg_id,
                      "Спасибо за жалобу. Мы проверили и приняли меры." if decision == "resolve"
                      else "Мы проверили вашу жалобу: нарушений не нашли. Спасибо, что помогаете делать KOREA HUB лучше.",
                      "moderation")
    if hidden_listing is not None:
        guard.notify_user(db, hidden_listing.author_telegram_id,
                          f"Объявление «{hidden_listing.title}» скрыто по жалобам пользователей. Если это ошибка, напишите в поддержку.",
                          "moderation")
        applications.notify_ended(db, hidden_listing, "скрыта модератором")
    return report


def hide_user_listings(db: Session, tg_id: str) -> list[Listing]:
    """A blocked account disappears from the site: its published listings are hidden (and come back on unblock)."""
    items = list(db.scalars(select(Listing).where(Listing.author_telegram_id == tg_id, Listing.status == "approved")).all())
    for item in items:
        item.status, item.note = "hidden", BLOCK_NOTE
    return items


def restore_user_listings(db: Session, tg_id: str) -> int:
    count = 0
    for item in db.scalars(select(Listing).where(Listing.author_telegram_id == tg_id, Listing.status == "hidden",
                                                 Listing.note == BLOCK_NOTE)).all():
        expired = item.expires_at is not None and aware(item.expires_at) <= now()
        item.status, item.note = ("expired" if expired else "approved"), ""
        count += 1
    return count


def block_user(db: Session, actor: str, tg_id: str, reason: str) -> User:
    if len(reason.strip()) < 3:
        raise OpError(400, "Укажите причину (не короче 3 символов)")
    user = db.get(User, tg_id)
    if user is None:
        raise OpError(404, "Пользователь не найден")
    user.blocked = True
    user.block_reason = reason.strip()[:300]
    hidden = hide_user_listings(db, tg_id)
    log_action(db, actor, "user.block", "user", tg_id, f"{reason}; скрыто объявлений: {len(hidden)}")
    db.commit()
    for item in hidden:
        applications.notify_ended(db, item, "скрыта модератором")
    return user


def unblock_user(db: Session, actor: str, tg_id: str, reason: str) -> User:
    if len(reason.strip()) < 3:
        raise OpError(400, "Укажите причину (не короче 3 символов)")
    user = db.get(User, tg_id)
    if user is None:
        raise OpError(404, "Пользователь не найден")
    user.blocked = False
    user.block_reason = ""
    back = restore_user_listings(db, tg_id)
    log_action(db, actor, "user.unblock", "user", tg_id, f"{reason}; возвращено объявлений: {back}")
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
    if decision not in {"approve", "reject", "verify", "reject_selfie"}:
        raise OpError(400, "Решение: approve, reject, verify или reject_selfie")
    profile = db.get(DatingProfile, profile_id)
    if profile is None:
        raise OpError(404, "Анкета не найдена")
    if decision in ("verify", "reject_selfie"):
        if not profile.selfie_key:
            raise OpError(400, "Фото-подтверждения нет")
        if decision == "verify":
            profile.selfie_status = "approved"
        else:
            from app.dating import upload_root
            path = upload_root() / profile.selfie_key
            if path.exists():
                path.unlink()
            profile.selfie_key, profile.selfie_status = None, "rejected"
        log_action(db, actor, f"dating.{decision}", "dating", profile_id, profile.selfie_status)
        db.commit()
        guard.notify_user(db, profile.owner_tg_id, "Фото-подтверждение принято: в вашей анкете появилась отметка «проверено»."
                          if decision == "verify" else "Фото-подтверждение не подошло. Пришлите новое: на нём должно быть видно ваше лицо и открытую ладонь.",
                          "moderation")
        return profile
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
