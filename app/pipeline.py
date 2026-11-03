"""The employer's private side of applications: a stage in the hiring funnel and a note nobody else sees
(the applicant only ever sees «приглашён» / «отказ» and the reply)."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Integer, String, DateTime, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import company
from app.audit import log_action
from app.database import Base, get_db
from app.models import Application, Listing
from app.payments import now
from app.users import writer

router = APIRouter()
STAGES = {"new": "Новые", "contact": "Связались", "interview": "Собеседование", "offer": "Оффер", "hired": "Приняты", "declined": "Не подошли"}


class ApplicationNote(Base):
    __tablename__ = "application_notes"
    application_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(12), default="new")
    note: Mapped[str] = mapped_column(String(1000), default="")
    updated_by: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


def for_applications(db: Session, ids: list[int]) -> dict[int, ApplicationNote]:
    if not ids:
        return {}
    return {n.application_id: n for n in db.scalars(select(ApplicationNote).where(ApplicationNote.application_id.in_(ids))).all()}


class NoteIn(BaseModel):
    stage: str | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.put("/api/company/{company_id}/applications/{app_id}/note")
def set_note(company_id: int, app_id: int, p: NoteIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.WRITE)
    a = db.get(Application, app_id)
    listing = db.get(Listing, a.listing_id) if a else None
    if a is None or listing is None or listing.company_id != c.id:
        raise HTTPException(404, "Отклик не найден")
    if p.stage is not None and p.stage not in STAGES:
        raise HTTPException(400, "Неизвестный этап")
    row = db.get(ApplicationNote, app_id)
    if row is None:
        row = ApplicationNote(application_id=app_id, stage="new", note="")
        db.add(row)
    if p.stage is not None:
        row.stage = p.stage
    if p.note is not None:
        row.note = p.note.strip()
    row.updated_by, row.updated_at = user_id, now()
    log_action(db, user_id, "company.stage", "company", c.id, f"отклик #{app_id}: {row.stage}")
    db.commit()
    return {"stage": row.stage, "note": row.note}
