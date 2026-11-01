"""The site outside Telegram: public listing pages that search engines and link previews can read, robots.txt,
sitemap.xml, and the legal pages (DRAFT texts that a lawyer must review before launch)."""
import html
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app import fields
from app.config import settings
from app.database import get_db
from app.models import Listing, ListingPhoto
from app.payments import now, aware

router = APIRouter()
LEGAL_VERSION = "0.1-draft 2026-11-01"
CAT = {"job": "Работа", "housing": "Жильё", "marketplace": "Барахолка"}
SITE_NAME = "KOREA HUB"


def base_url() -> str:
    return settings.webapp_url.rstrip("/")


def _bot_link(listing_id: int) -> str:
    """Where the button on a public page leads: the bot (it shows the card and opens the Mini App); else the site itself."""
    from app import webauth
    try:
        name = webauth.bot_username()
    except Exception:  # noqa: BLE001
        name = ""
    return f"https://t.me/{name}?start=l_{listing_id}" if name else f"{base_url()}/?listing={listing_id}"


def _price(x: Listing) -> str:
    if x.price_amount is None:
        return x.price or "Цена договорная"
    text = f"{x.price_amount:,}".replace(",", " ") + " ₩"
    suffix = {"job": "в месяц", "housing": "в месяц"}.get(x.category, "")
    return " ".join(p for p in (text, suffix, x.price) if p)


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{canonical}">
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta property="og:site_name" content="KOREA HUB"><meta property="og:type" content="website">
<meta property="og:title" content="{title}"><meta property="og:description" content="{desc}">
<meta property="og:url" content="{canonical}"><meta property="og:image" content="{image}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}"><meta name="twitter:image" content="{image}">
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#f3f6fb;color:#1b2540}}
main{{max-width:640px;margin:0 auto;padding:18px}}
.top{{display:flex;align-items:center;gap:10px;padding:12px 18px;background:#fff;box-shadow:0 1px 4px #1b25401a}}
.top b{{font-size:18px}}.top a{{color:inherit;text-decoration:none;display:flex;gap:10px;align-items:center}}
.top img{{width:34px;height:34px;border-radius:8px}}
.card{{background:#fff;border-radius:16px;padding:18px;box-shadow:0 1px 6px #1b25400f}}
h1{{font-size:22px;margin:4px 0 8px}}.price{{font-size:22px;font-weight:800;color:#1b4b9b;margin:6px 0}}
.meta{{color:#6f7b91;font-size:14px}}.pic{{width:100%;border-radius:12px;margin-bottom:12px}}
.cta{{display:block;text-align:center;background:#1b4b9b;color:#fff;text-decoration:none;font-weight:700;padding:14px;border-radius:12px;margin-top:16px}}
.draft{{background:#fff4d6;border:2px solid #f5a524;color:#7a4b00;padding:12px 14px;border-radius:12px;font-weight:700;margin-bottom:14px}}
.legal h2{{font-size:17px;margin:18px 0 6px}}.legal p,.legal li{{line-height:1.5}}
footer{{text-align:center;color:#6f7b91;font-size:13px;padding:18px}}
footer a{{color:#6f7b91}}
</style></head><body>
<div class="top"><a href="/"><img src="/static/favicon.png" alt=""><b>KOREA HUB</b></a></div>
<main>{body}</main>
<footer><a href="/legal/terms">Пользовательское соглашение</a> · <a href="/legal/privacy">Политика конфиденциальности</a></footer>
</body></html>"""


def page(title: str, desc: str, body: str, canonical: str, robots: str = "index,follow", image: str | None = None) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), desc=html.escape(desc[:200]), robots=robots,
                                    canonical=html.escape(canonical), image=html.escape(image or f"{base_url()}/static/og.png"),
                                    body=body))


@router.get("/l/{listing_id}")
def public_listing(listing_id: int, db: Session = Depends(get_db)):
    """A listing as a normal web page. Only published ones; anything else is "not found" and not indexed."""
    x = db.get(Listing, listing_id)
    live = x is not None and x.status == "approved" and (x.expires_at is None or aware(x.expires_at) > now())
    if not live:
        return _not_found(listing_id)
    cover = db.scalar(select(ListingPhoto.key).where(ListingPhoto.listing_id == x.id, ListingPhoto.status == "live")
                      .order_by(ListingPhoto.position, ListingPhoto.id).limit(1))
    image = f"{base_url()}/api/photos/{cover}.jpg" if cover else None
    desc = f"{CAT.get(x.category, '')} · {x.city} · {_price(x)}. {(x.description or '').strip()}"
    d = x.details or {}
    rows = []
    if x.category == "job":
        for label, value in (("Компания", d.get("company")), ("Должность", d.get("position")),
                             ("Занятость", dict(fields.LABELS["employment"]).get(x.employment or "")),
                             ("График", dict(fields.LABELS["schedule"]).get(x.schedule or "")),
                             ("Визы", ", ".join(v for v in (x.visas or "").split(",") if v)),
                             ("Жильё", "предоставляется" if x.housing_provided else ""), ("Питание", "включено" if x.meals_provided else "")):
            rows.append((label, value))
    elif x.category == "housing":
        for label, value in (("Тип", dict(fields.LABELS["housing_type"]).get(x.housing_type or "")),
                             ("Залог", f"{x.deposit:,} ₩".replace(",", " ") if x.deposit else ""),
                             ("Площадь", f"{x.area_m2} м²" if x.area_m2 else ""), ("Метро", d.get("station"))):
            rows.append((label, value))
    else:
        for label, value in (("Категория", dict(fields.LABELS["goods_category"]).get(x.goods_category or "")),
                             ("Состояние", dict(fields.LABELS["condition"]).get(x.condition or "")), ("Бренд", d.get("brand")),
                             ("Модель", d.get("model")), ("Год", d.get("year")),
                             ("Пробег", f"{d['mileage_km']:,} км".replace(",", " ") if d.get("mileage_km") is not None else "")):
            rows.append((label, value))
    facts = "".join(f"<tr><td class='meta'>{html.escape(a)}</td><td><b>{html.escape(str(b))}</b></td></tr>" for a, b in rows if b)
    picture = f'<img class="pic" src="{html.escape(image)}" alt="">' if image else ""
    body = (f'<div class="card">{picture}'
            f'<div class="meta">{html.escape(CAT.get(x.category, ""))} · 📍 {html.escape(x.city)}</div>'
            f'<h1>{html.escape(x.title)}</h1><div class="price">{html.escape(_price(x))}</div>'
            f'{f"<table>{facts}</table>" if facts else ""}'
            f'<p style="white-space:pre-wrap">{html.escape(x.description or "")}</p>'
            f'<a class="cta" href="{html.escape(_bot_link(x.id))}">Открыть в KOREA HUB и написать автору</a>'
            f'<p class="meta">Контакты автора видны в приложении. Не переводите предоплату незнакомым людям.</p></div>')
    return page(f"{x.title} — {SITE_NAME}", desc, body, f"{base_url()}/l/{x.id}", image=image)


def _not_found(listing_id: int) -> HTMLResponse:
    shown = page("Объявление не найдено — KOREA HUB", "Объявление снято с публикации или не существует.",
                 '<div class="card"><h1>Объявление не найдено</h1><p class="meta">Оно снято с публикации или срок его истёк.</p>'
                 f'<a class="cta" href="{html.escape(base_url())}">Открыть KOREA HUB</a></div>',
                 f"{base_url()}/l/{listing_id}", robots="noindex,nofollow")
    return HTMLResponse(shown.body, status_code=404)


SECTION_TITLE = {"job": "Вакансии в Южной Корее", "housing": "Жильё в Южной Корее", "marketplace": "Барахолка в Южной Корее"}


@router.get("/c/{category}")
@router.get("/c/{category}/{city}")
def public_section(category: str, city: str | None = None, db: Session = Depends(get_db)):
    """Landing pages for search engines: «Вакансии в Пусане». Empty pages are served but not indexed."""
    if category not in SECTION_TITLE:
        missing = page("Не найдено", "", "<div class='card'><h1>Раздел не найден</h1></div>", f"{base_url()}/", robots="noindex")
        missing.status_code = 404
        return missing
    cond = [Listing.status == "approved", Listing.category == category, or_(Listing.expires_at.is_(None), Listing.expires_at > now())]
    heading = SECTION_TITLE[category]
    if city:
        city = city.strip()[:60].replace("%", "").replace("_", "")
        cond.append(Listing.city_key.like(city.lower() + "%"))
        heading = f"{CAT[category]}: {city.strip()}"
    rows = db.scalars(select(Listing).where(*cond).order_by(Listing.id.desc()).limit(50)).all()
    items = "".join(f'<p><a href="{base_url()}/l/{x.id}"><b>{html.escape(x.title)}</b></a><br>'
                    f'<span class="meta">📍 {html.escape(x.city)} · {html.escape(_price(x))}</span></p>' for x in rows)
    body = (f'<div class="card"><h1>{html.escape(heading)}</h1>'
            f'{items or "<p>Пока здесь нет объявлений.</p>"}'
            f'<a class="cta" href="{html.escape(base_url())}/">Открыть KOREA HUB</a></div>')
    path = f"/c/{category}" + (f"/{quote(city.strip())}" if city else "")
    return page(f"{heading} — {SITE_NAME}", f"{heading}: свежие объявления на русском языке, без посредников.", body,
                f"{base_url()}{path}", robots="index,follow" if rows else "noindex,follow")


@router.get("/robots.txt")
def robots():
    return PlainTextResponse(
        "User-agent: *\nAllow: /\nAllow: /l/\nDisallow: /admin\nDisallow: /api/\n"
        f"Sitemap: {base_url()}/sitemap.xml\n")


@router.get("/sitemap.xml")
def sitemap(db: Session = Depends(get_db)):
    """The home page and every published listing (at most 5000, newest first)."""
    rows = db.scalars(select(Listing).where(Listing.status == "approved",
                                            or_(Listing.expires_at.is_(None), Listing.expires_at > now()))
                      .order_by(Listing.id.desc()).limit(5000)).all()
    urls = [f"<url><loc>{html.escape(base_url())}/</loc></url>",
            f"<url><loc>{html.escape(base_url())}/legal/terms</loc></url>",
            f"<url><loc>{html.escape(base_url())}/legal/privacy</loc></url>"]
    for cat in SECTION_TITLE:
        urls.append(f"<url><loc>{html.escape(base_url())}/c/{cat}</loc></url>")
    seen = set()
    for x in rows:                      # a section+city page exists for every city that has a live listing
        key = (x.category, (x.city or "").split(",")[0].strip())
        if key[1] and key not in seen and len(seen) < 300:
            seen.add(key)
            urls.append(f"<url><loc>{html.escape(base_url())}/c/{key[0]}/{html.escape(quote(key[1]))}</loc></url>")
    for x in rows:
        last = f"<lastmod>{aware(x.published_at).date().isoformat()}</lastmod>" if x.published_at else ""
        urls.append(f"<url><loc>{html.escape(base_url())}/l/{x.id}</loc>{last}</url>")
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + "".join(urls) + "</urlset>", media_type="application/xml")


@router.get("/manifest.webmanifest")
def manifest():
    import json
    data = {"name": "KOREA HUB", "short_name": "KOREA HUB", "description": "Работа, жильё и объявления для жизни в Южной Корее",
            "start_url": "/", "display": "standalone", "background_color": "#f3f6fb", "theme_color": "#1b4b9b", "lang": "ru",
            "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker():
    """Served from the root so that it may control the whole site; never cached by the browser itself."""
    from pathlib import Path
    body = (Path(__file__).parent / "static" / "sw.js").read_text(encoding="utf-8")
    return Response(body, media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/favicon.ico")
def favicon():
    from fastapi.responses import FileResponse
    from pathlib import Path
    return FileResponse(Path(__file__).parent / "static" / "favicon.png", media_type="image/png")


# ---------- legal pages: DRAFT texts ----------

DRAFT_BANNER = ('<div class="draft">⚠ ЧЕРНОВИК. Этот текст — шаблон, а не готовый юридический документ. Перед запуском его должен '
                f'проверить и доработать юрист. Версия: {LEGAL_VERSION}. Места в квадратных скобках [ ] нужно заполнить.</div>')

TERMS = """<h1>Пользовательское соглашение</h1>
<h2>1. Что это за сервис</h2>
<p>KOREA HUB (далее — «Сервис») — приложение в Telegram и сайт, где пользователи публикуют объявления о работе, жилье, товарах, автомобилях и услугах в Южной Корее, общаются, знакомятся и читают справочную информацию. Оператор Сервиса: [НАЗВАНИЕ ОПЕРАТОРА, ДАННЫЕ ЮРИДИЧЕСКОГО ЛИЦА ИЛИ ИП], контакт: [АДРЕС ЭЛЕКТРОННОЙ ПОЧТЫ ИЛИ TELEGRAM ПОДДЕРЖКИ].</p>
<h2>2. Кто может пользоваться</h2>
<p>Сервисом могут пользоваться совершеннолетние (18 лет и старше) дееспособные люди. Раздел «Знакомства» — только с 18 лет. Нажимая «Принять», вы подтверждаете, что прочитали это соглашение.</p>
<h2>3. Правила объявлений</h2>
<ul><li>Информация должна быть правдивой; нельзя публиковать чужие фото и тексты без прав.</li>
<li>Запрещены обман, просьбы о предоплате и переводе на карту, продажа запрещённых товаров и услуг, оскорбления, дискриминация.</li>
<li>Контакты (телефон, @username, ссылки) указываются только в поле «Контакт».</li>
<li>Каждое объявление проверяет модератор до публикации. Оператор вправе отклонить, скрыть или удалить объявление и заблокировать аккаунт при нарушении правил; причина сообщается автору.</li>
<li>Срок показа объявления — 30 дней; его можно продлить.</li></ul>
<h2>4. Ответственность сторон</h2>
<p>Сервис — площадка для объявлений и общения. Оператор не является стороной сделок между пользователями (найм, аренда, купля-продажа, оказание услуг) и не гарантирует их исполнение. Проверяйте собеседников и документы самостоятельно. [ЮРИСТУ: определить границы ответственности оператора по применимому праву.]</p>
<h2>5. Платные услуги</h2>
<p>Продвижение объявлений (поднятие, VIP, закрепление, выделение) оплачивается звёздами Telegram (Stars). Услуга включается после подтверждения платежа Telegram. Возврат: [ЮРИСТУ: условия возврата платежей, сроки, случаи отказа]. Администратор может вернуть платёж, если услуга не была оказана.</p>
<h2>6. Справочная информация</h2>
<p>Материалы раздела «Гид» носят справочный характер, сопровождаются ссылкой на официальный источник и датой проверки, но не являются юридической консультацией. Правила меняются: проверяйте актуальные данные в официальных источниках.</p>
<h2>7. Блокировка и удаление аккаунта</h2>
<p>Вы можете удалить аккаунт в разделе «Профиль» (ваши объявления скрываются, сообщения обезличиваются). Оператор может заблокировать аккаунт за нарушение правил.</p>
<h2>8. Изменения</h2>
<p>Оператор может изменять соглашение; о существенных изменениях пользователей уведомляют в приложении. Продолжая пользоваться Сервисом, вы принимаете новую версию. Применимое право и порядок разрешения споров: [ЮРИСТУ: указать].</p>"""

PRIVACY = """<h1>Политика конфиденциальности</h1>
<h2>1. Какие данные мы обрабатываем</h2>
<ul><li>Telegram ID и имя, которые передаёт Telegram при входе; выбранный город, виза (если указали), отображаемое имя и фото профиля (если загрузили).</li>
<li>Содержимое ваших объявлений, откликов, сообщений в чатах и фотографии, которые вы загружаете.</li>
<li>Для знакомств: год рождения, пол, имя, город, описание, фото (и фото-подтверждение, если вы его присылаете).</li>
<li>Платежи звёздами Telegram: номер платежа, сумма, услуга (данные банковских карт мы не получаем).</li>
<li>Технические данные: IP-адрес и тип браузера при входе на сайт, журнал ошибок. Тексты сообщений в технические журналы не пишутся.</li></ul>
<h2>2. Для чего</h2>
<p>Чтобы работал Сервис: показывать объявления, связывать пользователей, проверять объявления модераторами, принимать платежи, присылать уведомления в Telegram (их можно отключить в «Настройки»), защищать от мошенничества и спама.</p>
<h2>3. Кому доступны данные</h2>
<ul><li>Другим пользователям: то, что вы сами публикуете (объявление, ваше имя в отклике, анкета знакомств после проверки). Ваш Telegram ID и телефон другим пользователям не показываются.</li>
<li>Модераторам и поддержке: объявления, жалобы и переписка, на которую поступила жалоба; действия модераторов записываются в журнал.</li>
<li>Telegram и поставщикам инфраструктуры (хостинг), в объёме, необходимом для работы Сервиса. [ЮРИСТУ: перечислить обработчиков и страны размещения данных.]</li></ul>
<h2>4. Сколько хранятся данные</h2>
<p>Пока у вас есть аккаунт и в течение [СРОК — ЮРИСТУ] после удаления для защиты от повторных нарушений и по требованиям закона. Резервные копии хранятся 14 дней.</p>
<h2>5. Ваши права</h2>
<p>В «Профиль» → «Выгрузить мои данные» вы получаете копию своих данных; «Удалить аккаунт» удаляет профиль, скрывает объявления и обезличивает сообщения. По вопросам: [КОНТАКТ ОПЕРАТОРА]. [ЮРИСТУ: права по применимому закону о персональных данных (страна оператора и страны пользователей), порядок обращений, сроки ответа.]</p>
<h2>6. Безопасность</h2>
<p>Доступ к данным ограничен ролями сотрудников, вход в админ-панель подтверждается в Telegram, действия администраторов журналируются, фото очищаются от метаданных (в том числе геопозиции) при загрузке.</p>
<h2>7. Дети</h2>
<p>Сервис не предназначен для лиц младше 18 лет.</p>"""


@router.get("/legal/{doc}")
def legal(doc: str):
    if doc not in ("terms", "privacy"):
        raise HTTPException(404, "Страница не найдена")
    title = "Пользовательское соглашение" if doc == "terms" else "Политика конфиденциальности"
    return page(f"{title} — {SITE_NAME}", f"{title} KOREA HUB (черновик, версия {LEGAL_VERSION})",
                f'<div class="card legal">{DRAFT_BANNER}{TERMS if doc == "terms" else PRIVACY}</div>', f"{base_url()}/legal/{doc}")
