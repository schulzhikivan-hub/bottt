"""Safety of the admin side.

- Who looked at personal data (contacts in a listing, a person's card): every view is written to the journal as `pii.view`;
  more than `security.pii_per_hour` views by one employee in an hour alerts the super administrators (once an hour).
- Sign-ins of employees: where from (address, device) in the journal; an address that this employee never used before
  alerts the super administrators.
- Feature flags: a function can be off, on for staff only, or on for everyone - switched in the panel, no release needed.
  Changes are written to the journal as «было → стало»."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import guard, rules
from app.admin_auth import ADM, SUP, staff
from app.audit import log_action
from app.database import get_db
from app.models import Admin, AuditLog, Setting
from app.payments import now

router = APIRouter()
FLAGS = {
    "company": ("Кабинет компании", "Создание компаний и публикация от компании"),
    "pwlogin": ("Вход по e-mail и паролю", "Форма на сайте и задание пароля в настройках"),
    "ads": ("Реклама в лентах", "Показ рекламных карточек"),
    "dating_services": ("Платные услуги знакомств", "VIP, поднятие, невидимка, кто смотрел"),
}
STATES = {"off": "выключено", "staff": "только сотрудникам", "all": "всем"}


def flag_state(db: Session, name: str) -> str:
    row = db.get(Setting, f"flag.{name}")
    return row.value if row is not None and row.value in STATES else "all"


def flag_on(db: Session, name: str, user_id: str | None) -> bool:
    state = flag_state(db, name)
    if state == "all":
        return True
    if state == "off" or not user_id:
        return False
    boss = db.get(Admin, user_id)
    return boss is not None and bool(boss.active)


def require_flag(db: Session, name: str, user_id: str | None) -> None:
    if not flag_on(db, name, user_id):
        raise HTTPException(403, "Эта функция пока недоступна")


def ip_of(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


def record_login(db: Session, request: Request, tg_id: str, how: str) -> None:
    """Called when an employee signs in. Writes where from; alerts if the address is new for this person."""
    ip, device = ip_of(request), request.headers.get("user-agent", "")[:80]
    known = {r.details.split(" · ")[1] for r in db.scalars(select(AuditLog).where(AuditLog.action == "auth.login", AuditLog.actor == tg_id)).all()
             if " · " in r.details and len(r.details.split(" · ")) > 1}
    log_action(db, tg_id, "auth.login", "admin", tg_id, f"{how} · {ip} · {device}")
    db.commit()
    if known and ip and ip not in known:
        from app import adminplus
        adminplus.notify_staff(db, f"🔐 Вход сотрудника …{tg_id[-4:]} с нового адреса {ip}. Если это не он, закройте доступ в «Сотрудники».", "newip", ("super_admin",))


def note_pii_view(db: Session, actor: str, kind: str, object_id: str) -> None:
    """One view of personal data. Alerts the super administrators when one employee looks at too many in an hour."""
    log_action(db, actor, "pii.view", kind, object_id, "")
    db.commit()
    since = now() - dt.timedelta(hours=1)
    n = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.actor == actor, AuditLog.action == "pii.view", AuditLog.created_at >= since)) or 0
    if n > rules.val("security.pii_per_hour"):
        from app import adminplus
        adminplus.notify_staff(db, f"👁 Сотрудник …{actor[-4:]} за час открыл личные данные {n} раз. Проверьте журнал просмотров.", "piialert", ("super_admin",))


@router.get("/api/admin/security/pii-views")
def pii_views(limit: int = Query(default=100, ge=1, le=500), who: str = Query(default="", max_length=40), actor: str = Depends(staff(*SUP)),
              db: Session = Depends(get_db)):
    """Who looked at personal data (super administrator only). Reading this list is itself written to the journal."""
    stmt = select(AuditLog).where(AuditLog.action == "pii.view")
    if who:
        stmt = stmt.where(AuditLog.actor == who)
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit)).all()
    log_action(db, actor, "pii.list", "security", "pii", f"{len(rows)}")
    db.commit()
    by = {}
    for r in rows:
        by[r.actor] = by.get(r.actor, 0) + 1
    return {"by_staff": [{"staff": k, "views": v} for k, v in sorted(by.items(), key=lambda kv: -kv[1])],
            "items": [{"at": r.created_at.isoformat(), "staff": r.actor, "kind": r.object_type, "id": r.object_id} for r in rows]}


@router.get("/api/admin/security/logins")
def logins(limit: int = Query(default=100, ge=1, le=500), actor: str = Depends(staff(*SUP)), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login").order_by(AuditLog.id.desc()).limit(limit)).all()
    out = []
    for r in rows:
        parts = r.details.split(" · ")
        out.append({"at": r.created_at.isoformat(), "staff": r.actor, "how": parts[0], "ip": parts[1] if len(parts) > 1 else "", "device": parts[2] if len(parts) > 2 else ""})
    return out


@router.get("/api/admin/flags")
def list_flags(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [{"key": k, "title": t, "hint": h, "state": flag_state(db, k), "states": STATES} for k, (t, h) in FLAGS.items()]


class FlagIn(BaseModel):
    state: str


@router.put("/api/admin/flags/{name}")
def set_flag(name: str, p: FlagIn, actor: str = Depends(staff(*SUP)), db: Session = Depends(get_db)):
    if name not in FLAGS or p.state not in STATES:
        raise HTTPException(400, "Неизвестная функция или состояние (off, staff, all)")
    old = flag_state(db, name)
    row = db.get(Setting, f"flag.{name}")
    if row is None:
        db.add(Setting(key=f"flag.{name}", value=p.state))
    else:
        row.value = p.state
    log_action(db, actor, "flag.change", "flag", name, f"{old} → {p.state}")
    db.commit()
    return {"key": name, "state": p.state}
