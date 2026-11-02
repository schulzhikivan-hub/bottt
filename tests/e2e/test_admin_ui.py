"""The admin panel in a real browser: every section opens without errors, and the editing screens really save.

Needs `playwright` (installed in CI; the tests are skipped where it is missing). The app runs on a real port against the test SQLite database."""
import socket
import threading
import time
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx")
from playwright.sync_api import sync_playwright  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ClientError, DatingProfile, Listing, User  # noqa: E402
from tests.tg import as_user, reset_db  # noqa: E402

ADMIN = {"X-Admin-Session": "test-session-token"}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    import uvicorn
    from app.config import settings
    from app.main import app
    settings.upload_dir = str(tmp_path_factory.mktemp("uploads"))
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
        r = c.post("/api/listings", json={"category": "job", "title": "Повар в кафе", "city": "Сеул", "description": "Нужен повар", "contact": ""}, headers=as_user(1))
        assert r.status_code == 201
        assert c.post(f"/api/admin/listings/{r.json()['id']}/approve", headers=ADMIN).status_code == 200
        pid = c.put("/api/dating/profile", json={"birth_year": 1990, "gender": "f", "city": "Сеул", "bio": "Люблю кофе"}, headers=as_user(1)).json()["id"]
        c.post("/api/dating/photo", files={"file": ("me.jpg", JPEG, "image/jpeg")}, headers=as_user(1))
        assert c.post(f"/api/admin/dating/{pid}/approve", headers=ADMIN).status_code == 200
    with SessionLocal() as db:
        for i in range(3):
            db.add(ClientError(user_tg_id=str(i), where="/api/x", status=500, message="Ошибка для теста"))
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
def admin(browser, base):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.khdialog = lambda d: d.accept("тест причина")                  # prompts and confirms get an answer
    page.on("dialog", page.khdialog)
    page.route("**/telegram.org/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=""))
    page.add_init_script("try{if(window===window.top){sessionStorage.setItem('adminSession','test-session-token');sessionStorage.setItem('adminRole','super_admin');"
                         "sessionStorage.setItem('adminId','999');localStorage.setItem('adminTheme','light');}}catch(e){}")
    page.goto(base + "/admin")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Загрузка')", timeout=15000)
    yield page, errors
    ctx.close()


def settled(page):
    page.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Загрузка')", timeout=15000)
    return page.inner_text("#status")


def go(page, section, sub):
    page.locator(f"#tabs button[data-sec={section}]").click()
    page.locator(f"#subnav button[data-sub={sub}]").click()
    return settled(page)


def test_every_section_and_subsection_opens_without_errors(admin):
    page, errors = admin
    problems = []
    for i in range(page.locator("#tabs button[data-sec]").count()):
        page.locator("#tabs button[data-sec]").nth(i).click()
        sec = page.locator("#tabs button[data-sec]").nth(i).get_attribute("data-sec")
        status = settled(page)
        if not status.startswith("Обновлено"):
            problems.append((sec, status))
        if not page.locator("#subnav").is_visible():
            continue                                    # a section with one page has no second row
        for j in range(page.locator("#subnav button").count()):
            btn = page.locator("#subnav button").nth(j)
            key = btn.get_attribute("data-sub")
            btn.click()
            status = settled(page)
            if not status.startswith("Обновлено"):
                problems.append((key, status))
    assert not problems, problems
    assert not errors, errors


def test_listing_can_be_edited_in_the_side_panel_and_the_change_is_saved(admin):
    page, errors = admin
    assert go(page, "listings", "alllist").startswith("Обновлено")
    page.locator("[data-lrow]").first.click()
    page.wait_for_selector("#drawer [data-lf=title]", timeout=10000)
    page.fill("#drawer [data-lf=title]", "Новое название вакансии")
    page.fill("#drawer [data-lf=description]", "Новое описание вакансии подлиннее")
    page.click("#drawer [data-lfsave]")
    page.wait_for_selector(".atoast >> text=Сохранено", timeout=10000)
    with SessionLocal() as db:
        item = db.query(Listing).first()
        assert item.title == "Новое название вакансии" and "подлиннее" in item.description
    assert not errors, errors


def test_listing_decision_buttons_work_in_the_panel(admin):
    page, errors = admin
    go(page, "listings", "alllist")
    page.locator("[data-lrow]").first.click()
    page.wait_for_selector("#drawer [data-act=hide]", timeout=10000)
    page.click("#drawer [data-act=hide]")
    for _ in range(50):                                   # the click starts a request; wait until the server has applied it
        with SessionLocal() as db:
            if db.query(Listing).first().status == "hidden":
                break
        page.wait_for_timeout(200)
    with SessionLocal() as db:
        assert db.query(Listing).first().status == "hidden"
    assert not errors, errors


def test_dating_profile_can_be_edited(admin):
    page, errors = admin
    assert go(page, "dating", "datingall").startswith("Обновлено")
    page.locator("[data-drow]").first.click()
    page.wait_for_selector("#drawer [data-df=bio]", timeout=10000)
    page.fill("#drawer [data-df=bio]", "Новый текст анкеты от модератора")
    page.click("#drawer [data-dsave]")
    page.wait_for_selector(".atoast >> text=Сохранено", timeout=10000)
    with SessionLocal() as db:
        assert db.query(DatingProfile).first().bio == "Новый текст анкеты от модератора"
    assert not errors, errors


def test_person_can_be_managed_and_edited(admin):
    page, errors = admin
    assert go(page, "people", "users").startswith("Обновлено")
    page.locator("[data-ux=card]").first.click()
    page.wait_for_selector("#ueForm [data-ue=display_name]", timeout=10000)
    page.fill("#ueForm [data-ue=display_name]", "Новое Имя")
    page.click("#ueSave")
    page.wait_for_selector(".atoast >> text=Сохранено", timeout=10000)
    with SessionLocal() as db:
        assert any(u.display_name == "Новое Имя" for u in db.query(User).all())
    assert not errors, errors


def test_errors_can_be_reset(admin):
    page, errors = admin
    assert go(page, "system", "errors").startswith("Обновлено")
    page.click("[data-errclear=all]")
    page.wait_for_selector(".atoast >> text=Сброшено", timeout=10000)
    with SessionLocal() as db:
        assert db.query(ClientError).count() == 0
    assert not errors, errors


def test_list_columns_sorting_groups_views_and_inline_price(admin):
    page, errors = admin
    assert go(page, "listings", "alllist").startswith("Обновлено")
    assert "Срок до" not in page.inner_text("#allList")
    page.locator("details:has-text('Колонки') summary").click()
    page.locator("[data-kitcol=expires]").check()
    settled(page)
    assert "Срок до" in page.inner_text("#allList")                      # the chosen column appears
    page.locator("[data-kitsort='listings:views']").click()
    settled(page)
    assert "▲" in page.inner_text("#allList")                            # sorted by the header
    page.select_option("[data-kitgroup]", "status")
    settled(page)
    assert page.locator(".grouprow").count() >= 1
    # a saved view of this state
    page.remove_listener("dialog", page.khdialog)
    answers = ["Мой вид"]
    page.on("dialog", lambda d: d.accept(answers.pop(0) if answers else "x"))
    page.click("[data-kitsave]")
    page.wait_for_selector(".atoast >> text=Вид сохранён", timeout=10000)
    # inline price edit
    answers[:] = ["250000", "поправили цену"]
    page.locator("[data-inline$=':price_amount']").first.click()
    for _ in range(50):
        with SessionLocal() as db:
            if db.query(Listing).first().price_amount == 250000:
                break
        page.wait_for_timeout(200)
    with SessionLocal() as db:
        assert db.query(Listing).first().price_amount == 250000
    assert not errors, errors
