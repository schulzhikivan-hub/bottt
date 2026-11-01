"""Synonyms and stop words kept by the owner in the admin panel (no release needed).

Synonyms: one group per line, words separated by « = » or commas: `квартира = 원룸, студия`. A search for any word of the group also
finds the others. Stop words are skipped in a query («работа в Сеуле» → «работа Сеуле» if «в» is a stop word); if every word of a query
is a stop word the query is kept as it is.
The text lives in the settings table; each process re-reads it every 20 seconds."""
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.admin_auth import ADM, staff
from app.audit import log_action
from app.database import SessionLocal, get_db
from app.models import Setting

router = APIRouter()
_cache: dict = {"at": 0.0, "syn": {}, "stop": set()}


def _parse(syn_text: str, stop_text: str) -> tuple[dict[str, set[str]], set[str]]:
    groups: dict[str, set[str]] = {}
    for line in (syn_text or "").splitlines():
        words = {w.strip().lower() for w in line.replace("=", ",").split(",") if w.strip()}
        if len(words) < 2:
            continue
        for w in words:
            groups.setdefault(w, set()).update(words - {w})
    return groups, {w.strip().lower() for w in (stop_text or "").replace("\n", ",").split(",") if w.strip()}


def _load() -> None:
    try:
        with SessionLocal() as db:
            syn = db.get(Setting, "search.synonyms")
            stop = db.get(Setting, "search.stopwords")
            _cache["syn"], _cache["stop"] = _parse(syn.value if syn else "", stop.value if stop else "")
    except Exception:  # noqa: BLE001
        _cache["syn"], _cache["stop"] = {}, set()
    _cache["at"] = time.monotonic()


def _fresh() -> None:
    if time.monotonic() - _cache["at"] > 20:
        _load()


def synonyms_of(word: str) -> set[str]:
    _fresh()
    return _cache["syn"].get(word.lower(), set())


def clean_words(words: list[str]) -> list[str]:
    _fresh()
    kept = [w for w in words if w.lower() not in _cache["stop"]]
    return kept or words


def reset() -> None:
    _cache["at"] = 0.0


@router.get("/api/admin/search-dict")
def get_dict(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    syn, stop = db.get(Setting, "search.synonyms"), db.get(Setting, "search.stopwords")
    return {"synonyms": syn.value if syn else "", "stopwords": stop.value if stop else ""}


class DictIn(BaseModel):
    synonyms: str = Field(default="", max_length=6000)
    stopwords: str = Field(default="", max_length=2000)


@router.put("/api/admin/search-dict")
def put_dict(p: DictIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    for key, val in (("search.synonyms", p.synonyms), ("search.stopwords", p.stopwords)):
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=val))
        else:
            row.value = val
    log_action(db, actor, "search.dict", "search", "dict", f"{len(p.synonyms.splitlines())} групп, стоп-слов: {len([w for w in p.stopwords.split(',') if w.strip()])}")
    db.commit()
    reset()
    return {"saved": True}
