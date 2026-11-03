"""Duty rota and escalation for the staff.

- A shift says: from–to, in this area (поддержка · модерация · деньги · суперадмин), THIS person answers.
- A staff notice (a complaint, a support request, a refund...) goes to the person on duty for that area. If nobody is on duty it
  goes to everybody with the right role, as before.
- Every notice to a person on duty is remembered. If it is not seen within `staff.escalate_minutes` (30) it goes to every
  employee of the area; after `staff.escalate2_minutes` (60) to the super administrators. «Seen» = the person opened the
  panel (the counters are asked every minute while the panel is open) or pressed «принято».
- The table of who gets what is shown on the same page."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import guard, rules
from app.admin_auth import ADM, SUPPORT, staff
from app.audit import log_action
from app.database import Base, get_db
from app.models import Admin, _now
from app.payments import aware, now

router = APIRouter()
AREAS = {"support": ("Поддержка", ("support", "moderator", "admin", "super_admin")), "moderation": ("Модерация", ("moderator", "admin", "super_admin")),
         "money": ("Деньги", ("admin", "super_admin")), "super": ("Суперадмин", ("super_admin",))}
AREA_OF_KEY = {"ticket": "support", "appeal": "support", "report": "moderation", "listing": "moderation", "employer": "moderation", "company": "moderation",
               "refund": "money", "adbudget": "money", "newip": "super", "piialert": "super", "slow": "moderation"}
MATRIX = [("Новое обращение, оспаривание", "support"), ("Жалоба, объявление на проверке, компания, работодатель", "moderation"),
          ("Заявка на возврат, бюджет рекламы", "money"), ("Вход с нового адреса, много просмотров данных", "super")]


class DutyShift(Base):
    __tablename__ = "duty_shifts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area: Mapped[str] = mapped_column(String(12), index=True)
    tg_id: Mapped[str] = mapped_column(String(40))
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(40), default="")


class StaffAlert(Base):
    __tablename__ = "staff_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area: Mapped[str] = mapped_column(String(12), index=True)
    text: Mapped[str] = mapped_column(String(300))
    to_tg: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[int] = mapped_column(Integer, default=0)          # 0 on duty · 1 whole area · 2 super administrators
    seen: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


def on_duty(db: Session, area: str, at: dt.datetime | None = None) -> str | None:
    at = at or now()
    rows = db.scalars(select(DutyShift).where(DutyShift.area == area, DutyShift.starts_at <= at, DutyShift.ends_at > at).order_by(DutyShift.starts_at.desc())).all()
    for r in rows:
        adm = db.get(Admin, r.tg_id)
        if adm is not None and adm.active:
            return r.tg_id
    return None


def route(db: Session, text: str, key: str) -> bool:
    """Send a staff notice to the person on duty. False when nobody is on duty (the caller then tells everybody)."""
    area = AREA_OF_KEY.get(key)
    if area is None:
        return False
    person = on_duty(db, area)
    if person is None:
        return False
    try:
        guard.rate_limit(person, f"staffnote:{key}", 1, 600)       # a burst is one alert, not twenty
    except HTTPException:
        return True
    db.add(StaffAlert(area=area, text=text[:300], to_tg=person))
    db.commit()
    guard.notify_user(db, person, "🛎 Вы дежурный: " + text, "moderation")
    return True


def mark_seen(db: Session, tg_id: str) -> None:
    """The person opened the panel: their open alerts are seen."""
    rows = db.scalars(select(StaffAlert).where(StaffAlert.to_tg == tg_id, StaffAlert.seen.is_(False))).all()
    for r in rows:
        r.seen = True
    if rows:
        db.commit()


def escalate(db: Session, at: dt.datetime | None = None) -> int:
    """Run by the bot. Not seen in time → the whole area; still not seen → the super administrators."""
    moment = at or now()
    first = dt.timedelta(minutes=rules.val("staff.escalate_minutes"))
    second = dt.timedelta(minutes=rules.val("staff.escalate2_minutes"))
    done = 0
    for a in db.scalars(select(StaffAlert).where(StaffAlert.seen.is_(False), StaffAlert.level < 2)).all():
        age = moment - aware(a.created_at)
        if a.level == 0 and age >= first:
            targets = [x.tg_id for x in db.scalars(select(Admin).where(Admin.active.is_(True), Admin.role.in_(AREAS[a.area][1]))).all() if x.tg_id != a.to_tg]
            a.level = 1
            msg = f"⏰ Дежурный не ответил {int(first.total_seconds() // 60)} мин: {a.text}"
        elif a.level == 1 and age >= second:
            targets = [x.tg_id for x in db.scalars(select(Admin).where(Admin.active.is_(True), Admin.role == "super_admin")).all()]
            a.level = 2
            msg = f"🚨 Никто не ответил {int(second.total_seconds() // 60)} мин: {a.text}"
        else:
            continue
        for tg in targets:
            guard.notify_user(db, tg, msg, "moderation")
        done += 1
    db.commit()
    return done


@router.get("/api/admin/duty")
def duty_overview(actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    names = {a.tg_id: a.role for a in db.scalars(select(Admin)).all()}
    now_on = {k: on_duty(db, k) for k in AREAS}
    shifts = db.scalars(select(DutyShift).where(DutyShift.ends_at > now()).order_by(DutyShift.starts_at).limit(100)).all()
    open_alerts = db.scalars(select(StaffAlert).where(StaffAlert.seen.is_(False)).order_by(StaffAlert.id.desc()).limit(30)).all()
    return {"areas": {k: {"title": v[0], "on_duty": now_on[k]} for k, v in AREAS.items()},
            "shifts": [{"id": s.id, "area": s.area, "tg_id": s.tg_id, "role": names.get(s.tg_id, ""), "from": s.starts_at.isoformat(), "to": s.ends_at.isoformat()} for s in shifts],
            "open_alerts": [{"id": a.id, "area": a.area, "to": a.to_tg, "level": a.level, "text": a.text, "at": a.created_at.isoformat()} for a in open_alerts],
            "matrix": [{"what": w, "area": AREAS[a][0], "roles": list(AREAS[a][1])} for w, a in MATRIX],
            "admins": [{"tg_id": k, "role": v} for k, v in names.items()]}


class ShiftIn(BaseModel):
    area: str = Field(pattern="^(support|moderation|money|super)$")
    tg_id: str = Field(min_length=1, max_length=40)
    starts_at: dt.datetime
    ends_at: dt.datetime


@router.post("/api/admin/duty", status_code=201)
def add_shift(p: ShiftIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    adm = db.get(Admin, p.tg_id)
    if adm is None or not adm.active or adm.role not in AREAS[p.area][1]:
        raise HTTPException(400, "Этот сотрудник не может дежурить в этой области (нет роли или отключён)")
    start, end = aware(p.starts_at), aware(p.ends_at)
    if end <= start or end - start > dt.timedelta(days=31):
        raise HTTPException(400, "Конец смены должен быть позже начала, не дольше 31 дня")
    clash = db.scalar(select(DutyShift.id).where(DutyShift.area == p.area, DutyShift.starts_at < end, DutyShift.ends_at > start))
    if clash is not None:
        raise HTTPException(409, "В это время в этой области уже есть дежурный: сначала удалите ту смену")
    s = DutyShift(area=p.area, tg_id=p.tg_id, starts_at=start, ends_at=end, created_by=actor)
    db.add(s)
    log_action(db, actor, "duty.add", "duty", p.area, f"{p.tg_id} {start.isoformat()} – {end.isoformat()}")
    db.commit()
    return {"id": s.id}


@router.delete("/api/admin/duty/{shift_id}")
def delete_shift(shift_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    s = db.get(DutyShift, shift_id)
    if s is None:
        raise HTTPException(404, "Смена не найдена")
    log_action(db, actor, "duty.delete", "duty", s.area, s.tg_id)
    db.delete(s)
    db.commit()
    return {"deleted": True}


@router.post("/api/admin/alerts/{alert_id}/ack")
def ack(alert_id: int, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    a = db.get(StaffAlert, alert_id)
    if a is None:
        raise HTTPException(404, "Сообщение не найдено")
    a.seen = True
    db.commit()
    return {"seen": True}
