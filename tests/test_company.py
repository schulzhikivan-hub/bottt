import pytest
from fastapi.testclient import TestClient

from app import company as co
from app.database import SessionLocal, engine
from app.main import app
from app.models import Listing, Payment, User
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}
BODY = {"name": "Кафе Сеул", "reg_no": "123-45-67890", "description": "Корейская кухня", "city": "Сеул"}
JOB = {"category": "job", "title": "Повар", "city": "Сеул", "description": "ok", "contact": ""}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3, 4):
            accept_terms(c, uid)
        yield c


def verified_company(client, owner=1):
    cid = client.post("/api/company", json=BODY, headers=as_user(owner)).json()["id"]
    assert client.post(f"/api/admin/companies/{cid}/approve", json={}, headers=ADMIN).status_code == 200
    return cid


def team_member(client, cid, tg, role, owner=1):
    code = client.post(f"/api/company/{cid}/invite", json={"role": role}, headers=as_user(owner)).json()["code"]
    assert client.post("/api/company/join", json={"code": code}, headers=as_user(tg)).status_code == 200


def test_company_is_created_pending_checked_by_staff_and_posts_only_after(client):
    cid = client.post("/api/company", json=BODY, headers=as_user(1)).json()["id"]
    mine = client.get("/api/company/mine", headers=as_user(1)).json()
    assert mine[0]["status"] == "pending" and mine[0]["role"] == "owner"
    r = client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(1))
    assert r.status_code == 409                                            # not verified yet: post as yourself
    assert client.post(f"/api/admin/companies/{cid}/reject", json={"reason": "x"}, headers=ADMIN).status_code == 400   # reason too short
    client.post(f"/api/admin/companies/{cid}/approve", json={}, headers=ADMIN)
    r = client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(1))
    assert r.status_code == 201 and r.json()["company_name"] == "Кафе Сеул" and r.json()["company_verified"] is True
    with SessionLocal() as db:
        assert db.get(User, "1").employer_status == "approved"             # the old badge follows the company
    assert client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(2)).status_code == 403   # not in the team


def test_team_roles_and_leaving_closes_access(client):
    cid = verified_company(client)
    lid = client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    team_member(client, cid, 2, "recruiter")
    team_member(client, cid, 3, "viewer")
    assert client.put(f"/api/listings/{lid}", json={"title": "Повар кухни"}, headers=as_user(2)).status_code == 200      # recruiter edits
    assert client.put(f"/api/listings/{lid}", json={"title": "Другое"}, headers=as_user(3)).status_code == 404            # viewer only looks
    assert client.get(f"/api/company/{cid}/listings", headers=as_user(3)).status_code == 200
    assert client.get(f"/api/company/{cid}/team", headers=as_user(4)).status_code == 404                                  # stranger
    assert client.post(f"/api/company/{cid}/invite", json={"role": "recruiter"}, headers=as_user(2)).status_code == 403    # recruiter can not invite
    assert client.delete(f"/api/company/{cid}/team/2", headers=as_user(1)).status_code == 200
    assert client.put(f"/api/listings/{lid}", json={"title": "Снова"}, headers=as_user(2)).status_code == 404             # access went with the person
    assert client.delete(f"/api/company/{cid}/team/1", headers=as_user(1)).status_code == 409                             # owner stays


def test_applications_go_to_the_team_inbox(client):
    cid = verified_company(client)
    lid = client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    team_member(client, cid, 2, "recruiter")
    assert client.post(f"/api/listings/{lid}/apply", json={"contact": "@cook", "message": "Здравствуйте"}, headers=as_user(3)).status_code == 201
    inbox = client.get(f"/api/company/{cid}/applications", headers=as_user(2)).json()
    assert len(inbox) == 1 and inbox[0]["listing_title"] == "Повар" and inbox[0]["can_answer"] is True
    assert client.put(f"/api/applications/{inbox[0]['id']}", json={"status": "invited", "reply": "Ждём"}, headers=as_user(2)).status_code == 200
    ov = client.get(f"/api/company/{cid}", headers=as_user(2)).json()
    assert ov["listings"]["active"] == 1 and ov["applications"]["total"] == 1
    assert "balance" not in ov                                              # the money is not shown to a recruiter


def test_company_balance_is_separate_from_the_person(client):
    cid = verified_company(client)
    lid = client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 500                                # a personal balance must stay untouched
        db.commit()
    assert client.post(f"/api/admin/companies/{cid}/credit", json={"stars": 100}, headers=ADMIN).json()["balance"] == 100
    svc = client.get(f"/api/listings/{lid}/services", headers=as_user(1)).json()
    assert svc["balance"] == 100 and svc["balance_of"] == "company"
    r = client.post("/api/payments/invoice", json={"listing_id": lid, "service_code": "job_bump", "use_balance": True}, headers=as_user(1))
    assert r.status_code == 200 and r.json().get("paid") in (True, None)
    with SessionLocal() as db:
        assert db.get(co.Company, cid).balance_stars == 70 and db.get(User, "1").balance_stars == 500
        pay = db.query(Payment).one()
        assert pay.company_id == cid and pay.balance_used == 30
    bal = client.get(f"/api/company/{cid}/balance", headers=as_user(1)).json()
    assert bal["balance"] == 70 and len(bal["history"]) == 1
    # a refund goes back to the company purse
    assert client.post(f"/api/admin/payments/{pay.id}/refund?mode=balance&reason=test", headers=ADMIN).status_code == 200
    with SessionLocal() as db:
        assert db.get(co.Company, cid).balance_stars > 70 and db.get(User, "1").balance_stars == 500
    # a recruiter can not spend the company money
    team_member(client, cid, 2, "recruiter")
    r = client.post("/api/payments/invoice", json={"listing_id": lid, "service_code": "job_vip", "use_balance": True}, headers=as_user(2))
    assert r.status_code == 400


def test_old_employers_become_companies_with_their_vacancies(client):
    lid = client.post("/api/listings", json=JOB, headers=as_user(1)).json()["id"]
    with SessionLocal() as db:
        u = db.get(User, "1")
        u.employer_status, u.company_name, u.registration_no = "approved", "Старая компания", "111-22-33333"
        db.commit()
        assert co.migrate_employers(db) == 1
        assert co.migrate_employers(db) == 0                               # idempotent
        c = db.query(co.Company).one()
        assert c.status == "verified" and c.owner_tg_id == "1"
        co.sync_company_from_employer(db, db.get(User, "1"))
        assert db.get(Listing, lid).company_id == c.id


def test_public_page_only_for_verified_and_suspend_hides_listings(client):
    cid = client.post("/api/company", json=BODY, headers=as_user(1)).json()["id"]
    assert client.get(f"/api/companies/{cid}").status_code == 404
    client.post(f"/api/admin/companies/{cid}/approve", json={}, headers=ADMIN)
    lid = client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    assert [x["id"] for x in client.get(f"/api/companies/{cid}").json()["listings"]] == [lid]
    client.post(f"/api/admin/companies/{cid}/suspend", json={"reason": "жалобы"}, headers=ADMIN)
    assert client.get(f"/api/companies/{cid}").status_code == 404
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "hidden"
    client.post(f"/api/admin/companies/{cid}/restore", json={}, headers=ADMIN)
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "approved"


def approved_company_listing(client, cid, owner=1):
    lid = client.post("/api/listings", json=JOB | {"company_id": cid}, headers=as_user(owner)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def test_topup_goes_to_the_company_purse_once(client, monkeypatch):
    from app import payments
    monkeypatch.setattr(payments, "telegram_call", lambda method, payload: "https://t.me/invoice/x")
    cid = verified_company(client)
    team_member(client, cid, 2, "recruiter")
    assert client.post(f"/api/company/{cid}/topup", json={"stars": 100}, headers=as_user(2)).status_code == 403     # money: owner and admins
    assert client.post(f"/api/company/{cid}/topup", json={"stars": 5}, headers=as_user(1)).status_code == 400       # below the minimum
    r = client.post(f"/api/company/{cid}/topup", json={"stars": 200}, headers=as_user(1)).json()
    assert r["invoice_url"].startswith("https://")
    with SessionLocal() as db:
        pay = db.query(Payment).one()
        assert pay.company_id == cid and pay.service_code == "topup" and pay.status == "pending"
        done = payments.process_successful_payment(db, "1", pay.payload, "charge-1", "XTR", 200)
        assert done.kind == "topup" and done.credited == 200
        payments.process_successful_payment(db, "1", pay.payload, "charge-1", "XTR", 200)        # the same notice again: nothing added
        assert db.get(co.Company, cid).balance_stars == 200 and (db.get(User, "1").balance_stars or 0) == 0
        pid = pay.id
    assert client.post(f"/api/admin/payments/{pid}/refund?mode=balance", headers=ADMIN).status_code == 400          # not by the button
    assert client.get(f"/api/company/{cid}/balance", headers=as_user(1)).json()["history"][0]["service"] == "Пополнение баланса"
    me = client.post("/api/me/balance/topup", json={"stars": 50}, headers=as_user(3)).json()
    with SessionLocal() as db:
        p2 = db.query(Payment).filter_by(user_tg_id="3").one()
        payments.process_successful_payment(db, "3", p2.payload, "charge-2", "XTR", 50)
        assert db.get(User, "3").balance_stars == 50 and me["stars"] == 50


def test_company_listing_shows_only_the_company_not_the_person(client):
    cid = verified_company(client)
    mine = client.post("/api/listings", json=JOB | {"title": "Личная подработка"}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{mine}/approve", headers=ADMIN)
    lid = approved_company_listing(client, cid)
    a = client.get(f"/api/listings/{lid}").json()["author"]
    assert a["is_company"] is True and a["company"] == "Кафе Сеул" and a["active"] == 1 and a["trusted"] is False and a["reviews"] == 0
    p = client.get(f"/api/listings/{mine}").json()["author"]
    assert p["is_company"] is False and p["active"] == 1 and p["company_id"] is None            # the personal one knows nothing of the company
    page = client.get(f"/api/authors/{lid}").json()
    assert [x["id"] for x in page["items"]] == [lid]
    assert [x["id"] for x in client.get(f"/api/authors/{mine}").json()["items"]] == [mine]
    # reviews are about people, not about a company's employee
    assert client.get(f"/api/listings/{lid}/review-state", headers=as_user(3)).json()["can"] is False


def test_chats_of_a_company_listing_belong_to_the_team(client):
    cid = verified_company(client)
    team_member(client, cid, 2, "recruiter")
    lid = approved_company_listing(client, cid)
    assert client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).status_code == 400          # a colleague can not "buy" it
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(3)).json()["id"]
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "Вакансия ещё актуальна?"}, headers=as_user(3)).status_code == 201
    assert [c["id"] for c in client.get("/api/conversations", headers=as_user(2)).json()] == [conv]          # the recruiter sees it
    assert client.get("/api/conversations", headers=as_user(3)).json()[0]["other_name"] == "Кафе Сеул"       # the buyer sees the company
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "Да, приходите"}, headers=as_user(2)).status_code == 201
    assert client.delete(f"/api/company/{cid}/team/2", headers=as_user(1)).status_code == 200
    assert client.get(f"/api/conversations/{conv}/messages", headers=as_user(2)).status_code == 404         # access left with the person
    assert client.get(f"/api/conversations/{conv}/messages", headers=as_user(1)).status_code == 200


def test_close_company_needs_an_empty_balance(client):
    cid = verified_company(client)
    lid = approved_company_listing(client, cid)
    client.post(f"/api/admin/companies/{cid}/credit", json={"stars": 10}, headers=ADMIN)
    assert client.post(f"/api/company/{cid}/close", headers=as_user(1)).status_code == 409
    with SessionLocal() as db:
        db.get(co.Company, cid).balance_stars = 0
        db.commit()
    assert client.post(f"/api/company/{cid}/close", headers=as_user(1)).status_code == 200
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "closed"
    assert client.get("/api/company/mine", headers=as_user(1)).json() == []


def test_company_listing_is_not_in_the_personal_list_but_in_the_company_list(client):
    cid = verified_company(client)
    lid = approved_company_listing(client, cid)
    personal = client.post("/api/listings", json=JOB, headers=as_user(1)).json()["id"]
    mine = [x["id"] for x in client.get("/api/me/listings", headers=as_user(1)).json()]
    assert personal in mine and lid not in mine
    assert lid in [x["id"] for x in client.get(f"/api/company/{cid}/listings", headers=as_user(1)).json()]


def test_price_sort_is_kept_with_a_search_word(client):
    cid = verified_company(client)
    for price in (300, 100, 200):
        lid = client.post("/api/listings", json=JOB | {"company_id": cid, "price_amount": price, "title": f"Повар на {price}"}, headers=as_user(1)).json()["id"]
        with SessionLocal() as db:
            db.get(Listing, lid).status = "approved"
            db.commit()
    items = client.get("/api/catalog?q=повар&sort=price_asc").json()["items"]
    prices = [x["price_amount"] for x in items if x["price_amount"] is not None]
    assert prices == sorted(prices)


def test_duplicate_is_a_draft_copy_and_company_copy_stays_the_companys(client):
    cid = verified_company(client)
    lid = approved_company_listing(client, cid)
    r = client.post(f"/api/listings/{lid}/duplicate", headers=as_user(1))
    assert r.status_code == 201 and r.json()["status"] == "draft" and r.json()["company_id"] == cid and r.json()["id"] != lid
    assert client.post(f"/api/listings/{lid}/duplicate", headers=as_user(2)).status_code == 404       # not yours


def test_move_between_personal_and_company(client):
    cid = verified_company(client)
    pid = client.post("/api/listings", json=JOB | {"title": "Личная вакансия"}, headers=as_user(1)).json()["id"]
    assert client.post(f"/api/listings/{pid}/move", json={"company_id": cid}, headers=as_user(1)).json()["company_id"] == cid
    assert pid not in [x["id"] for x in client.get("/api/me/listings", headers=as_user(1)).json()]
    assert client.post(f"/api/listings/{pid}/move", json={"company_id": cid}, headers=as_user(1)).status_code == 409   # already there
    assert client.post(f"/api/listings/{pid}/move", json={"company_id": None}, headers=as_user(1)).json()["company_id"] is None
    assert client.post(f"/api/listings/{pid}/move", json={"company_id": cid}, headers=as_user(2)).status_code == 404   # not mine


def test_company_analytics_export_and_activity(client):
    cid = verified_company(client)
    lid = approved_company_listing(client, cid)
    client.post(f"/api/listings/{lid}/duplicate", headers=as_user(1))
    a = client.get(f"/api/company/{cid}/analytics", headers=as_user(1)).json()
    assert a["total"]["views"] == 0 and {x["id"] for x in a["items"]} >= {lid}
    r = client.get(f"/api/company/{cid}/applications.csv", headers=as_user(1))
    assert r.status_code == 200 and "Вакансия" in r.text and r.headers["content-type"].startswith("text/csv")
    log = client.get(f"/api/company/{cid}/activity", headers=as_user(1)).json()
    assert any(x["what"] == "Создана копия объявления" for x in log)
    assert client.get(f"/api/company/{cid}/analytics", headers=as_user(2)).status_code == 404


def test_hiring_funnel_stage_and_private_note(client):
    cid = verified_company(client)
    lid = approved_company_listing(client, cid)
    team_member(client, cid, 2, "recruiter")
    client.post(f"/api/listings/{lid}/apply", json={"contact": "@cook", "message": "Здравствуйте"}, headers=as_user(3))
    app_id = client.get(f"/api/company/{cid}/applications", headers=as_user(1)).json()[0]["id"]
    assert client.get(f"/api/company/{cid}/applications", headers=as_user(1)).json()[0]["stage"] == "new"
    r = client.put(f"/api/company/{cid}/applications/{app_id}/note", json={"stage": "interview", "note": "Звонить после 18:00"}, headers=as_user(2))
    assert r.status_code == 200
    row = client.get(f"/api/company/{cid}/applications", headers=as_user(1)).json()[0]
    assert row["stage"] == "interview" and row["note"] == "Звонить после 18:00"
    assert client.put(f"/api/company/{cid}/applications/{app_id}/note", json={"stage": "bogus"}, headers=as_user(1)).status_code == 400
    assert client.put(f"/api/company/{cid}/applications/{app_id}/note", json={"note": "x"}, headers=as_user(3)).status_code == 404   # not in the team
    mine = client.get("/api/me/applications", headers=as_user(3)).json()
    assert "Звонить" not in str(mine)                                      # the applicant never sees the employer's note


def test_templates_personal_and_company(client):
    cid = verified_company(client)
    team_member(client, cid, 2, "recruiter")
    body = {"category": "job", "title": "Повар", "description": "Полный день", "price_amount": 2500000, "region_code": "seoul", "evil": "x"}
    r = client.post("/api/me/templates", json={"name": "Повар, полный день", "data": body}, headers=as_user(3))
    assert r.status_code == 201 and "evil" not in r.json()["data"]
    assert [t["name"] for t in client.get("/api/me/templates", headers=as_user(3)).json()] == ["Повар, полный день"]
    assert client.get("/api/me/templates", headers=as_user(4)).json() == []                      # private
    c = client.post("/api/me/templates", json={"name": "Для команды", "data": body, "company_id": cid}, headers=as_user(1)).json()
    assert [t["name"] for t in client.get("/api/me/templates", headers=as_user(2)).json()] == ["Для команды"]   # the team sees it
    assert client.post("/api/me/templates", json={"name": "Чужой", "data": body, "company_id": cid}, headers=as_user(4)).status_code == 403
    assert client.delete(f"/api/me/templates/{c['id']}", headers=as_user(2)).status_code == 404       # a recruiter does not delete the team's
    assert client.delete(f"/api/me/templates/{c['id']}", headers=as_user(1)).status_code == 200
    assert client.post("/api/me/templates", json={"name": "Без раздела", "data": {"title": "x"}}, headers=as_user(3)).status_code == 400


def test_public_page_extras_and_company_reviews(client):
    cid = verified_company(client)
    lid = approved_company_listing(client, cid)
    team_member(client, cid, 2, "recruiter")
    assert client.put(f"/api/company/{cid}/page", json={"tagline": "Берём без опыта", "telegram": "@cafe_seoul", "benefits": ["housing", "bogus", "meals"]},
                      headers=as_user(2)).status_code == 403                                  # a recruiter does not edit the face of the company
    r = client.put(f"/api/company/{cid}/page", json={"tagline": "Берём без опыта", "telegram": "@cafe_seoul", "benefits": ["housing", "bogus", "meals"]}, headers=as_user(1))
    assert r.status_code == 200 and r.json()["benefits"] == ["housing", "meals"] and r.json()["telegram"] == "cafe_seoul"
    pub = client.get(f"/api/companies/{cid}").json()
    assert pub["page"]["tagline"] == "Берём без опыта" and pub["reviews"]["count"] == 0
    assert client.get(f"/api/companies/{cid}/review-state", headers=as_user(3)).json()["can"] is False          # never dealt with them
    assert client.post(f"/api/companies/{cid}/reviews", json={"rating": 5}, headers=as_user(3)).status_code == 403
    client.post(f"/api/listings/{lid}/apply", json={"contact": "@cook", "message": "Здравствуйте"}, headers=as_user(3))
    assert client.get(f"/api/companies/{cid}/review-state", headers=as_user(3)).json()["can"] is True
    assert client.post(f"/api/companies/{cid}/reviews", json={"rating": 5, "text": "Всё как в объявлении"}, headers=as_user(3)).status_code == 201
    assert client.post(f"/api/companies/{cid}/reviews", json={"rating": 1}, headers=as_user(3)).status_code == 409          # once
    assert client.post(f"/api/companies/{cid}/reviews", json={"rating": 5}, headers=as_user(1)).status_code == 403          # not about yourself
    pub = client.get(f"/api/companies/{cid}").json()
    rid = pub["reviews"]["items"][0]["id"]
    assert pub["reviews"]["rating"] == 5.0 and pub["reviews"]["count"] == 1
    assert client.post(f"/api/company/{cid}/reviews/{rid}/reply", json={"text": "Спасибо!"}, headers=as_user(2)).status_code == 403
    assert client.post(f"/api/company/{cid}/reviews/{rid}/reply", json={"text": "Спасибо!"}, headers=as_user(1)).status_code == 200
    assert client.post(f"/api/admin/company-reviews/{rid}/hide", headers=ADMIN).status_code == 200
    assert client.get(f"/api/companies/{cid}").json()["reviews"]["count"] == 0


def test_company_pro_is_paid_from_the_company_balance_and_lifts_limits(client):
    cid = verified_company(client)
    team_member(client, cid, 2, "recruiter")
    assert client.post(f"/api/company/{cid}/pro", headers=as_user(2)).status_code == 403                  # money: owner and admins
    r = client.post(f"/api/company/{cid}/pro", headers=as_user(1))
    assert r.status_code == 400 and "Недостаточно KH" in r.json()["detail"]
    assert client.get(f"/api/company/{cid}/pro", headers=as_user(1)).json()["active"] is False
    client.post(f"/api/admin/companies/{cid}/credit", json={"stars": 1200}, headers=ADMIN)
    assert client.post(f"/api/company/{cid}/pro", headers=as_user(1)).json()["active"] is True
    assert client.post(f"/api/company/{cid}/pro", headers=as_user(1)).json()["active"] is True              # extended
    with SessionLocal() as db:
        assert db.get(co.Company, cid).balance_stars == 200                                                  # 1200 - 2 × 500
    ov = client.get(f"/api/company/{cid}", headers=as_user(1)).json()
    assert ov["pro"]["active"] and ov["limits"]["members"] == 5 + 10
    assert client.get(f"/api/companies/{cid}").json()["pro"] is True
    assert any(x["service"] == "Подписка «Про»" for x in client.get(f"/api/company/{cid}/balance", headers=as_user(1)).json()["history"])


def _jpeg():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (800, 400), (30, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


def test_company_cover_and_reply_templates(client, tmp_path):
    from app.config import settings
    settings.upload_dir = str(tmp_path)
    cid = verified_company(client)
    team_member(client, cid, 2, "recruiter")
    assert client.post(f"/api/company/{cid}/cover", files={"file": ("c.jpg", _jpeg(), "image/jpeg")}, headers=as_user(2)).status_code == 403   # the face: owner and admins
    r = client.post(f"/api/company/{cid}/cover", files={"file": ("c.jpg", _jpeg(), "image/jpeg")}, headers=as_user(1))
    assert r.status_code == 201 and r.json()["cover"]
    assert client.get(f"/api/companies/{cid}").json()["page"]["cover"] == r.json()["cover"]
    assert client.delete(f"/api/company/{cid}/cover", headers=as_user(1)).json()["cover"] is None
    assert client.get(f"/api/companies/{cid}").json()["page"]["cover"] is None
    # the team's ready-made replies
    t = client.post(f"/api/company/{cid}/reply-templates", json={"kind": "invite", "text": "Приходите в офис к 10:00"}, headers=as_user(2))
    assert t.status_code == 201
    assert client.post(f"/api/company/{cid}/reply-templates", json={"kind": "spam", "text": "Приходите в офис"}, headers=as_user(1)).status_code == 400
    assert [x["text"] for x in client.get(f"/api/company/{cid}/reply-templates", headers=as_user(1)).json()] == ["Приходите в офис к 10:00"]
    assert client.get(f"/api/company/{cid}/reply-templates", headers=as_user(3)).status_code == 404                # outsiders see nothing
    assert client.delete(f"/api/company/{cid}/reply-templates/{t.json()['id']}", headers=as_user(1)).status_code == 200


def test_public_web_pages_have_structured_data_and_company_page(client):
    cid = verified_company(client)
    lid = approved_company_listing(client, cid)
    page = client.get(f"/l/{lid}").text
    assert "application/ld+json" in page and '"@type": "JobPosting"' in page and '"hiringOrganization"' in page and "Кафе Сеул" in page
    comp = client.get(f"/k/{cid}")
    assert comp.status_code == 200 and "Кафе Сеул" in comp.text and f"/l/{lid}" in comp.text and '"@type": "Organization"' in comp.text
    assert client.get("/k/9999").status_code == 404
    assert f"/k/{cid}" in client.get("/sitemap.xml").text
    other = client.post("/api/listings", json={"category": "marketplace", "title": "Велосипед", "city": "Сеул", "description": "ok", "contact": "", "price_amount": 100000}, headers=as_user(2)).json()["id"]
    client.post(f"/api/admin/listings/{other}/approve", headers=ADMIN)
    assert '"@type": "Product"' in client.get(f"/l/{other}").text
