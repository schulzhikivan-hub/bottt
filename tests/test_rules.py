import pytest
from fastapi.testclient import TestClient

from app import policy, rules
from app.database import SessionLocal, engine
from app.main import app
from app.models import AuditLog
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    rules.reset_cache()
    with TestClient(app) as c:
        accept_terms(c, 1)
        yield c
    rules.reset_cache()


def test_rules_list_change_validate_and_audit(client):
    groups = client.get("/api/admin/rules", headers=ADMIN).json()
    keys = {r["key"] for g in groups for r in g["rules"]}
    assert {"search.window", "reports.to_hide", "notify.quiet_from", "keep.notice_days"} <= keys
    assert client.put("/api/admin/rules/search.window", json={"value": 7}, headers=ADMIN).json()["value"] == 7
    assert client.put("/api/admin/rules/search.window", json={"value": 999}, headers=ADMIN).status_code == 400    # out of limits
    assert client.put("/api/admin/rules/nope", json={"value": 1}, headers=ADMIN).status_code == 404
    with SessionLocal() as db:
        row = db.query(AuditLog).filter_by(action="rule.change").one()
        assert row.details == "5 → 7"
    assert client.post("/api/admin/rules/search.window/reset", headers=ADMIN).json()["value"] == 5
    assert client.get("/api/admin/rules").status_code == 401                                                          # staff only


def test_paid_window_rule_can_be_switched_off(client):
    items = ["vip", "vip", "vip", "", "", ""]
    kinds = policy.arrange(items, lambda x: x)
    assert policy.window_ok(kinds)                                                                                      # 2 in 5 holds
    client.put("/api/admin/rules/search.paid_rule", json={"value": False}, headers=ADMIN)
    client.put("/api/admin/rules/search.cap_rule", json={"value": False}, headers=ADMIN)
    assert policy.arrange(items, lambda x: x) == items                                                                 # as they came
    client.put("/api/admin/rules/search.paid_in_window", json={"value": 1}, headers=ADMIN)
    client.put("/api/admin/rules/search.paid_rule", json={"value": True}, headers=ADMIN)
    assert not policy.window_ok(["vip", "vip", "", "", ""])                                                             # now only one is allowed


def test_ads_never_before_third_even_if_rule_is_low(client):
    client.put("/api/admin/rules/search.ad_after", json={"value": 1}, headers=ADMIN)                                   # below the floor
    assert client.put("/api/admin/rules/search.ad_after", json={"value": 2}, headers=ADMIN).status_code == 400


def test_undo_window_and_report_threshold_come_from_rules(client):
    client.put("/api/admin/rules/reports.to_hide", json={"value": 1}, headers=ADMIN)
    assert rules.val("reports.to_hide") == 1
    client.put("/api/admin/rules/moderation.undo_minutes", json={"value": 5}, headers=ADMIN)
    assert rules.val("moderation.undo_minutes") == 5
