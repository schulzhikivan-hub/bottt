"""Browsing the sections inside the bot chat: jobs, housing, goods, cars, services, dating, community, guide.

`open_section()` and `act()` return a `View` and never talk to Telegram, so they are tested without a
network (like botadmin). The aiogram glue at the bottom only sends what they return. Actions that need
a full screen (writing a message, applying to a vacancy, adding a listing) open the Mini App at that listing.
"""
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import catalog, dating, geo
from app.config import settings
from app.fields import LABELS
from app.models import Article, DatingLike, DatingProfile, Favorite, Group, Listing, ListingPhoto, Region, User
from app.photos import photo_root

CAPTION_LIMIT = 1000
GUIDE_SECTIONS = {"visas": "Визы и документы", "registration": "Регистрация и адрес", "medicine": "Медицина",
                  "banks": "Банки и мобильная связь", "housing": "Жильё", "work": "Работа и трудовые права",
                  "emergency": "Экстренные ситуации"}
# section code -> (title, catalog filters)
SECTIONS = {
    "j": ("💼 Работа", {"category": "job"}),
    "h": ("🏠 Жильё", {"category": "housing"}),
    "m": ("🛍️ Барахолка", {"category": "marketplace"}),
    "a": ("🚗 Автомобили", {"category": "marketplace", "goods_category": "auto"}),
    "s": ("🛠️ Услуги", {"category": "marketplace", "goods_category": "services"}),
    "f": ("🔎 Поиск по всем разделам", {}),
}
AGE_PRESETS = [("Любой возраст", None, None), ("18–25", 18, 25), ("26–35", 26, 35), ("36+", 36, None)]
REPORT_REASONS = {"fake": "Фейк или чужое фото", "spam": "Спам и реклама", "offensive": "Оскорбления",
                  "minor": "Несовершеннолетний", "inappropriate": "Неприемлемое содержание", "fraud": "Мошенничество"}
LABEL = {k: dict(v) for k, v in LABELS.items()}

STATE: dict[int, dict] = {}   # user id -> what they are looking at
WAIT_QUERY: set[int] = set()  # users who were asked to type a search word


@dataclass
class View:
    text: str
    rows: list[list[dict]] = field(default_factory=list)  # button: {"t": text, "d": callback} | {"t": text, "app": "?query"} | {"t": text, "url": ...}
    photo: Path | None = None
    alert: str = ""
    notify: list[tuple[str, str]] = field(default_factory=list)


def btn(text: str, data: str) -> dict:
    return {"t": text, "d": data}


def app_btn(text: str, query: str = "") -> dict:
    return {"t": text, "app": query}


def home_view() -> View:
    rows = [[btn("💼 Работа", "b:s:j"), btn("🏠 Жильё", "b:s:h")],
            [btn("🛍️ Барахолка", "b:s:m"), btn("🚗 Автомобили", "b:s:a")],
            [btn("🛠️ Услуги", "b:s:s"), btn("💗 Знакомства", "b:dt")],
            [btn("👥 Сообщество", "b:cm"), btn("📘 Гид по Корее", "b:gd")],
            [app_btn("📱 Открыть приложение")]]
    return View("🇰🇷 KOREA HUB\nВыберите раздел. Смотреть объявления можно прямо здесь, в чате.", rows)


# ---------- listings ----------

def _money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₩"


def price_line(x: Listing) -> str:
    if x.price_amount is None:
        return x.price or "Цена договорная"
    text = _money(x.price_amount)
    d = x.details or {}
    if x.category == "job" and d.get("salary_max"):
        text = f"{x.price_amount:,}".replace(",", " ") + " – " + _money(d["salary_max"])
    suffix = (LABEL["salary_period"].get(x.salary_period or "month", "") if x.category == "job"
              else "в месяц" if x.category == "housing" else "")
    return " ".join(p for p in (text, suffix, x.price) if p)


def describe(db: Session, x: Listing) -> str:
    d = x.details or {}
    yes = lambda v, a: a if v else None  # noqa: E731
    lines = [f"{SECTIONS_ICON.get(x.category, '📄')} {x.title}", f"📍 {x.city}", f"💰 {price_line(x)}"]
    facts: list[str | None] = []
    if x.category == "job":
        facts = [d.get("company"), d.get("position"), LABEL["employment"].get(x.employment or ""),
                 LABEL["schedule"].get(x.schedule or ""),
                 ("визы " + ", ".join(v for v in (x.visas or "").split(",") if v)) if x.visas else None,
                 yes(x.housing_provided, "🏠 жильё"), yes(x.meals_provided, "🍱 питание"),
                 yes(d.get("transport"), "🚌 развозка"), d.get("start_date") and "начало " + d["start_date"]]
    elif x.category == "housing":
        facts = [LABEL["housing_type"].get(x.housing_type or ""),
                 x.deposit is not None and "залог " + _money(x.deposit) or None,
                 x.area_m2 and f"{x.area_m2} м²", x.rooms is not None and f"{x.rooms} комн." or None,
                 d.get("floor") is not None and f"{d['floor']} этаж" or None,
                 d.get("station") and "метро " + d["station"], yes(x.pets_allowed, "🐾 можно с животными"),
                 yes(x.furnished, "🛋 с мебелью"), d.get("available_from") and "свободно с " + d["available_from"]]
    else:
        facts = [LABEL["goods_category"].get(x.goods_category or ""), LABEL["condition"].get(x.condition or ""),
                 d.get("brand"), d.get("model"), d.get("year") and f"{d['year']} г.",
                 d.get("mileage_km") is not None and f"{d['mileage_km']:,} км".replace(",", " ") or None,
                 d.get("service_area") and "где: " + d["service_area"],
                 d.get("experience_years") is not None and f"опыт {d['experience_years']} лет" or None,
                 yes(x.negotiable, "торг возможен")]
    facts = [str(f) for f in facts if f]
    if facts:
        lines.append(" · ".join(facts))
    if x.description:
        lines += ["", x.description[:380] + ("…" if len(x.description) > 380 else "")]
    author = db.get(User, x.author_telegram_id)
    if author is not None and author.employer_status == "approved":
        lines.append("\n✔ Проверенный работодатель" + (f": {author.company_name}" if author.company_name else ""))
    lines.append(f"\n№ {x.id} · 👁 {x.views or 0}")
    return "\n".join(lines)[:CAPTION_LIMIT]


SECTIONS_ICON = {"job": "💼", "housing": "🏠", "marketplace": "🛍️"}


def cover_path(db: Session, x: Listing) -> Path | None:
    for p in db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == x.id, ListingPhoto.status == "live")
                        .order_by(ListingPhoto.position, ListingPhoto.id)).all():
        path = photo_root() / f"{p.key}.jpg"
        if path.exists():
            return path
    return None


def _search(db: Session, st: dict) -> list[int]:
    _, filters = SECTIONS[st["sec"]]
    rows, _total = catalog.search(db, q=st.get("q", ""), region=st.get("region") or None, limit=50, **filters)
    return [r.id for r in rows]


def listing_view(db: Session, user_id: int, st: dict) -> View:
    title, _ = SECTIONS[st["sec"]]
    ids = st["ids"]
    where = " · ".join(x for x in (title, st.get("region_name"), f"«{st['q']}»" if st.get("q") else None) if x)
    tools = [btn("📍 Регион", "b:r"), btn("🔎 Поиск", "b:q")]
    if st.get("q") or st.get("region"):
        tools.append(btn("🧹 Сброс", "b:z"))
    if not ids:
        return View(f"{where}\n\nПока ничего не найдено.", [tools, [btn("⬅️ Разделы", "b:m")]])
    st["idx"] = max(0, min(st["idx"], len(ids) - 1))
    x = db.get(Listing, ids[st["idx"]])
    if x is None or x.status != "approved":
        ids.pop(st["idx"])
        return listing_view(db, user_id, st)
    fav = db.scalar(select(Favorite.id).where(Favorite.user_tg_id == str(user_id), Favorite.listing_id == x.id)) is not None
    nav = [btn("◀", "b:p"), btn(f"{st['idx'] + 1} / {len(ids)}", "b:x"), btn("▶", "b:n")]
    contact = (x.contact or "").strip()
    row = [btn("♥ В избранном" if fav else "♡ В избранное", f"b:f:{x.id}")]
    if contact.lstrip("@").replace("_", "").isalnum() and contact.lstrip("@")[:1].isalpha() and len(contact.lstrip("@")) >= 5:
        row.append({"t": "✈️ Написать в Telegram", "url": f"https://t.me/{contact.lstrip('@')}"})
    act = [app_btn("💬 Написать / откликнуться" if x.category == "job" else "💬 Написать в приложении", f"?listing={x.id}")]
    return View(f"{where}\n\n{describe(db, x)}", [nav, row, act, tools, [btn("⬅️ Разделы", "b:m")]], photo=cover_path(db, x))


def regions_view(db: Session, prefix: str = "b:rr") -> View:
    regs = db.scalars(select(Region).order_by(Region.sort)).all()
    rows = [[btn(r.name_ru, f"{prefix}:{r.code}") for r in regs[i:i + 3]] for i in range(0, len(regs), 3)]
    rows.append([btn("🇰🇷 Вся Корея", f"{prefix}:-")])
    return View("Выберите регион:", rows)


# ---------- dating ----------

def _consented(db: Session, user_id: int) -> bool:
    u = db.get(User, str(user_id))
    return u is not None and u.consent_at is not None and not u.blocked


def dating_view(db: Session, user_id: int, st: dict) -> View:
    gender = st.get("gender", "")
    chips = [btn(("• " if gender == g else "") + t, f"b:dg:{g or '-'}") for g, t in (("", "Все"), ("f", "Девушки"), ("m", "Парни"))]
    if not _consented(db, user_id):
        return View("💗 Знакомства (18+)\n\nЧтобы смотреть анкеты, примите правила в приложении: это один раз.",
                    [[app_btn("📱 Принять правила")], [btn("⬅️ Разделы", "b:m")]])
    preset = AGE_PRESETS[st.get("age", 0) % len(AGE_PRESETS)]
    region = db.get(Region, st["region"]) if st.get("region") else None
    try:
        likes = dating.likes_me(str(user_id), db)
        if st.get("mode") == "likes":
            items = likes["items"]
        else:
            items = dating.browse(gender or None, None, str(user_id), db, age_min=preset[1], age_max=preset[2],
                                  region=region.code if region else None)
    except HTTPException:
        items, likes = [], {"count": 0}
    st["ids"] = [p["id"] for p in items]
    tools = [btn(f"🎂 {preset[0]}", "b:da"), btn("📍 " + (region.name_ru if region else "Регион"), "b:dr")]
    liked = [btn("↩ Все анкеты", "b:dt")] if st.get("mode") == "likes" else [btn(f"💌 Меня лайкнули ({likes['count']})", "b:dm")]
    foot = [chips, tools, liked, [btn("⬅️ Разделы", "b:m")]]
    title = "💌 Вас лайкнули" if st.get("mode") == "likes" else "💗 Знакомства (18+)"
    if not items:
        return View(f"{title}\n\nАнкет по этим условиям нет. Измените возраст или регион.", foot + [[app_btn("Создать свою анкету")]])
    st["idx"] = max(0, min(st.get("idx", 0), len(items) - 1))
    p = items[st["idx"]]
    profile = db.get(DatingProfile, p["id"])
    path = Path(settings.upload_dir) / profile.photo_key if profile and profile.photo_key else None
    who = (f"{p['name']}, {p['age']}" if p.get("name") else f"{p['age']} лет") + (" ✔" if p.get("verified") else "")
    text = f"💗 {who} · {'девушка' if p['gender'] == 'f' else 'парень'}\n📍 {p['city']}\n\n{p['bio']}"
    if st.get("mode") == "likes":
        text = "💌 Вас лайкнули. Нажмите «Нравится», чтобы ответить взаимностью.\n\n" + text
    nav = [btn("◀", "b:dp"), btn(f"{st['idx'] + 1} / {len(items)}", "b:x"), btn("▶", "b:dn")]
    return View(text[:CAPTION_LIMIT], [nav, [btn("♥ Нравится", f"b:dl:{p['id']}"), btn("⚑ Пожаловаться", f"b:dx:{p['id']}")]] + foot,
                photo=path if path and path.exists() else None)


def report_view(profile_id: int) -> View:
    rows = [[btn(title, f"b:dy:{profile_id}:{key}")] for key, title in REPORT_REASONS.items()]
    return View("На что жалоба? Модератор проверит анкету. Автор не узнает, кто пожаловался.", rows + [[btn("↩ Отмена", "b:dc")]])


def single_view(db: Session, listing_id: int) -> View:
    """One listing from a shared link (deep link l_<id>): the card plus a button into the Mini App."""
    x = db.get(Listing, listing_id)
    if x is None or x.status != "approved":
        return View("Это объявление уже снято с публикации или не найдено.", [[btn("⬅️ Разделы", "b:m")]])
    return View(describe(db, x), [[app_btn("💬 Открыть в приложении", f"?listing={x.id}")], [btn("⬅️ Разделы", "b:m")]],
                photo=cover_path(db, x))


# ---------- community and guide ----------

def community_view(db: Session) -> View:
    groups = db.scalars(select(Group).order_by(Group.id)).all()
    if not groups:
        return View("👥 Сообщество\n\nГруппы появятся скоро.", [[btn("⬅️ Разделы", "b:m")]])
    text = "👥 Сообщество\nГруппы по странам и городам:\n\n" + "\n".join(
        f"{g.flag} {g.name}" + (f" — {g.description}" if g.description else "") for g in groups)
    rows = [[{"t": f"{g.flag} {g.name}", "url": g.telegram_link}] for g in groups if g.telegram_link.startswith("http")]
    return View(text[:3900], rows + [[btn("⬅️ Разделы", "b:m")]])


def guide_view(db: Session, section: str | None = None, article_id: int | None = None) -> View:
    if article_id is not None:
        a = db.get(Article, article_id)
        if a is None or not a.published:
            return View("Статья не найдена.", [[btn("⬅️ Гид", "b:gd")]])
        tail = f"\n\nИсточник: {a.source_url}" if a.source_url else ""
        checked = f"\nПроверено: {a.checked_at:%d.%m.%Y}" if a.checked_at else ""
        return View(f"📘 {a.title}\n\n{a.body}{tail}{checked}"[:3900], [[btn("⬅️ К разделу", f"b:ga:{a.section}")], [btn("⬅️ Гид", "b:gd")]])
    if section:
        arts = db.scalars(select(Article).where(Article.section == section, Article.published == True)  # noqa: E712
                          .order_by(Article.id)).all()
        rows = [[btn(a.title[:60], f"b:gr:{a.id}")] for a in arts]
        text = f"📘 {GUIDE_SECTIONS.get(section, section)}" + ("" if arts else "\n\nСтатьи проверяются и скоро появятся.")
        return View(text, rows + [[btn("⬅️ Гид", "b:gd")]])
    rows = [[btn(title, f"b:ga:{key}")] for key, title in GUIDE_SECTIONS.items()]
    return View("📘 Гид по Корее\nПроверенные статьи по жизни в Корее:", rows + [[btn("⬅️ Разделы", "b:m")]])


# ---------- actions ----------

def act(db: Session, user_id: int, data: str) -> View:
    """data is the callback string, for example 'b:s:j', 'b:n', 'b:f:12'."""
    parts = data.split(":")
    cmd = parts[1] if len(parts) > 1 else "m"
    arg = parts[2] if len(parts) > 2 else ""
    st = STATE.setdefault(user_id, {})
    WAIT_QUERY.discard(user_id)
    if cmd == "m":
        STATE.pop(user_id, None)
        return home_view()
    if cmd == "x":
        return View("", alert="")
    if cmd == "s" and arg in SECTIONS:
        STATE[user_id] = st = {"sec": arg, "idx": 0, "q": "", "region": ""}
        st["ids"] = _search(db, st)
        return listing_view(db, user_id, st)
    if cmd in ("n", "p", "z", "rr") and "sec" in st:
        if cmd == "n":
            st["idx"] = (st["idx"] + 1) % max(1, len(st["ids"]))
        elif cmd == "p":
            st["idx"] = (st["idx"] - 1) % max(1, len(st["ids"]))
        elif cmd == "z":
            st.update(q="", region="", region_name="", idx=0)
            st["ids"] = _search(db, st)
        else:
            reg = db.get(Region, arg) if arg != "-" else None
            st.update(region=reg.code if reg else "", region_name=reg.name_ru if reg else "", idx=0)
            st["ids"] = _search(db, st)
        return listing_view(db, user_id, st)
    if cmd == "r" and "sec" in st:
        return regions_view(db)
    if cmd == "q" and "sec" in st:
        WAIT_QUERY.add(user_id)
        return View("Напишите слово для поиска (например: повар, диван, Каннам) или нажмите /cancel:", [])
    if cmd == "f" and arg.isdigit():
        if not _consented(db, user_id):
            return View("Чтобы сохранять в избранное, примите правила в приложении.", [[app_btn("📱 Принять правила")]])
        existing = db.scalar(select(Favorite).where(Favorite.user_tg_id == str(user_id), Favorite.listing_id == int(arg)))
        item = db.get(Listing, int(arg))
        if existing:
            db.delete(existing)
        elif item is not None and item.status == "approved":
            db.add(Favorite(user_tg_id=str(user_id), listing_id=int(arg)))
        db.commit()
        return listing_view(db, user_id, st) if "sec" in st else home_view()
    if cmd == "dt":
        keep = {k: st[k] for k in ("gender", "age", "region") if st.get("dating") and k in st}
        STATE[user_id] = st = {"dating": True, "gender": "", "idx": 0, **keep}
        return dating_view(db, user_id, st)
    if cmd in ("da", "dr", "dz", "dm", "dx", "dy", "dc") and st.get("dating"):
        if cmd == "da":
            st.update(age=(st.get("age", 0) + 1) % len(AGE_PRESETS), idx=0)
        elif cmd == "dr":
            return regions_view(db, "b:dz")
        elif cmd == "dz":
            st.update(region="" if arg == "-" else arg, idx=0)
        elif cmd == "dm":
            st.update(mode="likes", idx=0)
        elif cmd == "dx" and arg.isdigit():
            return report_view(int(arg))
        elif cmd == "dy" and arg.isdigit() and len(parts) > 3 and parts[3] in REPORT_REASONS:
            from app.models import Report
            if db.get(DatingProfile, int(arg)) is not None and db.scalar(select(Report.id).where(
                    Report.reporter_tg_id == str(user_id), Report.target_type == "dating", Report.target_id == arg)) is None:
                db.add(Report(reporter_tg_id=str(user_id), target_type="dating", target_id=arg, reason=parts[3]))
                db.commit()
            view = dating_view(db, user_id, st)
            view.alert = "Жалоба отправлена. Спасибо!"
            return view
        return dating_view(db, user_id, st)
    if cmd in ("dn", "dp", "dg", "dl") and st.get("dating"):
        if cmd == "dg":
            st.update(gender="" if arg == "-" else arg, idx=0)
        elif cmd == "dn":
            st["idx"] = st.get("idx", 0) + 1
            if st["idx"] >= len(st.get("ids", [])):
                st["idx"] = 0
        elif cmd == "dp":
            st["idx"] = (st.get("idx", 0) - 1) % max(1, len(st.get("ids", [])))
        elif cmd == "dl" and arg.isdigit():
            try:
                result = dating.like(int(arg), str(user_id), db)
            except HTTPException as e:
                return View("", alert=e.detail)
            profile = db.get(DatingProfile, int(arg))
            view = dating_view(db, user_id, st)  # the liked profile disappears from the feed
            if result.get("match"):
                view.alert = "Взаимная симпатия! Теперь можно писать друг другу."
                view.rows.insert(0, [app_btn("💬 Открыть чат в приложении")])
                if profile is not None:
                    view.notify.append((profile.owner_tg_id, "💗 У вас взаимная симпатия! Откройте приложение, чтобы написать."))
            else:
                view.alert = "Отправлено"
            return view
        return dating_view(db, user_id, st)
    if cmd == "cm":
        return community_view(db)
    if cmd == "gd":
        return guide_view(db)
    if cmd == "ga":
        return guide_view(db, section=arg)
    if cmd == "gr" and arg.isdigit():
        return guide_view(db, article_id=int(arg))
    return home_view()


def apply_query(db: Session, user_id: int, text: str) -> View | None:
    if user_id not in WAIT_QUERY:
        return None
    WAIT_QUERY.discard(user_id)
    st = STATE.get(user_id)
    if not st or "sec" not in st:
        return home_view()
    st.update(q=text.strip()[:60], idx=0)
    st["ids"] = _search(db, st)
    return listing_view(db, user_id, st)


# ---------- aiogram glue ----------

def _markup(rows):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    from app import buildinfo
    out = []
    for row in rows:
        line = []
        for b in row:
            if "d" in b:
                line.append(InlineKeyboardButton(text=b["t"], callback_data=b["d"]))
            elif "url" in b:
                line.append(InlineKeyboardButton(text=b["t"], url=b["url"]))
            else:
                line.append(InlineKeyboardButton(text=b["t"], web_app=WebAppInfo(url=buildinfo.app_url(b["app"]))))
        out.append(line)
    return InlineKeyboardMarkup(inline_keyboard=out)


async def send_view(bot, chat_id: int, view: View) -> None:
    from aiogram.types import FSInputFile
    markup = _markup(view.rows) if view.rows else None
    sent = False
    if view.photo is not None:
        try:
            await bot.send_photo(chat_id, FSInputFile(view.photo), caption=view.text[:CAPTION_LIMIT], reply_markup=markup)
            sent = True
        except Exception:
            sent = False
    if not sent and view.text:
        await bot.send_message(chat_id, view.text[:4000], reply_markup=markup)
    for tg_id, text in view.notify:
        try:
            await bot.send_message(int(tg_id), text)
        except Exception:
            pass


def register_browse(dp, bot) -> None:
    from aiogram import F
    from aiogram.filters import Command, CommandObject
    from aiogram.types import CallbackQuery, Message
    from app.database import SessionLocal

    @dp.callback_query(F.data.startswith("b:"))
    async def pressed(query: CallbackQuery):
        with SessionLocal() as db:
            view = act(db, query.from_user.id, query.data)
        if view.alert and not view.text:
            await query.answer(view.alert, show_alert=True)
            return
        try:
            await query.message.delete()  # one live card instead of a long trail of old ones
        except Exception:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        await query.answer(view.alert or None)
        await send_view(bot, query.message.chat.id, view)

    @dp.message(Command("find"))
    async def find(message: Message, command: CommandObject):
        uid = message.from_user.id
        STATE[uid] = {"sec": "f", "idx": 0, "q": "", "region": "", "ids": []}
        word = (command.args or "").strip()[:60]
        if not word:
            WAIT_QUERY.add(uid)
            await message.answer("Что ищем? Напишите слово: работа, диван, Каннам… (отмена: /cancel)")
            return
        with SessionLocal() as db:
            STATE[uid]["q"] = word
            STATE[uid]["ids"] = _search(db, STATE[uid])
            view = listing_view(db, uid, STATE[uid])
        await send_view(bot, message.chat.id, view)

    @dp.message(F.text & ~F.text.startswith("/"), lambda m: m.from_user.id in WAIT_QUERY)
    async def typed(message: Message):
        with SessionLocal() as db:
            view = apply_query(db, message.from_user.id, message.text)
        if view is not None:
            await send_view(bot, message.chat.id, view)

    @dp.message(Command("cancel"), lambda m: m.from_user.id in WAIT_QUERY)
    async def stop_search(message: Message):
        WAIT_QUERY.discard(message.from_user.id)
        await message.answer("Поиск отменён.")
        await send_view(bot, message.chat.id, home_view())
