"""Saved views of the admin lists: filters + sorting + the chosen columns under a name.

- every employee keeps up to 20 views per list; a view can be shared: then all staff of that list see it (and can use it, not change it);
- only the owner (or the super administrator) deletes or unshares a view;
- the view stores only the state of the page, never any data."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, JSON, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.admin_auth import SUPPORT, staff
from app.audit import log_action
from app.database import Base, get_db
from app.models import Admin, _now
import datetime as dt

router = APIRouter()
LIMIT = 20
TABS = {"users", "listings", "section", "reports", "payments", "refunds"}


class SavedView(Base):
    __tablename__ = "saved_views"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(40), index=True)
    tab: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(60))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


def row(v: SavedView, me: str) -> dict:
    return {"id": v.id, "name": v.name, "state": v.state, "shared": v.shared, "mine": v.owner == me, "owner": "…" + v.owner[-4:]}


@router.get("/api/admin/views")
def list_views(tab: str = Query(max_length=20), actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    rows = db.scalars(select(SavedView).where(SavedView.tab == tab).order_by(SavedView.id)).all()
    return {"mine": [row(v, actor) for v in rows if v.owner == actor], "shared": [row(v, actor) for v in rows if v.shared and v.owner != actor]}


class ViewIn(BaseModel):
    tab: str = Field(max_length=20)
    name: str = Field(min_length=2, max_length=60)
    state: dict = {}
    shared: bool = False


@router.post("/api/admin/views", status_code=201)
def save_view(p: ViewIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    if p.tab not in TABS:
        raise HTTPException(400, "Для этого списка представления не поддерживаются")
    mine = db.scalars(select(SavedView).where(SavedView.owner == actor, SavedView.tab == p.tab)).all()
    if len(mine) >= LIMIT:
        raise HTTPException(409, f"Не больше {LIMIT} сохранённых видов на список: удалите ненужные")
    if any(v.name.lower() == p.name.strip().lower() for v in mine):
        raise HTTPException(409, "Вид с таким названием уже есть")
    if len(str(p.state)) > 4000:
        raise HTTPException(400, "Слишком большое состояние")
    v = SavedView(owner=actor, tab=p.tab, name=p.name.strip(), state=p.state, shared=p.shared)
    db.add(v)
    db.commit()
    return row(v, actor)


def _own(db: Session, view_id: int, actor: str) -> SavedView:
    v = db.get(SavedView, view_id)
    boss = db.get(Admin, actor)
    if v is None or (v.owner != actor and not (boss is not None and boss.role == "super_admin")):
        raise HTTPException(404, "Вид не найден")
    return v


@router.delete("/api/admin/views/{view_id}")
def delete_view(view_id: int, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    v = _own(db, view_id, actor)
    db.delete(v)
    db.commit()
    return {"deleted": True}


class ShareIn(BaseModel):
    shared: bool


@router.put("/api/admin/views/{view_id}/share")
def share_view(view_id: int, p: ShareIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    v = _own(db, view_id, actor)
    v.shared = p.shared
    log_action(db, actor, "view.share", "view", v.id, f"{v.tab}: {v.name} -> {p.shared}")
    db.commit()
    return row(v, actor)
