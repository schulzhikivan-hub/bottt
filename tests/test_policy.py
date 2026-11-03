import datetime as dt

from fastapi.testclient import TestClient

from app import policy
from app.database import SessionLocal
from app.main import app
from app.models import Listing, Placement
from app.payments import now
from tests.tg import reset_db


class Item:
    def __init__(self, name, kind=""):
        self.name, self.kind = name, kind

    def __repr__(self):
        return f"{self.name}:{self.kind}"


def kinds_of(items):
    return [x.kind for x in items]


def test_two_in_five_and_input_order_kept():
    items = [Item(f"a{i}", "vip") for i in range(4)] + [Item(f"r{i}") for i in range(10)]
    out = policy.arrange(items, lambda x: x.kind)
    assert len(out) == len(items) and {id(x) for x in out} == {id(x) for x in items}
    assert policy.window_ok(kinds_of(out)), kinds_of(out)
    regular = [x.name for x in out if not x.kind]
    assert regular == [f"r{i}" for i in range(10)]            # the order of ordinary items does not change
    assert sum(1 for x in out[:5] if x.kind == "vip") <= 2


def test_pinned_lane_is_at_most_two_and_not_counted_in_the_window():
    items = [Item("p1", "pin"), Item("p2", "pin"), Item("p3", "pin"), Item("v1", "vip"), Item("v2", "vip"), Item("r1"), Item("r2")]
    out = policy.arrange(items, lambda x: x.kind)
    assert [x.name for x in out[:2]] == ["p1", "p2"]
    assert policy.window_ok(kinds_of(out))
    assert "p3" in [x.name for x in out]                        # the third pinned item is not lost, it is just not pinned


def test_page_cap_keeps_paid_below_thirty_percent_in_the_head():
    items = [Item(f"v{i}", "vip") for i in range(5)] + [Item(f"r{i}") for i in range(15)]
    out = policy.arrange(items, lambda x: x.kind)
    head = out[:20 - 5]                                         # cap = ceil(0.3 * 20) = 6 paid in the body, the rest goes to the tail
    assert sum(1 for x in head if x.kind == "vip") <= 6
    assert policy.window_ok(kinds_of(out[:15]))


def test_only_paid_items_do_not_crash():
    items = [Item(f"v{i}", "vip") for i in range(6)]
    out = policy.arrange(items, lambda x: x.kind)
    assert len(out) == 6


def test_promo_kind_gets_no_lift_and_is_not_limited():
    items = [Item("a"), Item("b", "promo"), Item("c", "promo"), Item("d", "promo"), Item("e", "promo"), Item("f")]
    out = policy.arrange(items, lambda x: x.kind)
    assert [x.name for x in out] == ["a", "b", "c", "d", "e", "f"]


def test_ad_slots_rules():
    assert policy.ad_slots(2, 0) == []                           # fewer than three organic items: no ads
    assert policy.ad_slots(10, 0) == [3, 9]                      # after the third item, one per screen of six
    assert policy.ad_slots(10, 1) == []                          # first page only
    assert policy.ad_slots(10, 1, all_pages=True) == [3, 9]
    assert len(policy.ad_slots(40, 0, where="home")) == 2        # home: at most two
    assert len(policy.ad_slots(40, 0)) == 3                      # list: at most three per page


def listing(db, title, price, created_offset):
    row = Listing(author_telegram_id="1", category="marketplace", title=title, description="x" * 40, city="Сеул",
                  status="approved", contact="c", price_amount=price, expires_at=now() + dt.timedelta(days=5))
    db.add(row)
    db.commit()
    row.created_at = now() - dt.timedelta(minutes=created_offset)
    db.commit()
    return row.id


def place(db, lid, kind, promo=False):
    db.add(Placement(listing_id=lid, kind=kind, starts_at=now(), ends_at=now() + dt.timedelta(days=7), status="active", promo=promo))
    db.commit()


def titles(c, q=""):
    return [x["title"] for x in c.get("/api/catalog?category=marketplace" + q).json()["items"]]


def test_paid_vip_is_lifted_but_free_promo_vip_only_gets_a_mark():
    reset_db()
    c = TestClient(app)
    with SessionLocal() as db:
        old_paid = listing(db, "old paid", 100, 50)
        old_free = listing(db, "old free", 100, 40)
        listing(db, "new regular", 100, 1)
        place(db, old_paid, "vip")
        place(db, old_free, "vip", promo=True)
    got = c.get("/api/catalog?category=marketplace").json()["items"]
    names = [x["title"] for x in got]
    assert names[0] == "old paid"                                # a paid VIP stands first
    assert names.index("new regular") < names.index("old free")  # the free one is not lifted
    free = [x for x in got if x["title"] == "old free"][0]
    assert free["promo"] == "vip" and free["promo_free"] is True # but the mark is shown


def test_price_sort_is_not_broken_by_vip_but_pinning_stays_on_top():
    reset_db()
    c = TestClient(app)
    with SessionLocal() as db:
        cheap = listing(db, "cheap", 10, 5)
        mid = listing(db, "mid", 50, 4)
        dear = listing(db, "dear", 90, 3)
        place(db, dear, "vip")
    assert titles(c, "&sort=price_asc") == ["cheap", "mid", "dear"]          # VIP does not break «cheapest first»
    with SessionLocal() as db:
        place(db, dear, "pin")
    assert titles(c, "&sort=price_asc")[0] == "dear"                         # pinned item stays on top
