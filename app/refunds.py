"""Refunds as a process, not a button.

new → in_progress → approved | partially_refunded | rejected, and cancelled (a balance refund taken back by the super
administrator within 24 hours).

- a person can ask for a refund of a paid service (optionally tied to a support request);
- staff take it, then approve (the whole payment back to Telegram, or only the unused part to the balance) or reject with a
  reason the person sees;
- a payment is never returned twice (`refunded_amount` and the status are checked);
- what a refund does to the service: the paid status ends; an extension of the listing and the publication itself are NOT rolled
  back (a note on the record says so);
- a daily cap per employee (Rules), the super administrator has no cap;
- every step is in the journal; the history is on the person's card and can be exported."""
import csv
import datetime as dt
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Integer, String, Text, DateTime, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import guard, payments, rules
from app.admin_auth import FIN, staff
from app.audit import log_action
from app.database import Base, get_db
from app.models import Admin, AuditLog, Payment, User, _now
from app.payments import PaymentError, aware, now
from app.users import writer

router = APIRouter()
OPEN = {"new", "in_progress"}
NOTE = "Услуга снята. Продление срока объявления и сама публикация не откатываются."


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(Integer, index=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(18), default="new", index=True)
    mode: Mapped[str] = mapped_column(String(8), default="")                 # full | balance, set when decided
    reason: Mapped[str] = mapped_column(String(300), default="")             # why the person asks
    answer: Mapped[str] = mapped_column(String(300), default="")             # the staff reason shown to the person
    amount: Mapped[int] = mapped_column(Integer, default=0)                  # Stars given back
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned: Mapped[str] = mapped_column(String(40), default="")
    decided_by: Mapped[str] = mapped_column(String(40), default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


def view(r: Refund, p: Payment | None = None) -> dict:
    return {"id": r.id, "payment_id": r.payment_id, "user": r.user_tg_id, "status": r.status, "mode": r.mode, "reason": r.reason,
            "answer": r.answer, "amount": r.amount, "ticket_id": r.ticket_id, "assigned": r.assigned, "note": r.note,
            "paid": ((p.stars or 0) + (p.balance_used or 0)) if p else None, "service": p.service_code if p else "",
            "created_at": r.created_at.isoformat(), "decided_at": r.decided_at.isoformat() if r.decided_at else None}


class RequestIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    ticket_id: int | None = None


@router.post("/api/me/payments/{payment_id}/refund-request", status_code=201)
def ask_refund(payment_id: int, payload: RequestIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    guard.rate_limit(user_id, "refund_ask", 5, 86400)
    p = db.get(Payment, payment_id)
    if p is None or p.user_tg_id != user_id:
        raise HTTPException(404, "Платёж не найден")
    if p.status != "paid" or p.service_code == payments.TOPUP_CODE:
        raise HTTPException(409, "Вернуть можно только оплаченную услугу (пополнение баланса возвращает поддержка вручную)")
    if db.scalar(select(Refund.id).where(Refund.payment_id == p.id, Refund.status.in_(list(OPEN)))) is not None:
        raise HTTPException(409, "Заявка на возврат уже рассматривается")
    r = Refund(payment_id=p.id, user_tg_id=user_id, reason=payload.reason.strip(), ticket_id=payload.ticket_id)
    db.add(r)
    db.commit()
    from app import adminplus
    adminplus.notify_staff(db, "Новая заявка на возврат (админка → Платежи → Возвраты).", "refund", ("admin", "super_admin"))
    return view(r, p)


@router.get("/api/me/refunds")
def my_refunds(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    rows = db.scalars(select(Refund).where(Refund.user_tg_id == user_id).order_by(Refund.id.desc()).limit(50)).all()
    return [{**view(r, db.get(Payment, r.payment_id)), "note": ""} for r in rows]


@router.get("/api/admin/refunds")
def admin_refunds(status: str = Query(default="open"), user: str = Query(default="", max_length=40), actor: str = Depends(staff(*FIN)),
                  db: Session = Depends(get_db)):
    stmt = select(Refund).order_by(Refund.id.desc()).limit(200)
    if status == "open":
        stmt = select(Refund).where(Refund.status.in_(list(OPEN))).order_by(Refund.id.desc()).limit(200)
    elif status != "all":
        stmt = select(Refund).where(Refund.status == status).order_by(Refund.id.desc()).limit(200)
    if user:
        stmt = stmt.where(Refund.user_tg_id == user)
    return [view(r, db.get(Payment, r.payment_id)) for r in db.scalars(stmt).all()]


def _get(db: Session, refund_id: int) -> Refund:
    r = db.get(Refund, refund_id)
    if r is None:
        raise HTTPException(404, "Заявка не найдена")
    return r


@router.post("/api/admin/refunds/{refund_id}/take")
def take(refund_id: int, actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    r = _get(db, refund_id)
    if r.status != "new":
        raise HTTPException(409, "Заявка уже взята или закрыта")
    r.status, r.assigned = "in_progress", actor
    log_action(db, actor, "refund.take", "refund", r.id, "")
    db.commit()
    return view(r, db.get(Payment, r.payment_id))


def check_day_cap(db: Session, actor: str) -> None:
    boss = db.get(Admin, actor)
    if boss is not None and boss.role == "super_admin":
        return
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    done = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.actor == actor, AuditLog.action.in_(["payment.refund", "refund.approve"]),
                                                                   AuditLog.created_at >= today)) or 0
    if done >= rules.val("refund.day_count"):
        raise HTTPException(429, "Дневной лимит возвратов исчерпан: дальше возвращает суперадмин (настройка в «Правилах»)")


class ApproveIn(BaseModel):
    mode: str = Field(pattern="^(full|balance)$")
    comment: str = Field(default="", max_length=300)


@router.post("/api/admin/refunds/{refund_id}/approve")
def approve(refund_id: int, payload: ApproveIn, actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    r = _get(db, refund_id)
    if r.status not in OPEN:
        raise HTTPException(409, "Заявка уже закрыта")
    p = db.get(Payment, r.payment_id)
    if p is None or p.status != "paid" or (p.refunded_amount or 0) > 0:
        raise HTTPException(409, "Этот платёж уже возвращён или недоступен для возврата")
    check_day_cap(db, actor)
    full_amount = (p.stars or 0) + (p.balance_used or 0)
    try:
        if payload.mode == "balance":
            p2, credit = payments.refund_to_balance(db, p.id, "по заявке")
            amount = credit
        else:
            payments.refund(db, p.id)
            amount = full_amount
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    r.mode, r.amount = payload.mode, amount
    r.status = "partially_refunded" if amount < full_amount else "approved"
    r.answer, r.decided_by, r.decided_at, r.note = payload.comment.strip(), actor, now(), NOTE
    log_action(db, actor, "refund.approve", "refund", r.id, f"{payload.mode} {amount}/{full_amount}")
    db.commit()
    guard.notify_user(db, r.user_tg_id, (f"Возврат одобрен: {amount} ⭐ " + ("вернулись на ваш баланс." if payload.mode == "balance" else "возвращены в Telegram.")
                                         + (f" {r.answer}" if r.answer else "")), "payments")
    return view(r, p)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/api/admin/refunds/{refund_id}/reject")
def reject(refund_id: int, payload: RejectIn, actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    r = _get(db, refund_id)
    if r.status not in OPEN:
        raise HTTPException(409, "Заявка уже закрыта")
    r.status, r.answer, r.decided_by, r.decided_at = "rejected", payload.reason.strip(), actor, now()
    log_action(db, actor, "refund.reject", "refund", r.id, payload.reason.strip())
    db.commit()
    guard.notify_user(db, r.user_tg_id, f"В возврате отказано: {r.answer}", "payments")
    return view(r, db.get(Payment, r.payment_id))


@router.post("/api/admin/refunds/{refund_id}/cancel")
def cancel(refund_id: int, actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    """Take back a refund to the balance within 24 hours (super administrator only). A refund that went to Telegram can not be
    taken back by us."""
    boss = db.get(Admin, actor)
    if boss is None or boss.role != "super_admin":
        raise HTTPException(403, "Отменить возврат может только суперадмин")
    r = _get(db, refund_id)
    if r.status not in {"approved", "partially_refunded"} or r.mode != "balance":
        raise HTTPException(409, "Отменить можно только возврат на баланс")
    if r.decided_at is None or now() - aware(r.decided_at) > dt.timedelta(hours=24):
        raise HTTPException(409, "С момента возврата прошло больше 24 часов")
    p = db.get(Payment, r.payment_id)
    purse = payments._purse(db, r.user_tg_id, p.company_id if p else None)
    if purse is None or (purse.balance_stars or 0) < r.amount:
        raise HTTPException(409, "Этих звёзд уже нет на балансе: отменить нельзя")
    purse.balance_stars -= r.amount
    r.status = "cancelled"
    if p is not None:
        p.refunded_amount = 0
    log_action(db, actor, "refund.cancel", "refund", r.id, f"-{r.amount}")
    db.commit()
    return view(r, p)


@router.get("/api/admin/refunds/export")
def export(actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "payment", "user", "status", "mode", "amount", "reason", "answer", "created", "decided"])
    for r in db.scalars(select(Refund).order_by(Refund.id)).all():
        w.writerow([r.id, r.payment_id, r.user_tg_id, r.status, r.mode, r.amount, r.reason, r.answer, r.created_at.isoformat(),
                    r.decided_at.isoformat() if r.decided_at else ""])
    log_action(db, actor, "refund.export", "refund", "all", "")
    db.commit()
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=refunds.csv"})
