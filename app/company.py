"""Company account («кабинет компании»).

A company is its own thing, not a flag on a person: it has a profile, a team with roles, its own listings, its own applications
inbox and its OWN balance in Stars (separate from the personal balance of any member).

Roles: owner (everything) · admin (team, profile, money) · recruiter (listings and applications) · viewer (look only).
A listing posted «as a company» belongs to the company: the person who made it keeps being the author in the journal, but if the
person leaves the team the access goes with them.

The old «employer status» of a person is kept in step with the first company of that person, so the verified badge, the
admin queue «Работодатели» and the bot keep working."""
import datetime as dt
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import Integer, String, Text, DateTime, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import guard, photos, rules
from app.admin_auth import ADM, MOD, staff
from app.audit import log_action
from app.auth import optional_user_id
from app.config import settings
from app.database import Base, get_db
from app.models import Application, Listing, Payment, User, _now
from app.payments import aware, now
from app.users import writer

router = APIRouter()
ROLES = {"owner": "Владелец", "admin": "Админ", "recruiter": "Рекрутёр", "viewer": "Наблюдатель"}
WRITE = {"owner", "admin", "recruiter"}          # may post, edit and answer applications
MONEY = {"owner", "admin"}                       # may spend the company balance and pay for promotion
TEAM = {"owner", "admin"}
COMPANY_LIVE = {"verified"}


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    reg_no: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)     # pending | verified | rejected | suspended
    note: Mapped[str] = mapped_column(String(300), default="")                        # reason shown to the owner
    owner_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    balance_stars: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # the company's own money, not a person's
    logo_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CompanyMember(Base):
    __tablename__ = "company_members"
    __table_args__ = (UniqueConstraint("company_id", "tg_id", name="uq_company_member"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, index=True)
    tg_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(10), default="recruiter")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CompanyInvite(Base):
    __tablename__ = "company_invites"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(10), default="recruiter")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[str | None] = mapped_column(String(40), nullable=True)


# ---------- who may do what ----------
def role_of(db: Session, company_id: int | None, tg_id: str) -> str | None:
    if not company_id:
        return None
    row = db.scalar(select(CompanyMember).where(CompanyMember.company_id == company_id, CompanyMember.tg_id == tg_id))
    return row.role if row else None


def can_manage(db: Session, user_id: str, listing: Listing, write: bool = True) -> bool:
    """A personal listing is managed by its author; a company listing only by the team (by role), never by a person who left."""
    if listing.company_id:
        role = role_of(db, listing.company_id, user_id)
        return role in WRITE if write else role is not None
    return listing.author_telegram_id == user_id


def can_pay(db: Session, user_id: str, listing: Listing) -> bool:
    if listing.company_id:
        return role_of(db, listing.company_id, user_id) in MONEY
    return listing.author_telegram_id == user_id


def _company(db: Session, company_id: int, user_id: str, roles: set[str] | None = None) -> tuple[Company, str]:
    c = db.get(Company, company_id)
    role = role_of(db, company_id, user_id)
    if c is None or role is None:
        raise HTTPException(404, "Компания не найдена")
    if roles is not None and role not in roles:
        raise HTTPException(403, "Это действие недоступно для вашей роли в компании")
    return c, role


def my_posting_companies(db: Session, user_id: str) -> list[Company]:
    """Companies the person may post for: verified, and the role allows posting."""
    out = []
    for m in db.scalars(select(CompanyMember).where(CompanyMember.tg_id == user_id, CompanyMember.role.in_(WRITE))).all():
        c = db.get(Company, m.company_id)
        if c is not None and c.status in COMPANY_LIVE:
            out.append(c)
    return out


# ---------- the old employer status stays in step with the first company ----------
def sync_company_from_employer(db: Session, user: User) -> Company | None:
    """The person applied through the old form (or was approved in the old queue): mirror it in a company."""
    if user.employer_status not in {"pending", "approved", "rejected"} or not user.company_name:
        return None
    c = db.scalar(select(Company).where(Company.owner_tg_id == user.tg_id).order_by(Company.id))
    status = {"pending": "pending", "approved": "verified", "rejected": "rejected"}[user.employer_status]
    if c is None:
        c = Company(name=user.company_name, reg_no=user.registration_no, owner_tg_id=user.tg_id, status=status)
        db.add(c)
        db.flush()
        db.add(CompanyMember(company_id=c.id, tg_id=user.tg_id, role="owner"))
    else:
        c.status = status
        c.name = user.company_name or c.name
        c.reg_no = user.registration_no or c.reg_no
    if status == "verified":                                   # vacancies written before the company existed belong to it
        for item in db.scalars(select(Listing).where(Listing.author_telegram_id == user.tg_id, Listing.category == "job",
                                                     Listing.company_id.is_(None))).all():
            item.company_id = c.id
    db.commit()
    return c


def sync_employer_from_company(db: Session, c: Company) -> None:
    owner = db.get(User, c.owner_tg_id)
    first = db.scalar(select(Company.id).where(Company.owner_tg_id == c.owner_tg_id).order_by(Company.id))
    if owner is None or first != c.id:
        return
    owner.company_name, owner.registration_no = c.name, c.reg_no
    owner.employer_status = {"verified": "approved", "pending": "pending"}.get(c.status, "rejected")


def migrate_employers(db: Session) -> int:
    """Run at start: every person who already was an employer gets a company (idempotent)."""
    made = 0
    for u in db.scalars(select(User).where(User.employer_status.in_(["pending", "approved"]), User.company_name != "")).all():
        if db.scalar(select(Company.id).where(Company.owner_tg_id == u.tg_id)) is None:
            sync_company_from_employer(db, u)
            made += 1
    return made


# ---------- the person's side ----------
class CompanyIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    reg_no: str = Field(min_length=3, max_length=80)
    description: str = Field(default="", max_length=1500)
    website: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=100)


def _view(db: Session, c: Company, role: str) -> dict:
    out = {"id": c.id, "name": c.name, "logo": photos.url(c.logo_key) if c.logo_key else None, "status": c.status, "role": role, "role_title": ROLES.get(role, role), "note": c.note,
           "verified": c.status == "verified"}
    if role in MONEY:
        out["balance"] = c.balance_stars
    return out


@router.post("/api/company", status_code=201)
def create_company(payload: CompanyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    from app import security
    security.require_flag(db, "company", user_id)
    guard.rate_limit(user_id, "company_create", 5, 86400)
    guard.check_text(payload.name, payload.description)
    mine = db.scalar(select(func.count()).select_from(Company).where(Company.owner_tg_id == user_id)) or 0
    if mine >= rules.val("company.max_owned"):
        raise HTTPException(409, f"Можно создать не больше {rules.val('company.max_owned')} компаний")
    if payload.website and not payload.website.startswith(("https://", "http://")):
        raise HTTPException(400, "Сайт должен начинаться с https://")
    c = Company(name=payload.name.strip(), reg_no=payload.reg_no.strip(), description=payload.description.strip(),
                website=payload.website.strip(), city=payload.city.strip(), owner_tg_id=user_id, status="pending")
    db.add(c)
    db.flush()
    db.add(CompanyMember(company_id=c.id, tg_id=user_id, role="owner"))
    user = db.get(User, user_id)
    if user is not None and user.employer_status in {"none", "rejected", None, ""}:     # keeps the old queue and badge working
        user.employer_status, user.company_name, user.registration_no = "pending", c.name, c.reg_no
        user.contact_person = user.contact_person or (user.display_name or "")
    db.commit()
    from app import adminplus
    adminplus.notify_staff(db, "Новая компания ждёт проверки (админка → Компании).", "company")
    return _view(db, c, "owner")


@router.get("/api/company/mine")
def my_companies(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    out = []
    for m in db.scalars(select(CompanyMember).where(CompanyMember.tg_id == user_id).order_by(CompanyMember.id)).all():
        c = db.get(Company, m.company_id)
        if c is not None:
            out.append(_view(db, c, m.role))
    return out


@router.get("/api/company/{company_id}")
def company_overview(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id)
    ids = list(db.scalars(select(Listing.id).where(Listing.company_id == c.id)).all())
    by_status = dict(db.execute(select(Listing.status, func.count()).where(Listing.company_id == c.id).group_by(Listing.status)).all())
    new_apps = db.scalar(select(func.count()).select_from(Application).where(Application.listing_id.in_(ids or [0]), Application.status == "new")) or 0
    all_apps = db.scalar(select(func.count()).select_from(Application).where(Application.listing_id.in_(ids or [0]), Application.status != "withdrawn")) or 0
    views = db.scalar(select(func.coalesce(func.sum(Listing.views), 0)).where(Listing.company_id == c.id)) or 0
    members = db.scalar(select(func.count()).select_from(CompanyMember).where(CompanyMember.company_id == c.id)) or 0
    return {**_view(db, c, role), "description": c.description, "website": c.website, "city": c.city, "reg_no": c.reg_no if role in TEAM else "",
            "listings": {"total": len(ids), "active": by_status.get("approved", 0), "pending": by_status.get("pending", 0),
                         "other": len(ids) - by_status.get("approved", 0) - by_status.get("pending", 0)},
            "applications": {"new": new_apps, "total": all_apps}, "views": int(views), "members": members,
            "limits": {"members": rules.val("company.max_members")}}


@router.put("/api/company/{company_id}")
def update_company(company_id: int, payload: CompanyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id, TEAM)
    guard.check_text(payload.name, payload.description)
    if payload.website and not payload.website.startswith(("https://", "http://")):
        raise HTTPException(400, "Сайт должен начинаться с https://")
    reverify = c.status == "verified" and payload.reg_no.strip() != c.reg_no
    c.name, c.reg_no, c.description = payload.name.strip(), payload.reg_no.strip(), payload.description.strip()
    c.website, c.city = payload.website.strip(), payload.city.strip()
    if reverify:                                   # the registration number changed: the company is checked again
        c.status, c.note = "pending", "Номер регистрации изменён: компания на повторной проверке"
    sync_employer_from_company(db, c)
    db.commit()
    return _view(db, c, role)


@router.get("/api/company/{company_id}/listings")
def company_listings(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    from app import catalog
    c, role = _company(db, company_id, user_id)
    rows = db.scalars(select(Listing).where(Listing.company_id == c.id, Listing.status != "archived").order_by(Listing.id.desc()).limit(200)).all()
    items = catalog.with_promo(db, rows, owner=True)
    names = {u.tg_id: (u.display_name or "") for u in db.scalars(select(User).where(User.tg_id.in_({r.author_telegram_id for r in rows}))).all()} if rows else {}
    return [{**o.model_dump(mode="json"), "posted_by": names.get(o.id and next((r.author_telegram_id for r in rows if r.id == o.id), ""), "") or "сотрудник"} for o in items]


@router.get("/api/company/{company_id}/applications")
def company_applications(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id)
    listings = {x.id: x for x in db.scalars(select(Listing).where(Listing.company_id == c.id)).all()}
    if not listings:
        return []
    rows = db.scalars(select(Application).join(User, User.tg_id == Application.applicant_tg_id)
                      .where(Application.listing_id.in_(list(listings)), Application.status != "withdrawn",
                             User.blocked.is_(False), User.deleted_at.is_(None)).order_by(Application.id.desc()).limit(200)).all()
    return [{"id": a.id, "listing_id": a.listing_id, "listing_title": listings[a.listing_id].title, "name": a.name, "message": a.message,
             "contact": a.contact, "visa": a.visa, "city": a.city, "status": a.status, "reply": a.reply,
             "created_at": a.created_at.isoformat(), "can_answer": role in WRITE} for a in rows]


# ---------- team ----------
def _name(u: User | None, tg: str) -> str:
    return (u.display_name if u and u.display_name else f"Участник …{tg[-4:]}")


@router.get("/api/company/{company_id}/team")
def team(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id)
    rows = db.scalars(select(CompanyMember).where(CompanyMember.company_id == c.id).order_by(CompanyMember.id)).all()
    users = {u.tg_id: u for u in db.scalars(select(User).where(User.tg_id.in_([m.tg_id for m in rows]))).all()}
    return {"my_role": role, "can_edit": role in TEAM, "roles": [{"key": k, "title": v} for k, v in ROLES.items() if k != "owner"],
            "members": [{"tg_id": m.tg_id, "name": _name(users.get(m.tg_id), m.tg_id), "role": m.role, "role_title": ROLES.get(m.role, m.role),
                         "me": m.tg_id == user_id} for m in rows]}


class InviteIn(BaseModel):
    role: str = Field(default="recruiter", pattern="^(admin|recruiter|viewer)$")


@router.post("/api/company/{company_id}/invite", status_code=201)
def invite(company_id: int, payload: InviteIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id, TEAM)
    if payload.role == "admin" and role != "owner":
        raise HTTPException(403, "Назначить админа может только владелец")
    count = db.scalar(select(func.count()).select_from(CompanyMember).where(CompanyMember.company_id == c.id)) or 0
    if count >= rules.val("company.max_members"):
        raise HTTPException(409, f"В команде не больше {rules.val('company.max_members')} человек")
    code = secrets.token_urlsafe(10)
    db.add(CompanyInvite(code=code, company_id=c.id, role=payload.role, expires_at=now() + dt.timedelta(days=7)))
    db.commit()
    return {"code": code, "role": payload.role, "link": f"{settings.webapp_url.rstrip('/')}/?join={code}", "days": 7}


class JoinIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


@router.post("/api/company/join")
def join(payload: JoinIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    guard.rate_limit(user_id, "company_join", 10, 3600)
    inv = db.get(CompanyInvite, payload.code.strip())
    if inv is None or inv.used_by is not None or aware(inv.expires_at) < now():
        raise HTTPException(404, "Приглашение недействительно или уже использовано")
    c = db.get(Company, inv.company_id)
    if c is None:
        raise HTTPException(404, "Компания не найдена")
    if role_of(db, c.id, user_id):
        raise HTTPException(409, "Вы уже в этой компании")
    count = db.scalar(select(func.count()).select_from(CompanyMember).where(CompanyMember.company_id == c.id)) or 0
    if count >= rules.val("company.max_members"):
        raise HTTPException(409, "В команде нет мест")
    db.add(CompanyMember(company_id=c.id, tg_id=user_id, role=inv.role))
    inv.used_by = user_id
    db.commit()
    guard.notify_user(db, c.owner_tg_id, f"В команду компании «{c.name}» вошёл новый участник ({ROLES.get(inv.role, inv.role)}).", "applications")
    return _view(db, c, inv.role)


class RoleIn(BaseModel):
    role: str = Field(pattern="^(admin|recruiter|viewer)$")


@router.put("/api/company/{company_id}/team/{tg_id}")
def change_role(company_id: int, tg_id: str, payload: RoleIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id, TEAM)
    m = db.scalar(select(CompanyMember).where(CompanyMember.company_id == c.id, CompanyMember.tg_id == tg_id))
    if m is None or m.role == "owner":
        raise HTTPException(404, "Участник не найден")
    if role != "owner" and (m.role == "admin" or payload.role == "admin"):
        raise HTTPException(403, "Роли админа меняет только владелец")
    m.role = payload.role
    db.commit()
    return {"tg_id": tg_id, "role": m.role}


@router.delete("/api/company/{company_id}/team/{tg_id}")
def remove_member(company_id: int, tg_id: str, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    role = role_of(db, company_id, user_id)
    if c is None or role is None:
        raise HTTPException(404, "Компания не найдена")
    m = db.scalar(select(CompanyMember).where(CompanyMember.company_id == c.id, CompanyMember.tg_id == tg_id))
    if m is None:
        raise HTTPException(404, "Участник не найден")
    if m.role == "owner":
        raise HTTPException(409, "Владельца убрать нельзя: сначала передайте владение")
    if tg_id != user_id and role not in TEAM:                         # leaving yourself is always allowed
        raise HTTPException(403, "Убирать участников могут владелец и админы")
    if tg_id != user_id and role == "admin" and m.role == "admin":
        raise HTTPException(403, "Админа убирает только владелец")
    db.delete(m)
    db.commit()
    if tg_id != user_id:
        guard.notify_user(db, tg_id, f"Ваш доступ к компании «{c.name}» закрыт.", "applications")
    return {"removed": True}


class TransferIn(BaseModel):
    tg_id: str = Field(min_length=1, max_length=40)


@router.post("/api/company/{company_id}/transfer")
def transfer_ownership(company_id: int, payload: TransferIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id, {"owner"})
    target = db.scalar(select(CompanyMember).where(CompanyMember.company_id == c.id, CompanyMember.tg_id == payload.tg_id))
    if target is None or payload.tg_id == user_id:
        raise HTTPException(404, "Выберите другого участника команды")
    me_row = db.scalar(select(CompanyMember).where(CompanyMember.company_id == c.id, CompanyMember.tg_id == user_id))
    target.role, me_row.role, c.owner_tg_id = "owner", "admin", payload.tg_id
    db.commit()
    return {"owner": payload.tg_id}


# ---------- logo ----------
@router.post("/api/company/{company_id}/logo", status_code=201)
async def set_logo(company_id: int, file: UploadFile = File(...), user_id: str = Depends(writer), db: Session = Depends(get_db)):
    import io
    import uuid
    from PIL import Image, ImageOps
    c, role = _company(db, company_id, user_id, TEAM)
    data = await file.read(photos.MAX_UPLOAD_BYTES + 1)
    if len(data) > photos.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Фото больше 8 МБ")
    full, _thumb = photos.process_image(data)
    img = ImageOps.fit(Image.open(io.BytesIO(full)).convert("RGB"), (320, 320))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    key = uuid.uuid4().hex
    (photos.photo_root() / f"{key}.jpg").write_bytes(out.getvalue())
    if c.logo_key:
        photos.delete_files(c.logo_key)
    c.logo_key = key
    db.commit()
    return {"logo": photos.url(key)}


@router.delete("/api/company/{company_id}/logo")
def remove_logo(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id, TEAM)
    if c.logo_key:
        photos.delete_files(c.logo_key)
        c.logo_key = None
        db.commit()
    return {"logo": None}


# ---------- closing ----------
@router.post("/api/company/{company_id}/close")
def close_company(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """The owner closes the company: its listings stop being shown, the team loses access. The balance must be empty first
    (money is not made to disappear): ask support to pay it out or spend it."""
    c, role = _company(db, company_id, user_id, {"owner"})
    if (c.balance_stars or 0) > 0:
        raise HTTPException(409, f"На балансе компании {c.balance_stars} ⭐. Потратьте их или попросите поддержку вернуть, потом закрывайте")
    for item in db.scalars(select(Listing).where(Listing.company_id == c.id, Listing.status.in_(["approved", "pending", "draft", "needs_fix"]))).all():
        item.status = "closed"
    for m in db.scalars(select(CompanyMember).where(CompanyMember.company_id == c.id)).all():
        db.delete(m)
    c.status, c.note = "closed", "Закрыта владельцем"
    sync_employer_from_company(db, c)
    log_action(db, user_id, "company.close", "company", c.id, c.name)
    db.commit()
    return {"closed": True}


def notify_team(db: Session, listing: Listing, text: str, kind: str, skip: str = "") -> None:
    """A company listing has no single owner: everybody who may answer is told (the author of a personal listing is told alone)."""
    if not listing.company_id:
        if listing.author_telegram_id != skip:
            guard.notify_user(db, listing.author_telegram_id, text, kind)
        return
    for m in db.scalars(select(CompanyMember).where(CompanyMember.company_id == listing.company_id, CompanyMember.role.in_(WRITE))).all():
        if m.tg_id != skip:
            guard.notify_user(db, m.tg_id, text, kind)


# ---------- money of the company ----------
class TopupIn(BaseModel):
    stars: int


@router.post("/api/company/{company_id}/topup")
def company_topup(company_id: int, payload: TopupIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Put Stars on the COMPANY balance (owner and admins). The payment is made by the person in Telegram; the money goes to the company."""
    from app import payments
    c, role = _company(db, company_id, user_id, MONEY)
    if c.status == "closed":
        raise HTTPException(409, "Компания закрыта")
    try:
        pay, url = payments.create_topup(db, user_id, payload.stars, c.id)
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
    return {"payment_id": pay.id, "invoice_url": url, "stars": pay.stars}


@router.get("/api/companies/{company_id}/logo")
def public_logo_probe(company_id: int, db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    return {"logo": photos.url(c.logo_key) if c and c.status == "verified" and c.logo_key else None}


# ---------- money of the company (history) ----------
@router.get("/api/company/{company_id}/balance")
def company_balance(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = _company(db, company_id, user_id, MONEY)
    from app.models import Pricing
    rows = db.scalars(select(Payment).where(Payment.company_id == c.id).order_by(Payment.id.desc()).limit(50)).all()
    titles = {x.code: x.title for x in db.scalars(select(Pricing)).all()}
    titles["topup"] = "Пополнение баланса"
    listings = {x.id: x.title for x in db.scalars(select(Listing).where(Listing.id.in_([p.listing_id for p in rows if p.listing_id] or [0]))).all()}
    out = []
    for p in rows:
        status = p.status
        if status == "pending" and aware(p.created_at) < now() - dt.timedelta(hours=1):
            status = "abandoned"            # the invoice was opened and never paid
        out.append({"id": p.id, "service": titles.get(p.service_code, p.service_code), "stars": p.stars, "from_balance": p.balance_used,
                    "status": status, "by": p.user_tg_id[-4:], "listing": listings.get(p.listing_id, ""),
                    "at": str(p.paid_at or p.created_at)})
    return {"balance": c.balance_stars, "note": "Баланс компании отдельный: он не смешивается с личным балансом сотрудников. Пополняется возвратами за неиспользованные услуги и начислениями поддержки.",
            "history": out}


# ---------- public page ----------
@router.get("/api/companies/{company_id}")
def public_company(company_id: int, db: Session = Depends(get_db)):
    from app import catalog
    c = db.get(Company, company_id)
    if c is None or c.status != "verified":
        raise HTTPException(404, "Компания не найдена")
    rows = db.scalars(select(Listing).where(Listing.company_id == c.id, Listing.status == "approved").order_by(Listing.id.desc()).limit(50)).all()
    live = [x for x in rows if x.expires_at is None or aware(x.expires_at) > now()]
    return {"id": c.id, "name": c.name, "logo": photos.url(c.logo_key) if c.logo_key else None, "description": c.description, "website": c.website, "city": c.city, "verified": True,
            "since": c.created_at.date().isoformat(), "listings": [o.model_dump(mode="json") for o in catalog.with_promo(db, live)]}


# ---------- administrators ----------
@router.get("/api/admin/companies")
def admin_companies(status: str = "pending", actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    stmt = select(Company).order_by(Company.id.desc()).limit(100)
    if status != "all":
        stmt = select(Company).where(Company.status == status).order_by(Company.id.desc()).limit(100)
    out = []
    for c in db.scalars(stmt).all():
        owner = db.get(User, c.owner_tg_id)
        out.append({"id": c.id, "name": c.name, "reg_no": c.reg_no, "status": c.status, "note": c.note, "owner": c.owner_tg_id,
                    "owner_name": owner.display_name if owner else "", "description": c.description, "website": c.website, "city": c.city, "balance": c.balance_stars, "created_at": c.created_at.isoformat(),
                    "listings": db.scalar(select(func.count()).select_from(Listing).where(Listing.company_id == c.id)) or 0,
                    "members": db.scalar(select(func.count()).select_from(CompanyMember).where(CompanyMember.company_id == c.id)) or 0})
    return out


class CreditIn(BaseModel):
    stars: int = Field(ge=1, le=100000)
    note: str = Field(default="", max_length=200)


@router.post("/api/admin/companies/{company_id}/credit")
def admin_company_credit(company_id: int, payload: CreditIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "Компания не найдена")
    c.balance_stars = (c.balance_stars or 0) + payload.stars
    log_action(db, actor, "company.credit", "company", c.id, f"+{payload.stars} {payload.note}".strip())
    db.commit()
    guard.notify_user(db, c.owner_tg_id, f"На баланс компании «{c.name}» зачислено {payload.stars} ⭐." + (f" {payload.note}" if payload.note else ""), "payments")
    return {"balance": c.balance_stars}


class DecisionIn(BaseModel):
    reason: str = Field(default="", max_length=300)


@router.post("/api/admin/companies/{company_id}/{decision}")
def admin_company_decide(company_id: int, decision: str, payload: DecisionIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    if decision not in {"approve", "reject", "suspend", "restore"}:
        raise HTTPException(400, "Решение: approve, reject, suspend или restore")
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "Компания не найдена")
    reason = payload.reason.strip()
    if decision in {"reject", "suspend"} and len(reason) < 3:
        raise HTTPException(400, "Укажите причину: её увидит владелец")
    if decision in {"approve", "restore"}:
        c.status, c.note = "verified", ""
        for item in db.scalars(select(Listing).where(Listing.company_id == c.id, Listing.status == "hidden", Listing.note == "company suspended")).all():
            item.status, item.note = "approved", ""                    # only what the suspension hid comes back
        text = f"Компания «{c.name}» подтверждена: на её объявлениях значок «Проверенная компания»."
    elif decision == "reject":
        c.status, c.note = "rejected", reason
        text = f"Компанию «{c.name}» не подтвердили: {reason}. Исправьте данные и отправьте снова."
    else:
        c.status, c.note = "suspended", reason
        for item in db.scalars(select(Listing).where(Listing.company_id == c.id, Listing.status == "approved")).all():
            item.status, item.note = "hidden", "company suspended"
        text = f"Компания «{c.name}» приостановлена: {reason}. Объявления скрыты. Можно обратиться в поддержку."
    sync_employer_from_company(db, c)
    log_action(db, actor, f"company.{decision}", "company", c.id, f"{c.name} {reason}".strip())
    db.commit()
    guard.notify_user(db, c.owner_tg_id, text, "moderation")
    return {"id": c.id, "status": c.status}
