"""Complaints on one object are handled together, and a complaint can not wait forever.

- all complaints on the same object are decided at once; every person who complained gets an answer (10 complaints, 10 answers);
- the object action (hide a listing, reject a profile, hide a review, block a person) is applied once;
- complaints types: listing · user · message · dating · review - each has its own wording for the answer;
- if a moderator has not decided within `reports.auto_days` days (Rules; 0 = never) the system decides: it either returns the object
  that was hidden only because of the complaints (reports.auto_return = on) or leaves it hidden; in both cases the complaints are
  closed «автоматически» and everyone is told."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admin_ops as ops
from app import guard, rules
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.database import get_db
from app.models import Listing, Report
from app.payments import aware, now
import datetime as dt

router = APIRouter()
KIND_TEXT = {"listing": "на объявление", "user": "на человека", "message": "на сообщение", "dating": "на анкету", "review": "на отзыв"}
HIDDEN_NOTES = ("Скрыто по жалобам пользователей, пока модератор проверяет", "Скрыто по жалобам пользователей")


class GroupIn(BaseModel):
    target_type: str = Field(max_length=16)
    target_id: str = Field(max_length=40)
    decision: str = Field(pattern="^(resolve|reject)$")
    reason: str = Field(default="", max_length=300)


@router.post("/api/admin/reports/resolve-group")
def resolve_group(payload: GroupIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Report).where(Report.target_type == payload.target_type, Report.target_id == payload.target_id, Report.status == "new")).all()
    if not rows:
        raise HTTPException(404, "Новых жалоб на этот объект нет")
    if payload.decision == "reject" and payload.target_type == "listing" and payload.target_id.isdigit():
        item = db.get(Listing, int(payload.target_id))      # the complaints were wrong: what they hid comes back
        if item is not None and item.status == "hidden" and item.note in HIDDEN_NOTES:
            expired = item.expires_at is not None and aware(item.expires_at) <= now()
            item.status, item.note = ("expired" if expired else "approved"), ""
    done = 0
    for r in rows:                                  # the first one applies the action to the object, the rest only close and answer
        try:
            ops.decide_report(db, actor, r.id, payload.decision, payload.reason)
            done += 1
        except ops.OpError as exc:
            if exc.status != 409:
                raise HTTPException(exc.status, exc.message)
    return {"closed": done, "answers": done}


def auto_close(db: Session, at: dt.datetime | None = None) -> list[tuple[str, str]]:
    """Run by the bot. Returns nothing to send: the notices are written here."""
    days = rules.val("reports.auto_days")
    if not days:
        return []
    moment = at or now()
    old = [r for r in db.scalars(select(Report).where(Report.status == "new")).all() if aware(r.created_at) <= moment - dt.timedelta(days=days)]
    groups: dict[tuple[str, str], list[Report]] = {}
    for r in old:
        groups.setdefault((r.target_type, r.target_id), []).append(r)
    back = rules.val("reports.auto_return")
    for (kind, tid), rows in groups.items():
        returned = False
        if kind == "listing" and back and tid.isdigit():
            item = db.get(Listing, int(tid))
            if item is not None and item.status == "hidden" and item.note in HIDDEN_NOTES:
                expired = item.expires_at is not None and aware(item.expires_at) <= moment
                item.status, item.note = ("expired" if expired else "approved"), ""
                returned = True
                guard.notify_user(db, item.author_telegram_id, f"Объявление «{item.title}» снова опубликовано: проверка жалоб не завершилась в срок.", "moderation")
        for r in rows:
            r.status = "rejected" if returned or kind != "listing" else "resolved"
            guard.notify_user(db, r.reporter_tg_id,
                              f"Ваша жалоба {KIND_TEXT.get(kind, '')} закрыта автоматически: модератор не успел рассмотреть её за {days} дн. "
                              + ("Объект возвращён." if returned else "Объект остаётся скрытым, если он был скрыт."), "moderation")
        log_action(db, "system:auto", "report.auto_close", "report", f"{kind}:{tid}", f"{len(rows)} шт., вернули={returned}")
    db.commit()
    return []
