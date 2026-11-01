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
