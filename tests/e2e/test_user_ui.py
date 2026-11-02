"""The user part on a phone-size screen in a real browser: every main screen opens, nothing sticks out of the screen,
no script errors and no server errors while going through the menus, filters, picks and the posting wizard.

A report of what looks wrong is printed in the failure message. Screenshots are saved to SHOTS_DIR (CI keeps them as an artifact)."""
import datetime as dt
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx")
from playwright.sync_api import sync_playwright  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import WebSession  # noqa: E402
from app.webauth import _hash  # noqa: E402
from tests.tg import as_user, reset_db  # noqa: E402

ADMIN = {"X-Admin-Session": "test-session-token"}
SHOTS = Path(os.environ.get("SHOTS_DIR", "shots"))
TOKEN = "user-ui-test-session"

OVERFLOW_JS = """() => {
  const W = window.innerWidth, out = [];
  const scrolls = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('[hidden], .hidden')) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if ((r.right > W + 1 || r.left < -1) && !scrolls(el) && cs.position !== 'fixed') {
      out.push((el.tagName + '.' + String(el.className).split(' ')[0] + ' "' + (el.textContent || '').trim().slice(0, 30) + '" ' + Math.round(r.left) + '..' + Math.round(r.right)));
      if (out.length >= 8) break;
    }
  }
  const sw = Math.max(document.documentElement.scrollWidth, (document.querySelector('#scroll') || {scrollWidth: 0}).scrollWidth);
  if (sw > W + 1) out.push('PAGE scrollWidth ' + sw + ' > ' + W);
  return out;
}"""
SMALL_JS = """() => [...document.querySelectorAll('button, a, input[type=checkbox], select')].filter((el) => {
  if (el.closest('[hidden], .hidden')) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.top < innerHeight && (r.height < 28 || r.width < 28) && (el.textContent || '').trim().length < 40;
}).slice(0, 6).map((el) => el.tagName + '.' + String(el.className).split(' ')[0] + ' "' + (el.textContent || '').trim().slice(0, 20) + '" ' + Math.round(el.getBoundingClientRect().width) + 'x' + Math.round(el.getBoundingClientRect().height))"""


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    import uvicorn
    from app.config import settings
    from app.main import app
    settings.upload_dir = str(tmp_path_factory.mktemp("uploads_user"))
    reset_db()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    class Srv(uvicorn.Server):
        def install_signal_handlers(self):
            pass
    server = Srv(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    with httpx.Client(base_url=url, timeout=20) as c:
        for uid in (1, 2):
            assert c.post("/api/me/consent", json={"accept": True}, headers=as_user(uid)).status_code == 200
        c.post("/api/me/account/skip", headers=as_user(1))               # a returning visitor has already answered «заполню позже»
        for i, (cat, title, price) in enumerate([("job", "Повар в корейское кафе", 2500000), ("housing", "Студия у метро, без залога", 600000),
                                                  ("marketplace", "Велосипед почти новый", 150000)]):
            r = c.post("/api/listings", json={"category": cat, "title": title, "city": "Сеул", "description": "Подробное описание объявления " * 4,
                                              "contact": "", "price_amount": price}, headers=as_user(2))
            assert r.status_code == 201, r.text
            c.post(f"/api/admin/listings/{r.json()['id']}/approve", headers=ADMIN)
    with SessionLocal() as db:
        db.add(WebSession(token_hash=_hash(TOKEN), tg_id="1", expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)))
        db.commit()
    yield url
    server.should_exit = True


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def phone(browser, base):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    problems = {"js": [], "server": []}
    page.on("pageerror", lambda e: problems["js"].append(str(e)))
    page.on("response", lambda r: problems["server"].append(f"{r.status} {r.url}") if r.status >= 500 else None)
    page.on("dialog", lambda d: d.accept("тест"))
    page.route("**/telegram.org/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=""))
    page.add_init_script(f"try{{if(window===window.top){{localStorage.setItem('webSession','{TOKEN}');localStorage.setItem('welcomeSeen','1');}}}}catch(e){{}}")
    page.goto(base + "/")
    page.wait_for_selector("#view-home", timeout=15000)
    page.wait_for_timeout(1200)
    SHOTS.mkdir(parents=True, exist_ok=True)
    yield page, problems
    ctx.close()


def look(page, name, report):
    """Layout report of the current screen + a screenshot."""
    page.wait_for_timeout(500)
    over = page.evaluate(OVERFLOW_JS)
    if over:
        report.append(f"[{name}] выходит за экран: {over}")
    small = page.evaluate(SMALL_JS)
    if small:
        report.append(f"[{name}] мелкие элементы нажатия (не ошибка, подсказка): {small}")
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    return over


def test_main_screens_fit_the_phone_and_have_no_errors(phone):
    page, problems = phone
    report, hard = [], []
    hard += [f"home: {x}" for x in look(page, "home", report)]
    page.click("#tilesMore")                                   # the rest of the tiles
    hard += [f"home-more: {x}" for x in look(page, "home-more", report)]
    page.locator("[data-pick]").first.click()                   # a smart collection (some depend on the person's profile)
    page.wait_for_timeout(800)
    hard += [f"pick: {x}" for x in look(page, "pick", report)]
    for view in ("saved", "chats", "profile", "dating", "community", "guide"):
        page.evaluate(f"showView('{view}')")
        page.wait_for_timeout(900)
        hard += [f"{view}: {x}" for x in look(page, view, report)]
    print("\n".join(report))
    assert not problems["js"], problems["js"]
    assert not problems["server"], problems["server"]
    assert not hard, "\n".join(hard) + "\n\nВсе замечания:\n" + "\n".join(report)


def test_every_profile_menu_item_opens(phone):
    page, problems = phone
    page.evaluate("showView('profile')")
    page.wait_for_timeout(1000)
    page.click(".moremenu summary")
    keys = page.eval_on_selector_all("#view-profile [data-menu]", "els => [...new Set(els.map(e => e.dataset.menu))]")
    report, hard = [], []
    for key in keys:
        if key in {"logout", "delete", "export"}:
            continue
        page.evaluate("showView('profile')")
        page.wait_for_timeout(300)
        page.evaluate(f"document.querySelector('#view-profile [data-menu=\"{key}\"]').click()")
        page.wait_for_timeout(900)
        hard += [f"menu {key}: {x}" for x in look(page, f"menu-{key}", report)]
    print("\n".join(report))
    assert not problems["js"], problems["js"]
    assert not problems["server"], problems["server"]
    assert not hard, "\n".join(hard) + "\n\nВсе замечания:\n" + "\n".join(report)


def test_listing_page_and_posting_wizard(phone):
    page, problems = phone
    report, hard = [], []
    page.locator("#freshAds .listing, #listings .listing").first.click()
    page.wait_for_timeout(1000)
    hard += [f"detail: {x}" for x in look(page, "detail", report)]
    page.evaluate("showView('home')")
    page.click("#addBtn")
    page.wait_for_timeout(500)
    hard += [f"add-sheet: {x}" for x in look(page, "add-sheet", report)]
    page.locator("#addSheet button[data-add]").first.click()
    page.wait_for_selector("#listingForm", state="visible", timeout=10000)
    hard += [f"wizard-1: {x}" for x in look(page, "wizard-1", report)]
    print("\n".join(report))
    assert not problems["js"], problems["js"]
    assert not problems["server"], problems["server"]
    assert not hard, "\n".join(hard) + "\n\nВсе замечания:\n" + "\n".join(report)


def _api(base, method, path, user=None, admin=False, **kw):
    headers = dict(as_user(user)) if user else {}
    if admin:
        headers.update(ADMIN)
    return httpx.request(method, base + path, headers=headers, timeout=20, **kw)


def test_a_vacancy_is_posted_through_the_wizard_and_waits_for_moderation(phone, base):
    page, problems = phone
    page.evaluate("showView('home')")
    page.click("#addBtn")
    page.locator("#addSheet button[data-add='job']").click()
    page.wait_for_selector("#listingForm", state="visible", timeout=10000)
    f = page.locator("#listingForm")
    f.locator("[name=title]").fill("Официант в ресторан, Каннам")
    f.locator("[name=region_code]").select_option(index=1)
    page.wait_for_timeout(400)
    f.locator("[name=district_id]").select_option(index=1)
    f.locator("[name=price_amount]").fill("2400000")
    page.click("#nextBtn")
    page.click("#nextBtn")
    f.locator("[name=description]").fill("Нужен официант на полный день, график 5/2, обучение и питание.")
    page.click("#nextBtn")
    f.locator("[name=contact]").fill("@waiter_job")
    page.check("#rulesOk")
    page.click("#sendBtn")
    page.wait_for_selector("#doneSheet:not(.hidden)", timeout=15000)
    with SessionLocal() as db:
        from app.models import Listing
        row = db.query(Listing).filter(Listing.title == "Официант в ресторан, Каннам").one()
        assert row.status in {"pending", "approved"} and row.price_amount == 2400000 and row.author_telegram_id == "1"
    assert not problems["js"] and not problems["server"], problems


def test_own_listing_page_and_quick_price_change(phone, base):
    page, problems = phone
    r = _api(base, "POST", "/api/listings", user=1, json={"category": "marketplace", "title": "Стул деревянный", "city": "Сеул", "description": "ok " * 10, "contact": "", "price_amount": 100000})
    lid = r.json()["id"]
    _api(base, "POST", f"/api/admin/listings/{lid}/approve", admin=True)
    page.evaluate("showView('profile')")
    page.wait_for_timeout(600)
    page.evaluate("showView('mine', {push: true})")
    page.wait_for_selector(f"#myListings [data-ownopen='{lid}']", timeout=10000)
    page.click(f"#myListings [data-ownopen='{lid}']")
    page.wait_for_selector(".ownpage [data-own='price']", timeout=10000)
    assert "Стул деревянный" in page.inner_text(".ownpage")
    page.click(".ownpage [data-own='price']")
    page.fill("#textInput", "90000")
    page.click("#textOk")
    for _ in range(50):
        with SessionLocal() as db:
            from app.models import Listing
            if db.get(Listing, lid).price_amount == 90000:
                break
        page.wait_for_timeout(200)
    with SessionLocal() as db:
        from app.models import Listing
        assert db.get(Listing, lid).price_amount == 90000
    assert not problems["js"] and not problems["server"], problems


def test_a_message_is_written_and_shown_in_the_chat(phone, base):
    page, problems = phone
    lid = httpx.get(base + "/api/catalog?category=job").json()["items"][0]["id"]
    page.evaluate(f"openDetail({lid})")
    page.wait_for_selector("#view-detail [data-act='chat']", timeout=10000)
    page.click("#view-detail [data-act='chat']")
    page.wait_for_selector("#chatInput", state="visible", timeout=10000)
    page.fill("#chatInput", "Здравствуйте, вакансия ещё актуальна?")
    page.click("#chatSend")
    page.wait_for_selector("#messages >> text=вакансия ещё актуальна", timeout=10000)
    assert not problems["js"] and not problems["server"], problems


def test_interface_language_can_be_switched_to_english_and_korean(browser, base):
    for lang, expect in (("en", "My listings"), ("ko", "내 게시글")):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/telegram.org/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=""))
        page.add_init_script(f"try{{if(window===window.top){{localStorage.setItem('webSession','{TOKEN}');localStorage.setItem('welcomeSeen','1');localStorage.setItem('khLang','{lang}');}}}}catch(e){{}}")
        page.goto(base + "/")
        page.wait_for_selector("#view-home", timeout=15000)
        page.wait_for_timeout(1500)
        page.evaluate("showView('profile')")
        page.wait_for_timeout(800)
        assert expect in page.inner_text("#view-profile"), (lang, page.inner_text("#view-profile")[:300])
        assert page.inner_text("#langBtn") == lang.upper()
        assert not errors, errors
        ctx.close()
