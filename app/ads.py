"""Ads (a separate lane in lists, always marked «Реклама») and promotions (free services for chosen audiences).

Rules (one place, see also app/policy.py for the places in a list):
- an ad is shown only while it is active and inside its dates; an ad that points at a listing disappears with the listing;
- an 18+ ad is shown only to people with a confirmed age (approved selfie of the dating profile);
- an ad never stands in the first three organic places, and never replaces them (it is inserted between);
- a promotion gives a service with a mark «по акции» and without a lift; ending it early does not take back what was given;
- one running promo per service for a person; a per-person limit counts claims."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import guard, policy
from app.admin_auth import ADM, staff
from app.audit import log_action
from app.auth import optional_user_id
from app.database import Base, get_db
from app.models import DatingProfile, Listing, Pricing, User, _now
from app.payments import PaymentError, aware, now
from app.users import writer

router = APIRouter()
AUDIENCES = {"all": "Все", "new": "Новые (до 14 дней)", "dating": "С анкетой знакомств", "sellers": "С опубликованными объявлениями"}


class Ad(Base):
    __tablename__ = "ads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(String(200), default="")
    link: Mapped[str] = mapped_column(String(255), default="")
    listing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adult_only: Mapped[bool] = mapped_column(Boolean, default=False)
    place: Mapped[str] = mapped_column(String(8), default="both")          # list | home | both
    starts_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_shows: Mapped[int | None] = mapped_column(Integer, nullable=True)   # the budget: switched off when reached
    title_b: Mapped[str] = mapped_column(String(80), default="")             # variant B of an A/B test (empty: no test)
    text_b: Mapped[str] = mapped_column(String(200), default="")
    shows_b: Mapped[int] = mapped_column(Integer, default=0)
    clicks_b: Mapped[int] = mapped_column(Integer, default=0)
    winner: Mapped[str] = mapped_column(String(1), default="")               # empty = running; a or b = decided
    shows: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PromoCampaign(Base):
    __tablename__ = "promo_campaigns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    service_code: Mapped[str] = mapped_column(String(40))
    days: Mapped[int] = mapped_column(Integer, default=3)
    audience: Mapped[str] = mapped_column(String(12), default="all")
    mode: Mapped[str] = mapped_column(String(6), default="claim")          # claim: a person takes it; auto: given at the start
    per_person: Mapped[int] = mapped_column(Integer, default=1)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    pre_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PromoClaim(Base):
    __tablename__ = "promo_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    tg_id: Mapped[str] = mapped_column(String(40), index=True)
    service_code: Mapped[str] = mapped_column(String(40), default="")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------- ads ----------
def age_confirmed(db: Session, viewer: str | None) -> bool:
    if not viewer:
        return False
    p = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == viewer))
    return p is not None and p.selfie_status == "approved"


def is_live(ad: Ad, at: dt.datetime) -> bool:
    return bool(ad.is_active and (ad.starts_at is None or aware(ad.starts_at) <= at) and (ad.ends_at is None or aware(ad.ends_at) > at))


def target_alive(db: Session, ad: Ad, at: dt.datetime) -> bool:
    """An ad for a listing is shown only while that listing is published, not expired and its author is in good standing."""
    if ad.listing_id is None:
        return True
    item = db.get(Listing, ad.listing_id)
    if item is None or item.status != "approved":
        return False
    if item.expires_at is not None and aware(item.expires_at) <= at:
        return False
    author = db.get(User, item.author_telegram_id)
    return author is None or not (author.blocked or author.deleted_at is not None)


def visible_ads(db: Session, viewer: str | None, where: str = "list") -> list[Ad]:
    at = now()
    adult = None
    out = []
    for ad in db.scalars(select(Ad).where(Ad.is_active.is_(True)).order_by(Ad.shows, Ad.id)).all():
        if not is_live(ad, at) or ad.place not in (where, "both") or not target_alive(db, ad, at):
            continue
        if ad.adult_only:
            if adult is None:
                adult = age_confirmed(db, viewer)
            if not adult:
                continue
        out.append(ad)
    return out


def variant_for(ad: Ad) -> str:
    """A/B: a or b. While the test runs the two take turns (half and half); when a winner is known only it is shown."""
    if not ad.title_b:
        return "a"
    return ad.winner or ("b" if (ad.shows or 0) % 2 else "a")


def ad_view(ad: Ad, variant: str = "a") -> dict:
    link = f"/?listing={ad.listing_id}" if ad.listing_id else ad.link
    b = variant == "b" and bool(ad.title_b)
    return {"id": ad.id, "title": ad.title_b if b else ad.title, "text": ad.text_b if b else ad.text, "link": link, "label": "Реклама",
            "adult": ad.adult_only, "variant": "b" if b else "a"}


def judge(ad: Ad) -> str:
    """Decide the A/B test: both variants shown 200+ times and one clicked at least 20% better. Returns a, b or empty."""
    sa, sb = (ad.shows or 0) - (ad.shows_b or 0), ad.shows_b or 0
    if not ad.title_b or ad.winner or sa < 200 or sb < 200:
        return ""
    ca, cb = ((ad.clicks or 0) - (ad.clicks_b or 0)) / sa, (ad.clicks_b or 0) / sb
    if max(ca, cb) == 0 or max(ca, cb) < 1.2 * min(ca, cb):
        return ""
    return "b" if cb > ca else "a"


@router.get("/api/ads")
def ads_for_list(organic: int = 0, page: int = 0, where: str = "list", viewer: str | None = Depends(optional_user_id),
                 db: Session = Depends(get_db)):
    from app import security
    if not security.flag_on(db, "ads", viewer):
        return []
    """Where to put ads in a list of `organic` items: [{at: index, ad: {...}}]. Never in the first three places."""
    if where not in {"list", "home"}:
        raise HTTPException(400, "where: list или home")
    slots = policy.ad_slots(max(0, organic), max(0, page), where)
    chosen = visible_ads(db, viewer, where)[:len(slots)]
    out = []
    spent = []
    for at_index, ad in zip(slots, chosen):
        variant = variant_for(ad)
        ad.shows += 1
        if variant == "b":
            ad.shows_b += 1
        out.append({"at": at_index, "ad": ad_view(ad, variant)})
        if ad.max_shows and ad.shows >= ad.max_shows:          # the budget is spent: off, and the staff is told
            ad.is_active = False
            spent.append(ad.title)
    if out:
        db.commit()
    if spent:
        from app import adminplus
        adminplus.notify_staff(db, "💸 Бюджет рекламы исчерпан, показ остановлен: " + ", ".join(spent), "adbudget", ("admin", "super_admin"))
    return out


@router.post("/api/ads/{ad_id}/click", status_code=204)
def ad_click(ad_id: int, v: str = "a", db: Session = Depends(get_db)):
    ad = db.get(Ad, ad_id)
    if ad is not None:
        ad.clicks += 1
        if v == "b" and ad.title_b:
            ad.clicks_b += 1
        won = judge(ad)
        if won:
            ad.winner = won
            from app import adminplus
            adminplus.notify_staff(db, f"🏆 A/B-тест рекламы «{ad.title}»: победил вариант {won.upper()}, дальше показывается только он.", "adab", ("admin", "super_admin"))
        db.commit()


class AdIn(BaseModel):
    title: str = Field(min_length=3, max_length=80)
    text: str = Field(default="", max_length=200)
    link: str = Field(default="", max_length=255)
    listing_id: int | None = None
    adult_only: bool = False
    place: str = Field(default="both", pattern="^(list|home|both)$")
    days: int | None = Field(default=None, ge=1, le=365)
    max_shows: int | None = Field(default=None, ge=1, le=10_000_000)
    title_b: str = Field(default="", max_length=80)
    text_b: str = Field(default="", max_length=200)


def _words(db: Session, key: str) -> list[str]:
    from app.models import Setting
    row = db.get(Setting, key)
    return [w.strip().lower() for w in (row.value if row else "").replace("\n", ",").split(",") if w.strip()]


def check_ad_text(db: Session, payload: "AdIn") -> None:
    """Forbidden themes (words) and forbidden or not allowed link hosts are checked before an ad is saved."""
    text = f"{payload.title} {payload.text}".lower()
    for word in _words(db, "ads.banned_words"):
        if word in text:
            raise HTTPException(400, f"В рекламе запрещённая тема: «{word}»")
    link = payload.link.strip().lower()
    if link.startswith("https://"):
        host = link[8:].split("/")[0].split("?")[0]
        if any(host == h or host.endswith("." + h) for h in _words(db, "ads.banned_hosts")):
            raise HTTPException(400, f"Ссылки на {host} запрещены")
        allowed = _words(db, "ads.allowed_hosts")
        if allowed and not any(host == h or host.endswith("." + h) for h in allowed):
            raise HTTPException(400, f"Ссылка на {host} не из белого списка")


@router.get("/api/admin/ads/lists")
def ad_lists(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return {k: ", ".join(_words(db, f"ads.{k}")) for k in ("banned_words", "banned_hosts", "allowed_hosts")}


class AdListsIn(BaseModel):
    banned_words: str = Field(default="", max_length=1000)
    banned_hosts: str = Field(default="", max_length=1000)
    allowed_hosts: str = Field(default="", max_length=1000)


@router.put("/api/admin/ads/lists")
def ad_lists_save(payload: AdListsIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    from app.models import Setting
    for k, v in payload.model_dump().items():
        row = db.get(Setting, f"ads.{k}")
        if row is None:
            db.add(Setting(key=f"ads.{k}", value=v))
        else:
            row.value = v
    log_action(db, actor, "ads.lists", "ads", "lists", "")
    db.commit()
    return ad_lists(actor, db)


def ad_admin_view(ad: Ad, db: Session) -> dict:
    at = now()
    return {**ad_view(ad), "active": ad.is_active, "live": is_live(ad, at) and target_alive(db, ad, at), "place": ad.place,
            "ab": ({"shows_a": ad.shows - ad.shows_b, "clicks_a": ad.clicks - ad.clicks_b, "shows_b": ad.shows_b, "clicks_b": ad.clicks_b, "winner": ad.winner, "title_b": ad.title_b} if ad.title_b else None),
            "shows": ad.shows, "max_shows": ad.max_shows, "clicks": ad.clicks, "ends_at": ad.ends_at.isoformat() if ad.ends_at else None,
            "ctr": round(100 * ad.clicks / ad.shows, 1) if ad.shows else 0.0}


@router.get("/api/admin/ads")
def admin_ads(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [ad_admin_view(a, db) for a in db.scalars(select(Ad).order_by(Ad.id.desc()).limit(100)).all()]


@router.post("/api/admin/ads", status_code=201)
def admin_ad_create(payload: AdIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    if payload.listing_id is None and not (payload.link.startswith("https://") or payload.link.startswith("/?")):
        raise HTTPException(400, "Укажите объявление или ссылку (https:// или /?)")
    if payload.listing_id is not None and db.get(Listing, payload.listing_id) is None:
        raise HTTPException(404, "Объявление не найдено")
    check_ad_text(db, payload)
    ad = Ad(max_shows=payload.max_shows, title_b=payload.title_b.strip(), text_b=payload.text_b.strip(), title=payload.title.strip(), text=payload.text.strip(), link=payload.link.strip(), listing_id=payload.listing_id,
            adult_only=payload.adult_only, place=payload.place, starts_at=now(),
            ends_at=now() + dt.timedelta(days=payload.days) if payload.days else None)
    db.add(ad)
    log_action(db, actor, "ad.create", "ad", payload.title[:40], "18+" if payload.adult_only else "")
    db.commit()
    return ad_admin_view(ad, db)


@router.put("/api/admin/ads/{ad_id}/toggle")
def admin_ad_toggle(ad_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    ad = db.get(Ad, ad_id)
    if ad is None:
        raise HTTPException(404, "Реклама не найдена")
    ad.is_active = not ad.is_active
    log_action(db, actor, "ad.toggle", "ad", ad_id, f"active={ad.is_active}")
    db.commit()
    return ad_admin_view(ad, db)


@router.delete("/api/admin/ads/{ad_id}")
def admin_ad_delete(ad_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    ad = db.get(Ad, ad_id)
    if ad is None:
        raise HTTPException(404, "Реклама не найдена")
    db.delete(ad)
    log_action(db, actor, "ad.delete", "ad", ad_id, "")
    db.commit()
    return {"deleted": True}


# ---------- promotions ----------
def is_audience(db: Session, camp: PromoCampaign, user_id: str) -> bool:
    user = db.get(User, user_id)
    if user is None or user.blocked or user.deleted_at is not None:
        return False
    if camp.audience == "all":
        return True
    if camp.audience == "new":
        return aware(user.created_at) >= now() - dt.timedelta(days=14)
    if camp.audience == "dating":
        p = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
        return p is not None and p.status == "published"
    if camp.audience == "sellers":
        return db.scalar(select(func.count()).select_from(Listing).where(Listing.author_telegram_id == user_id, Listing.status == "approved")) > 0
    return False


def campaign_running(camp: PromoCampaign, at: dt.datetime) -> bool:
    return bool(camp.is_active and aware(camp.starts_at) <= at and (camp.ends_at is None or aware(camp.ends_at) > at))


def why_not(db: Session, camp: PromoCampaign, user_id: str) -> str:
    """Empty string if the person may take the promotion now; otherwise the reason in plain words."""
    at = now()
    if not campaign_running(camp, at):
        return "Акция не идёт"
    if not is_audience(db, camp, user_id):
        return "Эта акция не для вас"
    taken = db.scalar(select(func.count()).select_from(PromoClaim).where(PromoClaim.campaign_id == camp.id, PromoClaim.tg_id == user_id))
    if taken >= camp.per_person:
        return "Вы уже участвовали"
    running = db.scalar(select(PromoClaim).where(PromoClaim.tg_id == user_id, PromoClaim.service_code == camp.service_code,
                                                 PromoClaim.ends_at > at))
    if running is not None:
        return "Эта услуга уже действует по другой акции"
    return ""


def give(db: Session, camp: PromoCampaign, user_id: str, listing_id: int | None = None) -> None:
    from app import datingsvc
    if camp.mode == "auto" and db.get(Pricing, camp.service_code).category != "dating":
        raise PaymentError("Автоматически можно выдавать только услуги анкет: для объявления нужно выбрать объявление")
    datingsvc.grant(db, f"promo:{camp.id}", user_id, camp.service_code, listing_id, camp.title, promo=True, days=camp.days)
    db.add(PromoClaim(campaign_id=camp.id, tg_id=user_id, service_code=camp.service_code,
                      ends_at=now() + dt.timedelta(days=camp.days)))
    db.commit()


@router.get("/api/promos")
def my_promos(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    out = []
    for camp in db.scalars(select(PromoCampaign).where(PromoCampaign.is_active.is_(True)).order_by(PromoCampaign.id.desc())).all():
        if not campaign_running(camp, now()) or not is_audience(db, camp, user_id):
            continue
        reason = why_not(db, camp, user_id)
        pricing = db.get(Pricing, camp.service_code)
        out.append({"id": camp.id, "title": camp.title, "days": camp.days, "service": pricing.title if pricing else camp.service_code,
                    "mode": camp.mode, "can_claim": camp.mode == "claim" and not reason, "reason": reason,
                    "service_code": camp.service_code, "needs_listing": bool(pricing and pricing.category != "dating")})
    return out


class ClaimIn(BaseModel):
    listing_id: int | None = None


@router.post("/api/promos/{campaign_id}/claim", status_code=201)
def claim(campaign_id: int, payload: ClaimIn | None = None, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    camp = db.get(PromoCampaign, campaign_id)
    if camp is None:
        raise HTTPException(404, "Акция не найдена")
    reason = why_not(db, camp, user_id)
    if reason:
        raise HTTPException(409, reason)
    try:
        give(db, camp, user_id, payload.listing_id if payload else None)
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    return {"granted": True, "days": camp.days}


class CampaignIn(BaseModel):
    title: str = Field(min_length=3, max_length=80)
    service_code: str = Field(min_length=3, max_length=40)
    days: int = Field(default=3, ge=1, le=30)
    audience: str = Field(default="all", pattern="^(all|new|dating|sellers)$")
    mode: str = Field(default="claim", pattern="^(claim|auto)$")
    per_person: int = Field(default=1, ge=1, le=5)
    start_in_hours: int = Field(default=0, ge=0, le=24 * 60)
    last_days: int | None = Field(default=None, ge=1, le=365)


def campaign_view(c: PromoCampaign, db: Session) -> dict:
    claims = db.scalar(select(func.count()).select_from(PromoClaim).where(PromoClaim.campaign_id == c.id)) or 0
    return {"id": c.id, "title": c.title, "service_code": c.service_code, "days": c.days, "audience": c.audience,
            "audience_title": AUDIENCES.get(c.audience, c.audience), "mode": c.mode, "per_person": c.per_person,
            "active": c.is_active, "running": campaign_running(c, now()), "starts_at": c.starts_at.isoformat(),
            "ends_at": c.ends_at.isoformat() if c.ends_at else None, "claims": claims}


@router.get("/api/admin/campaigns")
def admin_campaigns(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [campaign_view(c, db) for c in db.scalars(select(PromoCampaign).order_by(PromoCampaign.id.desc()).limit(50)).all()]


@router.post("/api/admin/campaigns", status_code=201)
def admin_campaign_create(payload: CampaignIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    from app import payments
    payments.ensure_pricing(db)
    if db.get(Pricing, payload.service_code) is None:
        raise HTTPException(400, "Такой услуги нет")
    start = now() + dt.timedelta(hours=payload.start_in_hours)
    camp = PromoCampaign(title=payload.title.strip(), service_code=payload.service_code, days=payload.days, audience=payload.audience,
                         mode=payload.mode, per_person=payload.per_person, starts_at=start,
                         ends_at=start + dt.timedelta(days=payload.last_days) if payload.last_days else None)
    db.add(camp)
    log_action(db, actor, "promo.create", "campaign", payload.title[:40], f"{payload.service_code} {payload.audience} {payload.mode}")
    db.commit()
    return campaign_view(camp, db)


@router.put("/api/admin/campaigns/{campaign_id}/stop")
def admin_campaign_stop(campaign_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Early end: nobody gets it from now on; what was already given stays until its own end."""
    camp = db.get(PromoCampaign, campaign_id)
    if camp is None:
        raise HTTPException(404, "Акция не найдена")
    camp.is_active = False
    log_action(db, actor, "promo.stop", "campaign", campaign_id, "")
    db.commit()
    return campaign_view(camp, db)


def housekeeping(db: Session) -> list[tuple[str, str, str]]:
    """A notice a day before a campaign starts; automatic campaigns are applied once when they start."""
    at = now()
    out: list[tuple[str, str, str]] = []
    for camp in db.scalars(select(PromoCampaign).where(PromoCampaign.is_active.is_(True))).all():
        if not camp.pre_notified and at < aware(camp.starts_at) <= at + dt.timedelta(days=1):
            camp.pre_notified = True
            for user in db.scalars(select(User).where(User.blocked.is_(False), User.deleted_at.is_(None)).limit(500)).all():
                if is_audience(db, camp, user.tg_id):
                    out.append((user.tg_id, f"Скоро акция «{camp.title}»: {camp.days} дн. бесплатно. Начало: {aware(camp.starts_at).strftime('%d.%m %H:%M')} UTC.", "promo"))
        if camp.mode == "auto" and not camp.applied and campaign_running(camp, at):
            camp.applied = True
            db.commit()
            for user in db.scalars(select(User).where(User.blocked.is_(False), User.deleted_at.is_(None))).all():
                if not why_not(db, camp, user.tg_id):
                    try:
                        give(db, camp, user.tg_id)
                    except PaymentError:
                        continue
    db.commit()
    return out


@router.get("/api/admin/calendar")
def calendar(days: int = 60, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Ads and promotions on one time line, with the places where they clash: too many ads at once for the free places on a page,
    or two promotions of the same service for the same people at the same time."""
    from app import rules
    from_ = now()
    to_ = from_ + dt.timedelta(days=max(1, min(days, 180)))
    items = []
    for a in db.scalars(select(Ad).where(Ad.is_active.is_(True))).all():
        s, e = (aware(a.starts_at) if a.starts_at else from_), (aware(a.ends_at) if a.ends_at else to_)
        if e >= from_ and s <= to_:
            items.append({"kind": "ad", "id": a.id, "title": a.title, "from": s.isoformat(), "to": e.isoformat(), "where": a.place, "audience": "18+" if a.adult_only else "все", "conflicts": []})
    for c in db.scalars(select(PromoCampaign).where(PromoCampaign.is_active.is_(True))).all():
        s, e = aware(c.starts_at), (aware(c.ends_at) if c.ends_at else to_)
        if e >= from_ and s <= to_:
            items.append({"kind": "promo", "id": c.id, "title": c.title, "from": s.isoformat(), "to": e.isoformat(), "where": c.service_code, "audience": c.audience, "conflicts": []})
    cap = rules.val("search.ad_on_page")

    def overlap(x, y):
        return x["from"] < y["to"] and y["from"] < x["to"]
    ads = [i for i in items if i["kind"] == "ad"]
    for i in ads:
        same = [j for j in ads if overlap(i, j) and (j["where"] == i["where"] or "both" in (i["where"], j["where"]))]
        if len(same) > cap:
            i["conflicts"].append(f"одновременно {len(same)} реклам на {cap} мест")
    promos = [i for i in items if i["kind"] == "promo"]
    for i in promos:
        for j in promos:
            if i is not j and i["where"] == j["where"] and overlap(i, j) and ("all" in (i["audience"], j["audience"]) or i["audience"] == j["audience"]):
                i["conflicts"].append(f"пересекается с акцией «{j['title']}» по той же услуге")
    return sorted(items, key=lambda x: x["from"])
