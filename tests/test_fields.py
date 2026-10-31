import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def make(client, user=1, publish=True, **fields):
    body = {"category": "job", "title": "Объявление", "city": "Асан", "description": "ok", "contact": ""}
    body.update(fields)
    r = client.post("/api/listings", json=body, headers=as_user(user))
    assert r.status_code == 201, r.text
    if publish:
        client.post(f"/api/admin/listings/{r.json()['id']}/approve", headers=ADMIN)
    return r.json()["id"]


def ids(client, **params):
    return sorted(x["id"] for x in client.get("/api/catalog", params=params).json()["items"])


def item(client, lid):
    return client.get(f"/api/listings/{lid}").json()


def test_lists_for_forms_come_from_the_server(client):
    data = client.get("/api/fields").json()
    assert {"value": "E-9", "label": "E-9"} in data["visas"]
    assert {"value": "night", "label": "Ночная смена"} in data["schedule"]
    assert {"value": "dorm", "label": "Место в общежитии"} in data["housing_type"]
    assert len(data["goods_category"]) == 9


def test_vacancy_keeps_all_its_details(client):
    lid = make(client, title="Работа на заводе", price_amount=2_800_000, salary_period="month",
               visas=["F-4", "E-9"], employment="full", schedule="shift", housing_provided=True,
               details={"company": "Samin Motors", "position": "Оператор", "vacancies": 5,
                        "start_date": "2026-11-15", "experience": False, "transport": True, "salary_max": 3_200_000})
    x = item(client, lid)
    assert x["visas"] == ["E-9", "F-4"] and x["schedule"] == "shift" and x["salary_period"] == "month"
    assert x["details"]["company"] == "Samin Motors" and x["details"]["vacancies"] == 5
    assert x["details"]["experience"] is False  # False is a real answer, not "empty"


def test_filter_vacancies_by_visa_schedule_and_employment(client):
    a = make(client, visas=["E-9"], schedule="day", employment="full")
    b = make(client, visas=["F-4", "F-2"], schedule="night", employment="part")
    c = make(client)
    assert ids(client, visa="E-9") == [a]
    assert ids(client, visa="F-2") == [b]
    assert ids(client, schedule="night") == [b]
    assert ids(client, employment="full") == [a]
    assert ids(client, visa="D-2") == []
    assert c not in ids(client, visa="E-9")


def test_visa_filter_does_not_confuse_similar_codes(client):
    a = make(client, visas=["E-7"])
    assert ids(client, visa="E-9") == []
    assert ids(client, visa="E-7") == [a]


def test_housing_details_and_filters(client):
    cheap = make(client, category="housing", housing_type="room", deposit=1_000_000, area_m2=15, pets_allowed=True,
                 furnished=True, price_amount=400_000, details={"maintenance_fee": 50_000, "floor": 3, "station": "Каннам"})
    big = make(client, category="housing", housing_type="apartment", deposit=10_000_000, area_m2=60, rooms=2,
               price_amount=1_200_000)
    assert ids(client, category="housing", housing_type="room") == [cheap]
    assert ids(client, category="housing", deposit_max=2_000_000) == [cheap]
    assert ids(client, category="housing", area_min=30) == [big]
    assert ids(client, category="housing", pets_allowed="true") == [cheap]
    assert ids(client, category="housing", furnished="true") == [cheap]
    assert item(client, cheap)["details"]["station"] == "Каннам"


def test_goods_details_and_filters(client):
    phone = make(client, category="marketplace", goods_category="phones", negotiable=True, condition="used",
                 details={"brand": "Apple", "delivery": True})
    make(client, category="marketplace", goods_category="furniture")
    assert ids(client, category="marketplace", goods_category="phones") == [phone]
    assert item(client, phone)["negotiable"] is True


def test_details_of_other_sections_are_dropped(client):
    lid = make(client, category="housing", visas=["E-9"], schedule="night", housing_provided=True,
               goods_category="phones", rooms=2)
    x = item(client, lid)
    assert x["visas"] == [] and x["schedule"] is None and x["housing_provided"] is None
    assert x["goods_category"] is None and x["rooms"] == 2


def test_unknown_detail_keys_are_ignored_and_bad_ones_refused(client):
    lid = make(client, details={"company": "ООО Корея", "hacker": "x", "vacancies": 3})
    assert item(client, lid)["details"] == {"company": "ООО Корея", "vacancies": 3}
    bad = {"category": "job", "title": "Работа", "city": "Асан", "details": {"start_date": "завтра"}}
    assert client.post("/api/listings", json=bad, headers=as_user(1)).status_code == 422
    bad["details"] = {"vacancies": 0}
    assert client.post("/api/listings", json=bad, headers=as_user(1)).status_code == 422


def test_invalid_values_are_refused(client):
    for field, value in [("visas", ["X-1"]), ("schedule", "weekends"), ("housing_type", "castle"),
                         ("goods_category", "weapons"), ("employment", "slave"), ("area_m2", 0), ("deposit", -1)]:
        body = {"category": "job", "title": "Работа", "city": "Асан", field: value}
        assert client.post("/api/listings", json=body, headers=as_user(1)).status_code == 422, field


def test_search_finds_company_position_station_and_brand(client):
    job = make(client, details={"company": "Hyundai Mobis", "position": "Сварщик"})
    flat = make(client, category="housing", details={"station": "Хондэ"})
    phone = make(client, category="marketplace", details={"brand": "Samsung"})
    assert ids(client, q="hyundai") == [job]
    assert ids(client, q="сварщик") == [job]
    assert ids(client, q="хондэ") == [flat]
    assert ids(client, q="samsung") == [phone]


def test_edit_replaces_details_and_visas(client):
    lid = make(client, visas=["E-9"], details={"company": "Старая"})
    r = client.put(f"/api/listings/{lid}", json={"visas": ["F-4"], "details": {"company": "Новая", "vacancies": 2}},
                   headers=as_user(1))
    assert r.status_code == 200
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    x = item(client, lid)
    assert x["visas"] == ["F-4"] and x["details"] == {"company": "Новая", "vacancies": 2}
    assert ids(client, visa="F-4") == [lid] and ids(client, visa="E-9") == []


def test_listing_without_details_has_empty_defaults(client):
    x = item(client, make(client))
    assert x["details"] == {} and x["visas"] == []
