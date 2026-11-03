import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import site, webauth
from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Listing, User
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "webapp_url", "https://hub.example.org")
    monkeypatch.setattr(webauth, "bot_username", lambda: "korea_hub_bot")
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def publish(client, publish=True, **kw):
    body = {"category": "housing", "title": "Квартира в Каннаме", "city": "Сеул", "description": "Светлая квартира, 2 комнаты",
            "price_amount": 1200000}
    body.update(kw)
    lid = client.post("/api/listings", json=body, headers=as_user(1)).json()["id"]
    if publish:
        client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def test_public_page_has_content_and_link_preview_tags(client):
    lid = publish(client)
    r = client.get(f"/l/{lid}")
    assert r.status_code == 200 and "Квартира в Каннаме" in r.text and "1 200 000" in r.text
    for tag in ('property="og:title"', 'property="og:image"', 'name="twitter:card"', 'rel="canonical"', 'name="description"'):
        assert tag in r.text, tag
    assert f'href="https://hub.example.org/l/{lid}"' in r.text and "index,follow" in r.text
    assert f"https://t.me/korea_hub_bot?start=l_{lid}" in r.text            # the button leads into the bot/app
    assert "Контакты автора видны в приложении" in r.text and "@" not in r.text.split("<main>")[1].split("</main>")[0].replace("@username", "")


def test_public_page_shows_cover_photo_and_hides_contact(client):
    lid = publish(client, publish=False, contact="+82 10 1111 2222")
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (200, 150), "red").save(buf, "JPEG")
    client.post(f"/api/listings/{lid}/photos", files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")}, headers=as_user(1))
    assert "https://hub.example.org/api/photos/" not in client.get(f"/l/{lid}").text      # not published yet: not shown at all
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    r = client.get(f"/l/{lid}")
    assert "https://hub.example.org/api/photos/" in r.text
    assert "1111" not in r.text and "+82" not in r.text                       # a phone number never goes to a public page


def test_unpublished_expired_and_missing_are_404_and_not_indexed(client):
    draft = publish(client, publish=False)
    for lid in (draft, 9999):
        r = client.get(f"/l/{lid}")
        assert r.status_code == 404 and "noindex" in r.text
    lid = publish(client)
    client.post(f"/api/listings/{lid}/close", headers=as_user(1))
    assert client.get(f"/l/{lid}").status_code == 404


def test_page_escapes_html_in_user_text(client):
    lid = publish(client, title="<script>alert(1)</script> Дом", description='"><img src=x onerror=alert(2)>')
    r = client.get(f"/l/{lid}")
    assert "<script>alert(1)" not in r.text and "&lt;script&gt;" in r.text and "<img src=x" not in r.text


def test_sitemap_lists_only_published(client):
    a = publish(client, title="Первое объявление")
    publish(client, publish=False, title="Черновик не должен попасть")
    closed = publish(client, title="Закрытое объявление")
    client.post(f"/api/listings/{closed}/close", headers=as_user(1))
    r = client.get("/sitemap.xml")
    assert r.headers["content-type"].startswith("application/xml")
    urls = [u.text for u in ET.fromstring(r.text).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert f"https://hub.example.org/l/{a}" in urls and not any(u.endswith(f"/l/{closed}") for u in urls)
    assert "https://hub.example.org/" in urls and len([u for u in urls if "/l/" in u]) == 1


def test_robots_keeps_api_and_admin_out(client):
    t = client.get("/robots.txt").text
    assert "Disallow: /admin" in t and "Disallow: /api/" in t and "Sitemap: https://hub.example.org/sitemap.xml" in t


def test_legal_pages_are_clearly_drafts(client):
    for doc, word in (("terms", "Пользовательское соглашение"), ("privacy", "Политика конфиденциальности")):
        r = client.get(f"/legal/{doc}")
        assert r.status_code == 200 and word in r.text and "ЧЕРНОВИК" in r.text and site.LEGAL_VERSION in r.text
        assert "ЮРИСТУ" in r.text                                                    # open questions are marked in the text
    assert client.get("/legal/other").status_code == 404


def test_consent_stores_the_version_of_the_texts(client):
    with SessionLocal() as db:
        assert db.get(User, "1").consent_version == site.LEGAL_VERSION
    assert 'href="/legal/terms"' in client.get("/").text and 'href="/legal/privacy"' in client.get("/").text


def test_home_page_has_icons_description_and_manifest(client):
    html = client.get("/").text
    for part in ('name="description"', 'property="og:image"', 'rel="icon"', 'rel="manifest"', 'rel="apple-touch-icon"'):
        assert part in html, part
    m = client.get("/manifest.webmanifest").json()
    assert m["name"] == "KOREA HUB" and len(m["icons"]) == 2
    for path in ("/static/icon-192.png", "/static/icon-512.png", "/static/og.png", "/static/favicon.png", "/favicon.ico"):
        assert client.get(path).status_code == 200 and client.get(path).headers["content-type"].startswith("image/"), path
