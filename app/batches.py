"""Mass actions on listings that can be watched, stopped and taken back.

1. `preview`: what would be touched. It works on the whole result of a filter (not only the visible page, up to 500), or on the
   chosen numbers. Objects «in work» by another employee, objects «in dispute» and quarantined ones are skipped and listed.
2. `start`: the person confirms by typing the number shown in the preview; a batch is created. More than 100 objects or any
   «hide / reject» of more than 20 needs an administrator (the second confirmation).
3. `step`: the page calls it again and again (25 at a time) and shows progress; `stop` pauses it for good.
4. `undo`: within 60 minutes every object that is still in the state the batch gave it goes back to what it was.
Each object keeps its «before»; the journal says which batch changed it."""
import datetime as dt
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import admin_ops as ops
from app import catalog
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.database import Base, get_db
from app.models import Admin, Claim, Listing, _now
from app.payments import aware, now

router = APIRouter()
STEP = 25
LIMIT = 500
UNDO_MINUTES = 60
DECISIONS = {"approve", "reject", "hide", "show"}
RESULT = {"approve": "approved", "reject": "rejected", "hide": "hidden", "show": "approved"}


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    actor: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(10), default="running")        # running | done | stopped | undone
    queue: Mapped[str] = mapped_column(Text, default="[]")                    # ids still to do
    done: Mapped[str] = mapped_column(Text, default="[]")                     # [{id, before_status, before_note, after}]
    failed: Mapped[str] = mapped_column(Text, default="[]")
    total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Target(BaseModel):
    ids: list[int] = Field(default=[], max_length=LIMIT)
    category: str | None = None
    status: str | None = None
    q: str = Field(default="", max_length=60)


def resolve_targets(db: Session, t: Target, actor: str) -> tuple[list[int], list[dict]]:
    """Ids to work on and the ones skipped (with a reason)."""
    if t.ids:
        rows = [db.get(Listing, i) for i in dict.fromkeys(t.ids)]
        rows = [r for r in rows if r is not None]
    else:
        stmt = select(Listing)
        if t.category:
            stmt = stmt.where(Listing.category == t.category)
        stmt = stmt.where(Listing.status == (t.status or "pending"))
        for word in t.q.lower().split()[:4]:
            stmt = stmt.where(Listing.search_text.like(catalog._like(word), escape="\\"))
        rows = list(db.scalars(stmt.order_by(Listing.id.desc()).limit(LIMIT)).all())
    take, skip = [], []
    for r in rows:
        claim = db.get(Claim, ("listing", str(r.id)))
        if claim is not None and aware(claim.until) > now() and claim.staff != actor:
            skip.append({"id": r.id, "why": "сейчас в работе у другого сотрудника"})
        elif r.dispute:
            skip.append({"id": r.id, "why": "идёт спор"})
        elif r.quarantine:
            skip.append({"id": r.id, "why": "в карантине"})
        else:
            take.append(r.id)
    return take, skip


@router.post("/api/admin/batches/preview")
def preview(t: Target, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    take, skip = resolve_targets(db, t, actor)
    return {"count": len(take), "skipped": skip, "sample": take[:10], "limit": LIMIT}


class StartIn(BaseModel):
    target: Target
    decision: str
    reason: str = Field(default="", max_length=300)
    confirm_count: int


@router.post("/api/admin/batches", status_code=201)
def start(p: StartIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    if p.decision not in DECISIONS:
        raise HTTPException(400, "Решение: approve, reject, hide или show")
    if p.decision in {"reject", "hide"} and len(p.reason.strip()) < 3:
        raise HTTPException(400, "Укажите причину: люди её увидят")
    take, skip = resolve_targets(db, p.target, actor)
    if not take:
        raise HTTPException(404, "Нечего обрабатывать")
    if p.confirm_count != len(take):
        raise HTTPException(409, f"Вы подтвердили {p.confirm_count}, а затронется {len(take)}. Проверьте число и повторите")
    boss = db.get(Admin, actor)
    big = len(take) > 100 or (p.decision in {"reject", "hide"} and len(take) > 20)
    if big and (boss is None or boss.role not in {"admin", "super_admin"}):
        raise HTTPException(403, "Так много объектов (или массовое скрытие) может обработать только администратор")
    b = Batch(id=secrets.token_hex(6), actor=actor, decision=p.decision, reason=p.reason.strip(), queue=json.dumps(take), total=len(take))
    db.add(b)
    log_action(db, actor, "batch.start", "batch", b.id, f"{p.decision} {len(take)} шт., пропущено {len(skip)}")
    db.commit()
    return view(b)


def view(b: Batch) -> dict:
    done, failed, queue = json.loads(b.done), json.loads(b.failed), json.loads(b.queue)
    return {"id": b.id, "status": b.status, "decision": b.decision, "total": b.total, "done": len(done), "failed": failed, "left": len(queue),
            "created_at": b.created_at.isoformat(), "can_undo": b.status in {"done", "stopped"} and bool(done)
            and now() - aware(b.created_at) <= dt.timedelta(minutes=UNDO_MINUTES)}


def _get(db: Session, batch_id: str, actor: str) -> Batch:
    b = db.get(Batch, batch_id)
    if b is None:
        raise HTTPException(404, "Пакет не найден")
    return b


@router.post("/api/admin/batches/{batch_id}/step")
def step(batch_id: str, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    b = _get(db, batch_id, actor)
    if b.status != "running":
        return view(b)
    queue, done, failed = json.loads(b.queue), json.loads(b.done), json.loads(b.failed)
    for lid in queue[:STEP]:
        item = db.get(Listing, lid)
        before = {"id": lid, "before_status": item.status if item else "", "before_note": item.note if item else ""}
        try:
            if b.decision in {"approve", "reject"}:
                ops.moderate_listing(db, b.actor, lid, b.decision, b.reason)
            else:
                ops.moderate_listing(db, b.actor, lid, b.decision, b.reason or "массовое действие")
            done.append(before | {"after": RESULT[b.decision]})
        except ops.OpError as exc:
            db.rollback()
            failed.append({"id": lid, "error": exc.message})
        b = db.get(Batch, batch_id)
    b.queue, b.done, b.failed = json.dumps(queue[STEP:]), json.dumps(done), json.dumps(failed)
    if not queue[STEP:]:
        b.status = "done"
        log_action(db, b.actor, "batch.done", "batch", b.id, f"{len(done)} готово, {len(failed)} ошибок")
    db.commit()
    return view(b)


@router.post("/api/admin/batches/{batch_id}/stop")
def stop(batch_id: str, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    b = _get(db, batch_id, actor)
    if b.status == "running":
        b.status = "stopped"
        log_action(db, actor, "batch.stop", "batch", b.id, f"осталось {len(json.loads(b.queue))}")
        db.commit()
    return view(b)


@router.post("/api/admin/batches/{batch_id}/undo")
def undo(batch_id: str, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """Put back what the batch changed (only objects that are still as the batch left them, so later decisions are not erased)."""
    b = _get(db, batch_id, actor)
    if b.status not in {"done", "stopped"}:
        raise HTTPException(409, "Пакет ещё выполняется или уже отменён")
    if now() - aware(b.created_at) > dt.timedelta(minutes=UNDO_MINUTES):
        raise HTTPException(409, f"Отменить пакет можно в течение {UNDO_MINUTES} минут")
    back, kept = 0, 0
    for row in json.loads(b.done):
        item = db.get(Listing, row["id"])
        if item is not None and item.status == row["after"]:
            item.status, item.note = row["before_status"], row["before_note"]
            back += 1
        else:
            kept += 1
    b.status = "undone"
    log_action(db, actor, "batch.undo", "batch", b.id, f"возвращено {back}, не тронуто {kept}")
    db.commit()
    return {"returned": back, "left_as_is": kept}


@router.get("/api/admin/batches")
def recent(actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    return [view(b) for b in db.scalars(select(Batch).order_by(Batch.created_at.desc()).limit(20)).all()]


class FlagIn(BaseModel):
    quarantine: bool | None = None
    dispute: bool | None = None
    reason: str = Field(default="", max_length=300)


@router.post("/api/admin/listings/{listing_id}/flags")
def flags(listing_id: int, p: FlagIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """Quarantine: the object leaves search, staff see it with a mark, the way out is a moderator's decision.
    Dispute: stays in search with a staff mark, only an administrator may act on it until the dispute is closed."""
    item = db.get(Listing, listing_id)
    if item is None:
        raise HTTPException(404, "Объявление не найдено")
    if (p.quarantine is not None or p.dispute is not None) and len(p.reason.strip()) < 3:
        raise HTTPException(400, "Укажите причину: она остаётся в журнале")
    if p.dispute is False and item.dispute:
        boss = db.get(Admin, actor)
        if boss is None or boss.role not in {"admin", "super_admin"}:
            raise HTTPException(403, "Закрыть спор может только администратор")
    if p.quarantine is not None:
        item.quarantine = p.quarantine
    if p.dispute is not None:
        item.dispute = p.dispute
    log_action(db, actor, "listing.flags", "listing", listing_id, f"карантин={item.quarantine} спор={item.dispute}: {p.reason.strip()}")
    db.commit()
    return {"id": item.id, "quarantine": item.quarantine, "dispute": item.dispute}
