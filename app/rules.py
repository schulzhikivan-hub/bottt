"""Rules the owner changes in the admin panel without a release. One registry: key, type, default, limits, text.

Read with rules.val("search.window"). Values are cached for a few seconds in each process; a change made in the panel
applies at once in the process that saved it and within the cache time in the others. If the database can not be read
(tests of pure functions, start-up) the default is used.
Changes are written to the journal as «было → стало»."""
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.admin_auth import ADM, staff
from app.audit import log_action
from app.database import SessionLocal, get_db
from app.models import Setting

router = APIRouter()
CACHE_SECONDS = 10


@dataclass(frozen=True)
class Rule:
    key: str
    group: str
    label: str
    kind: str            # int | bool
    default: int | bool
    lo: int = 0
    hi: int = 100000
    hint: str = ""


RULES = [
    # --- выдача и платные места ---
    Rule("search.paid_rule", "Выдача и платные места", "Правило «не больше N платных в окне»", "bool", True, hint="Выключено: VIP и поднятые идут как есть, без ограничения"),
    Rule("search.window", "Выдача и платные места", "Окно, мест подряд", "int", 5, 2, 20),
    Rule("search.paid_in_window", "Выдача и платные места", "Платных в окне, не больше", "int", 2, 1, 10, "VIP и поднятие считаются платными; закреплённые и реклама идут отдельными полосами"),
    Rule("search.cap_rule", "Выдача и платные места", "Правило «доля платных на странице»", "bool", True),
    Rule("search.cap_pct", "Выдача и платные места", "Доля платных на странице, %", "int", 30, 5, 100),
    Rule("search.max_pinned", "Выдача и платные места", "Закреплённых над списком, не больше", "int", 2, 0, 10),
    Rule("search.ad_after", "Выдача и платные места", "Реклама: после какого по счёту обычного", "int", 3, 3, 30, "Не ниже 3: реклама никогда не стоит в первых трёх местах"),
    Rule("search.ad_every", "Выдача и платные места", "Реклама: не чаще, чем через N карточек", "int", 6, 3, 30),
    Rule("search.ad_on_page", "Выдача и платные места", "Рекламы на странице списка, не больше", "int", 3, 0, 10),
    Rule("search.ad_home", "Выдача и платные места", "Рекламы на главной, не больше", "int", 2, 0, 10),
    Rule("search.ad_all_pages", "Выдача и платные места", "Показывать рекламу не только на первой странице", "bool", False),
    # --- VIP и сроки ---
    Rule("vip.freeze", "VIP и сроки", "Платное время стоит, пока объект скрыт, на паузе или в проверке", "bool", True, hint="Выключено: время продолжает идти"),
    # --- знакомства ---
    Rule("dating.like_per_hour", "Знакомства", "Симпатий в час на человека", "int", 60, 5, 1000),
    Rule("dating.cancel_minutes", "Знакомства", "Сколько минут можно отменить симпатию", "int", 5, 0, 60),
    # --- модерация и жалобы ---
    Rule("moderation.undo_minutes", "Модерация и жалобы", "Сколько минут можно отменить решение модератора", "int", 30, 1, 1440),
    Rule("moderation.slow_hours", "Модерация и жалобы", "Через сколько часов ожидания сказать автору «проверка затянулась»", "int", 24, 1, 240),
    Rule("moderation.escalate_hours", "Модерация и жалобы", "Через сколько часов ожидания тревожить администраторов", "int", 48, 2, 480),
    Rule("moderation.claim_minutes", "Модерация и жалобы", "На сколько минут объект «берётся в работу»", "int", 15, 1, 240),
    Rule("reports.to_hide", "Модерация и жалобы", "Жалоб от разных людей, после которых объект скрывается", "int", 3, 1, 50),
    # --- деньги ---
    Rule("refund.day_count", "Деньги", "Возвратов в сутки на одного сотрудника, не больше", "int", 20, 1, 1000, "Больше — нужен суперадмин"),
    # --- уведомления ---
    Rule("notify.quiet_on", "Уведомления", "Тихие часы включены", "bool", True),
    Rule("notify.quiet_from", "Уведомления", "Тихие часы с (час по Корее)", "int", 22, 0, 23),
    Rule("notify.quiet_to", "Уведомления", "Тихие часы до (час по Корее)", "int", 9, 0, 23),
    # --- хранение данных ---
    Rule("keep.notice_days", "Хранение данных", "Уведомления в ленте, дней", "int", 90, 7, 3650),
    Rule("keep.view_days", "Хранение данных", "Просмотры анкет, дней", "int", 90, 7, 3650),
    Rule("keep.ticket_days", "Хранение данных", "Закрытые обращения, дней", "int", 365, 30, 3650),
]
BY_KEY = {r.key: r for r in RULES}
_cache: dict = {"at": 0.0, "vals": {}}


def _cast(rule: Rule, raw: str):
    if rule.kind == "bool":
        return raw in {"1", "true", "True", "on"}
    return max(rule.lo, min(rule.hi, int(raw)))


def _load() -> dict:
    vals = {}
    try:
        with SessionLocal() as db:
            for row in db.query(Setting).filter(Setting.key.in_(list(BY_KEY))).all():
                try:
                    vals[row.key] = _cast(BY_KEY[row.key], row.value)
                except (ValueError, TypeError):
                    continue
    except Exception:  # noqa: BLE001  - before the tables exist, in pure tests
        return {}
    return vals


def val(key: str):
    rule = BY_KEY[key]
    if time.monotonic() - _cache["at"] > CACHE_SECONDS:
        _cache["vals"], _cache["at"] = _load(), time.monotonic()
    return _cache["vals"].get(key, rule.default)


def reset_cache() -> None:
    _cache["at"] = 0.0


def current(db: Session, key: str):
    row = db.get(Setting, key)
    if row is None:
        return BY_KEY[key].default
    try:
        return _cast(BY_KEY[key], row.value)
    except (ValueError, TypeError):
        return BY_KEY[key].default


def set_value(db: Session, key: str, value, actor: str) -> None:
    rule = BY_KEY.get(key)
    if rule is None:
        raise HTTPException(404, "Такого правила нет")
    if rule.kind == "bool":
        new = bool(value)
        raw = "1" if new else "0"
    else:
        try:
            new = int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, "Нужно целое число")
        if not rule.lo <= new <= rule.hi:
            raise HTTPException(400, f"Допустимо от {rule.lo} до {rule.hi}")
        raw = str(new)
    old = current(db, key)
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=raw))
    else:
        row.value = raw
    log_action(db, actor, "rule.change", "rule", key, f"{old} → {new}")
    db.commit()
    reset_cache()


@router.get("/api/admin/rules")
def list_rules(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    groups: dict[str, list] = {}
    for r in RULES:
        groups.setdefault(r.group, []).append({"key": r.key, "label": r.label, "kind": r.kind, "value": current(db, r.key),
                                               "default": r.default, "min": r.lo, "max": r.hi, "hint": r.hint})
    return [{"group": g, "rules": items} for g, items in groups.items()]


class RuleIn(BaseModel):
    value: int | bool


@router.put("/api/admin/rules/{key}")
def change_rule(key: str, payload: RuleIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    set_value(db, key, payload.value, actor)
    return {"key": key, "value": current(db, key)}


@router.post("/api/admin/rules/{key}/reset")
def reset_rule(key: str, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    rule = BY_KEY.get(key)
    if rule is None:
        raise HTTPException(404, "Такого правила нет")
    set_value(db, key, rule.default, actor)
    return {"key": key, "value": rule.default}
