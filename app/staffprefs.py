"""What an employee wants to be told about: a level of importance and a list of muted kinds.
Every staff alert has a kind (key) and a priority; the person chooses «всё / важное / только срочное / ничего» and may mute single kinds.
Critical alerts about security and overdue moderation are never switched off completely by a mode other than «ничего»."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.admin_auth import SUPPORT, staff
from app.audit import log_action
from app.database import Base, get_db

router = APIRouter()
# key: (title, priority) priority 3 = critical, 2 = important, 1 = routine
KINDS = {
    "newip": ("Вход сотрудника с нового адреса", 3), "piialert": ("Много просмотров личных данных", 3), "slow": ("Модерация затянулась", 3),
    "adbudget": ("Бюджет рекламы исчерпан", 3), "appeal": ("Оспаривание блокировки", 2), "report": ("Новая жалоба", 2), "refund": ("Заявка на возврат", 2),
    "ticket": ("Обращение в поддержку", 2), "company": ("Новая компания на проверку", 2), "dating": ("Анкета знакомств на проверку", 1),
    "dating_private": ("Приватное фото на проверку", 1), "resume": ("Резюме на проверку", 1), "employer": ("Заявка работодателя", 1), "adab": ("Итог A/B-теста рекламы", 1),
}
MODES = {"all": ("Всё", 1), "important": ("Важное и срочное", 2), "critical": ("Только срочное", 3), "off": ("Ничего", 4)}


class StaffPref(Base):
    __tablename__ = "staff_prefs"
    admin_tg: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), default="all")
    muted: Mapped[list] = mapped_column(JSON, default=list)


def allows(db: Session, tg_id: str, key: str) -> bool:
    """Should this employee be told about this kind of event?"""
    p = db.get(StaffPref, tg_id)
    if p is None:
        return True
    if key in (p.muted or []):
        return False
    return KINDS.get(key, ("", 2))[1] >= MODES.get(p.mode, MODES["all"])[1]


@router.get("/api/admin/my-notifications")
def mine(actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    p = db.get(StaffPref, actor)
    return {"mode": p.mode if p else "all", "muted": list(p.muted or []) if p else [],
            "modes": [{"key": k, "title": v[0]} for k, v in MODES.items()],
            "kinds": [{"key": k, "title": v[0], "priority": v[1]} for k, v in KINDS.items()]}


class PrefIn(BaseModel):
    mode: str = Field(max_length=10)
    muted: list[str] = []


@router.put("/api/admin/my-notifications")
def save(p: PrefIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    if p.mode not in MODES:
        raise HTTPException(400, "Режим: all, important, critical или off")
    row = db.get(StaffPref, actor) or StaffPref(admin_tg=actor)
    row.mode, row.muted = p.mode, [k for k in dict.fromkeys(p.muted) if k in KINDS]
    db.add(row)
    log_action(db, actor, "staff.notify_prefs", "admin", actor, f"{row.mode}, без: {','.join(row.muted) or '—'}")
    db.commit()
    return {"mode": row.mode, "muted": row.muted}
