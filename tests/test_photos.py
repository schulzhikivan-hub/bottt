import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import ListingPhoto
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def image(fmt="JPEG", size=(120, 80), color="red", **save):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt, **save)
    return out.getvalue()


def make(client, category="housing", publish=False, **fields):
    body = {"category": category, "title": "Комната", "city": "Асан", "description": "ok", "contact": ""}
    body.update(fields)
    lid = client.post("/api/listings", json=body, headers=as_user(1)).json()["id"]
    if publish:
        client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def upload(client, lid, data=None, user=1, name="p.jpg", ctype="image/jpeg"):
    return client.post(f"/api/listings/{lid}/photos", files={"file": (name, data or image(), ctype)}, headers=as_user(user))


def mine(client, lid):
    return next(x for x in client.get("/api/me/listings", headers=as_user(1)).json() if x["id"] == lid)


def public(client, lid):
    return client.get(f"/api/listings/{lid}").json()


# ---- upload and processing ----

def test_accepts_jpeg_png_and_webp(client):
    lid = make(client)
    for fmt in ("JPEG", "PNG", "WEBP"):
        assert upload(client, lid, image(fmt)).status_code == 201, fmt


def test_files_are_checked_by_content_not_by_name(client):
    lid = make(client)
    assert upload(client, lid, b"<html>not an image</html>", name="photo.jpg").status_code == 400
    assert upload(client, lid, b"GIF89a" + b"0" * 50, name="x.jpg").status_code == 400


def test_big_files_are_refused_and_big_pictures_are_shrunk(client):
    lid = make(client)
    assert upload(client, lid, b"\xff\xd8\xff" + os.urandom(9 * 1024 * 1024)).status_code == 413
    r = upload(client, lid, image(size=(3200, 2000))).json()
    full = client.get(r["url"])
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(full.content)).size) == 1600
    assert max(Image.open(io.BytesIO(client.get(r["thumb"]).content)).size) == 480


def test_metadata_is_removed_and_orientation_applied(client):
    lid = make(client)
    img = Image.new("RGB", (40, 20), "blue")
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"      # camera make
    exif[0x0112] = 6                # rotated 90 degrees
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    r = upload(client, lid, out.getvalue()).json()
    result = Image.open(io.BytesIO(client.get(r["url"]).content))
    assert result.size == (20, 40)                      # turned upright
    assert len(result.getexif()) == 0                   # nothing from the original metadata is left


def test_only_the_author_can_add_photos(client):
    lid = make(client)
    assert upload(client, lid, user=2).status_code == 404


def test_limit_of_ten_photos(client):
    lid = make(client)
    for _ in range(10):
        assert upload(client, lid).status_code == 201
    r = upload(client, lid)
    assert r.status_code == 409 and "10" in r.json()["detail"]


def test_photo_names_cannot_escape_the_folder(client):
    for bad in ("../../etc/passwd", "..%2f..%2fsecret.jpg", "x.jpg", "a" * 32 + ".png"):
        assert client.get(f"/api/photos/{bad}").status_code == 404


# ---- order, cover, delete ----

def test_reorder_makes_the_cover(client):
    lid = make(client, publish=True, category="job")
    a, b, c = (upload(client, lid, image(color=col)).json()["id"] for col in ("red", "green", "blue"))
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)  # waiting photos go live
    assert [p["id"] for p in mine(client, lid)["photos"]] == [a, b, c]
    r = client.put(f"/api/listings/{lid}/photos/order", json={"ids": [c, a, b]}, headers=as_user(1))
    assert r.status_code == 200
    assert [p["id"] for p in mine(client, lid)["photos"]] == [c, a, b]
    assert client.put(f"/api/listings/{lid}/photos/order", json={"ids": [c, a]}, headers=as_user(1)).status_code == 400


def test_delete_before_publication_removes_the_file(client):
    lid = make(client)
    p = upload(client, lid).json()
    assert client.get(p["url"]).status_code == 200
    assert client.delete(f"/api/listings/{lid}/photos/{p['id']}", headers=as_user(1)).status_code == 200
    assert client.get(p["url"]).status_code == 404
    assert mine(client, lid)["photos"] == []


# ---- visibility and moderation ----

def test_photos_appear_with_the_listing_and_cover_in_catalog(client):
    lid = make(client)
    p = upload(client, lid).json()
    assert client.get(f"/api/listings/{lid}").status_code == 404           # not published yet
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    detail = public(client, lid)
    assert [x["id"] for x in detail["photos"]] == [p["id"]]
    cards = client.get("/api/catalog").json()["items"]
    assert cards[0]["cover"] == p["thumb"]


def test_new_photo_on_a_published_listing_waits_for_a_moderator(client):
    lid = make(client, publish=True)
    p = upload(client, lid).json()
    assert p["status"] == "pending"
    assert public(client, lid)["photos"] == []                              # visitors do not see it yet
    assert mine(client, lid)["photos"][0]["status"] == "pending"            # the author does
    queue = client.get("/api/admin/listings", headers=ADMIN).json()
    assert any(x["id"] == lid and x["has_edit"] for x in queue)
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    assert [x["id"] for x in public(client, lid)["photos"]] == [p["id"]]


def test_rejected_photo_edit_removes_it_and_keeps_the_listing(client):
    lid = make(client, publish=True)
    p = upload(client, lid).json()
    client.post(f"/api/admin/listings/{lid}/reject?reason=Плохое+фото", headers=ADMIN)
    assert public(client, lid)["photos"] == []
    assert client.get(p["url"]).status_code == 404
    assert mine(client, lid)["status"] == "approved" and mine(client, lid)["note"] == "Плохое фото"


def test_removing_a_shown_photo_waits_and_can_be_rejected(client):
    lid = make(client)
    p = upload(client, lid).json()
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    client.delete(f"/api/listings/{lid}/photos/{p['id']}", headers=as_user(1))
    assert len(public(client, lid)["photos"]) == 1                         # still shown to visitors
    client.post(f"/api/admin/listings/{lid}/reject?reason=Нужно+фото", headers=ADMIN)
    assert len(public(client, lid)["photos"]) == 1 and mine(client, lid)["photos"][0]["status"] == "live"
    client.delete(f"/api/listings/{lid}/photos/{p['id']}", headers=as_user(1))
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    assert public(client, lid)["photos"] == [] and client.get(p["url"]).status_code == 404


# ---- the "needs a photo" rule ----

def test_housing_and_goods_need_a_photo_when_the_rule_is_on(client, monkeypatch):
    monkeypatch.setattr(settings, "require_photos", True)
    flat = make(client, category="housing")
    goods = make(client, category="marketplace")
    job = make(client, category="job")
    for lid in (flat, goods):
        r = client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
        assert r.status_code == 400 and "фото" in r.json()["detail"]
    assert client.post(f"/api/admin/listings/{job}/approve", headers=ADMIN).status_code == 200
    upload(client, flat)
    assert client.post(f"/api/admin/listings/{flat}/approve", headers=ADMIN).status_code == 200


def test_draft_needs_a_photo_before_it_can_be_sent(client, monkeypatch):
    monkeypatch.setattr(settings, "require_photos", True)
    lid = make(client, category="housing", draft=True)
    assert mine(client, lid)["status"] == "draft"
    assert client.post(f"/api/listings/{lid}/submit", headers=as_user(1)).status_code == 400
    upload(client, lid)
    r = client.post(f"/api/listings/{lid}/submit", headers=as_user(1))
    assert r.status_code == 200 and r.json()["status"] == "pending"


def test_deleting_the_account_removes_the_photo_files(client):
    lid = make(client)
    p = upload(client, lid).json()
    client.post("/api/me/delete", headers=as_user(1))
    assert client.get(p["url"]).status_code == 404
    with SessionLocal() as db:
        assert db.query(ListingPhoto).count() == 0
